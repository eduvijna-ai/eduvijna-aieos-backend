"""HTTP generic append bypass for worksheet@2 learner_hint key (ADR-063 §7.5, R2-F2)."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

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
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from tests.domains.education.test_aieos_v1_hint_t1 import (
    _hint_free_v2_payload,
    _worksheet_v2_payload,
)
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload
from tests.fakes import (
    AllowClassroomAssessmentAuthorization,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
)

pytestmark = pytest.mark.aieos_v1_hint_t1

CURSOR_KEY = b"hint-t1-http-cursor-key"


def _headers(tenant_id: UUID, **extra: str) -> dict[str, str]:
    out = {"X-AIEOS-Tenant-ID": str(tenant_id), **extra}
    if "Idempotency-Key" not in out:
        out["Idempotency-Key"] = f"http-{uuid.uuid7()}"
    return out


def _client(runtime_engine, tenant_id: UUID, principal_id: UUID) -> TestClient:
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
            review_authorization=AllowReviewAuthorization(),
            review_comment_policy=AllowReviewCommentPolicy(),
            publication_authorization=AllowPublicationAuthorization(),
            publication_governance=AllowPublicationGovernance(),
            asset_reference_validation=AllowAssetReferenceValidation(),
            asset_current_governance=AllowAssetCurrentGovernance(),
        ),
        raise_server_exceptions=False,
    )


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


def _counts(engine: Engine, tenant_id: UUID, content_id: str) -> tuple[int, int, int]:
    with engine.connect() as conn:
        versions = conn.execute(
            text(
                "SELECT count(*) FROM content.content_versions WHERE content_id = :cid"
            ),
            {"cid": content_id},
        ).scalar_one()
        idem = conn.execute(
            text(
                """
                SELECT count(*) FROM api.idempotency_records
                WHERE tenant_id = :tid AND result_content_id = :cid
                """
            ),
            {"tid": tenant_id, "cid": content_id},
        ).scalar_one()
        audits = conn.execute(
            text(
                """
                SELECT count(*) FROM security.audit_records
                WHERE tenant_id = :tid AND primary_resource_id = :cid
                  AND action = 'content.version.create'
                """
            ),
            {"tid": tenant_id, "cid": content_id},
        ).scalar_one()
    return int(versions), int(idem), int(audits)


class TestGenericHttpWorksheetV2HintBypass:
    @pytest.mark.parametrize(
        "hint_value",
        [
            "Valid hint text.",
            "",
            None,
            42,
            [],
        ],
    )
    def test_hint_key_presence_fail_close_before_mutations(
        self,
        runtime_engine,
        bootstrap_engine,
        hint_value,
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        content_id, etag = _create_worksheet(client, tenant_id)
        v_before, idem_before, audit_before = _counts(
            bootstrap_engine, tenant_id, content_id
        )
        payload = _worksheet_v2_payload(hint="Valid hint text.")
        payload["questions"][0]["learner_hint"] = hint_value
        hdrs = _headers(tenant_id, **{"Idempotency-Key": f"bypass-{uuid.uuid7()}"})
        hdrs["If-Match"] = etag
        response = client.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": WORKSHEET_SCHEMA_ID,
                "schema_version": WORKSHEET_SCHEMA_VERSION_V2,
                "payload": payload,
            },
            headers=hdrs,
        )
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["code"] == "content_payload_invalid"
        v_after, idem_after, audit_after = _counts(
            bootstrap_engine, tenant_id, content_id
        )
        assert v_after == v_before
        assert idem_after == idem_before
        assert audit_after == audit_before

    def test_hint_free_v2_append_succeeds(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        content_id, etag = _create_worksheet(client, tenant_id)
        hdrs = _headers(tenant_id)
        hdrs["If-Match"] = etag
        response = client.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": WORKSHEET_SCHEMA_ID,
                "schema_version": WORKSHEET_SCHEMA_VERSION_V2,
                "payload": _hint_free_v2_payload(),
            },
            headers=hdrs,
        )
        assert response.status_code == 201, response.text

    def test_worksheet_v1_append_still_succeeds(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        content_id, etag = _create_worksheet(client, tenant_id)
        hdrs = _headers(tenant_id)
        hdrs["If-Match"] = etag
        response = client.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": WORKSHEET_SCHEMA_ID,
                "schema_version": WORKSHEET_SCHEMA_VERSION,
                "payload": valid_worksheet_payload(),
            },
            headers=hdrs,
        )
        assert response.status_code == 201, response.text
