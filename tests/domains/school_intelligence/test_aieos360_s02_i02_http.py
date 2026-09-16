"""AIEOS360-S02-I02 — Principal School Intelligence HTTP GET."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.engine import Engine

from aieos.development.principal_school_context import CLASS_REF_6A, CLASS_REF_6B
from aieos.domains.school_intelligence.application.errors import (
    SchoolContextContractError,
    SchoolContextUnavailable,
)
from aieos.domains.school_intelligence.application.models import (
    SchoolIntelligenceFactsSnapshot,
)
from aieos.domains.school_intelligence.application.ports import (
    AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
    UnconfiguredSchoolContextPrincipalScopeReader,
)
from aieos.platform.security.authorization import (
    AIEOS_CONTENT_CAPABILITIES,
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
    KernelSchoolIntelligenceAuthorization,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.domains.school_intelligence.helpers_s02_i02 import (
    PATH,
    UNAUTHORIZED_CLASS_REF,
    DenySchoolIntelligenceAuthorization,
    MutablePrincipalScopeReader,
    build_client,
    default_scope,
    headers,
    insert_assignment,
    revoke_school_intelligence_read,
    seed_content,
    seed_human_with_capability,
    source_row_counts,
)
from tests.platform.security.authorization.helpers import seed_active_authority, seed_principal

pytestmark = pytest.mark.aieos360_s02_i02


def _kernel_auth(engine: Engine) -> KernelSchoolIntelligenceAuthorization:
    return KernelSchoolIntelligenceAuthorization(
        AuthorizationKernel(
            engine,
            known_capabilities=AIEOS_CONTENT_CAPABILITIES
            | AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES,
        )
    )


def _client(
    bootstrap_engine: Engine,
    runtime_engine: Engine,
    *,
    tenant_id,
    principal_id,
    scope=None,
    authorization=None,
    facts_reader=None,
    unauthenticated: bool = False,
):
    return build_client(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        scope_reader=scope if scope is not None else MutablePrincipalScopeReader(default_scope()),
        school_intelligence_authorization=authorization,
        facts_reader=facts_reader,
        unauthenticated=unauthenticated,
        principal_classification_authority=CurrentPrincipalClassificationAuthority(
            bootstrap_engine
        ),
    )


class TestAuthorityHttp:
    def test_active_human_with_capability_and_scope_succeeds(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        seed_human_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            authorization=_kernel_auth(bootstrap_engine),
        )
        response = client.get(PATH, headers=headers(tenant_id))
        assert response.status_code == 200
        body = response.json()
        assert body["projection_mode"] == "DERIVED_ON_REQUEST"
        assert body["time_window"]["mode"] == "CURRENT_FACTS_AS_OF_REQUEST"
        assert body["time_window"]["start"] is None
        assert [item["class_ref"] for item in body["classes"]] == [
            CLASS_REF_6A,
            CLASS_REF_6B,
        ]
        assert "percentage" not in str(body).lower()
        assert "learner_principal_id" not in response.text
        assert "teacher_principal_id" not in response.text
        assert "class_result_level" not in response.text
        assert "class_result_note" not in response.text
        assert "mastery" not in response.text.lower()

    def test_workload_fails_closed(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            principal_kind=PrincipalKind.WORKLOAD,
            capabilities=("school.intelligence.read",),
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            authorization=_kernel_auth(bootstrap_engine),
        )
        response = client.get(PATH, headers=headers(tenant_id))
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    def test_inactive_missing_and_null_kind_fail_closed(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        inactive = uuid.uuid7()
        missing = uuid.uuid7()
        unclassified = uuid.uuid7()
        seed_principal(
            bootstrap_engine,
            inactive,
            principal_kind=PrincipalKind.HUMAN,
            status="DISABLED",
        )
        seed_principal(bootstrap_engine, unclassified, principal_kind=None)
        auth = _kernel_auth(bootstrap_engine)
        for principal_id in (inactive, missing, unclassified):
            client = _client(
                bootstrap_engine,
                runtime_engine,
                tenant_id=tenant_id,
                principal_id=principal_id,
                authorization=auth,
            )
            response = client.get(PATH, headers=headers(tenant_id))
            assert response.status_code in {403, 503}

    def test_capability_absent_is_403(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(),
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            authorization=_kernel_auth(bootstrap_engine),
        )
        response = client.get(PATH, headers=headers(tenant_id))
        assert response.status_code == 403
        assert response.json()["code"] == "school_intelligence_capability_forbidden"

    def test_capability_revoked_denies_subsequent_get(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        seed_human_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        auth = _kernel_auth(bootstrap_engine)
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            authorization=auth,
        )
        assert client.get(PATH, headers=headers(tenant_id)).status_code == 200
        revoke_school_intelligence_read(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        denied = client.get(PATH, headers=headers(tenant_id))
        assert denied.status_code == 403

    def test_school_context_unavailable_is_503(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        seed_human_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            scope=UnconfiguredSchoolContextPrincipalScopeReader(),
        )
        response = client.get(PATH, headers=headers(tenant_id))
        assert response.status_code == 503
        assert response.json()["code"] == "school_context_unavailable"

    def test_malformed_scope_is_503(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        seed_human_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )

        class _Bad:
            def list_current_authorized_classes(self, tenant_id, principal_id):
                raise SchoolContextContractError("invalid")

        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            scope=_Bad(),
        )
        response = client.get(PATH, headers=headers(tenant_id))
        assert response.status_code == 503

    def test_incomplete_facts_snapshot_is_503(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        seed_human_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )

        class _IncompleteFacts:
            def read_authorized_class_facts(self, **kwargs):
                del kwargs
                return SchoolIntelligenceFactsSnapshot(
                    generated_at=datetime.now(UTC),
                    classes=(),
                )

        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            facts_reader=_IncompleteFacts(),
        )
        response = client.get(PATH, headers=headers(tenant_id))
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "school_intelligence_unavailable"

    def test_unauthenticated_is_401(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            unauthenticated=True,
        )
        response = client.get(PATH, headers=headers(tenant_id))
        assert response.status_code == 401


class TestScopeAndSideEffects:
    def test_removed_class_ref_is_excluded_on_next_get(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        seed_human_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6B,
        )
        scope = MutablePrincipalScopeReader(default_scope())
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            scope=scope,
        )
        first = client.get(PATH, headers=headers(tenant_id)).json()
        assert first["summary"]["teaching_assignment_count"] == 2
        scope.items = [
            AuthorizedSchoolClassRef(class_ref=CLASS_REF_6A, display_label="Grade 6A")
        ]
        second = client.get(PATH, headers=headers(tenant_id)).json()
        assert [item["class_ref"] for item in second["classes"]] == [CLASS_REF_6A]
        assert second["summary"]["teaching_assignment_count"] == 1
        assert second["summary"]["in_scope_class_count"] == 1

    def test_client_class_ref_cannot_broaden_scope(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        seed_human_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=UNAUTHORIZED_CLASS_REF,
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
        )
        response = client.get(
            PATH,
            headers={
                **headers(tenant_id),
                "X-Class-Refs": UNAUTHORIZED_CLASS_REF,
                "X-Principal-Role": "principal",
            },
            params={"class_ref": UNAUTHORIZED_CLASS_REF},
        )
        assert response.status_code == 200
        body = response.json()
        assert UNAUTHORIZED_CLASS_REF not in [
            item["class_ref"] for item in body["classes"]
        ]
        assert body["summary"]["teaching_assignment_count"] == 0

    def test_get_is_side_effect_free(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        seed_human_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
        )
        before = source_row_counts(bootstrap_engine, tenant_id)
        assert client.get(PATH, headers=headers(tenant_id)).status_code == 200
        after = source_row_counts(bootstrap_engine, tenant_id)
        assert after == before

    def test_deny_adapter_is_403(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        seed_human_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            authorization=DenySchoolIntelligenceAuthorization(),
        )
        response = client.get(PATH, headers=headers(tenant_id))
        assert response.status_code == 403
