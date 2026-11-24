"""Shared real-auth PG18 matrix for CI03-G1 baseline (pre-fix) and candidate (post-fix)."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from aieos.domains.content.api.v1.routes import content_version_get
from tests.domains.content.api.helpers_ci03_g1_i01 import (
    GET_VERSION,
    assert_no_private_fields_recursive,
    auth_headers,
    baseline_mode,
    build_real_auth_app,
    client_for,
    emit_receipt,
    mint_token,
    owner_worksheet_fixtures,
    private_field_names,
    private_fields_recursive,
    problem_code,
    rsa_material,
    seed_owner_bundle,
    seed_principal_fixture,
    seed_worksheet_http_ids,
    set_content_owner,
)
from tests.domains.content.api.test_gci_i05_http import APPEND_BODY, CREATE_BODY
from tests.platform.security.authorization.helpers import (
    revoke_membership,
    seed_principal,
)
from aieos.platform.security.authorization.decisions import PrincipalKind, PrincipalStatus

pytestmark = pytest.mark.ci03_g1_pg18_matrix


@pytest.fixture(scope="module")
def _rsa():
    return rsa_material()


class TestCi03G1Pg18SharedMatrix:
    def test_owner_v1_v2_historical_positive(
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
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, v1_id, v2_id = owner_worksheet_fixtures(
            owner, hint_bearing_v2=True
        )
        for schema, vid in (("worksheet@1", v1_id), ("worksheet@2", v2_id)):
            response = GET_VERSION(owner, content_id, vid)
            assert response.status_code == 200
            assert response.headers.get("Cache-Control") == "private, no-store"
            names = private_field_names(response.json().get("payload"))
            assert "answer" in names and "explanation" in names
            if schema == "worksheet@2":
                assert "learner_hint" in names
            emit_receipt(
                f"owner_positive_{schema}",
                pg_major="18",
                runtime_role=postgres18["runtime_user"],
                schema=schema,
                status=200,
                problem_code=problem_code(response),
                private_field_names=sorted(names),
                no_store=response.headers.get("Cache-Control"),
            )

    @pytest.mark.parametrize(
        "fixture_label",
        [
            "unrelated_human",
            "reviewer_decide",
            "publisher_only",
            "principal",
            "parent",
            "student",
        ],
    )
    def test_same_tenant_non_owner_v1_v2(
        self,
        runtime_engine,
        bootstrap_engine,
        postgres18,
        _rsa,
        fixture_label: str,
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        actor_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        if fixture_label == "unrelated_human":
            seed_principal_fixture(
                bootstrap_engine,
                tenant_id=tenant_id,
                principal_id=actor_id,
                fixture_label="student",
            )
        else:
            seed_principal_fixture(
                bootstrap_engine,
                tenant_id=tenant_id,
                principal_id=actor_id,
                fixture_label=fixture_label,
            )
        app, _ = build_real_auth_app(
            runtime_engine,
            tenant_id=tenant_id,
            owner_principal_id=owner_id,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, v1_id, v2_id = owner_worksheet_fixtures(
            owner, hint_bearing_v2=True
        )
        actor = client_for(app, private_key, tenant_id=tenant_id, principal_id=actor_id)
        for schema, vid in (("worksheet@1", v1_id), ("worksheet@2", v2_id)):
            response = GET_VERSION(actor, content_id, vid)
            if baseline_mode():
                assert response.status_code == 200
                names = private_fields_recursive(response.json())
                assert "answer" in names
                emit_receipt(
                    f"baseline_non_owner_{fixture_label}_{schema}",
                    fixture=fixture_label,
                    schema=schema,
                    status=200,
                    problem_code=problem_code(response),
                    private_field_names=sorted(names),
                )
            else:
                assert response.status_code == 404
                assert problem_code(response) == "content_version_not_found"
                assert_no_private_fields_recursive(response.json())
                emit_receipt(
                    f"candidate_non_owner_{fixture_label}_{schema}",
                    fixture=fixture_label,
                    schema=schema,
                    status=404,
                    problem_code=problem_code(response),
                    private_field_names=[],
                )

    def test_workload_owner_classification_denied(
        self, runtime_engine, bootstrap_engine, postgres18, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        human_owner = uuid.uuid7()
        workload_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, human_owner)
        from tests.domains.content.api.helpers_ci03_g1_i01 import seed_human_member

        seed_human_member(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=workload_id,
            principal_kind=PrincipalKind.WORKLOAD,
        )
        app, _ = build_real_auth_app(
            runtime_engine,
            tenant_id=tenant_id,
            owner_principal_id=human_owner,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=human_owner)
        content_id, v1_id, _ = owner_worksheet_fixtures(owner)
        set_content_owner(bootstrap_engine, content_id, workload_id)
        workload = client_for(
            app, private_key, tenant_id=tenant_id, principal_id=workload_id
        )
        response = GET_VERSION(workload, content_id, v1_id)
        if baseline_mode():
            assert response.status_code == 200
            names = private_fields_recursive(response.json())
            emit_receipt(
                "baseline_workload_owner",
                status=200,
                private_field_names=sorted(names),
            )
        else:
            assert response.status_code == 404
            assert problem_code(response) == "content_version_not_found"
            assert_no_private_fields_recursive(response.json())
            emit_receipt(
                "candidate_workload_owner",
                status=404,
                problem_code=problem_code(response),
            )

    def test_suspended_owner_auth_boundary(
        self, runtime_engine, bootstrap_engine, postgres18, _rsa
    ) -> None:
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
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, v1_id, _ = owner_worksheet_fixtures(owner)
        seed_principal(
            bootstrap_engine,
            owner_id,
            principal_kind=PrincipalKind.HUMAN,
            status=PrincipalStatus.SUSPENDED,
        )
        response = GET_VERSION(owner, content_id, v1_id)
        assert response.status_code == 403
        assert problem_code(response) == "forbidden"
        assert_no_private_fields_recursive(response.json())
        emit_receipt(
            "suspended_owner",
            status=403,
            problem_code=problem_code(response),
        )

    def test_revoked_membership_same_resource(
        self, runtime_engine, bootstrap_engine, postgres18, _rsa
    ) -> None:
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
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, v1_id, _ = owner_worksheet_fixtures(owner)
        revoke_membership(bootstrap_engine, tenant_id=tenant_id, principal_id=owner_id)
        response = GET_VERSION(owner, content_id, v1_id)
        assert response.status_code == 403
        assert problem_code(response) == "forbidden"
        emit_receipt(
            "revoked_membership_same_resource",
            status=403,
            problem_code=problem_code(response),
        )

    def test_wrong_content_id_correlation(
        self, runtime_engine, bootstrap_engine, postgres18, _rsa
    ) -> None:
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
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, v1_id, _ = owner_worksheet_fixtures(owner)
        _, _, _, content_b = seed_worksheet_http_ids(
            runtime_engine,
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        response = GET_VERSION(owner, content_b, v1_id)
        assert response.status_code == 404
        assert problem_code(response) == "content_version_not_found"
        emit_receipt(
            "wrong_content_id_correlation",
            status=404,
            problem_code=problem_code(response),
        )

    def test_missing_version_same_envelope(
        self, runtime_engine, bootstrap_engine, postgres18, _rsa
    ) -> None:
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
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, _, _ = owner_worksheet_fixtures(owner)
        missing = GET_VERSION(owner, content_id, str(uuid.uuid7()))
        assert missing.status_code == 404
        assert problem_code(missing) == "content_version_not_found"
        emit_receipt(
            "missing_version",
            status=404,
            problem_code=problem_code(missing),
        )

    def test_trust_boundaries(
        self, runtime_engine, bootstrap_engine, postgres18, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_a = uuid.uuid7()
        owner_a = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_a, owner_a)
        app, _ = build_real_auth_app(
            runtime_engine,
            tenant_id=tenant_a,
            owner_principal_id=owner_a,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        owner = client_for(app, private_key, tenant_id=tenant_a, principal_id=owner_a)
        content_id, v1_id, _ = owner_worksheet_fixtures(owner)
        anon = TestClient(app, raise_server_exceptions=False)
        unauth = anon.get(
            f"/api/v1/contents/{content_id}/versions/{v1_id}",
            headers={"X-AIEOS-Tenant-ID": str(tenant_a)},
        )
        assert unauth.status_code == 401
        assert problem_code(unauth) == "unauthenticated"
        forged = anon.get(
            f"/api/v1/contents/{content_id}/versions/{v1_id}",
            headers=auth_headers(uuid.uuid7(), mint_token(private_key, principal_id=owner_a)),
        )
        assert forged.status_code == 403
        assert problem_code(forged) == "forbidden"
        emit_receipt("anonymous", status=401, problem_code=problem_code(unauth))
        emit_receipt("forged_tenant", status=403, problem_code=problem_code(forged))

    def test_owner_library_and_review_queue(
        self, runtime_engine, bootstrap_engine, postgres18, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        seed_principal_fixture(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            fixture_label="reviewer_decide",
        )
        app, _ = build_real_auth_app(
            runtime_engine,
            tenant_id=tenant_id,
            owner_principal_id=owner_id,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        owner = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
        content_id, v1_id, _ = owner_worksheet_fixtures(owner)
        token = owner._ci03_token  # type: ignore[attr-defined]
        library = owner.get(
            f"/api/v1/teacher-os/library/{content_id}/versions/{v1_id}",
            headers=auth_headers(tenant_id, token),
        )
        assert library.status_code == 200
        reviewer = client_for(
            app, private_key, tenant_id=tenant_id, principal_id=reviewer_id
        )
        rev_tok = reviewer._ci03_token  # type: ignore[attr-defined]
        queue = reviewer.get(
            f"/api/v1/teacher-os/review-queue/{content_id}/versions/{v1_id}",
            headers=auth_headers(tenant_id, rev_tok),
        )
        assert queue.status_code == 404
        emit_receipt("owner_library_positive", status=library.status_code)
        emit_receipt(
            "review_queue_non_owner",
            status=queue.status_code,
            problem_code=problem_code(queue),
        )

    def test_nonworksheet_regression(
        self, runtime_engine, bootstrap_engine, postgres18, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        other_id = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
        seed_principal_fixture(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=other_id,
            fixture_label="student",
        )
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
        emit_receipt("nonworksheet_cross_principal", status=200)

    def test_cross_tenant_hidden(
        self, runtime_engine, bootstrap_engine, postgres18, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        owner_a = uuid.uuid7()
        owner_b = uuid.uuid7()
        seed_owner_bundle(bootstrap_engine, tenant_a, owner_a)
        seed_owner_bundle(bootstrap_engine, tenant_b, owner_b)
        app_a, _ = build_real_auth_app(
            runtime_engine,
            tenant_id=tenant_a,
            owner_principal_id=owner_a,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        owner_a_client = client_for(
            app_a, private_key, tenant_id=tenant_a, principal_id=owner_a
        )
        content_id, v1_id, _ = owner_worksheet_fixtures(owner_a_client)
        app_b, _ = build_real_auth_app(
            runtime_engine,
            tenant_id=tenant_b,
            owner_principal_id=owner_b,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        intruder = client_for(
            app_b, private_key, tenant_id=tenant_b, principal_id=owner_b
        )
        response = GET_VERSION(intruder, content_id, v1_id)
        assert response.status_code == 404
        assert problem_code(response) == "content_version_not_found"
        assert_no_private_fields_recursive(response.json())
        emit_receipt(
            "cross_tenant",
            status=404,
            problem_code=problem_code(response),
        )

    def test_artifact_view_operation_still_registered(self) -> None:
        assert content_version_get.__name__ == "content_version_get"