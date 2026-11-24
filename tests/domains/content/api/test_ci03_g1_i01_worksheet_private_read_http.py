"""CI03-G1-I01 — generic content_version_get worksheet @1/@2 owner+HUMAN private read (ADR-AIEOS-064 O64-06)."""

from __future__ import annotations

import json
import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
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
from aieos.platform.api.app import create_app, _UnavailablePrincipalClassificationAuthority
from aieos.platform.security.authorization import (
    CurrentPrincipalClassificationAuthority,
    PrincipalKind,
)
from tests.domains.education.test_aieos_v1_hint_t1 import (
    _hint_free_v2_payload,
    _worksheet_v2_payload,
)
from tests.domains.teaching.helpers_dev10_i03 import seed_teacher_principal
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
from tests.platform.security.authorization.helpers import seed_grant, seed_membership

pytestmark = pytest.mark.ci03_g1_i01

CURSOR_KEY = b"ci03-g1-i01-cursor-key"
PRIVATE_FIELD_NAMES = frozenset({"answer", "explanation", "learner_hint"})


def _headers(tenant_id: UUID, **extra: str) -> dict[str, str]:
    out = {"X-AIEOS-Tenant-ID": str(tenant_id), **extra}
    if "Idempotency-Key" not in out:
        out["Idempotency-Key"] = f"ci03-{uuid.uuid7()}"
    return out


def _build_client(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    classification=None,
) -> TestClient:
    if classification is None:
        classification = CurrentPrincipalClassificationAuthority(runtime_engine)
    return TestClient(
        create_app(
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
            assessment_authorization=AllowClassroomAssessmentAuthorization(),
            request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
            security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
            content_types=StaticContentTypeCatalog({WORKSHEET_CONTENT_TYPE, "test.generic"}),
            cursor_signing_key=CURSOR_KEY,
            schema_registry=build_preparation_content_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
            review_authorization=AllowReviewAuthorization(),
            review_comment_policy=AllowReviewCommentPolicy(),
            publication_authorization=AllowPublicationAuthorization(),
            publication_governance=AllowPublicationGovernance(),
            asset_reference_validation=AllowAssetReferenceValidation(),
            asset_current_governance=AllowAssetCurrentGovernance(),
            principal_classification_authority=classification,
        ),
        raise_server_exceptions=False,
    )


