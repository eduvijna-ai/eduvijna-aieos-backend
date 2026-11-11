"""PK-V1-HINT-T2 — attestation digest, QA heuristics, gated APPROVE/publish."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    WorksheetLearnerHintAttestationInvalid,
    WorksheetLearnerHintAttestationRequired,
)
from aieos.domains.content.application.publish import PublishContentService
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.application.worksheet_hint_attestation_record import (
    RecordWorksheetLearnerHintAttestationService,
)
from aieos.domains.content.domain.errors import InvalidPayloadError
from aieos.domains.content.domain.identities import AggregateRevision
from aieos.domains.content.domain.version import ContentPayload, thaw_json_value
from aieos.domains.content.domain.worksheet_hint_attestation import HintQuestionAttestationOutcome
from aieos.domains.content.infrastructure.persistence.mapping import (
    worksheet_hint_attestation_from_row,
)
from aieos.domains.education.worksheet_v2 import parse_worksheet_v2
from aieos.domains.education.worksheet_hint_digest import (
    worksheet_hint_attestation_digest_from_version_payload,
)
from aieos.domains.education.worksheet_hint_qa import (
    HintReviewFlag,
    SIMILARITY_FLAG_JACCARD_THRESHOLD,
    assess_worksheet_hint_qa,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from tests.domains.education.test_aieos_v1_hint_t1 import (
    StubHumanClassification,
    _worksheet_v2_payload,
    build_preparation_content_schema_registry,
)
from tests.domains.education.test_aieos_v1_hint_t1_persistence import (
    FIXED_NOW,
    _append_v1,
    _event_context,
    _guarded_service,
    _seed_worksheet_content,
)
from tests.fakes import (
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    AllowAssetCurrentGovernance,
    IDEMPOTENCY_RETENTION,
)

pytestmark = pytest.mark.aieos_v1_hint_t2

FIXED_HINT = "Think about equivalent fractions."


def _positive_outcomes(question_ids: list[str]) -> list[dict[str, object]]:
    return [
        {
            "question_id": qid,
            "hard_block_clear": True,
            "human_review_pass": True,
        }
        for qid in question_ids
    ]


def _review_service(runtime_engine) -> ReviewCommandService:
    return ReviewCommandService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        AllowReviewAuthorization(),
        AllowReviewCommentPolicy(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        principal_classification=StubHumanClassification(),
    )


def _attest_service(runtime_engine) -> RecordWorksheetLearnerHintAttestationService:
    return RecordWorksheetLearnerHintAttestationService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        AllowReviewAuthorization(),
        StubHumanClassification(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def _hinted_ids_from_version(runtime_engine, tenant_id: uuid.UUID, version_id) -> list[str]:
    with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
        version = uow.versions.get(version_id)
        assert version is not None
        worksheet = parse_worksheet_v2(dict(thaw_json_value(version.payload.body)))
        return [q.id for q in worksheet.questions if q.learner_hint is not None]


class TestHintAttestationGateUnit:
    def test_approve_fails_closed_without_human_classification(self) -> None:
        from aieos.domains.content.application.worksheet_hint_attestation_gate import (
            enforce_worksheet_hint_review_interlock,
        )
        from aieos.domains.content.domain.identities import ContentVersionId, VersionNumber
        from aieos.domains.content.domain.origin import ContentOrigin
        from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
        from aieos.domains.content.domain.version import ContentPayload, ContentVersion
        from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION_V2
        from unittest.mock import MagicMock
        from datetime import UTC, datetime

        version = ContentVersion(
            version_id=ContentVersionId.generate(),
            tenant_id=uuid.uuid7(),
            content_id=__import__(
                "aieos.domains.content.domain.identities", fromlist=["ContentId"]
            ).ContentId.generate(),
            version_number=VersionNumber(1),
            parent_version_id=None,
            schema_id=SchemaId(WORKSHEET_SCHEMA_ID),
            schema_version=SchemaVersion(WORKSHEET_SCHEMA_VERSION_V2),
            payload=ContentPayload.from_mapping(_worksheet_v2_payload(hint=FIXED_HINT)),
            origin=ContentOrigin.HUMAN,
            created_at=datetime.now(UTC),
            created_by_principal_id=uuid.uuid7(),
        )
        uow = MagicMock()
        uow.worksheet_hint_attestations.get_for_version.return_value = None
        with pytest.raises(WorksheetLearnerHintAttestationInvalid):
            enforce_worksheet_hint_review_interlock(
                uow,
                version,
                reviewer_principal_id=uuid.uuid7(),
                principal_classification=None,
            )


class TestDigestAndQa:
    def test_digest_includes_schema_envelope(self) -> None:
        payload = ContentPayload.from_mapping(_worksheet_v2_payload(hint=FIXED_HINT))
        digest = worksheet_hint_attestation_digest_from_version_payload(payload.body)
        assert len(digest) == 64

    def test_similarity_flag_requires_explicit_human_pass(self) -> None:
        payload = _worksheet_v2_payload(hint="Explanation for question 1 about fractions")
        assessments = assess_worksheet_hint_qa(payload)
        assert assessments
        assert HintReviewFlag.EXPLANATION_SIMILARITY in assessments[0].flags
        worksheet = parse_worksheet_v2(payload)
        overlap = assessments[0]
        assert overlap.question_id == worksheet.questions[0].id
        from aieos.domains.content.application.worksheet_hint_attestation_gate import (
            assert_attestation_outcomes_against_qa,
        )

        with pytest.raises(WorksheetLearnerHintAttestationInvalid):
            assert_attestation_outcomes_against_qa(
                payload=payload,
                outcomes=(
                    HintQuestionAttestationOutcome(
                        question_id=overlap.question_id,
                        hard_block_clear=True,
                        human_review_pass=False,
                    ),
                ),
            )


class TestAttestationPostgres:
    def test_three_human_publish_path(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        tenant_id = uuid.uuid7()
        author_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        publisher_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=author_id
        )
        _, rev_after_v1 = _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            content_id=content_id,
        )
        append = _guarded_service(runtime_engine)
        version_read, revision = append.append(
            tenant_id,
            author_id,
            content_id=content_id,
            expected_aggregate_revision=rev_after_v1,
            payload=_worksheet_v2_payload(hint=FIXED_HINT),
            authoring_class_ref="class-5a",
            idempotency_key=f"t2-hint-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        review = _review_service(runtime_engine)
        submitted = review.submit(
            tenant_id,
            author_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=revision,
            idempotency_key=f"submit-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        hinted_ids = _hinted_ids_from_version(
            runtime_engine, tenant_id, version_read.version_id
        )
        attest = _attest_service(runtime_engine)
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=submitted.aggregate_revision,
            per_question_outcomes=_positive_outcomes(hinted_ids),
            idempotency_key=f"attest-{uuid.uuid7()}",
            event_context=_event_context(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=FIXED_NOW,
        )
        approved = review.approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=submitted.aggregate_revision,
            reason_code=None,
            comment=None,
            idempotency_key=f"approve-{uuid.uuid7()}",
            event_context=_event_context(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=FIXED_NOW,
        )
        assert approved.decision == "APPROVE"
        publish = PublishContentService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowPublicationAuthorization(),
            AllowPublicationGovernance(),
            AllowAssetCurrentGovernance(),
            build_preparation_content_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
            principal_classification=StubHumanClassification(),
        )
        published = publish.publish(
            tenant_id,
            publisher_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=approved.aggregate_revision,
            idempotency_key=f"publish-{uuid.uuid7()}",
            event_context=_event_context(publisher_id),
            audit_provenance=api_mutation_audit_provenance(publisher_id),
            now=FIXED_NOW,
        )
        assert published.version_id == version_read.version_id

    def test_record_attestation_then_approve_succeeds(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        tenant_id = uuid.uuid7()
        author_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=author_id
        )
        _, rev_after_v1 = _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            content_id=content_id,
        )
        version_read, revision = _guarded_service(runtime_engine).append(
            tenant_id,
            author_id,
            content_id=content_id,
            expected_aggregate_revision=rev_after_v1,
            payload=_worksheet_v2_payload(hint=FIXED_HINT),
            authoring_class_ref="class-5a",
            idempotency_key=f"t2-approve-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        review = _review_service(runtime_engine)
        submitted = review.submit(
            tenant_id,
            author_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=revision,
            idempotency_key=f"submit-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        hinted_ids = _hinted_ids_from_version(
            runtime_engine, tenant_id, version_read.version_id
        )
        _attest_service(runtime_engine).record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=submitted.aggregate_revision,
            per_question_outcomes=_positive_outcomes(hinted_ids),
            idempotency_key=f"attest-{uuid.uuid7()}",
            event_context=_event_context(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=FIXED_NOW,
        )
        approved = review.approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=submitted.aggregate_revision,
            reason_code=None,
            comment=None,
            idempotency_key=f"approve-{uuid.uuid7()}",
            event_context=_event_context(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=FIXED_NOW,
        )
        assert approved.decision == "APPROVE"

    def test_attestation_idempotency_replay_validates_stored_record(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        author_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=author_id
        )
        _, rev_after_v1 = _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            content_id=content_id,
        )
        version_read, revision = _guarded_service(runtime_engine).append(
            tenant_id,
            author_id,
            content_id=content_id,
            expected_aggregate_revision=rev_after_v1,
            payload=_worksheet_v2_payload(hint=FIXED_HINT),
            authoring_class_ref="class-5a",
            idempotency_key=f"idem-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        submitted = _review_service(runtime_engine).submit(
            tenant_id,
            author_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=revision,
            idempotency_key=f"submit-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        hinted_ids = _hinted_ids_from_version(
            runtime_engine, tenant_id, version_read.version_id
        )
        idem_key = f"attest-replay-{uuid.uuid7()}"
        service = _attest_service(runtime_engine)
        first = service.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=submitted.aggregate_revision,
            per_question_outcomes=_positive_outcomes(hinted_ids),
            idempotency_key=idem_key,
            event_context=_event_context(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=FIXED_NOW,
        )
        second = service.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=submitted.aggregate_revision,
            per_question_outcomes=_positive_outcomes(hinted_ids),
            idempotency_key=idem_key,
            event_context=_event_context(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=FIXED_NOW,
        )
        assert second == first

    def test_mapping_rejects_string_booleans(self) -> None:
        row = {
            "attestation_id": uuid.uuid7(),
            "tenant_id": uuid.uuid7(),
            "content_id": uuid.uuid7(),
            "version_id": uuid.uuid7(),
            "payload_digest": "a" * 64,
            "qa_policy_version": "O63-03-v1",
            "author_principal_id": uuid.uuid7(),
            "reviewer_principal_id": uuid.uuid7(),
            "per_question_outcomes": [
                {
                    "question_id": "q-1",
                    "hard_block_clear": "true",
                    "human_review_pass": True,
                }
            ],
            "attested_at": FIXED_NOW,
            "correlation_id": uuid.uuid7(),
        }
        with pytest.raises(InvalidPayloadError):
            worksheet_hint_attestation_from_row(row)

    def test_attestation_rows_are_immutable(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        author_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=author_id
        )
        _, rev_after_v1 = _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            content_id=content_id,
        )
        version_read, revision = _guarded_service(runtime_engine).append(
            tenant_id,
            author_id,
            content_id=content_id,
            expected_aggregate_revision=rev_after_v1,
            payload=_worksheet_v2_payload(hint=FIXED_HINT),
            authoring_class_ref="class-5a",
            idempotency_key=f"imm-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        submitted = _review_service(runtime_engine).submit(
            tenant_id,
            author_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=revision,
            idempotency_key=f"submit-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        hinted_ids = _hinted_ids_from_version(
            runtime_engine, tenant_id, version_read.version_id
        )
        _attest_service(runtime_engine).record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=submitted.aggregate_revision,
            per_question_outcomes=_positive_outcomes(hinted_ids),
            idempotency_key=f"attest-{uuid.uuid7()}",
            event_context=_event_context(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=FIXED_NOW,
        )
        with bootstrap_engine.begin() as conn:
            attestation_id = conn.execute(
                text(
                    """
                    SELECT attestation_id
                    FROM content.worksheet_learner_hint_version_attestations
                    WHERE tenant_id = :tid AND version_id = :vid
                    """
                ),
                {"tid": tenant_id, "vid": version_read.version_id.value},
            ).scalar_one()
        with pytest.raises(Exception):
            with bootstrap_engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE content.worksheet_learner_hint_version_attestations
                        SET payload_digest = repeat('a', 64)
                        WHERE attestation_id = :aid
                        """
                    ),
                    {"aid": attestation_id},
                )

    def test_approve_without_attestation_still_denied(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        author_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=author_id
        )
        _, rev_after_v1 = _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            content_id=content_id,
        )
        version_read, revision = _guarded_service(runtime_engine).append(
            tenant_id,
            author_id,
            content_id=content_id,
            expected_aggregate_revision=rev_after_v1,
            payload=_worksheet_v2_payload(hint=FIXED_HINT),
            authoring_class_ref="class-5a",
            idempotency_key=f"deny-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        submitted = _review_service(runtime_engine).submit(
            tenant_id,
            author_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=revision,
            idempotency_key=f"submit-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        with pytest.raises(WorksheetLearnerHintAttestationRequired):
            _review_service(runtime_engine).approve(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_read.version_id,
                expected_aggregate_revision=submitted.aggregate_revision,
                reason_code=None,
                comment=None,
                idempotency_key=f"approve-{uuid.uuid7()}",
                event_context=_event_context(reviewer_id),
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=FIXED_NOW,
            )

    def test_db_rejects_malformed_outcome_booleans(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        tenant_id = uuid.uuid7()
        author_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=author_id
        )
        _, rev_after_v1 = _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            content_id=content_id,
        )
        version_read, _revision = _guarded_service(runtime_engine).append(
            tenant_id,
            author_id,
            content_id=content_id,
            expected_aggregate_revision=rev_after_v1,
            payload=_worksheet_v2_payload(hint=FIXED_HINT),
            authoring_class_ref="class-5a",
            idempotency_key=f"db-bool-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        with pytest.raises(IntegrityError) as exc_info:
            with runtime_engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO content.worksheet_learner_hint_version_attestations (
                            attestation_id, tenant_id, content_id, version_id,
                            payload_digest, qa_policy_version, author_principal_id,
                            reviewer_principal_id, per_question_outcomes, attested_at,
                            correlation_id
                        ) VALUES (
                            :aid, :tid, :cid, :vid, :digest, 'O63-03-v1',
                            :author, :reviewer,
                            CAST(:outcomes AS jsonb), :now, :corr
                        )
                        """
                    ),
                    {
                        "aid": uuid.uuid7(),
                        "tid": tenant_id,
                        "cid": content_id.value,
                        "vid": version_read.version_id.value,
                        "digest": "b" * 64,
                        "author": author_id,
                        "reviewer": reviewer_id,
                        "outcomes": json.dumps(
                            [
                                {
                                    "question_id": "q-1",
                                    "hard_block_clear": "false",
                                    "human_review_pass": True,
                                }
                            ]
                        ),
                        "now": FIXED_NOW,
                        "corr": uuid.uuid7(),
                    },
                )
        assert "ck_worksheet_hint_attestations_outcomes_booleans" in str(exc_info.value)

    def test_similarity_threshold_fixture_is_above_cutoff(self) -> None:
        payload = _worksheet_v2_payload(hint="Explanation for question 1 about fractions")
        worksheet = parse_worksheet_v2(payload)
        hint = worksheet.questions[0].learner_hint
        explanation = worksheet.questions[0].explanation
        assert hint is not None
        from aieos.domains.education.worksheet_hint_qa import _jaccard

        assert _jaccard(hint, explanation) >= SIMILARITY_FLAG_JACCARD_THRESHOLD


class TestR3GrantProvisioningRegression:
    """R3-F3: version-aware runtime grants for the T2 attestation table."""

    def test_provision_skips_missing_table_at_historical_revision(
        self, postgres18, bootstrap_engine
    ) -> None:
        from alembic import command

        from tests.conftest import alembic_config, provision_runtime_grants
        from tests.dbutil import (
            WORKSHEET_HINT_ATTESTATION_TABLE,
            worksheet_hint_attestation_table_exists,
        )

        from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade

        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "a360s010004")
        with bootstrap_engine.connect() as conn:
            assert not worksheet_hint_attestation_table_exists(conn)
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'content'")
                )
            }
            assert WORKSHEET_HINT_ATTESTATION_TABLE not in tables
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "a360s010004"
            )
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
        from tests.conftest import RUNTIME_USER

        with bootstrap_engine.connect() as conn:
            assert worksheet_hint_attestation_table_exists(conn)
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == EXPECTED_ALEMBIC_HEAD
            )
            assert conn.execute(
                text(
                    """
                    SELECT has_table_privilege(
                        :role,
                        'content.worksheet_learner_hint_version_attestations',
                        'INSERT'
                    )
                    """
                ),
                {"role": RUNTIME_USER},
            ).scalar_one() is True
            assert conn.execute(
                text(
                    """
                    SELECT has_table_privilege(
                        :role,
                        'content.worksheet_learner_hint_version_attestations',
                        'UPDATE'
                    )
                    """
                ),
                {"role": RUNTIME_USER},
            ).scalar_one() is False
