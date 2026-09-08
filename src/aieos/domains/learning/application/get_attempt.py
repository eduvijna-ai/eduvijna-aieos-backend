"""Learner-owned attempt read."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from aieos.domains.learning.application.command_support import (
    conceal_membership_denied,
    require_human_learner,
    require_locked_assignment_consumable,
    require_membership,
    require_owner,
)
from aieos.domains.learning.application.errors import (
    AttemptNotFound,
    InvalidLearnerRequest,
    LearnerClassMembershipDenied,
)
from aieos.domains.learning.application.learner_membership import (
    SchoolContextLearnerMembershipAuthority,
)
from aieos.domains.learning.application.models import AttemptReadModel, attempt_read_model
from aieos.domains.learning.application.ports import (
    StudentLearningCommandUnitOfWorkFactory,
)
from aieos.domains.learning.domain.identities import AttemptId
from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)


class GetAttemptService:
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
    ) -> AttemptReadModel:
        require_human_learner(self._classification, principal_id)
        try:
            typed_attempt_id = AttemptId(attempt_id)
        except Exception as exc:
            raise InvalidLearnerRequest("attempt identity is invalid") from exc
        with self._uow_factory(execution_tenant_id) as uow:
            loaded = uow.attempts.get(typed_attempt_id)
            if loaded is None:
                raise AttemptNotFound("LearnerAttempt is not visible")
            require_owner(loaded, principal_id)
            if loaded.lifecycle_state is AttemptLifecycleState.IN_PROGRESS:
                try:
                    require_membership(
                        self._membership,
                        execution_tenant_id,
                        principal_id,
                        loaded.class_ref,
                    )
                except LearnerClassMembershipDenied as exc:
                    raise conceal_membership_denied(exc)
                assignment = uow.get_assignment(loaded.teaching_assignment_id)
                if assignment is None:
                    raise AttemptNotFound("LearnerAttempt is not visible")
                require_locked_assignment_consumable(
                    assignment, now=datetime.now(UTC)
                )
            responses = tuple(uow.responses.list_for_attempt(loaded.attempt_id))
        return attempt_read_model(loaded, responses)
