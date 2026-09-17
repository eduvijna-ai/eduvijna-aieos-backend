"""AIEOS360-S03-I02 — Parent Intelligence HTTP GET."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.learning.application.errors import (
    SchoolContextContractError,
    SchoolContextUnavailable,
)
from aieos.domains.learning.application.learner_membership import (
    CurrentLearnerClassMembership,
)
from aieos.domains.parent_intelligence.application.errors import (
    ParentIntelligenceReadUnavailable,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    UnconfiguredSchoolContextParentLearnerAccessReader,
)
from aieos.domains.parent_intelligence.application.models import (
    MAX_AUTHORIZED_LEARNER_COUNT,
)
from aieos.domains.parent_intelligence.application.ports import (
    AIEOS_PARENT_INTELLIGENCE_CAPABILITIES,
)
from aieos.platform.security.authorization import (
    AIEOS_CONTENT_CAPABILITIES,
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
    KernelParentIntelligenceAuthorization,
    SecurityAuthorityLearnerPrincipalIntegrity,
)
from aieos.platform.security.authorization.decisions import PrincipalKind, PrincipalStatus
from tests.domains.parent_intelligence.helpers_s03_i02 import (
    CLASS_REF_HOME,
    FORBIDDEN_BODY_TOKENS,
    HOME_PATH,
    InvalidParentAccessReader,
    MutableParentAccessReader,
    RecordingFactsReader,
    RecordingIntegrity,
    RecordingMembershipReader,
    UnavailableParentAccessReader,
    assert_concealed_404,
    assert_exact_response_keys,
    build_client,
    child_path,
    headers,
    seed_human_adult_with_capability,
    seed_human_learner,
    source_row_counts,
)
from tests.platform.security.authorization.helpers import seed_active_authority, seed_principal

pytestmark = pytest.mark.aieos360_s03_i02


def _kernel_auth(engine: Engine) -> KernelParentIntelligenceAuthorization:
    return KernelParentIntelligenceAuthorization(
        AuthorizationKernel(
            engine,
            known_capabilities=AIEOS_CONTENT_CAPABILITIES
            | AIEOS_PARENT_INTELLIGENCE_CAPABILITIES,
        )
    )


def _client(
    bootstrap_engine: Engine,
    runtime_engine: Engine,
    *,
    tenant_id,
    principal_id,
    learner_ids: tuple = (),
    access_reader=None,
    authorization=None,
    facts_reader=None,
    membership_reader=None,
    integrity=None,
    unauthenticated: bool = False,
):
    return build_client(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        access_reader=(
            access_reader
            if access_reader is not None
            else MutableParentAccessReader(learner_ids)
        ),
        parent_intelligence_authorization=authorization,
        facts_reader=facts_reader,
        membership_reader=membership_reader,
        integrity=integrity,
        unauthenticated=unauthenticated,
        principal_classification_authority=CurrentPrincipalClassificationAuthority(
            bootstrap_engine
        ),
    )


class TestHomeHttp:
    def test_authorized_adult_one_child_no_assignments(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=(learner_id,),
            authorization=_kernel_auth(bootstrap_engine),
            membership_reader=RecordingMembershipReader(),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 200
        body = response.json()
        assert_exact_response_keys(body)
        assert body["projection_mode"] == "DERIVED_ON_REQUEST"
        assert body["time_window"]["mode"] == "CURRENT_FACTS_AS_OF_REQUEST"
        assert body["time_window"]["start"] is None
        assert body["time_window"]["end"] == body["generated_at"]
        assert [child["learner_principal_id"] for child in body["children"]] == [
            str(learner_id)
        ]
        assert body["children"][0]["assignments"] == []
        dumped = response.text.lower()
        for token in FORBIDDEN_BODY_TOKENS:
            assert token.lower() not in dumped

    def test_zero_authorized_children_skips_facts(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        facts = RecordingFactsReader()
        membership = RecordingMembershipReader()
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=(),
            authorization=_kernel_auth(bootstrap_engine),
            facts_reader=facts,
            membership_reader=membership,
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 200
        assert response.json()["children"] == []
        assert facts.calls == []
        assert membership.calls == []

    def test_multiple_children_are_deterministic(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        first = uuid.uuid7()
        second = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        for learner_id in (first, second):
            seed_human_learner(
                bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
            )
        low, high = sorted((first, second), key=lambda item: item.bytes)
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=(high, low),
            authorization=_kernel_auth(bootstrap_engine),
            membership_reader=RecordingMembershipReader(),
        )
        body = client.get(HOME_PATH, headers=headers(tenant_id)).json()
        assert [child["learner_principal_id"] for child in body["children"]] == [
            str(low),
            str(high),
        ]

    def test_workload_adult_is_403(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            principal_kind=PrincipalKind.WORKLOAD,
            capabilities=("parent.intelligence.read",),
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            authorization=_kernel_auth(bootstrap_engine),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 403

    def test_inactive_human_adult_is_403(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        seed_principal(
            bootstrap_engine,
            adult_id,
            principal_kind=PrincipalKind.HUMAN,
            status="DISABLED",
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            authorization=_kernel_auth(bootstrap_engine),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code in {403, 503}

    def test_missing_capability_is_403(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(),
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            authorization=_kernel_auth(bootstrap_engine),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 403
        assert response.json()["code"] == "parent_intelligence_capability_forbidden"

    def test_access_provider_unavailable_is_503(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            access_reader=UnavailableParentAccessReader(),
            authorization=_kernel_auth(bootstrap_engine),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 503
        assert response.json()["code"] == "parent_learner_access_unavailable"

    def test_access_contract_invalid_is_503(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            access_reader=InvalidParentAccessReader(),
            authorization=_kernel_auth(bootstrap_engine),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 503
        assert response.json()["code"] == "parent_learner_access_unavailable"

    def test_unauthenticated_is_401(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=uuid.uuid7(),
            unauthenticated=True,
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 401

    def test_production_unconfigured_access_is_503(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            access_reader=UnconfiguredSchoolContextParentLearnerAccessReader(),
            authorization=_kernel_auth(bootstrap_engine),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 503


class TestSelectorHttp:
    def test_selected_authorized_child(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        first = uuid.uuid7()
        second = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        for learner_id in (first, second):
            seed_human_learner(
                bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
            )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=(first, second),
            authorization=_kernel_auth(bootstrap_engine),
            membership_reader=RecordingMembershipReader(),
        )
        response = client.get(child_path(second), headers=headers(tenant_id))
        assert response.status_code == 200
        body = response.json()
        assert_exact_response_keys(body)
        assert [child["learner_principal_id"] for child in body["children"]] == [
            str(second)
        ]

    def test_concealment_404_is_identical_across_unauthorized_selectors(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        other_tenant = uuid.uuid7()
        adult_id = uuid.uuid7()
        authorized = uuid.uuid7()
        same_class = uuid.uuid7()
        cross_class = uuid.uuid7()
        sibling = uuid.uuid7()
        other_school = uuid.uuid7()
        other_tenant_learner = uuid.uuid7()
        formerly = uuid.uuid7()
        invented = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=authorized
        )
        access = MutableParentAccessReader((authorized,))
        facts = RecordingFactsReader()
        membership = RecordingMembershipReader()
        integrity = SecurityAuthorityLearnerPrincipalIntegrity(bootstrap_engine)
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            access_reader=access,
            authorization=_kernel_auth(bootstrap_engine),
            facts_reader=facts,
            membership_reader=membership,
            integrity=integrity,
        )
        selectors = (
            same_class,
            cross_class,
            sibling,
            other_school,
            other_tenant_learner,
            formerly,
            invented,
        )
        for guessed in selectors:
            response = client.get(child_path(guessed), headers=headers(tenant_id))
            assert_concealed_404(response)
        assert facts.calls == []
        assert membership.calls == []
        other_tenant_header = client.get(
            child_path(authorized), headers=headers(other_tenant)
        )
        assert other_tenant_header.status_code in {400, 403, 404, 503}

    def test_selector_miss_does_not_probe_guessed_integrity(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        authorized = uuid.uuid7()
        guessed = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=authorized
        )
        integrity = RecordingIntegrity()
        facts = RecordingFactsReader()
        membership = RecordingMembershipReader()
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=(authorized,),
            authorization=_kernel_auth(bootstrap_engine),
            facts_reader=facts,
            membership_reader=membership,
            integrity=integrity,
        )
        response = client.get(child_path(guessed), headers=headers(tenant_id))
        assert_concealed_404(response)
        assert facts.calls == []
        assert membership.calls == []
        assert integrity.calls == [authorized]
        assert guessed not in integrity.calls

    def test_suspended_and_disabled_authorized_children_succeed(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        suspended = uuid.uuid7()
        disabled = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=suspended,
            status=PrincipalStatus.SUSPENDED,
        )
        seed_human_learner(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=disabled,
            status=PrincipalStatus.DISABLED,
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=(suspended, disabled),
            authorization=_kernel_auth(bootstrap_engine),
            membership_reader=RecordingMembershipReader(),
        )
        home = client.get(HOME_PATH, headers=headers(tenant_id))
        assert home.status_code == 200
        for learner_id in (suspended, disabled):
            response = client.get(child_path(learner_id), headers=headers(tenant_id))
            assert response.status_code == 200
            assert response.json()["children"][0]["learner_principal_id"] == str(
                learner_id
            )


class TestMembershipAndProtectionHttp:
    def test_membership_unavailable_is_503(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=(learner_id,),
            authorization=_kernel_auth(bootstrap_engine),
            membership_reader=RecordingMembershipReader(
                exc=SchoolContextUnavailable("School Context is temporarily unavailable")
            ),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 503
        assert response.json()["code"] == "parent_intelligence_unavailable"

    def test_membership_contract_invalid_is_503(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=(learner_id,),
            authorization=_kernel_auth(bootstrap_engine),
            membership_reader=RecordingMembershipReader(
                exc=SchoolContextContractError(
                    "School Context provider returned an invalid response"
                )
            ),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 503

    def test_successful_empty_membership_is_200_with_empty_assignments(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=(learner_id,),
            authorization=_kernel_auth(bootstrap_engine),
            membership_reader=RecordingMembershipReader({learner_id: ()}),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 200
        assert response.json()["children"][0]["assignments"] == []

    def test_access_revoked_between_requests(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        access = MutableParentAccessReader((learner_id,))
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            access_reader=access,
            authorization=_kernel_auth(bootstrap_engine),
            membership_reader=RecordingMembershipReader(),
        )
        first = client.get(HOME_PATH, headers=headers(tenant_id))
        assert first.status_code == 200
        assert first.json()["children"]
        access.set_current(())
        second = client.get(HOME_PATH, headers=headers(tenant_id))
        assert second.status_code == 200
        assert second.json()["children"] == []
        concealed = client.get(child_path(learner_id), headers=headers(tenant_id))
        assert_concealed_404(concealed)

    def test_protection_limit_exceeded_is_503_without_partial_body(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        learner_ids = tuple(uuid.uuid7() for _ in range(MAX_AUTHORIZED_LEARNER_COUNT + 1))
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=learner_ids,
            authorization=_kernel_auth(bootstrap_engine),
            integrity=RecordingIntegrity(),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 503
        body = response.json()
        assert body["code"] == "parent_intelligence_unavailable"
        assert "children" not in body

    def test_facts_unavailable_is_503(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=(learner_id,),
            authorization=_kernel_auth(bootstrap_engine),
            facts_reader=RecordingFactsReader(
                exc=ParentIntelligenceReadUnavailable(
                    "Parent Intelligence source is temporarily unavailable"
                )
            ),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 503
        assert response.json()["code"] == "parent_intelligence_unavailable"

    def test_get_is_side_effect_free(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        before = source_row_counts(bootstrap_engine, tenant_id)
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            learner_ids=(learner_id,),
            authorization=_kernel_auth(bootstrap_engine),
            membership_reader=RecordingMembershipReader(
                {
                    learner_id: (
                        CurrentLearnerClassMembership(class_ref=CLASS_REF_HOME),
                    )
                }
            ),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 200
        after = source_row_counts(bootstrap_engine, tenant_id)
        assert after == before

    def test_adult_identity_is_not_taken_from_query_or_custom_header(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        other_adult = uuid.uuid7()
        learner_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        access = MutableParentAccessReader((learner_id,))
        client = _client(
            bootstrap_engine,
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            access_reader=access,
            authorization=_kernel_auth(bootstrap_engine),
            membership_reader=RecordingMembershipReader(),
        )
        response = client.get(
            HOME_PATH,
            headers={
                **headers(tenant_id),
                "X-Parent-Principal-Id": str(other_adult),
                "X-Principal-Role": "parent",
            },
            params={"adult_principal_id": str(other_adult)},
        )
        assert response.status_code == 200
        assert access.calls == [(tenant_id, adult_id)]
