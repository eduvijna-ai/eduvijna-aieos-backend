"""Record immutable worksheet learner-hint version attestation (ADR-063 §5.3 T2)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.content.application.audit import (
    MutationAuditProvenance,
    content_primary_ref,
    content_version_ref,
    insert_required_content_audit,
    worksheet_hint_attestation_ref,
)
from aieos.domains.content.application.errors import (
    ContentNotFound,
    ContentVersionNotFound,
    WorksheetLearnerHintAttestationInvalid,
)
from aieos.domains.content.application.ports import (
    CONTENT_REVIEW_DECIDE,
    ContentUnitOfWorkFactory,
    ReviewAuthorizationPort,
)
from aieos.domains.content.application.worksheet_hint_attestation_gate import (
    assert_attestation_outcomes_against_qa,
    hinted_question_ids,
    load_valid_attestation,
    outcomes_from_command,
)
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    WorksheetLearnerHintAttestationId,
)
from aieos.domains.content.domain.worksheet_hint_attestation import (
    WorksheetLearnerHintVersionAttestationV1,
)
from aieos.domains.education.worksheet_hint_digest import (
    worksheet_hint_attestation_digest_from_version_payload,
)
from aieos.domains.education.worksheet_hint_qa import WORKSHEET_HINT_QA_POLICY_VERSION
from aieos.domains.education.worksheet_hint_safety import (
    version_requires_worksheet_hint_attestation,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    CONTENT_WORKSHEET_HINT_ATTEST_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction
from aieos.domains.content.domain.states import StewardshipState
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)


class RecordWorksheetLearnerHintAttestationService:
    """Backend-only command to persist version-scoped human hint QA attestation."""

    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        authorization: ReviewAuthorizationPort,
        principal_classification: CurrentPrincipalClassificationAuthority,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._authorization = authorization
        self._principal_classification = principal_classification
        self._idempotency_retention = idempotency_retention

    def record(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        content_id: ContentId,
        version_id: ContentVersionId,
        expected_aggregate_revision: AggregateRevision,
        per_question_outcomes: Sequence[Mapping[str, object]],
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> WorksheetLearnerHintAttestationId:
        self._principal_classification.require_current_human_principal(principal_id)
        if event_context.effective_actor_id != principal_id:
            raise WorksheetLearnerHintAttestationInvalid(
                "effective actor must align with reviewer principal"
            )
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
            capability=CONTENT_REVIEW_DECIDE,
        )
        attested_at = now if now is not None else datetime.now(UTC)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=CONTENT_WORKSHEET_HINT_ATTEST_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        fingerprint = fingerprint_material(
            {
                "content_id": str(content_id),
                "version_id": str(version_id),
                "expected_aggregate_revision": int(expected_aggregate_revision),
                "qa_policy_version": WORKSHEET_HINT_QA_POLICY_VERSION,
                "per_question_outcomes": list(per_question_outcomes),
            }
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise WorksheetLearnerHintAttestationInvalid(
                        "idempotency key already bound"
                    )
                if existing.result_content_id != content_id.value:
                    raise WorksheetLearnerHintAttestationInvalid(
                        "idempotency replay content binding mismatch"
                    )
                if existing.result_version_id != version_id.value:
                    raise WorksheetLearnerHintAttestationInvalid(
                        "idempotency replay version binding mismatch"
                    )
                version_row = uow.versions.get(version_id)
                if version_row is None or version_row.content_id != content_id:
                    raise ContentVersionNotFound(
                        "ContentVersion is not visible for the requested Content"
                    )
                stored_attestation = load_valid_attestation(
                    uow.worksheet_hint_attestations, version_row
                )
                if stored_attestation.reviewer_principal_id != principal_id:
                    raise WorksheetLearnerHintAttestationInvalid(
                        "idempotent attestation reviewer mismatch"
                    )
                return stored_attestation.attestation_id

            head = uow.contents.get_head_for_update(content_id)
            if head is None or head.tenant_id != execution_tenant_id:
                raise ContentNotFound("Content is not visible in the execution tenant")
            if head.aggregate_revision != expected_aggregate_revision:
                raise WorksheetLearnerHintAttestationInvalid(
                    "expected aggregate_revision does not match stored head"
                )
            if head.current_version_id is None or head.current_version_id != version_id:
                raise WorksheetLearnerHintAttestationInvalid(
                    "attestation requires the current ContentVersion"
                )
            if head.stewardship_state != StewardshipState.IN_REVIEW.value:
                raise WorksheetLearnerHintAttestationInvalid(
                    "attestation requires IN_REVIEW stewardship state"
                )
            version = uow.versions.get(version_id)
            if version is None or version.content_id != content_id:
                raise ContentVersionNotFound(
                    "ContentVersion is not visible for the requested Content"
                )
            payload_body = dict(version.payload.body)
            if not version_requires_worksheet_hint_attestation(
                schema_id=str(version.schema_id),
                schema_version=int(version.schema_version),
                payload=payload_body,
            ):
                raise WorksheetLearnerHintAttestationInvalid(
                    "attestation is only valid for hint-bearing worksheet@2"
                )
            if version.created_by_principal_id == principal_id:
                raise WorksheetLearnerHintAttestationInvalid(
                    "reviewer must be distinct from hint version author"
                )
            if (
                uow.worksheet_hint_attestations.get_for_version(content_id, version_id)
                is not None
            ):
                raise WorksheetLearnerHintAttestationInvalid(
                    "attestation already exists for this ContentVersion"
                )
            hinted_ids = hinted_question_ids(payload_body)
            outcomes = outcomes_from_command(hinted_ids, per_question_outcomes)
            assert_attestation_outcomes_against_qa(payload=payload_body, outcomes=outcomes)
            digest = worksheet_hint_attestation_digest_from_version_payload(payload_body)
            attestation_id = WorksheetLearnerHintAttestationId.generate()
            stored = WorksheetLearnerHintVersionAttestationV1(
                attestation_id=attestation_id,
                tenant_id=execution_tenant_id,
                content_id=content_id,
                version_id=version_id,
                payload_digest=digest,
                qa_policy_version=WORKSHEET_HINT_QA_POLICY_VERSION,
                author_principal_id=version.created_by_principal_id,
                reviewer_principal_id=principal_id,
                per_question_outcomes=outcomes,
                attested_at=attested_at,
                correlation_id=event_context.correlation_id,
            )
            uow.worksheet_hint_attestations.insert(stored)
            insert_required_content_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.CONTENT_WORKSHEET_HINT_ATTEST,
                content_id=content_id.value,
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(expected_aggregate_revision),
                related_resource_refs=(
                    content_version_ref(version_id.value),
                    worksheet_hint_attestation_ref(attestation_id.value),
                ),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=attested_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=content_id.value,
                    result_version_id=version_id.value,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(expected_aggregate_revision),
                    created_at=attested_at,
                    expires_at=attested_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return attestation_id