def _create_worksheet(client: TestClient, tenant_id: UUID) -> tuple[str, str]:
    response = client.post(
        "/api/v1/contents",
        json={
            "content_type": WORKSHEET_CONTENT_TYPE,
            "title": "CI03 worksheet",
            "description": "",
            "locale": "en-IN",
        },
        headers=_headers(tenant_id),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["content_id"], response.headers["ETag"]


def _append_version(
    client: TestClient,
    tenant_id: UUID,
    content_id: str,
    *,
    etag: str,
    schema_version: int,
    payload: dict,
) -> dict:
    hdrs = _headers(tenant_id)
    hdrs["If-Match"] = etag
    response = client.post(
        f"/api/v1/contents/{content_id}/versions",
        json={
            "schema_id": WORKSHEET_SCHEMA_ID,
            "schema_version": schema_version,
            "payload": payload,
        },
        headers=hdrs,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _get_version(client: TestClient, tenant_id: UUID, content_id: str, version_id: str):
    return client.get(
        f"/api/v1/contents/{content_id}/versions/{version_id}",
        headers=_headers(tenant_id),
    )


def _payload_has_private_field_names(payload: dict) -> set[str]:
    found: set[str] = set()
    questions = payload.get("questions")
    if not isinstance(questions, list):
        return found
    for item in questions:
        if not isinstance(item, dict):
            continue
        for key in PRIVATE_FIELD_NAMES:
            if key in item:
                found.add(key)
    return found


def _response_text_field_names(response) -> set[str]:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return set()
    payload = body.get("payload")
    if not isinstance(payload, dict):
        return set()
    return _payload_has_private_field_names(payload)


class TestWorksheetPrivateReadGate:
    def test_owner_human_worksheet_v1_and_v2_positive(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_teacher_principal(bootstrap_engine, owner_id)
        seed_membership(bootstrap_engine, tenant_id=tenant_id, principal_id=owner_id)
        client = _build_client(runtime_engine, tenant_id=tenant_id, principal_id=owner_id)
        content_id, etag = _create_worksheet(client, tenant_id)
        v1 = _append_version(
            client,
            tenant_id,
            content_id,
            etag=etag,
            schema_version=WORKSHEET_SCHEMA_VERSION,
            payload=valid_worksheet_payload(),
        )
        get_v1 = _get_version(client, tenant_id, content_id, v1["version_id"])
        assert get_v1.status_code == 200, get_v1.text
        assert get_v1.headers.get("Cache-Control") == "private, no-store"
        assert _response_text_field_names(get_v1) >= {"answer", "explanation"}

        v2 = _append_version(
            client,
            tenant_id,
            content_id,
            etag=f'"r{1}"',
            schema_version=WORKSHEET_SCHEMA_VERSION_V2,
            payload=_hint_free_v2_payload(),
        )
        get_v2 = _get_version(client, tenant_id, content_id, v2["version_id"])
        assert get_v2.status_code == 200
        assert get_v2.headers.get("Cache-Control") == "private, no-store"
        get_v1_hist = _get_version(client, tenant_id, content_id, v1["version_id"])
        assert get_v1_hist.status_code == 200

    def test_owner_hint_bearing_v2_exposes_learner_hint_field_name(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_teacher_principal(bootstrap_engine, owner_id)
        client = _build_client(runtime_engine, tenant_id=tenant_id, principal_id=owner_id)
        content_id, etag = _create_worksheet(client, tenant_id)
        v2 = _append_version(
            client,
            tenant_id,
            content_id,
            etag=etag,
            schema_version=WORKSHEET_SCHEMA_VERSION_V2,
            payload=_worksheet_v2_payload(),
        )
        response = _get_version(client, tenant_id, content_id, v2["version_id"])
        assert response.status_code == 200
        assert "learner_hint" in _response_text_field_names(response)

    def test_same_tenant_non_owner_denied_non_enumerating(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        seed_teacher_principal(bootstrap_engine, owner_id)
        seed_teacher_principal(bootstrap_engine, reviewer_id)
        seed_membership(bootstrap_engine, tenant_id=tenant_id, principal_id=reviewer_id)
        seed_grant(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            capability="content.review.decide",
        )
        owner_client = _build_client(
            runtime_engine, tenant_id=tenant_id, principal_id=owner_id
        )
        reviewer_client = _build_client(
            runtime_engine, tenant_id=tenant_id, principal_id=reviewer_id
        )
        content_id, etag = _create_worksheet(owner_client, tenant_id)
        version = _append_version(
            owner_client,
            tenant_id,
            content_id,
            etag=etag,
            schema_version=WORKSHEET_SCHEMA_VERSION_V2,
            payload=_worksheet_v2_payload(),
        )
        denied = _get_version(
            reviewer_client, tenant_id, content_id, version["version_id"]
        )
        assert denied.status_code == 404
        assert denied.json()["code"] == "content_version_not_found"
        assert not _response_text_field_names(denied)

    def test_workload_owner_denied(self, runtime_engine, bootstrap_engine, postgres18) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_teacher_principal(
            bootstrap_engine,
            owner_id,
            principal_kind=PrincipalKind.WORKLOAD,
        )
        client = _build_client(runtime_engine, tenant_id=tenant_id, principal_id=owner_id)
        content_id, etag = _create_worksheet(client, tenant_id)
        version = _append_version(
            client,
            tenant_id,
            content_id,
            etag=etag,
            schema_version=WORKSHEET_SCHEMA_VERSION,
            payload=valid_worksheet_payload(),
        )
        response = _get_version(client, tenant_id, content_id, version["version_id"])
        assert response.status_code == 404
        assert response.json()["code"] == "content_version_not_found"

    def test_nonworksheet_generic_get_unchanged(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        from tests.domains.content.api.test_gci_i05_http import (
            APPEND_BODY,
            CREATE_BODY,
            _client,
        )

        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        other_id = uuid.uuid7()
        seed_teacher_principal(bootstrap_engine, owner_id)
        seed_teacher_principal(bootstrap_engine, other_id)
        owner_client = _client(runtime_engine, tenant_id, owner_id)
        other_client = _client(runtime_engine, tenant_id, other_id)
        created = owner_client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(tenant_id),
        )
        assert created.status_code == 201
        content_id = created.json()["content_id"]
        appended = owner_client.post(
            f"/api/v1/contents/{content_id}/versions",
            json=APPEND_BODY,
            headers={**_headers(tenant_id), "If-Match": '"r0"'},
        )
        assert appended.status_code == 201
        version_id = appended.json()["version_id"]
        cross = other_client.get(
            f"/api/v1/contents/{content_id}/versions/{version_id}",
            headers=_headers(tenant_id),
        )
        assert cross.status_code == 200
        assert "Cache-Control" not in cross.headers

    def test_classification_unavailable_returns_503(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_teacher_principal(bootstrap_engine, owner_id)
        client = _build_client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=owner_id,
            classification=_UnavailablePrincipalClassificationAuthority(),
        )
        content_id, etag = _create_worksheet(client, tenant_id)
        version = _append_version(
            client,
            tenant_id,
            content_id,
            etag=etag,
            schema_version=WORKSHEET_SCHEMA_VERSION,
            payload=valid_worksheet_payload(),
        )
        response = _get_version(client, tenant_id, content_id, version["version_id"])
        assert response.status_code == 503
        assert response.json()["code"] == "authorization_unavailable"