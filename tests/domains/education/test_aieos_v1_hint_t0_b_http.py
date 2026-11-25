"""PK-V1-HINT-T0-B — Teacher worksheet@2 hint append + attestation HTTP (ADR-063 §7)."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.schema import (
    WORKSHEET_CONTENT_TYPE,
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
    WORKSHEET_SCHEMA_VERSION_V2,
    build_preparation_content_schema_registry,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.development.coherent_school_context import DevelopmentCoherentSchoolContextProvider
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.platform.api.app import create_app
from aieos.platform.api.etag import encode_revision_etag
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    assignment_bound_deps,
    issue_governed_review_assignment,
)
from tests.domains.education.test_aieos_v1_hint_t1_persistence import FIXED_NOW
from tests.domains.assessment.helpers_dev08_i02 import MutableSchoolContextClassReader
from tests.domains.education.test_aieos_v1_hint_t1 import (
    StubHumanClassification,
    _hint_free_v2_payload,
    _worksheet_v2_payload,
)
from tests.domains.education.test_aieos_v1_hint_t1_persistence import _append_v1
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload
from tests.fakes import (
    AllowAIGenerationAuthorization,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowClassroomAssessmentAuthorization,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    IDEMPOTENCY_RETENTION,
    StubSecurityContextResolver,
)

pytestmark = pytest.mark.aieos_v1_hint_t1

CURSOR_KEY = b"hint-t0-b-http-cursor-key"


def _headers(tenant_id: UUID, **extra: str) -> dict[str, str]:
    out = {"X-AIEOS-Tenant-ID": str(tenant_id), **extra}
    if "Idempotency-Key" not in out:
        out["Idempotency-Key"] = f"http-{uuid.uuid7()}"
    return out


def _client(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    school_context_reader: MutableSchoolContextClassReader | None,
    version_create_auth: AllowAIGenerationAuthorization | None = None,
    review_auth: AllowReviewAuthorization | None = None,
    school_context_provider: DevelopmentCoherentSchoolContextProvider | None = None,
) -> TestClient:
    assignment_bound = None
    if school_context_provider is not None:
        assignment_bound = assignment_bound_deps(
            runtime_engine,
            tenant_id,
            clock=lambda: FIXED_NOW,
            school_context_provider=school_context_provider,
        )
    return TestClient(
        create_app(
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
            assessment_authorization=AllowClassroomAssessmentAuthorization(),
            request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
            security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
            content_types=StaticContentTypeCatalog({WORKSHEET_CONTENT_TYPE}),
            cursor_signing_key=CURSOR_KEY,
            schema_registry=build_preparation_content_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
            review_authorization=review_auth or AllowReviewAuthorization(),
            review_comment_policy=AllowReviewCommentPolicy(),
            publication_authorization=AllowPublicationAuthorization(),
            publication_governance=AllowPublicationGovernance(),
            asset_reference_validation=AllowAssetReferenceValidation(),
            asset_current_governance=AllowAssetCurrentGovernance(),
            ai_generation_authorization=version_create_auth
            or AllowAIGenerationAuthorization(),
            school_context_class_reader=school_context_reader,
            principal_classification_authority=StubHumanClassification(),
            assignment_bound=assignment_bound,
        ),
        raise_server_exceptions=False,
    )


def _head_revision_etag(
    runtime_engine: Engine, *, tenant_id: UUID, content_id: UUID
) -> str:
    with runtime_engine.connect() as conn:
        rev = conn.execute(
            text(
                """
                SELECT aggregate_revision FROM content.contents
                WHERE tenant_id = :tid AND content_id = :cid
                """
            ),
            {"tid": tenant_id, "cid": content_id},
        ).scalar_one()
    return encode_revision_etag(int(rev))


def _create_worksheet(client: TestClient, tenant_id: UUID) -> tuple[str, str]:
    response = client.post(
        "/api/v1/contents",
        json={
            "content_type": WORKSHEET_CONTENT_TYPE,
            "title": "Fractions",
            "description": "",
            "locale": "en-IN",
        },
        headers=_headers(tenant_id),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["content_id"], response.headers["ETag"]


class TestGuardedHintAppendHttp:
    def test_positive_guarded_append_returns_location_etag_idempotency(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        reader = MutableSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
            class_refs=("class-5a",),
        )
        client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            school_context_reader=reader,
        )
        content_id, etag = _create_worksheet(client, tenant_id)
        hint_free = client.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": WORKSHEET_SCHEMA_ID,
                "schema_version": WORKSHEET_SCHEMA_VERSION,
                "payload": valid_worksheet_payload(),
                "asset_refs": [],
            },
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert hint_free.status_code == 201, hint_free.text
        etag = hint_free.headers["ETag"]
        idem = f"guard-hint-{uuid.uuid7()}"
        append = client.post(
            f"/api/v1/contents/{content_id}/worksheet-learner-hint-versions",
            json={
                "authoring_class_ref": "class-5a",
                "payload": _worksheet_v2_payload(),
                "asset_refs": [],
            },
            headers=_headers(tenant_id, **{"If-Match": etag, "Idempotency-Key": idem}),
        )
        assert append.status_code == 201, append.text
        version = append.json()
        assert version["schema_id"] == WORKSHEET_SCHEMA_ID
        assert version["schema_version"] == WORKSHEET_SCHEMA_VERSION_V2
        assert "Location" in append.headers
        assert append.headers["ETag"]
        replay = client.post(
            f"/api/v1/contents/{content_id}/worksheet-learner-hint-versions",
            json={
                "authoring_class_ref": "class-5a",
                "payload": _worksheet_v2_payload(hint="Different body"),
                "asset_refs": [],
            },
            headers=_headers(tenant_id, **{"If-Match": etag, "Idempotency-Key": idem}),
        )
        assert replay.status_code == 409

    def test_fail_closed_without_school_context_reader(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            school_context_reader=None,
        )
        content_id, etag = _create_worksheet(client, tenant_id)
        response = client.post(
            f"/api/v1/contents/{content_id}/worksheet-learner-hint-versions",
            json={
                "authoring_class_ref": "class-5a",
                "payload": _worksheet_v2_payload(),
            },
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/problem+json")

    def test_denied_without_version_create_grant(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        reader = MutableSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )
        client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            school_context_reader=reader,
            version_create_auth=AllowAIGenerationAuthorization(allow=False),
        )
        content_id, etag = _create_worksheet(client, tenant_id)
        response = client.post(
            f"/api/v1/contents/{content_id}/worksheet-learner-hint-versions",
            json={
                "authoring_class_ref": "class-5a",
                "payload": _worksheet_v2_payload(),
            },
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["code"] == "forbidden"
        assert body["title"] == "Forbidden"

    @pytest.mark.parametrize(
        "hint_value",
        [None, "", "hint", {}, 42],
    )
    def test_generic_append_still_rejects_learner_hint_key_presence(
        self, runtime_engine, bootstrap_engine, hint_value
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=tenant_id, teacher_principal_id=principal_id
            ),
        )
        content_id, etag = _create_worksheet(client, tenant_id)
        payload = _hint_free_v2_payload()
        payload["questions"][0]["learner_hint"] = hint_value
        response = client.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": WORKSHEET_SCHEMA_ID,
                "schema_version": WORKSHEET_SCHEMA_VERSION_V2,
                "payload": payload,
            },
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert response.status_code == 422


class TestHintAttestationRecordHttp:
    def _hinted_version_in_review(
        self, runtime_engine, bootstrap_engine
    ) -> tuple[UUID, UUID, UUID, UUID, UUID, str]:
        tenant_id = uuid.uuid7()
        author_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        reader = MutableSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=author_id,
        )
        client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            school_context_reader=reader,
        )
        content_id_str, etag = _create_worksheet(client, tenant_id)
        cid = ContentId(UUID(content_id_str))
        _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            content_id=cid,
        )
        etag = encode_revision_etag(1)
        author_client = client
        append = author_client.post(
            f"/api/v1/contents/{content_id_str}/worksheet-learner-hint-versions",
            json={
                "authoring_class_ref": "class-5a",
                "payload": _worksheet_v2_payload(),
            },
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert append.status_code == 201, append.text
        version_id = append.json()["version_id"]
        submit = author_client.post(
            f"/api/v1/contents/{content_id_str}/versions/{version_id}/actions/submit-for-review",
            headers=_headers(
                tenant_id,
                **{"If-Match": append.headers["ETag"]},
            ),
        )
        assert submit.status_code == 200, submit.text
        provider = issue_governed_review_assignment(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            owner_id=author_id,
            reviewer_id=reviewer_id,
            content_id=cid,
            version_id=ContentVersionId(UUID(version_id)),
            class_ref="class-5a",
            now=FIXED_NOW,
        )
        etag = _head_revision_etag(
            runtime_engine, tenant_id=tenant_id, content_id=cid
        )
        return (
            tenant_id,
            author_id,
            reviewer_id,
            UUID(content_id_str),
            UUID(version_id),
            etag,
            provider,
        )

    def test_positive_attestation_record(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        (
            tenant_id,
            author_id,
            reviewer_id,
            content_id,
            version_id,
            etag,
            provider,
        ) = self._hinted_version_in_review(runtime_engine, bootstrap_engine)
        reviewer_client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=tenant_id, teacher_principal_id=reviewer_id
            ),
            school_context_provider=provider,
        )
        outcomes = [
            {
                "question_id": "q-1",
                "hard_block_clear": True,
                "human_review_pass": True,
            }
        ]
        response = reviewer_client.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/record-worksheet-hint-attestation",
            json={"per_question_outcomes": outcomes},
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["content_id"] == str(content_id)
        assert body["version_id"] == str(version_id)
        assert body["attestation_id"]

    def test_reviewer_must_differ_from_author(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        (
            tenant_id,
            author_id,
            _reviewer_id,
            content_id,
            version_id,
            etag,
            provider,
        ) = self._hinted_version_in_review(runtime_engine, bootstrap_engine)
        author_client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=tenant_id, teacher_principal_id=author_id
            ),
            school_context_provider=provider,
        )
        response = author_client.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/record-worksheet-hint-attestation",
            json={
                "per_question_outcomes": [
                    {
                        "question_id": "q-1",
                        "hard_block_clear": True,
                        "human_review_pass": True,
                    }
                ]
            },
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "review_decision_not_allowed"

    def test_denied_without_review_decide_grant(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        (
            tenant_id,
            _author_id,
            reviewer_id,
            content_id,
            version_id,
            etag,
            provider,
        ) = self._hinted_version_in_review(runtime_engine, bootstrap_engine)
        reviewer_client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=tenant_id, teacher_principal_id=reviewer_id
            ),
            review_auth=AllowReviewAuthorization(allow_decide=False),
            school_context_provider=provider,
        )
        response = reviewer_client.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/record-worksheet-hint-attestation",
            json={
                "per_question_outcomes": [
                    {
                        "question_id": "q-1",
                        "hard_block_clear": True,
                        "human_review_pass": True,
                    }
                ]
            },
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert response.status_code == 403
        body = response.json()
        assert body["code"] == "forbidden"
        assert response.headers["content-type"].startswith("application/problem+json")

    def test_denied_stale_class_ref(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        reader = MutableSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
            class_refs=("class-other",),
        )
        client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            school_context_reader=reader,
        )
        content_id, etag = _create_worksheet(client, tenant_id)
        response = client.post(
            f"/api/v1/contents/{content_id}/worksheet-learner-hint-versions",
            json={
                "authoring_class_ref": "class-5a",
                "payload": _worksheet_v2_payload(),
            },
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_content_request"

    def test_missing_if_match_returns_precondition_required(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=tenant_id, teacher_principal_id=principal_id
            ),
        )
        content_id, _etag = _create_worksheet(client, tenant_id)
        headers = _headers(tenant_id)
        headers.pop("If-Match", None)
        response = client.post(
            f"/api/v1/contents/{content_id}/worksheet-learner-hint-versions",
            json={
                "authoring_class_ref": "class-5a",
                "payload": _worksheet_v2_payload(),
            },
            headers=headers,
        )
        assert response.status_code == 428


def _review_decision_count(
    runtime_engine: Engine, tenant_id: UUID, version_id: UUID
) -> int:
    with runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :t, true)"),
            {"t": str(tenant_id)},
        )
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.review_decisions
                    WHERE tenant_id = :tid AND version_id = :vid
                    """
                ),
                {"tid": tenant_id, "vid": version_id},
            ).scalar_one()
        )


