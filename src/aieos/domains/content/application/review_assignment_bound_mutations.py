"""Assignment-bound hint-bearing mutation authority (ADR-AIEOS-064 O64-03-C / O64-05)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.content.application.errors import (
    IdempotencyKeyReused,
    PersistenceInvariantViolation,
    ReviewAssignmentConflict,
    ReviewAssignmentForbidden,
    ReviewAssignmentNotAllowed,
    ReviewForbidden,
    WorksheetLearnerHintAttestationInvalid,
)
from aieos.domains.content.application.ports import (
    ContentUnitOfWork,
    ReviewAssignmentAuthorizationPort,
    ReviewAuthorizationPort,
)
from aieos.domains.content.application.review_assignment_denial_audit import (
    AssignmentDenialRecord,
    ReviewAssignmentDenialAuditPort,
    record_assignment_denial,
    reason_for_assignment_error,
)
from aieos.domains.content.application.review_assignment_guard import (
    ReviewerUseGuardContext,
    ReviewerUseGuardDependencies,
    ReviewerUseMode,
    enforce_reviewer_use_guard,
)
from aieos.domains.content.domain.identities import (
    ContentId,
    ContentVersionId,
    ReviewAssignmentId,
)
from aieos.domains.content.domain.review_assignment import ActiveReviewAssignment
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.content.domain.version import ContentVersion
from aieos.domains.education.worksheet_hint_safety import version_requires_worksheet_hint_attestation
from aieos.domains.teaching.application.errors import (
    ClassRefNotAssignable,
    SchoolContextContractError,
    SchoolContextUnavailable,
)
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthority
from aieos.platform.security import UnauthorizedError
from aieos.platform.security.context import AuthorizationUnavailableError
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)

CONTENT_REVIEW_MUTATION_DENIED = "content.review.mutation.denied"

_MUTATION_DENIED_EXCEPTIONS = (
    ReviewAssignmentForbidden,
    ReviewAssignmentNotAllowed,
    ReviewForbidden,
    UnauthorizedError,
    SchoolContextUnavailable,
    ClassRefNotAssignable,
    SchoolContextContractError,
    AuthorizationUnavailableError,
    ReviewAssignmentConflict,
    PersistenceInvariantViolation,
    WorksheetLearnerHintAttestationInvalid,
    IdempotencyKeyReused,
)


@dataclass(frozen=True, slots=True)
class AssignmentBoundMutationDependencies:
    assign_authorization: ReviewAssignmentAuthorizationPort
    review_authorization: ReviewAuthorizationPort
    principal_classification: CurrentPrincipalClassificationAuthority
    reviewer_eligibility: SchoolContextReviewerEligibility
    issuer_class_authority: SchoolContextClassAuthority
    denial_audit: ReviewAssignmentDenialAuditPort
    clock: Callable[[], datetime]


def assignment_bound_deps_configured(
    deps: AssignmentBoundMutationDependencies | None,
) -> bool:
    return deps is not None


def _guard_deps(deps: AssignmentBoundMutationDependencies) -> ReviewerUseGuardDependencies:
    return ReviewerUseGuardDependencies(
        assign_authorization=deps.assign_authorization,
        authorization=deps.review_authorization,
        principal_classification=deps.principal_classification,
        reviewer_eligibility=deps.reviewer_eligibility,
        issuer_class_authority=deps.issuer_class_authority,
        clock=deps.clock,
    )


def record_mutation_denial(
    deps: AssignmentBoundMutationDependencies,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    correlation_id: UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    exc: BaseException,
) -> None:
    record_assignment_denial(
        deps.denial_audit,
        AssignmentDenialRecord(
            tenant_id=tenant_id,
            principal_id=principal_id,
            action=CONTENT_REVIEW_MUTATION_DENIED,
            correlation_id=correlation_id,
            reason_code=reason_for_assignment_error(exc),
            content_id=content_id.value,
            version_id=version_id.value,
        ),
    )


def requires_hint_assignment(version: ContentVersion) -> bool:
    return version_requires_worksheet_hint_attestation(
        schema_id=str(version.schema_id),
        schema_version=int(version.schema_version),
        payload=version.payload.body,
    )


def hint_mutation_replay_is_terminal(
    uow: ContentUnitOfWork,
    *,
    content_id: ContentId,
    version_id: ContentVersionId,
) -> bool:
    """Terminal replay only when an immutable review decision exists for the version."""
    return uow.reviews.get_for_version(content_id, version_id) is not None


def require_assignment_authority_for_new_effect(
    uow: ContentUnitOfWork,
    deps: AssignmentBoundMutationDependencies,
    *,
    execution_tenant_id: UUID,
    principal_id: UUID,
    effective_actor_id: UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
) -> tuple[ActiveReviewAssignment, datetime]:
    head = uow.contents.get_head_for_update(content_id)
    if head is None or head.tenant_id != execution_tenant_id:
        raise ReviewAssignmentNotAllowed("content head missing")
    if uow.review_assignments.get_slot_for_update(content_id, version_id) is None:
        raise ReviewAssignmentConflict("assignment slot missing")
    effect_at = deps.clock()
    from aieos.domains.content.application.review_assignment_state import (
        resolve_slot_active_assignment,
    )

    active = resolve_slot_active_assignment(
        uow.review_assignments,
        tenant_id=execution_tenant_id,
        content_id=content_id,
        version_id=version_id,
        now=effect_at,
    )
    if active is None:
        raise ReviewAssignmentConflict("no active assignment binding at effect time")
    guard_ctx = ReviewerUseGuardContext(
        execution_tenant_id=execution_tenant_id,
        principal_id=principal_id,
        effective_actor_id=effective_actor_id,
        content_id=content_id,
        version_id=version_id,
        assignment_id=active.assignment_id,
        mode=ReviewerUseMode.PROTECTED_EFFECT,
        effect_at=effect_at,
    )
    guard_active = enforce_reviewer_use_guard(
        uow, ctx=guard_ctx, deps=_guard_deps(deps)
    )
    return guard_active, effect_at


def validate_bound_assignment_replay(
    uow: ContentUnitOfWork,
    deps: AssignmentBoundMutationDependencies,
    *,
    execution_tenant_id: UUID,
    principal_id: UUID,
    effective_actor_id: UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    bound_assignment_id: ReviewAssignmentId,
    fingerprint_assignment_id: str | None,
    terminal_outcome: bool,
) -> None:
    if fingerprint_assignment_id is None:
        raise WorksheetLearnerHintAttestationInvalid(
            "idempotency replay requires assignment binding"
        )
    if str(bound_assignment_id) != fingerprint_assignment_id:
        raise WorksheetLearnerHintAttestationInvalid(
            "idempotency replay assignment binding mismatch"
        )
    uow.contents.get_head_for_update(content_id)
    if uow.review_assignments.get_slot_for_update(content_id, version_id) is None:
        raise ReviewAssignmentConflict("assignment slot missing")
    mode = (
        ReviewerUseMode.TERMINAL_RECEIPT
        if terminal_outcome
        else ReviewerUseMode.PROTECTED_EFFECT
    )
    effect_at = deps.clock()
    guard_ctx = ReviewerUseGuardContext(
        execution_tenant_id=execution_tenant_id,
        principal_id=principal_id,
        effective_actor_id=effective_actor_id,
        content_id=content_id,
        version_id=version_id,
        assignment_id=bound_assignment_id,
        mode=mode,
        effect_at=effect_at,
    )
    enforce_reviewer_use_guard(uow, ctx=guard_ctx, deps=_guard_deps(deps))


def assert_approve_attestation_assignment_match(
    attestation_assignment_id: ReviewAssignmentId | None,
    active_assignment_id: ReviewAssignmentId,
) -> None:
    if attestation_assignment_id is None:
        raise WorksheetLearnerHintAttestationInvalid(
            "approve requires assignment-bound attestation"
        )
    if attestation_assignment_id != active_assignment_id:
        raise WorksheetLearnerHintAttestationInvalid(
            "attestation assignment does not match active slot binding"
        )


def stewardship_allows_protected_replay(head_state: str) -> bool:
    return head_state == StewardshipState.IN_REVIEW.value


def raise_assignment_unavailable() -> None:
    raise ReviewAssignmentNotAllowed(
        "assignment-bound mutation authority is unavailable"
    )


def fingerprint_assignment_id(
    *,
    assignment_id_for_fp: ReviewAssignmentId | None,
    replay_assignment_id: ReviewAssignmentId | None,
) -> str | None:
    chosen = assignment_id_for_fp or replay_assignment_id
    return None if chosen is None else str(chosen)