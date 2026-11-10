"""PostgreSQL integration for PK-V1-HINT-T1 guarded append and review interlock."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.create import CreateContentService
from aieos.domains.content.application.errors import (
    InvalidContentRequest,
    WorksheetLearnerHintAttestationRequired,
    WorksheetLearnerHintPublicationBlocked,
)
from aieos.domains.content.application.http_append import HttpAppendContentVersionService
from aieos.domains.content.application.models import CreateContentCommand
from aieos.domains.content.application.publish import PublishContentService
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.application.worksheet_hint_append import (
    AppendWorksheetLearnerHintsContentVersionService,
    worksheet_learner_hint_version_provenance,
)
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
)
from aieos.domains.content.domain.version import (
    ContentPayload,
    canonical_payload_json,
    thaw_json_value,
)
from aieos.domains.education.schema import (
    WORKSHEET_CONTENT_TYPE,
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
    WORKSHEET_SCHEMA_VERSION_V2,
    build_preparation_content_schema_registry,
)
from aieos.domains.teaching.application.school_context import AssignableClassRef
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.authorization.decisions import PrincipalKind
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from tests.domains.education.test_aieos_v1_hint_t1 import (
    StubClassAuthority,
    StubHumanClassification,
    _hint_free_v2_payload,
    _worksheet_v2_payload,
)
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload
from tests.fakes import (
    AllowAIGenerationAuthorization,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowAssetCurrentGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    make_test_schema_registry,
)

pytestmark = pytest.mark.aieos_v1_hint_t1

FIXED_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _event_context(principal_id: uuid.UUID) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


def _seed_worksheet_content(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    owner_principal_id: uuid.UUID,
    content_id: ContentId | None = None,
) -> ContentId:
    content_id = content_id or ContentId.generate()
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.contents (
                    content_id, tenant_id, owner_principal_id, content_type, title,
                    description, locale, stewardship_state, current_version_id,
                    published_version_id, aggregate_revision, created_at,
                    created_by_principal_id, updated_at, archived_at
                ) VALUES (
                    :content_id, :tenant_id, :owner, :ctype, 'Fractions',
                    '', 'en-IN', 'DRAFT', NULL, NULL, 0, :now, :owner, :now, NULL
                )
                """
            ),
            {
                "content_id": content_id.value,
                "tenant_id": tenant_id,
                "owner": owner_principal_id,
                "ctype": WORKSHEET_CONTENT_TYPE,
                "now": FIXED_NOW,
            },
        )
    return content_id