def _publication_count(
    runtime_engine: Engine, tenant_id: UUID, content_id: UUID
) -> int:
    with runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :t, true)"),
            {"t": str(tenant_id)},
        )
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.publications
                    WHERE tenant_id = :tid AND content_id = :cid
                    """
                ),
                {"tid": tenant_id, "cid": content_id},
            ).scalar_one()
        )


def _assert_problem(response, *, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == code
    assert body["status"] == status
    blob = response.text.lower()
    assert "consider the denominator" not in blob
    assert "learner_hint" not in blob
    return body


class TestHintReviewPublishProblemTaxonomy:
    """R2: WorksheetLearnerHintAttestationRequired / PublicationBlocked → HTTP 409."""

    def _hinted_version_approved(
        self, runtime_engine, bootstrap_engine
    ) -> tuple[UUID, UUID, UUID, UUID, UUID, str]:
        (
            tenant_id,
            author_id,
            reviewer_id,
            content_id,
            version_id,
            etag,
            provider,
        ) = TestHintAttestationRecordHttp()._hinted_version_in_review(
            runtime_engine, bootstrap_engine
        )
        reviewer_client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=tenant_id, teacher_principal_id=reviewer_id
            ),
            school_context_provider=provider,
        )
        attest = reviewer_client.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/record-worksheet-hint-attestation",
            json={
                "per_question_outcomes": [
                    {
                        "question_id": "q-1",
                        "hard_block_clear": True,
                        "human_review_pass": True,
                    }
                ]
            },
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert attest.status_code == 201, attest.text
        etag = attest.headers["ETag"]
        approved = reviewer_client.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/approve",
            json={},
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert approved.status_code == 200, approved.text
        return (
            tenant_id,
            author_id,
            reviewer_id,
            content_id,
            version_id,
            approved.headers["ETag"],
        )

    def test_approve_without_attestation_returns_409_not_500(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        (
            tenant_id,
            _author_id,
            reviewer_id,
            content_id,
            version_id,
            etag,
            provider,
        ) = TestHintAttestationRecordHttp()._hinted_version_in_review(
            runtime_engine, bootstrap_engine
        )
        before = _review_decision_count(runtime_engine, tenant_id, version_id)
        reviewer_client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=tenant_id, teacher_principal_id=reviewer_id
            ),
            school_context_provider=provider,
        )
        response = reviewer_client.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/approve",
            json={},
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        _assert_problem(response, status=409, code="review_decision_not_allowed")
        assert _review_decision_count(runtime_engine, tenant_id, version_id) == before

    def test_author_and_reviewer_publish_denied_third_publisher_allowed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        (
            tenant_id,
            author_id,
            reviewer_id,
            content_id,
            version_id,
            etag,
        ) = self._hinted_version_approved(runtime_engine, bootstrap_engine)
        pub_before = _publication_count(runtime_engine, tenant_id, content_id)

        author_client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=tenant_id, teacher_principal_id=author_id
            ),
        )
        author_pub = author_client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": str(version_id)},
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        _assert_problem(author_pub, status=409, code="publication_not_allowed")

        reviewer_client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=tenant_id, teacher_principal_id=reviewer_id
            ),
        )
        reviewer_pub = reviewer_client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": str(version_id)},
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        _assert_problem(reviewer_pub, status=409, code="publication_not_allowed")
        assert _publication_count(runtime_engine, tenant_id, content_id) == pub_before

        publisher_id = uuid.uuid7()
        publisher_client = _client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=publisher_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=tenant_id, teacher_principal_id=publisher_id
            ),
        )
        ok = publisher_client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": str(version_id)},
            headers=_headers(tenant_id, **{"If-Match": etag}),
        )
        assert ok.status_code == 200, ok.text
        assert _publication_count(runtime_engine, tenant_id, content_id) == pub_before + 1