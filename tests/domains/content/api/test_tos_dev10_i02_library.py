"""TOS-DEV10-I02 Teacher OS Library read projection HTTP tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from tests.dbutil import REPO_ROOT
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
    make_test_schema_registry,
)
from tests.platform.workflows.helpers import (
    create_content,
    decide,
    headers,
    submit_review,
)

pytestmark = pytest.mark.tos_dev10_i02

CURSOR_KEY = b"tos-dev10-i02-library-cursor-signing-key"


def _app(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID):
    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic", "test.other"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )


def _client(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID) -> TestClient:
    return TestClient(
        _app(runtime_engine, tenant_id, principal_id),
        raise_server_exceptions=False,
    )


def _assert_problem(response, *, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == code
    return body


def _library_list(client: TestClient, tenant_id: UUID, **params):
    return client.get(
        "/api/v1/teacher-os/library",
        params=params or None,
        headers=headers(tenant_id),
    )


def _library_get(client: TestClient, tenant_id: UUID, content_id: str):
    return client.get(
        f"/api/v1/teacher-os/library/{content_id}",
        headers=headers(tenant_id),
    )


def _library_version(
    client: TestClient, tenant_id: UUID, content_id: str, version_id: str
):
    return client.get(
        f"/api/v1/teacher-os/library/{content_id}/versions/{version_id}",
        headers=headers(tenant_id),
    )


def _append(
    client: TestClient, tenant_id: UUID, content_id: str, *, etag: str, marker: str = "v1"
):
    hdrs = headers(tenant_id)
    hdrs["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions",
        json={
            "schema_id": "test.generic",
            "schema_version": 1,
            "payload": {"marker": marker},
        },
        headers=hdrs,
    )


def _publish_flow(client: TestClient, tenant_id: UUID, *, marker: str = "pub"):
    created = create_content(client, tenant_id)
    content_id = created["content_id"]
    appended = _append(client, tenant_id, content_id, etag='"r0"', marker=marker)
    assert appended.status_code == 201, appended.text
    version_id = appended.json()["version_id"]
    submitted = submit_review(
        client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
    )
    assert submitted.status_code == 200, submitted.text
    approved = decide(
        client,
        tenant_id,
        content_id,
        version_id,
        action="approve",
        etag=submitted.headers["ETag"],
    )
    assert approved.status_code == 200, approved.text
    hdrs = headers(tenant_id)
    hdrs["If-Match"] = approved.headers["ETag"]
    published = client.post(
        f"/api/v1/contents/{content_id}/actions/publish",
        json={"version_id": version_id},
        headers=hdrs,
    )
    assert published.status_code == 200, published.text
    return content_id, version_id


class TestLibraryVisibility:
    def test_teacher_sees_own_content(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        created = create_content(client, tenant_id)
        listed = _library_list(client, tenant_id)
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert len(items) == 1
        assert items[0]["content_id"] == created["content_id"]
        assert items[0]["title"] == created["title"]
        assert items[0]["stewardship_state"] == "DRAFT"
        assert items[0]["published_version_id"] is None
        assert "tenant_id" not in items[0]
        assert "owner_principal_id" not in items[0]

    def test_cannot_see_other_teacher_same_tenant(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_a = uuid.uuid7()
        owner_b = uuid.uuid7()
        client_a = _client(runtime_engine, tenant_id, owner_a)
        client_b = _client(runtime_engine, tenant_id, owner_b)
        created = create_content(client_a, tenant_id)
        content_id = created["content_id"]
        assert len(_library_list(client_a, tenant_id).json()["items"]) == 1
        assert _library_list(client_b, tenant_id).json()["items"] == []
        _assert_problem(
            _library_get(client_b, tenant_id, content_id),
            status=404,
            code="library_item_not_found",
        )

    def test_tenant_isolation(self, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal = uuid.uuid7()
        client_a = _client(runtime_engine, tenant_a, principal)
        client_b = _client(runtime_engine, tenant_b, principal)
        created = create_content(client_a, tenant_a)
        assert [i["content_id"] for i in _library_list(client_a, tenant_a).json()["items"]] == [
            created["content_id"]
        ]
        assert _library_list(client_b, tenant_b).json()["items"] == []
        _assert_problem(
            _library_get(client_b, tenant_b, created["content_id"]),
            status=404,
            code="library_item_not_found",
        )

    def test_transport_cannot_redefine_ownership(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner = uuid.uuid7()
        other = uuid.uuid7()
        client_owner = _client(runtime_engine, tenant_id, owner)
        created = create_content(client_owner, tenant_id)
        content_id = created["content_id"]
        with bootstrap_engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT owner_principal_id FROM content.contents "
                    "WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).one()
            assert row.owner_principal_id == owner
        client_other = _client(runtime_engine, tenant_id, other)
        # Query param must not redefine ownership; other teacher still sees empty.
        listed = client_other.get(
            "/api/v1/teacher-os/library",
            params={"owner_principal_id": str(owner)},
            headers=headers(tenant_id),
        )
        assert listed.status_code == 200
        assert listed.json()["items"] == []
        _assert_problem(
            _library_get(client_other, tenant_id, content_id),
            status=404,
            code="library_item_not_found",
        )


class TestLibraryFiltersAndProjection:
    def test_type_and_state_filtering(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        draft = create_content(client, tenant_id)
        content_id, _ = _publish_flow(client, tenant_id, marker="typed")
        by_state = _library_list(client, tenant_id, stewardship_state="DRAFT")
        assert by_state.status_code == 200
        assert [i["content_id"] for i in by_state.json()["items"]] == [draft["content_id"]]
        by_type = _library_list(client, tenant_id, content_type="test.generic")
        ids = {i["content_id"] for i in by_type.json()["items"]}
        assert draft["content_id"] in ids
        assert content_id in ids
        published_only = _library_list(client, tenant_id, published_only=True)
        assert [i["content_id"] for i in published_only.json()["items"]] == [content_id]
        bad_state = _library_list(client, tenant_id, stewardship_state="NOT_A_STATE")
        _assert_problem(bad_state, status=400, code="invalid_content_request")

    def test_unpublished_current_published_projection(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        content_id = created["content_id"]
        draft_detail = _library_get(client, tenant_id, content_id)
        assert draft_detail.status_code == 200
        assert draft_detail.json()["current_version_id"] is None
        assert draft_detail.json()["published_version_id"] is None
        appended = _append(client, tenant_id, content_id, etag='"r0"', marker="cur")
        version_id = appended.json()["version_id"]
        after_append = _library_get(client, tenant_id, content_id).json()
        assert after_append["current_version_id"] == version_id
        assert after_append["published_version_id"] is None
        submitted = submit_review(
            client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        in_review = _library_get(client, tenant_id, content_id).json()
        assert in_review["stewardship_state"] == "IN_REVIEW"
        assert in_review["review_navigation"] == {
            "content_id": content_id,
            "version_id": version_id,
        }
        approved = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = approved.headers["ETag"]
        published = client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers=hdrs,
        )
        assert published.status_code == 200
        final = _library_get(client, tenant_id, content_id).json()
        assert final["published_version_id"] == version_id
        assert final["current_version_id"] == version_id
        assert final["review_navigation"] is None
        version = _library_version(client, tenant_id, content_id, version_id)
        assert version.status_code == 200
        assert version.json()["payload"] == {"marker": "cur"}
        assert version.json()["version_id"] == version_id

    def test_empty_library(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        listed = _library_list(client, tenant_id)
        assert listed.status_code == 200
        assert listed.json() == {"items": [], "next_cursor": None}


class TestLibraryPaginationAndHttp:
    def test_deterministic_ordering_and_bounded_pagination(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        over = _library_list(client, tenant_id, limit=101)
        _assert_problem(over, status=400, code="invalid_content_request")
        zero = _library_list(client, tenant_id, limit=0)
        _assert_problem(zero, status=400, code="invalid_content_request")
        ids = []
        for i in range(3):
            created = create_content(client, tenant_id)
            ids.append(created["content_id"])
            with bootstrap_engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE content.contents SET updated_at = :ts "
                        "WHERE content_id = :cid"
                    ),
                    {
                        "cid": created["content_id"],
                        "ts": datetime(2026, 9, 1, i + 1, tzinfo=UTC),
                    },
                )
        page1 = _library_list(client, tenant_id, limit=2)
        assert page1.status_code == 200
        body1 = page1.json()
        assert len(body1["items"]) == 2
        assert body1["next_cursor"]
        # newest first
        assert [i["content_id"] for i in body1["items"]] == [ids[2], ids[1]]
        page2 = _library_list(client, tenant_id, limit=2, cursor=body1["next_cursor"])
        assert [i["content_id"] for i in page2.json()["items"]] == [ids[0]]
        assert page2.json()["next_cursor"] is None

    def test_auth_failure_and_no_existence_leakage(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal)
        created = create_content(client, tenant_id)
        unauth_app = create_app(
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
            assessment_authorization=AllowClassroomAssessmentAuthorization(),
            request_identity_authenticator=FixedPrincipalAuthenticator(
                principal, unauthenticated=True
            ),
            security_resolver=StubSecurityContextResolver(tenant_id, principal),
            content_types=StaticContentTypeCatalog({"test.generic"}),
            cursor_signing_key=CURSOR_KEY,
            schema_registry=make_test_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
            review_authorization=AllowReviewAuthorization(),
            review_comment_policy=AllowReviewCommentPolicy(),
            publication_authorization=AllowPublicationAuthorization(),
            publication_governance=AllowPublicationGovernance(),
            asset_reference_validation=AllowAssetReferenceValidation(),
            asset_current_governance=AllowAssetCurrentGovernance(),
        )
        unauth = TestClient(unauth_app, raise_server_exceptions=False)
        missing = _assert_problem(
            unauth.get(
                "/api/v1/teacher-os/library",
                headers=headers(tenant_id),
            ),
            status=401,
            code="unauthenticated",
        )
        assert "content_id" not in missing
        other = uuid.uuid7()
        other_client = _client(runtime_engine, tenant_id, other)
        leaked = _library_get(other_client, tenant_id, created["content_id"])
        body = _assert_problem(leaked, status=404, code="library_item_not_found")
        assert body["detail"] == "Library item was not found"
        assert created["content_id"] not in body.get("detail", "")


class TestLibraryArchitecture:
    def test_no_library_table_or_lifecycle_sor(self) -> None:
        content_root = REPO_ROOT / "src" / "aieos" / "domains" / "content"
        forbidden = (
            "class Library(",
            "class LibraryAggregate",
            "library_table",
            "libraries_table",
            "CREATE TABLE content.library",
            "CREATE TABLE content.libraries",
        )
        hits: list[str] = []
        for path in content_root.rglob("*.py"):
            text_body = path.read_text(encoding="utf-8")
            for needle in forbidden:
                if needle in text_body:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{needle}")
        migrations = REPO_ROOT / "migrations" / "versions"
        for path in migrations.glob("*.py"):
            text_body = path.read_text(encoding="utf-8")
            for needle in ("content.library", "content.libraries", "library_items"):
                if needle in text_body.lower() and "teacher-os/library" not in text_body:
                    # only flag table-like DDL
                    if "create table" in text_body.lower() and "library" in text_body.lower():
                        hits.append(str(path.name))
        assert hits == []
        library_src = (
            content_root / "application" / "library.py"
        ).read_text(encoding="utf-8")
        assert "No mutations" in library_src or "read" in library_src.lower()
        assert "uow.library.list_page" in library_src
