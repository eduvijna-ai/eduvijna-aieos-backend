"""AIEOS360-S04-I02 — coherent School Context development composition proofs."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from aieos.development.coherent_school_context import (
    CLASS_LABEL_5A,
    CLASS_REF_5A,
    CLASS_REF_5B,
    PARENT_OS_HUMAN_ADULT_A_ID,
    PARENT_OS_HUMAN_ADULT_B_ID,
    PRINCIPAL_OS_HUMAN_PRINCIPAL_ID,
    STUDENT_A_PRINCIPAL_ID,
    STUDENT_A_SCHOOL_LEARNER_REF,
    STUDENT_B_PRINCIPAL_ID,
    DevelopmentCoherentSchoolContextProvider,
)
from aieos.domains.learning.application.errors import SchoolContextUnavailable
from aieos.domains.learning.application.learner_membership import (
    CurrentLearnerClassMembership,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    AuthorizedLearnerAccess,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
)
from aieos.domains.teaching.application.school_context import AssignableClassRef
from aieos.platform.runtime.composition import (
    ApiRuntimeDependencies,
    compose_api_application,
)
from aieos.platform.runtime.models import (
    ApiRuntimeConfig,
    DeploymentEnvironment,
    ReleaseIdentity,
)
from tools.dev.compose_local_api import compose_local_api_runtime_dependencies
from tools.dev.local_config import LOCAL_DEV_PRINCIPAL_ID, LOCAL_DEV_TENANT_ID

pytestmark = pytest.mark.aieos360_s04_i02


def _local_config() -> ApiRuntimeConfig:
    return ApiRuntimeConfig(
        environment=DeploymentEnvironment.STAGING,
        release_identity=ReleaseIdentity(
            application_version="0.0.0-local",
            git_sha="a" * 40,
            build_id="s04-i02-local",
            artifact_digest="sha256:" + ("0" * 64),
        ),
        runtime_database_url="postgresql+psycopg://unused:unused@127.0.0.1:5432/unused",
        runtime_database_role="unused",
        content_schema_owner_role="unused",
        security_schema_owner_role="unused",
        migrator_role="unused",
        cursor_signing_key=b"aieos-local-f5-cursor-signing-key",
        idempotency_retention=timedelta(seconds=86400),
        runtime_database_connect_timeout_seconds=5,
        auth_issuer="https://local-dev.aieos.invalid/",
        auth_audience="aieos-api",
        auth_jwks_uri="https://local-dev.aieos.invalid/.well-known/jwks.json",
    )


def _compose_local_dependencies() -> ApiRuntimeDependencies:
    return compose_local_api_runtime_dependencies(
        engine=MagicMock(name="local-engine"),
        config=_local_config(),
    )


class TestSharedProviderIdentityInvariant:
    def test_four_reader_contracts_are_the_same_provider_instance(self) -> None:
        dependencies = _compose_local_dependencies()
        provider = dependencies.school_context_class_reader
        assert isinstance(provider, DevelopmentCoherentSchoolContextProvider)
        assert dependencies.learner_membership_reader is provider
        assert dependencies.school_context_principal_scope_reader is provider
        assert dependencies.school_context_parent_learner_access_reader is provider

    def test_parent_facts_membership_reader_is_the_same_provider(self) -> None:
        dependencies = _compose_local_dependencies()
        provider = dependencies.school_context_class_reader
        facts = dependencies.parent_intelligence_facts_reader
        assert facts is not None
        membership_service = facts._memberships
        assert membership_service._reader is provider

    def test_parent_learner_access_service_uses_the_same_provider(self) -> None:
        dependencies = _compose_local_dependencies()
        provider = dependencies.school_context_class_reader
        service = dependencies.parent_learner_access_service
        assert service is not None
        assert service._reader is provider


class TestLocalF5Coherence:
    def test_local_teacher_and_canonical_roles_share_class_5a(self) -> None:
        dependencies = _compose_local_dependencies()
        provider = dependencies.school_context_class_reader
        assert isinstance(provider, DevelopmentCoherentSchoolContextProvider)

        teacher = provider.list_assignable_classes(
            LOCAL_DEV_TENANT_ID, LOCAL_DEV_PRINCIPAL_ID
        )
        student_a = provider.list_current_memberships(
            LOCAL_DEV_TENANT_ID, STUDENT_A_PRINCIPAL_ID
        )
        principal = provider.list_current_authorized_classes(
            LOCAL_DEV_TENANT_ID, PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
        )
        parent_a = provider.list_current_authorized_learners(
            LOCAL_DEV_TENANT_ID, PARENT_OS_HUMAN_ADULT_A_ID
        )

        assert AssignableClassRef(
            class_ref=CLASS_REF_5A, display_label=CLASS_LABEL_5A
        ) in teacher
        assert student_a == (
            CurrentLearnerClassMembership(
                class_ref=CLASS_REF_5A,
                school_learner_ref=STUDENT_A_SCHOOL_LEARNER_REF,
            ),
        )
        assert AuthorizedSchoolClassRef(
            class_ref=CLASS_REF_5A, display_label=CLASS_LABEL_5A
        ) in principal
        assert parent_a == (
            AuthorizedLearnerAccess(learner_principal_id=STUDENT_A_PRINCIPAL_ID),
        )
        assert provider.list_current_memberships(
            LOCAL_DEV_TENANT_ID, parent_a[0].learner_principal_id
        ) == student_a

    def test_optional_class_5b_student_b_story_remains(self) -> None:
        dependencies = _compose_local_dependencies()
        provider = dependencies.school_context_class_reader
        assert isinstance(provider, DevelopmentCoherentSchoolContextProvider)

        teacher_refs = {
            item.class_ref
            for item in provider.list_assignable_classes(
                LOCAL_DEV_TENANT_ID, LOCAL_DEV_PRINCIPAL_ID
            )
        }
        principal_refs = {
            item.class_ref
            for item in provider.list_current_authorized_classes(
                LOCAL_DEV_TENANT_ID, PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
            )
        }
        local_principal_refs = {
            item.class_ref
            for item in provider.list_current_authorized_classes(
                LOCAL_DEV_TENANT_ID, LOCAL_DEV_PRINCIPAL_ID
            )
        }
        assert CLASS_REF_5B in teacher_refs
        assert CLASS_REF_5B in principal_refs
        assert CLASS_REF_5B in local_principal_refs
        assert provider.list_current_memberships(
            LOCAL_DEV_TENANT_ID, STUDENT_B_PRINCIPAL_ID
        ) == (CurrentLearnerClassMembership(class_ref=CLASS_REF_5B),)
        assert provider.list_current_authorized_learners(
            LOCAL_DEV_TENANT_ID, PARENT_OS_HUMAN_ADULT_B_ID
        ) == (AuthorizedLearnerAccess(learner_principal_id=STUDENT_B_PRINCIPAL_ID),)

    def test_local_principal_is_not_granted_parent_or_learner_identity(self) -> None:
        dependencies = _compose_local_dependencies()
        provider = dependencies.school_context_class_reader
        assert isinstance(provider, DevelopmentCoherentSchoolContextProvider)
        assert (
            provider.list_current_authorized_learners(
                LOCAL_DEV_TENANT_ID, LOCAL_DEV_PRINCIPAL_ID
            )
            == ()
        )
        assert (
            provider.list_current_memberships(
                LOCAL_DEV_TENANT_ID, LOCAL_DEV_PRINCIPAL_ID
            )
            == ()
        )


class TestRevocationInComposition:
    def test_teacher_revocation_is_visible_while_parent_facts_stay(self) -> None:
        dependencies = _compose_local_dependencies()
        provider = dependencies.school_context_class_reader
        assert isinstance(provider, DevelopmentCoherentSchoolContextProvider)

        assert CLASS_REF_5A in {
            item.class_ref
            for item in provider.list_assignable_classes(
                LOCAL_DEV_TENANT_ID, LOCAL_DEV_PRINCIPAL_ID
            )
        }
        parent_before = provider.list_current_authorized_learners(
            LOCAL_DEV_TENANT_ID, PARENT_OS_HUMAN_ADULT_A_ID
        )
        assert parent_before == (
            AuthorizedLearnerAccess(learner_principal_id=STUDENT_A_PRINCIPAL_ID),
        )

        provider.set_teacher_class_authority(LOCAL_DEV_PRINCIPAL_ID, ())
        assert (
            provider.list_assignable_classes(
                LOCAL_DEV_TENANT_ID, LOCAL_DEV_PRINCIPAL_ID
            )
            == ()
        )
        assert (
            dependencies.school_context_class_reader.list_assignable_classes(
                LOCAL_DEV_TENANT_ID, LOCAL_DEV_PRINCIPAL_ID
            )
            == ()
        )
        assert (
            dependencies.school_context_parent_learner_access_reader.list_current_authorized_learners(
                LOCAL_DEV_TENANT_ID, PARENT_OS_HUMAN_ADULT_A_ID
            )
            == parent_before
        )

    def test_fresh_composition_does_not_leak_mutated_state(self) -> None:
        first = _compose_local_dependencies()
        first_provider = first.school_context_class_reader
        assert isinstance(first_provider, DevelopmentCoherentSchoolContextProvider)
        first_provider.set_teacher_class_authority(LOCAL_DEV_PRINCIPAL_ID, ())
        assert (
            first_provider.list_assignable_classes(
                LOCAL_DEV_TENANT_ID, LOCAL_DEV_PRINCIPAL_ID
            )
            == ()
        )

        second = _compose_local_dependencies()
        second_provider = second.school_context_class_reader
        assert isinstance(second_provider, DevelopmentCoherentSchoolContextProvider)
        assert first_provider is not second_provider
        restored = {
            item.class_ref
            for item in second_provider.list_assignable_classes(
                LOCAL_DEV_TENANT_ID, LOCAL_DEV_PRINCIPAL_ID
            )
        }
        assert restored == {CLASS_REF_5A, CLASS_REF_5B}


class TestApplicationCompositionPath:
    def test_compose_api_application_carries_all_four_readers(self) -> None:
        dependencies = _compose_local_dependencies()
        provider = dependencies.school_context_class_reader
        assert isinstance(provider, DevelopmentCoherentSchoolContextProvider)

        app = compose_api_application(_local_config(), dependencies)

        list_classes = app.state.list_assignable_school_classes_service
        assert list_classes is not None
        assert list_classes._reader is provider
        assert app.state.school_context_class_authority is not None
        assert app.state.school_context_class_authority._reader is provider

        list_assignments = app.state.list_current_assignments_service
        assert list_assignments is not None
        assert list_assignments._membership_reader is provider

        principal_service = app.state.get_principal_school_intelligence_service
        assert principal_service._school_scope._reader is provider

        parent_service = app.state.get_parent_intelligence_service
        assert (
            parent_service._learner_access is dependencies.parent_learner_access_service
        )
        assert parent_service._learner_access._reader is provider
        assert (
            parent_service._facts_reader is dependencies.parent_intelligence_facts_reader
        )
        assert parent_service._facts_reader._memberships._reader is provider

    def test_carrier_fields_forward_none_without_forcing_development_provider(
        self,
    ) -> None:
        dependencies = _compose_local_dependencies()
        bare = ApiRuntimeDependencies(
            uow_factory=dependencies.uow_factory,
            teaching_uow_factory=dependencies.teaching_uow_factory,
            assessment_uow_factory=dependencies.assessment_uow_factory,
            assessment_authorization=dependencies.assessment_authorization,
            request_identity_authenticator=dependencies.request_identity_authenticator,
            security_resolver=dependencies.security_resolver,
            content_types=dependencies.content_types,
            schema_registry=dependencies.schema_registry,
            review_authorization=dependencies.review_authorization,
            review_comment_policy=dependencies.review_comment_policy,
            publication_authorization=dependencies.publication_authorization,
            publication_governance=dependencies.publication_governance,
            asset_reference_validation=dependencies.asset_reference_validation,
            asset_current_governance=dependencies.asset_current_governance,
            readiness_probe=dependencies.readiness_probe,
            mutation_activation_gate=dependencies.mutation_activation_gate,
            principal_classification_authority=(
                dependencies.principal_classification_authority
            ),
            teaching_authorization=dependencies.teaching_authorization,
            student_learning_uow_factory=dependencies.student_learning_uow_factory,
        )
        assert bare.school_context_class_reader is None
        assert bare.learner_membership_reader is None
        app = compose_api_application(_local_config(), bare)
        assert app.state.list_assignable_school_classes_service is None
        assert app.state.school_context_class_authority is None
        assert app.state.list_current_assignments_service is not None
        with pytest.raises(SchoolContextUnavailable):
            app.state.list_current_assignments_service._membership_reader.list_current_memberships(
                uuid4(), uuid4()
            )
