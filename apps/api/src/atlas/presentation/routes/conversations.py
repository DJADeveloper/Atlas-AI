"""Conversations & chat endpoints (M07; docs/12 §4.3).

`POST /conversations/{id}/messages` is the flagship: pre-stream
failures are plain problem+json; once generation starts, the exchange
is pumped into the stream buffer by a background task and the HTTP
response merely tails it. A dropped connection kills the tail, never
the generation — `GET /messages/{id}/stream` + `Last-Event-ID` resumes
from the buffer for 15 minutes after the terminal event (§4.3.5).
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field

from atlas.application.chat import ChatStreamEvent, ConversationDetail, StreamStarted
from atlas.domain.conversation.entities import MAX_TITLE_LENGTH, Conversation, Message, MessageRole
from atlas.observability.logging import current_trace_id
from atlas.presentation.composition import Container
from atlas.presentation.dependencies import get_container, get_workspace_id
from atlas.presentation.sse import pump_stream, sse_response
from atlas.shared.errors import NotFound, StreamInProgress

router = APIRouter(prefix="/api/v1", tags=["conversations"])

MAX_MESSAGE_LENGTH = 32_000


class CreateConversationBody(BaseModel):
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    # Accepted for forward-compatibility; projects arrive at M13.
    project_id: UUID | None = None


class ConversationView(BaseModel):
    id: UUID
    title: str | None
    project_id: UUID | None = None  # M13
    message_count: int
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    async def build(cls, container: Container, conversation: Conversation) -> "ConversationView":
        async with container.unit_of_work() as uow:
            count = await uow.messages.count_for_conversation(
                conversation.workspace_id, conversation.id
            )
            latest = await uow.messages.latest_for_conversation(
                conversation.workspace_id, conversation.id
            )
        return cls(
            id=conversation.id,
            title=conversation.title,
            message_count=count,
            last_message_at=latest.created_at if latest else None,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )


class ConversationListResponse(BaseModel):
    items: list[ConversationView]


class UsageView(BaseModel):
    model: str
    provider: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None


class MessageView(BaseModel):
    id: UUID
    role: MessageRole
    content: str
    abstained: bool
    citations: list[dict[str, object]] = Field(default_factory=list)  # populated at M08
    usage: UsageView | None
    created_at: datetime

    @classmethod
    def from_message(cls, message: Message) -> "MessageView":
        usage = None
        if message.model is not None:
            usage = UsageView(
                model=message.model,
                provider=message.provider,
                input_tokens=message.input_tokens,
                output_tokens=message.output_tokens,
                cost_usd=message.cost_usd,
            )
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            abstained=message.abstained,
            usage=usage,
            created_at=message.created_at,
        )


class MessageListResponse(BaseModel):
    items: list[MessageView]


class SendMessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: CreateConversationBody,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> ConversationView:
    conversation = await container.create_conversation().execute(workspace_id, title=body.title)
    return await ConversationView.build(container, conversation)


@router.get("/conversations")
async def list_conversations(
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> ConversationListResponse:
    conversations = await container.list_conversations().execute(workspace_id)
    return ConversationListResponse(
        items=[await ConversationView.build(container, c) for c in conversations]
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> ConversationView:
    detail = await container.get_conversation().execute(workspace_id, conversation_id)
    return await ConversationView.build(container, detail.conversation)


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: UUID,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
) -> MessageListResponse:
    detail: ConversationDetail = await container.get_conversation().execute(
        workspace_id, conversation_id
    )
    return MessageListResponse(
        items=[MessageView.from_message(message) for message in detail.messages]
    )


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: UUID,
    body: SendMessageBody,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> Response:
    buffer = container.stream_buffer

    # A replayed key re-attaches to the existing stream (docs/12 §4.3)
    # instead of generating a second answer.
    scoped_key = f"{conversation_id}:{idempotency_key}"
    existing = await buffer.recall_message_for(scoped_key)
    if existing is not None and await buffer.exists(existing):
        return sse_response(buffer, existing)

    if not await buffer.try_claim_conversation(conversation_id):
        raise StreamInProgress(f"conversation {conversation_id} is already streaming an answer")

    events = container.stream_answer().execute(
        workspace_id, conversation_id, body.content, trace_id=current_trace_id()
    )
    try:
        started = await anext(events)
    except BaseException:
        await buffer.release_conversation(conversation_id)
        raise  # pre-stream failures stay plain problem+json (§4.3)
    if not isinstance(started, StreamStarted):  # generator contract
        await buffer.release_conversation(conversation_id)
        raise TypeError(f"StreamAnswer yielded {type(started).__name__} before StreamStarted")

    await buffer.remember_message_for(scoped_key, started.message_id)
    _spawn_pump(request, events, started, container)
    return sse_response(buffer, started.message_id)


@router.get("/messages/{message_id}/stream")
async def resume_stream(
    message_id: UUID,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
) -> Response:
    del workspace_id  # resolved for auth parity; buffer keys are message-scoped
    buffer = container.stream_buffer
    if not await buffer.exists(message_id):
        raise NotFound(
            f"no buffered stream for message {message_id}; "
            "fetch the persisted message from the conversation instead"
        )
    return sse_response(
        buffer, message_id, after=last_event_id if last_event_id is not None else -1
    )


def _spawn_pump(
    request: Request,
    events: AsyncIterator[ChatStreamEvent],
    started: StreamStarted,
    container: Container,
) -> None:
    task = asyncio.create_task(
        pump_stream(events, started, container.stream_buffer),
        name=f"sse-pump-{started.message_id}",
    )
    registry: set[asyncio.Task[None]] = request.app.state.pump_tasks
    registry.add(task)
    task.add_done_callback(registry.discard)