def _append_v1(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    content_id: ContentId,
) -> tuple[ContentVersionId, AggregateRevision]:
    registry = build_preparation_content_schema_registry()
    service = HttpAppendContentVersionService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        registry,
        AllowAssetReferenceValidation(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    version_read, revision = service.append(
        tenant_id,
        principal_id,
        content_id=content_id,
        expected_aggregate_revision=AggregateRevision(0),
        schema_id=WORKSHEET_SCHEMA_ID,
        schema_version=WORKSHEET_SCHEMA_VERSION,
        payload=valid_worksheet_payload(),
        idempotency_key=f"v1-{uuid.uuid7()}",
        event_context=_event_context(principal_id),
        audit_provenance=api_mutation_audit_provenance(principal_id),
        now=FIXED_NOW,
    )
    return version_read.version_id, revision


def _guarded_service(
    runtime_engine: Engine, class_authority: StubClassAuthority | None = None
) -> AppendWorksheetLearnerHintsContentVersionService:
    return AppendWorksheetLearnerHintsContentVersionService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        AllowAIGenerationAuthorization(),
        StubHumanClassification(),
        class_authority or StubClassAuthority(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


class TestGuardedAppendPostgres:
    def test_guarded_append_persists_normalized_hint_provenance_and_audit(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=principal_id
        )
        _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
        )
        raw_payload = _worksheet_v2_payload(hint="  Think about the units.  ")
        expected_hint = "Think about the units."
        idem = f"hint-{uuid.uuid7()}"
        service = _guarded_service(runtime_engine)
        version_read, revision = service.append(
            tenant_id,
            principal_id,
            content_id=content_id,
            expected_aggregate_revision=AggregateRevision(1),
            payload=raw_payload,
            authoring_class_ref="class-5a",
            idempotency_key=idem,
            event_context=_event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert int(revision) == 2
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
            stored = uow.versions.get(version_read.version_id)
            assert stored is not None
            mapping = thaw_json_value(stored.payload.body)
            assert isinstance(mapping, dict)
            q0 = mapping["questions"][0]
            assert q0["learner_hint"] == expected_hint
            assert "learner_hint" not in mapping["questions"][1]
            assert stored.payload.sha256.value == ContentPayload.from_mapping(
                mapping
            ).sha256.value
            prov = uow.versions.get_provenance(version_read.version_id)
            assert prov is not None
            assert prov["authoring_class_ref"] == "class-5a"
            assert prov["authoring_principal_id"] == str(principal_id)
        with bootstrap_engine.connect() as conn:
            audit_count = conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_events
                    WHERE tenant_id = :tid AND action = 'content.version.create'
                    AND primary_resource_id = :cid
                    """
                ),
                {"tid": tenant_id, "cid": content_id.value},
            ).scalar_one()
        assert int(audit_count) >= 1

    def test_idempotent_replay_succeeds_while_still_authorized(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=principal_id
        )
        _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
        )
        service = _guarded_service(runtime_engine)
        idem = f"replay-ok-{uuid.uuid7()}"
        first, _ = service.append(
            tenant_id,
            principal_id,
            content_id=content_id,
            expected_aggregate_revision=AggregateRevision(1),
            payload=_worksheet_v2_payload(),
            authoring_class_ref="class-5a",
            idempotency_key=idem,
            event_context=_event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        second, rev = service.append(
            tenant_id,
            principal_id,
            content_id=content_id,
            expected_aggregate_revision=AggregateRevision(1),
            payload=_worksheet_v2_payload(),
            authoring_class_ref="class-5a",
            idempotency_key=idem,
            event_context=_event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert second.version_id == first.version_id
        assert int(rev) == 2

    def test_idempotent_replay_denied_after_class_ref_revoked(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=principal_id
        )
        _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
        )
        authority = StubClassAuthority(assignable=True)
        service = _guarded_service(runtime_engine, authority)
        idem = f"replay-deny-{uuid.uuid7()}"
        service.append(
            tenant_id,
            principal_id,
            content_id=content_id,
            expected_aggregate_revision=AggregateRevision(1),
            payload=_worksheet_v2_payload(),
            authoring_class_ref="class-5a",
            idempotency_key=idem,
            event_context=_event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        authority.assignable = False
        with pytest.raises(InvalidContentRequest):
            service.append(
                tenant_id,
                principal_id,
                content_id=content_id,
                expected_aggregate_revision=AggregateRevision(1),
                payload=_worksheet_v2_payload(),
                authoring_class_ref="class-5a",
                idempotency_key=idem,
                event_context=_event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        with bootstrap_engine.connect() as conn:
            version_count = conn.execute(
                text(
                    "SELECT count(*) FROM content.content_versions WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).scalar_one()
        assert int(version_count) == 2


class TestReviewPublishInterlockServices:
    def test_review_approve_service_blocks_hint_version(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=principal_id
        )
        _, rev_after_v1 = _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
        )
        service = _guarded_service(runtime_engine)
        version_read, revision = service.append(
            tenant_id,
            principal_id,
            content_id=content_id,
            expected_aggregate_revision=rev_after_v1,
            payload=_worksheet_v2_payload(),
            authoring_class_ref="class-5a",
            idempotency_key=f"approve-block-{uuid.uuid7()}",
            event_context=_event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        review = ReviewCommandService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            AllowReviewCommentPolicy(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        with pytest.raises(WorksheetLearnerHintAttestationRequired):
            review.approve(
                tenant_id,
                principal_id,
                content_id=content_id,
                version_id=version_read.version_id,
                expected_aggregate_revision=revision,
                reason_code=None,
                comment=None,
                idempotency_key=f"decide-{uuid.uuid7()}",
                event_context=_event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_publish_service_blocks_hint_version_in_approved_state(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=principal_id
        )
        version_id = ContentVersionId.generate()
        payload = ContentPayload.from_mapping(_worksheet_v2_payload())
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO content.content_versions (
                        version_id, tenant_id, content_id, version_number,
                        parent_version_id, schema_id, schema_version, payload,
                        payload_sha256, origin, created_at, created_by_principal_id
                    ) VALUES (
                        :vid, :tid, :cid, 1, NULL,
                        :schema_id, :schema_version, CAST(:payload AS jsonb),
                        :digest, 'HUMAN', :now, :principal
                    )
                    """
                ),
                {
                    "vid": version_id.value,
                    "tid": tenant_id,
                    "cid": content_id.value,
                    "schema_id": WORKSHEET_SCHEMA_ID,
                    "schema_version": WORKSHEET_SCHEMA_VERSION_V2,
                    "payload": canonical_payload_json(payload.body),
                    "digest": payload.sha256.value,
                    "now": FIXED_NOW,
                    "principal": principal_id,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE content.contents
                    SET current_version_id = :vid, aggregate_revision = 1,
                        stewardship_state = 'APPROVED'
                    WHERE content_id = :cid
                    """
                ),
                {"vid": version_id.value, "cid": content_id.value},
            )
        publish = PublishContentService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowPublicationAuthorization(),
            AllowPublicationGovernance(),
            AllowAssetCurrentGovernance(),
            make_test_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        with pytest.raises(WorksheetLearnerHintPublicationBlocked):
            publish.publish(
                tenant_id,
                principal_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=AggregateRevision(1),
                idempotency_key=f"pub-{uuid.uuid7()}",
                event_context=_event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
