"""Attempt-scoped learner-hint GET (ADR-063 §8.2, PK-V1-HINT-B)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from aieos.domains.learning.application.command_support import (
    assignment_or_not_found,
    conceal_membership_denied,
    require_attempt_matches_assignment,
    require_human_learner,
    require_locked_assignment_consumable,
    require_membership,
    require_owner,
)
from aieos.domains.learning.application.errors import (
    AttemptNotFound,
    InvalidAttemptState,
    InvalidLearnerRequest,
    LearnerClassMembershipDenied,
)
from aieos.domains.learning.application.learner_membership import (
    SchoolContextLearnerMembershipAuthority,
)
from aieos.domains.learning.application.models import LearnerHintItem
from aieos.domains.learning.application.ports import (
    StudentLearningCommandUnitOfWorkFactory,
)
from aieos.domains.learning.domain.identities import AttemptId
from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)


@dataclass(frozen=True, slots=True)
class AttemptHintPreview:
    teaching_assignment_id: UUID
    class_ref: str
    content_id: UUID
    content_version_id: UUID


@dataclass(frozen=True, slots=True)
class LearnerHintsReadModel:
    attempt_id: UUID
    content_version_id: UUID
    hints: tuple[LearnerHintItem, ...]


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


class GetLearnerHintsService:
    def __init__(
        self,
        uow_factory: StudentLearningCommandUnitOfWorkFactory,
        membership: SchoolContextLearnerMembershipAuthority,
        classification: CurrentPrincipalClassificationAuthority,
    ) -> None:
        self._uow_factory = uow_factory
        self._membership = membership
        self._classification = classification

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        attempt_id: UUID,
        *,
        now: datetime | None = None,
    ) -> LearnerHintsReadModel:
        require_human_learner(self._classification, principal_id)
        evaluated_at = _now(now)
        try:
            typed_attempt_id = AttemptId(attempt_id)
        except Exception as exc:
            raise InvalidLearnerRequest("attempt identity is invalid") from exc

        with self._uow_factory(execution_tenant_id) as preview:
            loaded = preview.attempts.get(typed_attempt_id)
            if loaded is None:
                raise AttemptNotFound("LearnerAttempt is not visible")
            require_owner(loaded, principal_id)
            preview_state = AttemptHintPreview(
                teaching_assignment_id=loaded.teaching_assignment_id,
                class_ref=loaded.class_ref,
                content_id=loaded.content_id,
                content_version_id=loaded.content_version_id,
            )
        try:
            require_membership(
                self._membership,
                execution_tenant_id,
                principal_id,
                preview_state.class_ref,
            )
        except LearnerClassMembershipDenied as exc:
            raise conceal_membership_denied(exc)

        hints: tuple[LearnerHintItem, ...] = ()
        with self._uow_factory(execution_tenant_id) as uow:
            previewed = uow.attempts.get(typed_attempt_id)
            if previewed is None:
                raise AttemptNotFound("LearnerAttempt is not visible")
            require_owner(previewed, principal_id)
            if (
                previewed.teaching_assignment_id != preview_state.teaching_assignment_id
                or previewed.class_ref != preview_state.class_ref
                or previewed.content_id != preview_state.content_id
                or previewed.content_version_id != preview_state.content_version_id
            ):
                raise AttemptNotFound("LearnerAttempt is not visible")

            locked_assignment = assignment_or_not_found(
                uow.get_assignment_for_update(preview_state.teaching_assignment_id)
            )
            require_locked_assignment_consumable(locked_assignment, now=evaluated_at)
            locked_attempt = uow.attempts.get_for_update(typed_attempt_id)
            if locked_attempt is None:
                raise AttemptNotFound("LearnerAttempt is not visible")
            require_owner(locked_attempt, principal_id)
            require_attempt_matches_assignment(locked_attempt, locked_assignment)
            if locked_attempt.lifecycle_state is not AttemptLifecycleState.IN_PROGRESS:
                raise InvalidAttemptState(
                    "learner hints are only available for IN_PROGRESS attempts"
                )
            hints = uow.load_governed_worksheet_learner_hints(
                locked_attempt.content_id,
                locked_attempt.content_version_id,
            )
            if locked_attempt.lifecycle_state is not AttemptLifecycleState.IN_PROGRESS:
                raise InvalidAttemptState(
                    "learner hints are only available for IN_PROGRESS attempts"
                )
            uow.commit()

        return LearnerHintsReadModel(
            attempt_id=attempt_id,
            content_version_id=preview_state.content_version_id,
            hints=hints,
        )
