"""Shared current-authority helpers for learner assignment commands."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from aieos.domains.learning.application.errors import (
    AssignmentClosedOrCancelled,
    AssignmentNotCurrentlyConsumable,
    AssignmentNotFound,
    AssignmentNotYetAvailable,
    ContentNotLearnerConsumable,
    ExactContentVersionNotFound,
    IdempotencyKeyReused,
    LearnerAttemptForbidden,
    LearnerClassMembershipDenied,
    PersistenceInvariantViolation,
)
from aieos.domains.learning.application.learner_membership import (
    SchoolContextLearnerMembershipAuthority,
)
from aieos.domains.learning.application.learner_projection import project_learner_resource
from aieos.domains.learning.application.models import (
    ASSIGNMENT_LIFECYCLE_ACTIVE,
    ASSIGNMENT_LIFECYCLE_CANCELLED,
    ASSIGNMENT_LIFECYCLE_CLOSED,
    AssignmentConsumptionView,
    AttemptReadModel,
    LearnerResource,
    attempt_read_model,
    currently_consumable,
)
from aieos.domains.learning.domain.attempt import LearnerAttempt
from aieos.domains.learning.domain.identities import AttemptId
from aieos.platform.idempotency.models import IdempotencyScope
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)
from aieos.platform.security.context import UnauthorizedError


def require_human_learner(
    classification: CurrentPrincipalClassificationAuthority,
    principal_id: UUID,
) -> None:
    try:
        classification.require_current_human_principal(principal_id)
    except UnauthorizedError:
        raise


def require_membership(
    membership: SchoolContextLearnerMembershipAuthority,
    tenant_id: UUID,
    learner_principal_id: UUID,
    class_ref: str,
) -> None:
    membership.require_current_membership(tenant_id, learner_principal_id, class_ref)


def conceal_membership_denied(exc: LearnerClassMembershipDenied) -> AssignmentNotFound:
    return AssignmentNotFound("TeachingAssignment is not visible")


def require_owner(attempt: LearnerAttempt, learner_principal_id: UUID) -> None:
    if attempt.learner_principal_id != learner_principal_id:
        raise LearnerAttemptForbidden("LearnerAttempt is not visible")


def established_idempotent_replay(
    uow,
    *,
    scope: IdempotencyScope,
    fingerprint: str,
    principal_id: UUID,
    missing_message: str,
) -> AttemptReadModel | None:
    """Replay an already-committed exact outcome without live current authority.

    Caller must already have authenticated the current HUMAN Principal and
    bound the idempotency scope to that same learner. Does not acquire the
    idempotency row lock; the fresh-mutation path rechecks under acquire_scope.
    """

    existing = uow.idempotency.get(scope)
    if existing is None:
        return None
    if existing.request_fingerprint_sha256 != fingerprint:
        raise IdempotencyKeyReused("idempotency key already bound")
    replayed = uow.attempts.get(AttemptId(existing.result_content_id))
    if replayed is None:
        raise PersistenceInvariantViolation(missing_message)
    require_owner(replayed, principal_id)
    responses = tuple(uow.responses.list_for_attempt(replayed.attempt_id))
    return attempt_read_model(replayed, responses)


def require_locked_assignment_consumable(
    assignment: AssignmentConsumptionView,
    *,
    now: datetime,
) -> None:
    if assignment.lifecycle_state in {
        ASSIGNMENT_LIFECYCLE_CLOSED,
        ASSIGNMENT_LIFECYCLE_CANCELLED,
    }:
        raise AssignmentClosedOrCancelled(
            "TeachingAssignment is CLOSED or CANCELLED"
        )
    if assignment.lifecycle_state != ASSIGNMENT_LIFECYCLE_ACTIVE:
        raise AssignmentNotCurrentlyConsumable(
            "TeachingAssignment is not currently consumable"
        )
    if assignment.available_from > now:
        raise AssignmentNotYetAvailable(
            "TeachingAssignment is not yet available"
        )


def require_attempt_matches_assignment(
    attempt: LearnerAttempt, assignment: AssignmentConsumptionView
) -> None:
    if (
        attempt.teaching_assignment_id != assignment.assignment_id
        or attempt.content_id != assignment.content_id
        or attempt.content_version_id != assignment.content_version_id
        or attempt.class_ref != assignment.class_ref
    ):
        raise AssignmentNotCurrentlyConsumable(
            "LearnerAttempt is not bound to the locked TeachingAssignment"
        )


def load_learner_resource(uow, assignment: AssignmentConsumptionView) -> LearnerResource:
    assigned = uow.load_exact_assigned_content(
        assignment.content_id, assignment.content_version_id
    )
    if assigned is None:
        raise ExactContentVersionNotFound(
            "exact assigned ContentVersion is not visible"
        )
    try:
        return project_learner_resource(assigned)
    except ContentNotLearnerConsumable:
        raise


def assignment_or_not_found(
    assignment: AssignmentConsumptionView | None,
) -> AssignmentConsumptionView:
    if assignment is None:
        raise AssignmentNotFound("TeachingAssignment is not visible")
    return assignment


def require_visible_current_assignment(
    assignment: AssignmentConsumptionView,
    membership_class_refs: set[str],
    *,
    now: datetime,
) -> None:
    if assignment.class_ref not in membership_class_refs:
        raise AssignmentNotFound("TeachingAssignment is not visible")
    if assignment.lifecycle_state in {
        ASSIGNMENT_LIFECYCLE_CLOSED,
        ASSIGNMENT_LIFECYCLE_CANCELLED,
    }:
        raise AssignmentNotFound("TeachingAssignment is not visible")
    if assignment.lifecycle_state != ASSIGNMENT_LIFECYCLE_ACTIVE:
        raise AssignmentNotFound("TeachingAssignment is not visible")
    if assignment.available_from > now:
        raise AssignmentNotYetAvailable(
            "TeachingAssignment is not yet available"
        )
    if not currently_consumable(assignment, now):
        raise AssignmentNotCurrentlyConsumable(
            "TeachingAssignment is not currently consumable"
        )
