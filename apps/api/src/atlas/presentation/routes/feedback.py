"""POST /messages/{id}/feedback (M09; docs/12 §4.3): thumbs verdicts
feeding the eval datasets."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from atlas.domain.conversation.entities import Feedback, FeedbackRating
from atlas.presentation.composition import Container
from atlas.presentation.dependencies import get_container, get_user_id, get_workspace_id

router = APIRouter(prefix="/api/v1", tags=["feedback"])

MAX_COMMENT_LENGTH = 2_000


class FeedbackBody(BaseModel):
    rating: FeedbackRating
    categories: list[str] = Field(default_factory=list, max_length=10)
    comment: str | None = Field(default=None, min_length=1, max_length=MAX_COMMENT_LENGTH)


class FeedbackView(BaseModel):
    id: UUID
    message_id: UUID
    rating: FeedbackRating
    categories: list[str]
    comment: str | None
    created_at: datetime

    @classmethod
    def from_feedback(cls, feedback: Feedback) -> "FeedbackView":
        return cls(
            id=feedback.id,
            message_id=feedback.message_id,
            rating=feedback.rating,
            categories=list(feedback.categories),
            comment=feedback.comment,
            created_at=feedback.created_at,
        )


@router.post("/messages/{message_id}/feedback", status_code=201)
async def record_feedback(
    message_id: UUID,
    body: FeedbackBody,
    container: Annotated[Container, Depends(get_container)],
    workspace_id: Annotated[UUID, Depends(get_workspace_id)],
    user_id: Annotated[UUID, Depends(get_user_id)],
) -> FeedbackView:
    feedback = await container.record_feedback().execute(
        workspace_id,
        message_id,
        user_id=user_id,
        rating=body.rating,
        categories=tuple(body.categories),
        comment=body.comment,
    )
    return FeedbackView.from_feedback(feedback)
