"""CI03-G1-I01 / R1 — real JWT + kernel PG18 HTTP matrix for worksheet private generic GET."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from aieos.domains.content.application.errors import ContentVersionNotFound
from aieos.domains.content.application.http_append import GetContentVersionService
from aieos.domains.content.application.ports import (
    CONTENT_PUBLISH,
    CONTENT_REVIEW_DECIDE,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.api.app import _UnavailablePrincipalClassificationAuthority, create_app
from aieos.platform.security.authorization import (
    CurrentPrincipalClassificationAuthority,
    PrincipalKind,
)
from aieos.platform.security.authorization.decisions import PrincipalStatus
from aieos.platform.security.context import AuthorizationUnavailableError
from tests.domains.content.api.helpers_ci03_g1_i01 import (
    GET_VERSION,
    auth_headers,
    build_real_auth_app,
    client_for,
    mint_token,
    owner_worksheet_fixtures,
    private_field_names,
    problem_code,
    rsa_material,
    seed_human_member,
    seed_owner_bundle,
)
from tests.domains.content.api.test_gci_i05_http import (
    APPEND_BODY,
    CREATE_BODY,
    _client as gci_stub_client,
)
from tests.domains.content.api.helpers_ci03_g1_i01 import _synthetic_worksheet_v1_payload
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.education.schema import (
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
    build_preparation_content_schema_registry,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from tests.fakes import (
    AllowClassroomAssessmentAuthorization,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    IDEMPOTENCY_RETENTION,
    StubSecurityContextResolver,
)
from tests.platform.security.authorization.helpers import revoke_membership, seed_principal

pytestmark = pytest.mark.ci03_g1_i01


@pytest.fixture(scope="module")
def _rsa():
    return rsa_material()


class TestGetContentVersionServiceUnit:
    def test_worksheet_gate_unavailable_classification(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        service = GetContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            principal_classification=None,
        )
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        stub = gci_stub_client(runtime_engine, tenant_id, owner_id)
        created = stub.post(
            "/api/v1/contents",
            json={
                "content_type": "worksheet",
                "title": "u",
                "description": "",
                "locale": "en-IN",
            },
            headers={"X-AIEOS-Tenant-ID": str(tenant_id), "Idempotency-Key": str(uuid.uuid7())},
        )
        assert created.status_code == 201
        cid = ContentId(created.json()["content_id"])
        etag = created.headers["ETag"]
        appended = stub.post(
            f"/api/v1/contents/{cid.value}/versions",
            json={
                "schema_id": WORKSHEET_SCHEMA_ID,
                "schema_version": WORKSHEET_SCHEMA_VERSION,
                "payload": _synthetic_worksheet_v1_payload(),
            },
            headers={
                "X-AIEOS-Tenant-ID": str(tenant_id),
                "If-Match": etag,
                "Idempotency-Key": str(uuid.uuid7()),
            },
        )
        assert appended.status_code == 201
        vid = ContentVersionId(appended.json()["version_id"])
        with pytest.raises(AuthorizationUnavailableError):
            service.get(tenant_id, owner_id, cid, vid)

    def test_mismatched_version_identity_denied(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        classification = CurrentPrincipalClassificationAuthority(runtime_engine)
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        stub = gci_stub_client(runtime_engine, tenant_id, owner_id)
        created = stub.post(
            "/api/v1/contents",
            json={
                "content_type": "worksheet",
                "title": "u",
                "description": "",
                "locale": "en-IN",
            },
            headers={"X-AIEOS-Tenant-ID": str(tenant_id), "Idempotency-Key": str(uuid.uuid7())},
        )
        assert created.status_code == 201
        cid = ContentId(created.json()["content_id"])
        etag = created.headers["ETag"]
        appended = stub.post(
            f"/api/v1/contents/{cid.value}/versions",
            json={
                "schema_id": WORKSHEET_SCHEMA_ID,
                "schema_version": WORKSHEET_SCHEMA_VERSION,
                "payload": _synthetic_worksheet_v1_payload(),
            },
            headers={
                "X-AIEOS-Tenant-ID": str(tenant_id),
                "If-Match": etag,
                "Idempotency-Key": str(uuid.uuid7()),
            },
        )
        assert appended.status_code == 201
        service = GetContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            principal_classification=classification,
        )
        with pytest.raises(ContentVersionNotFound):
            service.get(
                tenant_id,
                owner_id,
                cid,
                ContentVersionId(uuid.uuid7()),
            )


class TestWorksheetPrivateReadRealAuth:
    def test_owner_current_historical_v1_hint_v2_no_store(
        self, runtime_engine, bootstrap_engine, postgres18, _rsa
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        app, _ = build_real_auth_app(
            runtime_engine,
            tenant_id=tenant_id,
            owner_principal_id=owner_id,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        client = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, v1_id, v2_id = owner_worksheet_fixtures(
            client, hint_bearing_v2=True
        )
        assert v2_id
        for vid in (v1_id, v2_id):
            response = GET_VERSION(client, content_id, vid)
            assert response.status_code == 200
            assert response.headers.get("Cache-Control") == "private, no-store"
            names = private_field_names(response.json().get("payload"))
            assert "answer" in names and "explanation" in names
            if vid == v2_id:
                assert "learner_hint" in names

    @pytest.mark.parametrize("with_decide", [False, True])
    def test_same_tenant_non_owner_non_enumerating(
        self,
        runtime_engine,
        bootstrap_engine,
        _rsa,
        with_decide: bool,
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        other_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        caps = (CONTENT_REVIEW_DECIDE,) if with_decide else ()
        seed_human_member(bootstrap_engine, tenant_id=tenant_id, principal_id=other_id, capabilities=caps)
        app, _ = build_real_auth_app(
            runtime_engine,
            tenant_id=tenant_id,
            owner_principal_id=owner_id,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, _, v2_id = owner_worksheet_fixtures(owner, hint_bearing_v2=True)
        other = client_for(app, private_key, tenant_id=tenant_id, principal_id=other_id)
        denied = GET_VERSION(other, content_id, v2_id)
        assert denied.status_code == 404
        assert problem_code(denied) == "content_version_not_found"
        assert not private_field_names(denied.json().get("payload"))

    def test_publisher_only_non_owner_denied(
        self, runtime_engine, bootstrap_engine, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        publisher_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        seed_human_member(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=publisher_id,
            capabilities=(CONTENT_PUBLISH,),
        )
        app, _ = build_real_auth_app(
            runtime_engine, tenant_id=tenant_id, owner_principal_id=owner_id,
            public_jwk=public_jwk, private_key=private_key,
        )
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, _, v2_id = owner_worksheet_fixtures(owner, hint_bearing_v2=True)
        publisher = client_for(app, private_key, tenant_id=tenant_id, principal_id=publisher_id)
        response = GET_VERSION(publisher, content_id, v2_id)
        assert response.status_code == 404
        assert problem_code(response) == "content_version_not_found"

    def test_workload_owner_denied(self, runtime_engine, bootstrap_engine, _rsa) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_human_member(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=owner_id,
            principal_kind=PrincipalKind.WORKLOAD,
            capabilities=(),
        )
        app, _ = build_real_auth_app(
            runtime_engine, tenant_id=tenant_id, owner_principal_id=owner_id,
            public_jwk=public_jwk, private_key=private_key,
        )
        client = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, v1_id, _ = owner_worksheet_fixtures(client)
        response = GET_VERSION(client, content_id, v1_id)
        assert response.status_code == 404
        assert problem_code(response) == "content_version_not_found"

    def test_inactive_human_owner_denied(
        self, runtime_engine, bootstrap_engine, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        app, _ = build_real_auth_app(
            runtime_engine, tenant_id=tenant_id, owner_principal_id=owner_id,
            public_jwk=public_jwk, private_key=private_key,
        )
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, v1_id, _ = owner_worksheet_fixtures(owner)
        seed_principal(
            bootstrap_engine,
            owner_id,
            principal_kind=PrincipalKind.HUMAN,
            status=PrincipalStatus.SUSPENDED,
        )
        response = GET_VERSION(owner, content_id, v1_id)
        assert response.status_code == 404
        assert problem_code(response) == "content_version_not_found"

    def test_revoked_membership_blocked_at_tenant_boundary(
        self, runtime_engine, bootstrap_engine, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        revoke_membership(bootstrap_engine, tenant_id=tenant_id, principal_id=owner_id)
        app, _ = build_real_auth_app(
            runtime_engine, tenant_id=tenant_id, owner_principal_id=owner_id,
            public_jwk=public_jwk, private_key=private_key,
        )
        client = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        token = client._ci03_token  # type: ignore[attr-defined]
        response = client.get(
            f"/api/v1/contents/{uuid.uuid7()}/versions/{uuid.uuid7()}",
            headers=auth_headers(tenant_id, token),
        )
        assert response.status_code == 403
        assert problem_code(response) == "forbidden"

    def test_anonymous_401(self, runtime_engine, bootstrap_engine, _rsa) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        app, _ = build_real_auth_app(
            runtime_engine, tenant_id=tenant_id, owner_principal_id=owner_id,
            public_jwk=public_jwk, private_key=private_key,
        )
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, v1_id, _ = owner_worksheet_fixtures(owner)
        anon = TestClient(app, raise_server_exceptions=False)
        response = anon.get(
            f"/api/v1/contents/{content_id}/versions/{v1_id}",
            headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
        )
        assert response.status_code == 401
        assert problem_code(response) == "unauthenticated"

    def test_forged_tenant_header_denied(
        self, runtime_engine, bootstrap_engine, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        owner_a = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_a, owner_a)
        app, _ = build_real_auth_app(
            runtime_engine, tenant_id=tenant_a, owner_principal_id=owner_a,
            public_jwk=public_jwk, private_key=private_key,
        )
        owner = client_for(app, private_key, tenant_id=tenant_a, principal_id=owner_a)
        content_id, v1_id, _ = owner_worksheet_fixtures(owner)
        token = mint_token(private_key, principal_id=owner_a)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"/api/v1/contents/{content_id}/versions/{v1_id}",
            headers=auth_headers(tenant_b, token),
        )
        assert response.status_code == 403
        assert problem_code(response) == "forbidden"

    def test_cross_tenant_resource_hidden(
        self, runtime_engine, bootstrap_engine, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        owner_a = uuid.uuid7()
        owner_b = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_a, owner_a)
        seed_owner_bundle(bootstrap_engine, tenant_b, owner_b)
        app_a, _ = build_real_auth_app(
            runtime_engine, tenant_id=tenant_a, owner_principal_id=owner_a,
            public_jwk=public_jwk, private_key=private_key,
        )
        owner_a_client = client_for(
            app_a, private_key, tenant_id=tenant_a, principal_id=owner_a
        )
        content_id, v1_id, _ = owner_worksheet_fixtures(owner_a_client)
        app_b, _ = build_real_auth_app(
            runtime_engine, tenant_id=tenant_b, owner_principal_id=owner_b,
            public_jwk=public_jwk, private_key=private_key,
        )
        intruder = client_for(app_b, private_key, tenant_id=tenant_b, principal_id=owner_b)
        response = GET_VERSION(intruder, content_id, v1_id)
        assert response.status_code == 404
        assert problem_code(response) == "content_version_not_found"

    def test_wrong_version_and_missing_resource_match_denial(
        self, runtime_engine, bootstrap_engine, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        app, _ = build_real_auth_app(
            runtime_engine, tenant_id=tenant_id, owner_principal_id=owner_id,
            public_jwk=public_jwk, private_key=private_key,
        )
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, _, _ = owner_worksheet_fixtures(owner)
        missing = GET_VERSION(owner, content_id, str(uuid.uuid7()))
        wrong_parent = GET_VERSION(owner, str(uuid.uuid7()), str(uuid.uuid7()))
        for response in (missing, wrong_parent):
            assert response.status_code == 404
            assert problem_code(response) == "content_version_not_found"

    def test_nonworksheet_generic_regression(
        self, runtime_engine, bootstrap_engine, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        other_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        seed_human_member(bootstrap_engine, tenant_id=tenant_id, principal_id=other_id)
        app, _ = build_real_auth_app(
            runtime_engine,
            tenant_id=tenant_id,
            owner_principal_id=owner_id,
            public_jwk=public_jwk,
            private_key=private_key,
            include_worksheet=False,
        )
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        other = client_for(app, private_key, tenant_id=tenant_id, principal_id=other_id)
        hdr = owner._ci03_auth_headers  # type: ignore[attr-defined]
        created = owner.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=hdr({}),
        )
        assert created.status_code == 201
        content_id = created.json()["content_id"]
        appended = owner.post(
            f"/api/v1/contents/{content_id}/versions",
            json=APPEND_BODY,
            headers=hdr({"If-Match": created.headers["ETag"]}),
        )
        assert appended.status_code == 201
        version_id = appended.json()["version_id"]
        cross = GET_VERSION(other, content_id, version_id)
        assert cross.status_code == 200
        assert cross.headers.get("Cache-Control") is None

    def test_owner_library_version_compat(
        self, runtime_engine, bootstrap_engine, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        app, _ = build_real_auth_app(
            runtime_engine, tenant_id=tenant_id, owner_principal_id=owner_id,
            public_jwk=public_jwk, private_key=private_key,
        )
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, v1_id, _ = owner_worksheet_fixtures(owner)
        token = owner._ci03_token  # type: ignore[attr-defined]
        library = owner.get(
            f"/api/v1/teacher-os/library/{content_id}/versions/{v1_id}",
            headers=auth_headers(tenant_id, token),
        )
        assert library.status_code == 200
        assert private_field_names(library.json().get("payload"))


@pytest.mark.ci03_g1_i01_fault
class TestWorksheetPrivateReadFaultInjection:
    """Isolated fault-injection: not counted as normal real-authority matrix."""

    def test_classification_unavailable_503(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, principal_id)
        app = create_app(
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
            assessment_authorization=AllowClassroomAssessmentAuthorization(),
            request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
            security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
            content_types=StaticContentTypeCatalog({"worksheet"}),
            cursor_signing_key=b"ci03-fault",
            schema_registry=build_preparation_content_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
            review_authorization=AllowReviewAuthorization(),
            review_comment_policy=AllowReviewCommentPolicy(),
            publication_authorization=AllowPublicationAuthorization(),
            publication_governance=AllowPublicationGovernance(),
            asset_reference_validation=AllowAssetReferenceValidation(),
            asset_current_governance=AllowAssetCurrentGovernance(),
            principal_classification_authority=_UnavailablePrincipalClassificationAuthority(),
        )
        client = TestClient(app, raise_server_exceptions=False)
        stub = gci_stub_client(runtime_engine, tenant_id, principal_id)
        created = stub.post(
            "/api/v1/contents",
            json={
                "content_type": "worksheet",
                "title": "f",
                "description": "",
                "locale": "en-IN",
            },
            headers={"X-AIEOS-Tenant-ID": str(tenant_id), "Idempotency-Key": str(uuid.uuid7())},
        )
        assert created.status_code == 201
        cid = created.json()["content_id"]
        etag = created.headers["ETag"]
        appended = stub.post(
            f"/api/v1/contents/{cid}/versions",
            json={
                "schema_id": WORKSHEET_SCHEMA_ID,
                "schema_version": WORKSHEET_SCHEMA_VERSION,
                "payload": _synthetic_worksheet_v1_payload(),
            },
            headers={
                "X-AIEOS-Tenant-ID": str(tenant_id),
                "If-Match": etag,
                "Idempotency-Key": str(uuid.uuid7()),
            },
        )
        assert appended.status_code == 201
        vid = appended.json()["version_id"]
        response = client.get(
            f"/api/v1/contents/{cid}/versions/{vid}",
            headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
        )
        assert response.status_code == 503
        assert problem_code(response) == "authorization_unavailable"