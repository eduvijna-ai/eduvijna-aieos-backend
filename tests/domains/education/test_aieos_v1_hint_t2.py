"""PK-V1-HINT-T2 — attestation digest, QA heuristics, gated APPROVE/publish."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    WorksheetLearnerHintAttestationInvalid,
    WorksheetLearnerHintAttestationRequired,
)
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.application.worksheet_hint_attestation_record import (
    RecordWorksheetLearnerHintAttestationService,
)
from aieos.domains.content.domain.identities import AggregateRevision, ContentId
from aieos.domains.content.domain.version import ContentPayload, thaw_json_value
from aieos.domains.content.domain.worksheet_hint_attestation import HintQuestionAttestationOutcome
from aieos.domains.education.worksheet_v2 import parse_worksheet_v2
from aieos.domains.education.worksheet_hint_digest import (
    worksheet_hint_attestation_digest_from_version_payload,
)
from aieos.domains.education.worksheet_hint_qa import (
    HintReviewFlag,
    assess_worksheet_hint_qa,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from tests.domains.education.test_aieos_v1_hint_t1 import (
    StubHumanClassification,
    _worksheet_v2_payload,
)
from tests.domains.education.test_aieos_v1_hint_t1_persistence import (
    FIXED_NOW,
    _append_v1,
    _event_context,
    _guarded_service,
    _seed_worksheet_content,
)
from tests.fakes import (
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
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


class TestDigestAndQa:
    def test_digest_includes_schema_envelope(self) -> None:
        payload = ContentPayload.from_mapping(_worksheet_v2_payload(hint=FIXED_HINT))
        digest = worksheet_hint_attestation_digest_from_version_payload(payload.body)
        assert len(digest) == 64

    def test_similarity_flag_requires_explicit_human_pass(self) -> None:
        payload = _worksheet_v2_payload(
            hint="Explanation for question about fractions carefully"
        )
        assessments = assess_worksheet_hint_qa(payload)
        assert assessments
        assert HintReviewFlag.EXPLANATION_SIMILARITY in assessments[0].flags
        with pytest.raises(WorksheetLearnerHintAttestationInvalid):
            from aieos.domains.content.application.worksheet_hint_attestation_gate import (
                assert_attestation_outcomes_against_qa,
            )

            assert_attestation_outcomes_against_qa(
                payload=payload,
                outcomes=(
                    HintQuestionAttestationOutcome(
                        question_id=assessments[0].question_id,
                        hard_block_clear=True,
                        human_review_pass=False,
                    ),
                ),
            )


class TestAttestationPostgres:
    def test_record_attestation_then_approve_succeeds(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
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
        review = ReviewCommandService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            AllowReviewCommentPolicy(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
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
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
            version = uow.versions.get(version_read.version_id)
            assert version is not None
            worksheet = parse_worksheet_v2(dict(thaw_json_value(version.payload.body)))
            hinted_ids = [
                q.id for q in worksheet.questions if q.learner_hint is not None
            ]
        attest = RecordWorksheetLearnerHintAttestationService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            StubHumanClassification(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
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
        review = ReviewCommandService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            AllowReviewCommentPolicy(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
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
        with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
            version = uow.versions.get(version_read.version_id)
            assert version is not None
            worksheet = parse_worksheet_v2(dict(thaw_json_value(version.payload.body)))
            hinted_ids = [
                q.id for q in worksheet.questions if q.learner_hint is not None
            ]
        RecordWorksheetLearnerHintAttestationService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            StubHumanClassification(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        ).record(
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
        review = ReviewCommandService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            AllowReviewCommentPolicy(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
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
        with pytest.raises(WorksheetLearnerHintAttestationRequired):
            review.approve(
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
