"""Guarded worksheet@2 learner_hint ContentVersion append (ADR-063 §7.2, T1)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from aieos.domains.content.application.asset_refs import (
    build_version_asset_refs,
    canonical_asset_ref_fingerprint_items,
    validate_asset_bindings,
)
from aieos.domains.content.application.audit import (
    MutationAuditProvenance,
    content_version_ref,
    insert_required_content_audit,
)
from aieos.domains.content.application.errors import (
    AggregateRevisionConflict,
    ContentNotFound,
    ContentPayloadInvalid,
    ContentSchemaMismatch,
    ContentSchemaNotFound,
    IdempotencyKeyReused,
    InvalidContentRequest,
    PersistenceInvariantViolation,
)
from aieos.domains.content.application.models import (
    AppendContentVersionCommand,
    ContentVersionReadModel,
    LockedContentHead,
    content_version_read_model,
)
from aieos.domains.content.application.ports import (
    CONTENT_VERSION_CREATE,
    AIGenerationAuthorizationPort,
    AssetReferenceValidationPort,
    ContentUnitOfWork,
    ContentUnitOfWorkFactory,
)
from aieos.domains.content.application.services import append_version_in_uow
from aieos.domains.content.domain.content import Content
from aieos.domains.content.domain.errors import InvalidPayloadError, SchemaNotFoundError
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.schema import ContentSchemaRegistry, SchemaId, SchemaVersion
from aieos.domains.content.domain.version import ContentPayload, ContentVersion
from aieos.domains.education.schema import (
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION_V2,
)
from aieos.domains.education.worksheet_hint_safety import (
    LearnerHintHardBlockError,
    LearnerHintValidationError,
    enforce_worksheet_hint_hard_blocks,
)
from aieos.domains.education.worksheet_v2 import (
    normalized_worksheet_v2_mapping,
    parse_worksheet_v2,
)
from aieos.domains.teaching.application.errors import ClassRefNotAssignable
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthority
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    CONTENT_VERSION_APPEND_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)


def worksheet_learner_hint_version_provenance(
    *, authoring_class_ref: str, principal_id: UUID
) -> dict[str, object]:
    """Durable human-version provenance for guarded worksheet hint append (no SoR claim)."""
    return {
        "kind": "worksheet_learner_hint",
        "schema_version": 1,
        "authoring_class_ref": authoring_class_ref.strip(),
        "authoring_principal_id": str(principal_id),
    }


class AppendWorksheetLearnerHintsContentVersionService:
    """Governed append for worksheet@2 payloads with validated learner_hint strings."""

    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        schema_registry: ContentSchemaRegistry,
        asset_reference_validation: AssetReferenceValidationPort,
        version_create_authorization: AIGenerationAuthorizationPort,
        principal_classification: CurrentPrincipalClassificationAuthority,
        class_authority: SchoolContextClassAuthority,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._schema_registry = schema_registry
        self._asset_reference_validation = asset_reference_validation
        self._version_create_authorization = version_create_authorization
        self._principal_classification = principal_classification
        self._class_authority = class_authority
        self._idempotency_retention = idempotency_retention

    def _assert_current_guarded_authority(
        self,
        uow: ContentUnitOfWork,
        *,
        execution_tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
        authoring_class_ref: str,
        expected_aggregate_revision: AggregateRevision | None,
    ) -> tuple[LockedContentHead, Content]:
        self._version_create_authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            capability=CONTENT_VERSION_CREATE,
        )
        head = uow.contents.get_head_for_update(content_id)
        if head is None or head.tenant_id != execution_tenant_id:
            raise ContentNotFound("Content is not visible in the execution tenant")
        content = uow.contents.get(content_id)
        if content is None or content.owner_principal_id != principal_id:
            raise InvalidContentRequest("content owner does not match current principal")
        if (
            expected_aggregate_revision is not None
            and head.aggregate_revision != expected_aggregate_revision
        ):
            raise AggregateRevisionConflict(
                "expected aggregate_revision does not match stored head"
            )
        try:
            self._class_authority.require_assignable_class_ref(
                execution_tenant_id, principal_id, authoring_class_ref
            )
        except ClassRefNotAssignable as exc:
            raise InvalidContentRequest(
                "authoring class is not assignable for the current teacher"
            ) from exc
        return head, content

    def append(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        content_id: ContentId,
        expected_aggregate_revision: AggregateRevision,
        payload: Mapping[str, object],
        authoring_class_ref: str,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        asset_refs: Sequence[Mapping[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> tuple[ContentVersionReadModel, AggregateRevision]:
        self._principal_classification.require_current_human_principal(principal_id)
        created_at = now if now is not None else datetime.now(UTC)
        raw_asset_refs: list[Mapping[str, Any]] = (
            [] if asset_refs is None else list(asset_refs)
        )
        normalized_authoring_class_ref = authoring_class_ref.strip()

        try:
            registered = self._schema_registry.get(
                WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION_V2
            )
        except SchemaNotFoundError as exc:
            raise ContentSchemaNotFound("schema is not registered") from exc
        try:
            registered.validate(payload)
            parsed = parse_worksheet_v2(payload)
        except (InvalidPayloadError, LearnerHintValidationError) as exc:
            raise ContentPayloadInvalid("payload failed schema validation") from exc
        if not any(q.learner_hint is not None for q in parsed.questions):
            raise InvalidContentRequest(
                "guarded worksheet hint append requires at least one learner_hint"
            )
        try:
            enforce_worksheet_hint_hard_blocks(parsed)
        except (LearnerHintHardBlockError, LearnerHintValidationError) as exc:
            raise InvalidContentRequest("worksheet learner_hint failed safety checks") from exc

        normalized_payload = normalized_worksheet_v2_mapping(parsed)
        version_provenance = worksheet_learner_hint_version_provenance(
            authoring_class_ref=normalized_authoring_class_ref,
            principal_id=principal_id,
        )
        fingerprint = fingerprint_material(
            {
                "content_id": str(content_id),
                "expected_aggregate_revision": int(expected_aggregate_revision),
                "schema_id": WORKSHEET_SCHEMA_ID,
                "schema_version": WORKSHEET_SCHEMA_VERSION_V2,
                "authoring_class_ref": normalized_authoring_class_ref,
                "payload": normalized_payload,
                "asset_refs": canonical_asset_ref_fingerprint_items(raw_asset_refs),
            }
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=CONTENT_VERSION_APPEND_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                self._assert_current_guarded_authority(
                    uow,
                    execution_tenant_id=execution_tenant_id,
                    principal_id=principal_id,
                    content_id=content_id,
                    authoring_class_ref=normalized_authoring_class_ref,
                    expected_aggregate_revision=None,
                )
                if existing.result_version_id is None:
                    raise PersistenceInvariantViolation(
                        "idempotent append outcome is missing version identity"
                    )
                stored = uow.versions.get(ContentVersionId(existing.result_version_id))
                if stored is None or stored.content_id != content_id:
                    raise PersistenceInvariantViolation(
                        "idempotent append outcome is not visible"
                    )
                return (
                    content_version_read_model(stored),
                    AggregateRevision(existing.result_aggregate_revision),
                )

            head, _content = self._assert_current_guarded_authority(
                uow,
                execution_tenant_id=execution_tenant_id,
                principal_id=principal_id,
                content_id=content_id,
                authoring_class_ref=normalized_authoring_class_ref,
                expected_aggregate_revision=expected_aggregate_revision,
            )
            if registered.content_type != head.content_type:
                raise ContentSchemaMismatch(
                    "registered schema content_type does not match Content"
                )
            try:
                domain_payload = ContentPayload.from_mapping(normalized_payload)
            except InvalidPayloadError as exc:
                raise ContentPayloadInvalid("payload failed schema validation") from exc

            if head.current_version_id is None:
                version_number = VersionNumber(1)
                parent = None
            else:
                if head.current_version_number is None:
                    raise PersistenceInvariantViolation(
                        "locked head is missing current version_number"
                    )
                version_number = VersionNumber(head.current_version_number.value + 1)
                parent = head.current_version_id

            version = ContentVersion(
                version_id=ContentVersionId.generate(),
                tenant_id=execution_tenant_id,
                content_id=content_id,
                version_number=version_number,
                parent_version_id=parent,
                schema_id=SchemaId(WORKSHEET_SCHEMA_ID),
                schema_version=SchemaVersion(WORKSHEET_SCHEMA_VERSION_V2),
                payload=domain_payload,
                origin=ContentOrigin.HUMAN,
                created_at=created_at,
                created_by_principal_id=principal_id,
            )
            built_refs = build_version_asset_refs(
                tenant_id=execution_tenant_id,
                content_id=content_id,
                version_id=version.version_id,
                created_at=created_at,
                items=raw_asset_refs,
            )
            if built_refs:
                validate_asset_bindings(
                    self._asset_reference_validation,
                    execution_tenant_id,
                    principal_id,
                    tuple(ref.resource_ref for ref in built_refs),
                )
            result = append_version_in_uow(
                uow,
                execution_tenant_id,
                AppendContentVersionCommand(
                    expected_aggregate_revision=expected_aggregate_revision,
                    version=version,
                    provenance=version_provenance,
                    asset_refs=built_refs,
                ),
                now=created_at,
                event_context=event_context,
                head=head,
            )
            insert_required_content_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.CONTENT_VERSION_CREATE,
                content_id=content_id.value,
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(result.aggregate_revision),
                related_resource_refs=(content_version_ref(version.version_id.value),),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=created_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=content_id.value,
                    result_version_id=result.version_id.value,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(result.aggregate_revision),
                    created_at=created_at,
                    expires_at=created_at + self._idempotency_retention,
                )
            )
            uow.commit()
            return content_version_read_model(version), result.aggregate_revision
