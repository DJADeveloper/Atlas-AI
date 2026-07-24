"""Feedback on answers (M09; docs/12 §4.3): one thumbs verdict per
call, immutable, feeding the eval datasets (M11). Only assistant
messages can be rated — rating your own question is a client bug."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from atlas.application.ports import UnitOfWork
from atlas.domain.conversation.entities import Feedback, FeedbackRating
from atlas.shared.errors import NotFound, ValidationFailed


@dataclass(frozen=True)
class RecordFeedback:
    uow_factory: Callable[[], UnitOfWork]

    async def execute(
        self,
        workspace_id: UUID,
        message_id: UUID,
        *,
        user_id: UUID,
        rating: FeedbackRating,
        categories: tuple[str, ...] = (),
        comment: str | None = None,
    ) -> Feedback:
        async with self.uow_factory() as uow:
            message = await uow.messages.get(workspace_id, message_id)
            if message is None:
                raise NotFound(f"message {message_id} not found")
            if message.role != "assistant":
                raise ValidationFailed("feedback applies to assistant answers only")
            feedback = Feedback(
                message_id=message_id,
                user_id=user_id,
                rating=rating,
                categories=categories,
                comment=comment,
            )
            await uow.feedback.add(feedback)
            await uow.commit()
        return feedback
