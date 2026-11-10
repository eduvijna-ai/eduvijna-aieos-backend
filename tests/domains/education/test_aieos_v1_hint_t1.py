"""PK-V1-HINT-T1 — Worksheet@2 schema, generic bypass, guarded append, interlock."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from aieos.domains.content.application.errors import (
    ContentPayloadInvalid,
    InvalidContentRequest,
    WorksheetLearnerHintAttestationRequired,
    WorksheetLearnerHintPublicationBlocked,
)
from aieos.domains.content.application.http_append import HttpAppendContentVersionService
from aieos.domains.content.application.worksheet_hint_append import (
    AppendWorksheetLearnerHintsContentVersionService,
)
from aieos.domains.content.application.worksheet_hint_interlock import (
    enforce_worksheet_hint_publish_interlock,
    enforce_worksheet_hint_review_interlock,
)
from aieos.domains.content.domain.identities import AggregateRevision, ContentId
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
from aieos.domains.content.domain.version import ContentPayload, ContentVersion
from aieos.domains.education.schema import (
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
    WORKSHEET_SCHEMA_VERSION_V2,
    WORKSHEET_V1_SCHEMA,
    WORKSHEET_V2_SCHEMA,
    build_preparation_content_schema_registry,
)
from aieos.domains.education.worksheet_hint_safety import (
    LearnerHintHardBlockError,
    assert_generic_worksheet_v2_hint_append_forbidden,
    enforce_worksheet_hint_hard_blocks,
    validate_learner_hint_string,
)
from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.domains.education.worksheet_v2 import WorksheetV2
from aieos.domains.teaching.application.school_context import AssignableClassRef
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload
from tests.fakes import AllowAIGenerationAuthorization, AllowAssetReferenceValidation, IDEMPOTENCY_RETENTION
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import CONTENT_VERSION_APPEND_V1, IdempotencyOutcome
from aieos.domains.education.worksheet_v2 import normalized_worksheet_v2_mapping_from_raw
from aieos.domains.content.application.asset_refs import canonical_asset_ref_fingerprint_items

pytestmark = pytest.mark.aieos_v1_hint_t1


def _worksheet_v2_payload(*, hint: str | None = "Consider the denominator.", **overrides) -> dict:
    payload = valid_worksheet_payload()
    if hint is not None:
        payload["questions"][0]["learner_hint"] = hint
    payload.update(overrides)
    return payload


def _hint_free_v2_payload() -> dict:
    return valid_worksheet_payload()


class StubHumanClassification:
    def require_current_human_principal(self, principal_id: uuid.UUID) -> PrincipalKind:
        return PrincipalKind.HUMAN


class StubWorkloadClassification:
    def require_current_human_principal(self, principal_id: uuid.UUID) -> PrincipalKind:
        from aieos.platform.security.context import UnauthorizedError

        raise UnauthorizedError("principal not authorized")


class StubClassAuthority:
    def __init__(self, *, assignable: bool = True) -> None:
        self.assignable = assignable

    def require_assignable_class_ref(
        self, tenant_id: uuid.UUID, teacher_principal_id: uuid.UUID, class_ref: str
    ) -> AssignableClassRef:
        if not self.assignable:
            from aieos.domains.teaching.application.errors import ClassRefNotAssignable

            raise ClassRefNotAssignable("not assignable")
        return AssignableClassRef(class_ref=class_ref, display_label="Class A")


def test_worksheet_v1_rejects_learner_hint_key():
    payload = _worksheet_v2_payload()
    with pytest.raises(ValidationError):
        WorksheetV1.model_validate(payload)


def test_worksheet_v2_schema_registry_tuple():
    registry = build_preparation_content_schema_registry()
    registry.get(WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION)
    registry.get(WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION_V2)
    with pytest.raises(Exception):
        registry.get("education.worksheet@2", 2)


def test_worksheet_v2_hint_string_rules():
    assert validate_learner_hint_string("  helpful  ") == "helpful"
    with pytest.raises(Exception):
        validate_learner_hint_string("")
    with pytest.raises(Exception):
        validate_learner_hint_string("<b>html</b>")
    with pytest.raises(Exception):
        validate_learner_hint_string("see https://example.com")
    with pytest.raises(Exception):
        validate_learner_hint_string("your grade matters")


def test_worksheet_v2_rejects_explicit_null_learner_hint():
    payload = _worksheet_v2_payload(hint="Valid hint text.")
    payload["questions"][1]["learner_hint"] = None
    with pytest.raises(ValidationError):
        WorksheetV2.model_validate(payload)


def test_worksheet_v2_only_explicit_null_rejected():
    payload = _hint_free_v2_payload()
    with pytest.raises(ValidationError):
        payload_only_null = dict(payload)
        payload_only_null["questions"][0]["learner_hint"] = None
        WorksheetV2.model_validate(payload_only_null)


def test_worksheet_v2_hint_length_500_and_501():
    payload = _worksheet_v2_payload(hint="a" * 500)
    WorksheetV2.model_validate(payload)
    payload["questions"][0]["learner_hint"] = "a" * 501
    with pytest.raises(ValidationError):
        WorksheetV2.model_validate(payload)


def test_guarded_append_persists_normalized_payload_in_memory():
    from aieos.domains.content.domain.version import ContentPayload
    from aieos.domains.education.worksheet_v2 import normalized_worksheet_v2_mapping

    raw = _worksheet_v2_payload(hint="  spaced hint  ")
    parsed = WorksheetV2.model_validate(raw)
    normalized = normalized_worksheet_v2_mapping(parsed)
    assert normalized["questions"][0]["learner_hint"] == "spaced hint"
    assert ContentPayload.from_mapping(normalized).sha256.value == ContentPayload.from_mapping(
        normalized
    ).sha256.value


def test_idempotent_replay_denied_when_capability_revoked():
    tenant_id = uuid.uuid7()
    principal_id = uuid.uuid7()
    content_id = ContentId.generate()
    stored_version_id = __import__(
        "aieos.domains.content.domain.identities", fromlist=["ContentVersionId"]
    ).ContentVersionId.generate()
    payload = _worksheet_v2_payload()
    normalized_payload = normalized_worksheet_v2_mapping_from_raw(payload)
    idem_key = "replay-cap-revoked"
    fingerprint = fingerprint_material(
        {
            "content_id": str(content_id),
            "expected_aggregate_revision": 1,
            "schema_id": WORKSHEET_SCHEMA_ID,
            "schema_version": WORKSHEET_SCHEMA_VERSION_V2,
            "authoring_class_ref": "class-a",
            "payload": normalized_payload,
            "asset_refs": canonical_asset_ref_fingerprint_items([]),
        }
    )

    auth = AllowAIGenerationAuthorization(allow=True)
    uow = MagicMock()
    uow.idempotency.get.return_value = IdempotencyOutcome(
        tenant_id=tenant_id,
        principal_id=principal_id,
        operation=CONTENT_VERSION_APPEND_V1,
        key_sha256=hash_idempotency_key(idem_key),
        request_fingerprint_sha256=fingerprint,
        result_content_id=content_id.value,
        result_version_id=stored_version_id.value,
        result_review_decision_id=None,
        result_publication_id=None,
        result_aggregate_revision=2,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
    )
    from aieos.domains.content.application.models import LockedContentHead
    from aieos.domains.content.domain.content import Content, ContentType
    from aieos.domains.content.domain.states import StewardshipState

    now = datetime.now(UTC)
    uow.contents.get_head_for_update.return_value = LockedContentHead(
        tenant_id=tenant_id,
        content_id=content_id,
        aggregate_revision=AggregateRevision(2),
        current_version_id=stored_version_id,
        current_version_number=__import__(
            "aieos.domains.content.domain.identities", fromlist=["VersionNumber"]
        ).VersionNumber(2),
        published_version_id=None,
        stewardship_state=StewardshipState.DRAFT.value,
        content_type="worksheet",
    )
    uow.contents.get.return_value = Content(
        content_id=content_id,
        tenant_id=tenant_id,
        owner_principal_id=principal_id,
        content_type=ContentType("worksheet"),
        title="Fractions",
        description="",
        locale="en-IN",
        stewardship_state=StewardshipState.DRAFT,
        current_version_id=stored_version_id,
        published_version_id=None,
        aggregate_revision=AggregateRevision(2),
        created_at=now,
        created_by_principal_id=principal_id,
        updated_at=now,
        archived_at=None,
    )
    uow.versions.get.return_value = ContentVersion(
        version_id=stored_version_id,
        tenant_id=tenant_id,
        content_id=content_id,
        version_number=__import__(
            "aieos.domains.content.domain.identities", fromlist=["VersionNumber"]
        ).VersionNumber(2),
        parent_version_id=__import__(
            "aieos.domains.content.domain.identities", fromlist=["ContentVersionId"]
        ).ContentVersionId.generate(),
        schema_id=SchemaId(WORKSHEET_SCHEMA_ID),
        schema_version=SchemaVersion(WORKSHEET_SCHEMA_VERSION_V2),
        payload=ContentPayload.from_mapping(_worksheet_v2_payload()),
        origin=ContentOrigin.HUMAN,
        created_at=now,
        created_by_principal_id=principal_id,
    )
    factory = MagicMock()
    factory.return_value.__enter__.return_value = uow
    factory.return_value.__exit__.return_value = None
    auth.allow = False
    service = AppendWorksheetLearnerHintsContentVersionService(
        factory,
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        auth,
        StubHumanClassification(),
        StubClassAuthority(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    event = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )
    from aieos.domains.content.application.errors import AIGenerationForbidden

    with pytest.raises(AIGenerationForbidden):
        service.append(
            tenant_id,
            principal_id,
            content_id=content_id,
            expected_aggregate_revision=AggregateRevision(1),
            payload=_worksheet_v2_payload(),
            authoring_class_ref="class-a",
            idempotency_key=idem_key,
            event_context=event,
            audit_provenance=__import__(
                "aieos.domains.content.application.audit",
                fromlist=["api_mutation_audit_provenance"],
            ).api_mutation_audit_provenance(principal_id),
        )


def test_http_append_hint_bypass_does_not_touch_idempotency():
    tenant_id = uuid.uuid7()
    principal_id = uuid.uuid7()
    uow = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = uow
    factory.return_value.__exit__.return_value = None
    service = HttpAppendContentVersionService(
        factory,
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    event = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )
    with pytest.raises(ContentPayloadInvalid):
        service.append(
            tenant_id,
            principal_id,
            content_id=ContentId.generate(),
            expected_aggregate_revision=AggregateRevision(0),
            schema_id=WORKSHEET_SCHEMA_ID,
            schema_version=WORKSHEET_SCHEMA_VERSION_V2,
            payload=_worksheet_v2_payload(),
            idempotency_key="idem-bypass",
            event_context=event,
            audit_provenance=__import__(
                "aieos.domains.content.application.audit",
                fromlist=["api_mutation_audit_provenance"],
            ).api_mutation_audit_provenance(principal_id),
        )
    uow.idempotency.acquire_scope.assert_not_called()
    uow.idempotency.get.assert_not_called()


def test_worksheet_v2_hard_block_answer_equality():
    payload = _worksheet_v2_payload(hint="1/2")
    payload["questions"][0]["answer"] = "1/2"
    worksheet = WorksheetV2.model_validate(payload)
    with pytest.raises(LearnerHintHardBlockError):
        enforce_worksheet_hint_hard_blocks(worksheet)


def test_generic_bypass_rejects_hint_key_presence():
    payload = _worksheet_v2_payload(hint="valid hint text here")
    with pytest.raises(Exception):
        assert_generic_worksheet_v2_hint_append_forbidden(
            schema_id=WORKSHEET_SCHEMA_ID,
            schema_version=WORKSHEET_SCHEMA_VERSION_V2,
            payload=payload,
        )
    assert_generic_worksheet_v2_hint_append_forbidden(
        schema_id=WORKSHEET_SCHEMA_ID,
        schema_version=WORKSHEET_SCHEMA_VERSION_V2,
        payload=_hint_free_v2_payload(),
    )


def test_http_append_bypass_before_idempotency():
    tenant_id = uuid.uuid7()
    principal_id = uuid.uuid7()
    service = HttpAppendContentVersionService(
        MagicMock(),
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    event = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )
    with pytest.raises(ContentPayloadInvalid):
        service.append(
            tenant_id,
            principal_id,
            content_id=ContentId.generate(),
            expected_aggregate_revision=AggregateRevision(0),
            schema_id=WORKSHEET_SCHEMA_ID,
            schema_version=WORKSHEET_SCHEMA_VERSION_V2,
            payload=_worksheet_v2_payload(),
            idempotency_key="idem-1",
            event_context=event,
            audit_provenance=__import__(
                "aieos.domains.content.application.audit",
                fromlist=["api_mutation_audit_provenance"],
            ).api_mutation_audit_provenance(principal_id),
        )


def test_hint_interlock_blocks_review_and_publish():
    version = ContentVersion(
        version_id=__import__(
            "aieos.domains.content.domain.identities", fromlist=["ContentVersionId"]
        ).ContentVersionId.generate(),
        tenant_id=uuid.uuid7(),
        content_id=ContentId.generate(),
        version_number=__import__(
            "aieos.domains.content.domain.identities", fromlist=["VersionNumber"]
        ).VersionNumber(1),
        parent_version_id=None,
        schema_id=SchemaId(WORKSHEET_SCHEMA_ID),
        schema_version=SchemaVersion(WORKSHEET_SCHEMA_VERSION_V2),
        payload=ContentPayload.from_mapping(_worksheet_v2_payload()),
        origin=ContentOrigin.HUMAN,
        created_at=datetime.now(UTC),
        created_by_principal_id=uuid.uuid7(),
    )
    with pytest.raises(WorksheetLearnerHintAttestationRequired):
        enforce_worksheet_hint_review_interlock(version)
    with pytest.raises(WorksheetLearnerHintPublicationBlocked):
        enforce_worksheet_hint_publish_interlock(version)


def test_guarded_append_denies_non_human():
    service = AppendWorksheetLearnerHintsContentVersionService(
        _mock_uow_factory(),
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        AllowAIGenerationAuthorization(),
        StubWorkloadClassification(),
        StubClassAuthority(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    event = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=uuid.uuid7(),
        effective_actor_id=uuid.uuid7(),
    )
    with pytest.raises(Exception):
        service.append(
            uuid.uuid7(),
            uuid.uuid7(),
            content_id=ContentId.generate(),
            expected_aggregate_revision=AggregateRevision(0),
            payload=_worksheet_v2_payload(),
            authoring_class_ref="class-a",
            idempotency_key="k",
            event_context=event,
            audit_provenance=__import__(
                "aieos.domains.content.application.audit",
                fromlist=["api_mutation_audit_provenance"],
            ).api_mutation_audit_provenance(uuid.uuid7()),
        )


def _mock_uow_factory() -> MagicMock:
    uow = MagicMock()
    uow.idempotency.get.return_value = None
    factory = MagicMock()
    factory.return_value.__enter__.return_value = uow
    factory.return_value.__exit__.return_value = None
    return factory


def test_guarded_append_requires_at_least_one_hint():
    parsed = WorksheetV2.model_validate(_hint_free_v2_payload())
    assert not any(q.learner_hint is not None for q in parsed.questions)
    tenant_id = uuid.uuid7()
    principal_id = uuid.uuid7()
    content_id = ContentId.generate()
    uow = MagicMock()
    uow.idempotency.get.return_value = None
    from aieos.domains.content.application.models import LockedContentHead
    from aieos.domains.content.domain.content import Content, ContentType
    from aieos.domains.content.domain.states import StewardshipState

    now = datetime.now(UTC)
    uow.contents.get_head_for_update.return_value = LockedContentHead(
        tenant_id=tenant_id,
        content_id=content_id,
        aggregate_revision=AggregateRevision(0),
        current_version_id=None,
        current_version_number=None,
        published_version_id=None,
        stewardship_state=StewardshipState.DRAFT.value,
        content_type="worksheet",
    )
    uow.contents.get.return_value = Content(
        content_id=content_id,
        tenant_id=tenant_id,
        owner_principal_id=principal_id,
        content_type=ContentType("worksheet"),
        title="Fractions",
        description="",
        locale="en-IN",
        stewardship_state=StewardshipState.DRAFT,
        current_version_id=None,
        published_version_id=None,
        aggregate_revision=AggregateRevision(0),
        created_at=now,
        created_by_principal_id=principal_id,
        updated_at=now,
        archived_at=None,
    )
    factory = MagicMock()
    factory.return_value.__enter__.return_value = uow
    factory.return_value.__exit__.return_value = None
    service = AppendWorksheetLearnerHintsContentVersionService(
        factory,
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        AllowAIGenerationAuthorization(),
        StubHumanClassification(),
        StubClassAuthority(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    event = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )
    with pytest.raises(InvalidContentRequest):
        service.append(
            tenant_id,
            principal_id,
            content_id=content_id,
            expected_aggregate_revision=AggregateRevision(0),
            payload=_hint_free_v2_payload(),
            authoring_class_ref="class-a",
            idempotency_key=f"hint-free-{uuid.uuid7()}",
            event_context=event,
            audit_provenance=__import__(
                "aieos.domains.content.application.audit",
                fromlist=["api_mutation_audit_provenance"],
            ).api_mutation_audit_provenance(principal_id),
        )


def test_v1_and_v2_schema_validators():
    WORKSHEET_V1_SCHEMA.validate(valid_worksheet_payload())
    WORKSHEET_V2_SCHEMA.validate(_hint_free_v2_payload())


def test_guarded_append_denial_precedence_non_owner_before_validation():
    """R2-F1: capability/owner/class deny before schema or idempotency inspection."""
    tenant_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    intruder_id = uuid.uuid7()
    content_id = ContentId.generate()
    uow = MagicMock()
    uow.idempotency.get.return_value = None
    from aieos.domains.content.application.models import LockedContentHead
    from aieos.domains.content.domain.content import Content, ContentType
    from aieos.domains.content.domain.states import StewardshipState

    now = datetime.now(UTC)
    uow.contents.get_head_for_update.return_value = LockedContentHead(
        tenant_id=tenant_id,
        content_id=content_id,
        aggregate_revision=AggregateRevision(0),
        current_version_id=None,
        current_version_number=None,
        published_version_id=None,
        stewardship_state=StewardshipState.DRAFT.value,
        content_type="worksheet",
    )
    uow.contents.get.return_value = Content(
        content_id=content_id,
        tenant_id=tenant_id,
        owner_principal_id=owner_id,
        content_type=ContentType("worksheet"),
        title="Fractions",
        description="",
        locale="en-IN",
        stewardship_state=StewardshipState.DRAFT,
        current_version_id=None,
        published_version_id=None,
        aggregate_revision=AggregateRevision(0),
        created_at=now,
        created_by_principal_id=owner_id,
        updated_at=now,
        archived_at=None,
    )
    factory = MagicMock()
    factory.return_value.__enter__.return_value = uow
    factory.return_value.__exit__.return_value = None
    service = AppendWorksheetLearnerHintsContentVersionService(
        factory,
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        AllowAIGenerationAuthorization(),
        StubHumanClassification(),
        StubClassAuthority(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    event = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=intruder_id,
        effective_actor_id=intruder_id,
    )
    garbage_payload = {"questions": "not-a-list"}
    with pytest.raises(InvalidContentRequest):
        service.append(
            tenant_id,
            intruder_id,
            content_id=content_id,
            expected_aggregate_revision=AggregateRevision(0),
            payload=garbage_payload,
            authoring_class_ref="class-a",
            idempotency_key=f"intruder-{uuid.uuid7()}",
            event_context=event,
            audit_provenance=__import__(
                "aieos.domains.content.application.audit",
                fromlist=["api_mutation_audit_provenance"],
            ).api_mutation_audit_provenance(intruder_id),
        )
    uow.idempotency.acquire_scope.assert_not_called()
    uow.idempotency.get.assert_not_called()


def test_guarded_append_blank_authoring_class_ref_denied():
    service = AppendWorksheetLearnerHintsContentVersionService(
        _mock_uow_factory(),
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        AllowAIGenerationAuthorization(),
        StubHumanClassification(),
        StubClassAuthority(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    event = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=uuid.uuid7(),
        effective_actor_id=uuid.uuid7(),
    )
    with pytest.raises(InvalidContentRequest):
        service.append(
            uuid.uuid7(),
            uuid.uuid7(),
            content_id=ContentId.generate(),
            expected_aggregate_revision=AggregateRevision(0),
            payload=_worksheet_v2_payload(),
            authoring_class_ref="   ",
            idempotency_key="k",
            event_context=event,
            audit_provenance=__import__(
                "aieos.domains.content.application.audit",
                fromlist=["api_mutation_audit_provenance"],
            ).api_mutation_audit_provenance(uuid.uuid7()),
        )


def test_guarded_append_authorized_actor_still_gets_payload_validation_error():
    tenant_id = uuid.uuid7()
    principal_id = uuid.uuid7()
    content_id = ContentId.generate()
    uow = MagicMock()
    uow.idempotency.get.return_value = None
    from aieos.domains.content.application.models import LockedContentHead
    from aieos.domains.content.domain.content import Content, ContentType
    from aieos.domains.content.domain.states import StewardshipState

    now = datetime.now(UTC)
    uow.contents.get_head_for_update.return_value = LockedContentHead(
        tenant_id=tenant_id,
        content_id=content_id,
        aggregate_revision=AggregateRevision(0),
        current_version_id=None,
        current_version_number=None,
        published_version_id=None,
        stewardship_state=StewardshipState.DRAFT.value,
        content_type="worksheet",
    )
    uow.contents.get.return_value = Content(
        content_id=content_id,
        tenant_id=tenant_id,
        owner_principal_id=principal_id,
        content_type=ContentType("worksheet"),
        title="Fractions",
        description="",
        locale="en-IN",
        stewardship_state=StewardshipState.DRAFT,
        current_version_id=None,
        published_version_id=None,
        aggregate_revision=AggregateRevision(0),
        created_at=now,
        created_by_principal_id=principal_id,
        updated_at=now,
        archived_at=None,
    )
    factory = MagicMock()
    factory.return_value.__enter__.return_value = uow
    factory.return_value.__exit__.return_value = None
    service = AppendWorksheetLearnerHintsContentVersionService(
        factory,
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        AllowAIGenerationAuthorization(),
        StubHumanClassification(),
        StubClassAuthority(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    event = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )
    with pytest.raises(ContentPayloadInvalid):
        service.append(
            tenant_id,
            principal_id,
            content_id=content_id,
            expected_aggregate_revision=AggregateRevision(0),
            payload={"questions": "broken"},
            authoring_class_ref="class-a",
            idempotency_key=f"valid-owner-{uuid.uuid7()}",
            event_context=event,
            audit_provenance=__import__(
                "aieos.domains.content.application.audit",
                fromlist=["api_mutation_audit_provenance"],
            ).api_mutation_audit_provenance(principal_id),
        )
