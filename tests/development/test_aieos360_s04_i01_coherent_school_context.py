"""AIEOS360-S04-I01 — coherent NON_PRODUCTION School Context current-fact proofs."""

from __future__ import annotations

from uuid import uuid4

import pytest

from aieos.development.coherent_school_context import (
    CLASS_LABEL_5A,
    CLASS_LABEL_5B,
    CLASS_REF_5A,
    CLASS_REF_5B,
    PARENT_OS_HUMAN_ADULT_A_ID,
    PARENT_OS_HUMAN_ADULT_B_ID,
    PRINCIPAL_OS_HUMAN_PRINCIPAL_ID,
    STUDENT_A_PRINCIPAL_ID,
    STUDENT_A_SCHOOL_LEARNER_REF,
    STUDENT_B_PRINCIPAL_ID,
    SYNTHETIC_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
    CoherentSchoolContextFixtureError,
    DevelopmentCoherentSchoolContextProvider,
    development_coherent_school_context_provider,
)
from aieos.domains.learning.application.learner_membership import (
    CurrentLearnerClassMembership,
    ListCurrentLearnerMembershipsService,
    SchoolContextLearnerMembershipReader,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    AuthorizedLearnerAccess,
    SchoolContextParentLearnerAccessReader,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
    SchoolContextPrincipalScopeReader,
)
from aieos.domains.teaching.application.school_context import (
    AssignableClassRef,
    ListAssignableSchoolClassesService,
    SchoolContextClassReader,
)

pytestmark = pytest.mark.aieos360_s04_i01


def _provider() -> DevelopmentCoherentSchoolContextProvider:
    return development_coherent_school_context_provider()


class TestFourExistingPortShapes:
    def test_one_provider_answers_all_four_existing_port_shapes(self) -> None:
        provider = _provider()
        class_reader: SchoolContextClassReader = provider
        membership_reader: SchoolContextLearnerMembershipReader = provider
        scope_reader: SchoolContextPrincipalScopeReader = provider
        access_reader: SchoolContextParentLearnerAccessReader = provider

        assignable = class_reader.list_assignable_classes(
            SYNTHETIC_TENANT_ID, SYNTHETIC_PRINCIPAL_ID
        )
        memberships = membership_reader.list_current_memberships(
            SYNTHETIC_TENANT_ID, STUDENT_A_PRINCIPAL_ID
        )
        scope = scope_reader.list_current_authorized_classes(
            SYNTHETIC_TENANT_ID, PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
        )
        access = access_reader.list_current_authorized_learners(
            SYNTHETIC_TENANT_ID, PARENT_OS_HUMAN_ADULT_A_ID
        )

        assert assignable == (
            AssignableClassRef(class_ref=CLASS_REF_5A, display_label=CLASS_LABEL_5A),
            AssignableClassRef(class_ref=CLASS_REF_5B, display_label=CLASS_LABEL_5B),
        )
        assert memberships == (
            CurrentLearnerClassMembership(
                class_ref=CLASS_REF_5A,
                school_learner_ref=STUDENT_A_SCHOOL_LEARNER_REF,
            ),
        )
        assert scope == (
            AuthorizedSchoolClassRef(
                class_ref=CLASS_REF_5A, display_label=CLASS_LABEL_5A
            ),
            AuthorizedSchoolClassRef(
                class_ref=CLASS_REF_5B, display_label=CLASS_LABEL_5B
            ),
        )
        assert access == (
            AuthorizedLearnerAccess(learner_principal_id=STUDENT_A_PRINCIPAL_ID),
        )

    def test_existing_application_list_services_accept_the_provider(self) -> None:
        provider = _provider()
        assignable = ListAssignableSchoolClassesService(provider).list(
            SYNTHETIC_TENANT_ID, SYNTHETIC_PRINCIPAL_ID
        )
        memberships = ListCurrentLearnerMembershipsService(provider).list(
            SYNTHETIC_TENANT_ID, STUDENT_A_PRINCIPAL_ID
        )
        assert CLASS_REF_5A in {item.class_ref for item in assignable}
        assert memberships[0].class_ref == CLASS_REF_5A


class TestCoherentSchoolStory:
    def test_teacher_learner_principal_share_the_same_class_ref(self) -> None:
        provider = _provider()
        teacher_refs = {
            item.class_ref
            for item in provider.list_assignable_classes(
                SYNTHETIC_TENANT_ID, SYNTHETIC_PRINCIPAL_ID
            )
        }
        learner_refs = {
            item.class_ref
            for item in provider.list_current_memberships(
                SYNTHETIC_TENANT_ID, STUDENT_A_PRINCIPAL_ID
            )
        }
        principal_refs = {
            item.class_ref
            for item in provider.list_current_authorized_classes(
                SYNTHETIC_TENANT_ID, PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
            )
        }
        assert CLASS_REF_5A in teacher_refs
        assert learner_refs == {CLASS_REF_5A}
        assert CLASS_REF_5A in principal_refs
        assert learner_refs <= teacher_refs
        assert learner_refs <= principal_refs

    def test_authorized_adult_resolves_the_same_learner_principal(self) -> None:
        provider = _provider()
        access = provider.list_current_authorized_learners(
            SYNTHETIC_TENANT_ID, PARENT_OS_HUMAN_ADULT_A_ID
        )
        assert access == (
            AuthorizedLearnerAccess(learner_principal_id=STUDENT_A_PRINCIPAL_ID),
        )
        memberships = provider.list_current_memberships(
            SYNTHETIC_TENANT_ID, access[0].learner_principal_id
        )
        assert memberships[0].class_ref == CLASS_REF_5A

    def test_optional_parallel_class_5b_student_b_story(self) -> None:
        provider = _provider()
        assert provider.list_current_memberships(
            SYNTHETIC_TENANT_ID, STUDENT_B_PRINCIPAL_ID
        ) == (CurrentLearnerClassMembership(class_ref=CLASS_REF_5B),)
        assert provider.list_current_authorized_learners(
            SYNTHETIC_TENANT_ID, PARENT_OS_HUMAN_ADULT_B_ID
        ) == (AuthorizedLearnerAccess(learner_principal_id=STUDENT_B_PRINCIPAL_ID),)
        teacher_refs = {
            item.class_ref
            for item in provider.list_assignable_classes(
                SYNTHETIC_TENANT_ID, SYNTHETIC_PRINCIPAL_ID
            )
        }
        principal_refs = {
            item.class_ref
            for item in provider.list_current_authorized_classes(
                SYNTHETIC_TENANT_ID, PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
            )
        }
        assert CLASS_REF_5B in teacher_refs
        assert CLASS_REF_5B in principal_refs


class TestFailClosedUnknownAndTenant:
    def test_tenant_mismatch_fails_closed_for_all_four_contracts(self) -> None:
        provider = _provider()
        other_tenant = uuid4()
        assert (
            provider.list_assignable_classes(other_tenant, SYNTHETIC_PRINCIPAL_ID) == ()
        )
        assert (
            provider.list_current_memberships(other_tenant, STUDENT_A_PRINCIPAL_ID) == ()
        )
        assert (
            provider.list_current_authorized_classes(
                other_tenant, PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
            )
            == ()
        )
        assert (
            provider.list_current_authorized_learners(
                other_tenant, PARENT_OS_HUMAN_ADULT_A_ID
            )
            == ()
        )

    def test_unknown_actors_return_no_authority(self) -> None:
        provider = _provider()
        unknown = uuid4()
        assert provider.list_assignable_classes(SYNTHETIC_TENANT_ID, unknown) == ()
        assert provider.list_current_memberships(SYNTHETIC_TENANT_ID, unknown) == ()
        assert (
            provider.list_current_authorized_classes(SYNTHETIC_TENANT_ID, unknown) == ()
        )
        assert (
            provider.list_current_authorized_learners(SYNTHETIC_TENANT_ID, unknown) == ()
        )


class TestCurrentFactRevocation:
    def test_teacher_authority_revocation_is_visible_on_the_next_read(self) -> None:
        provider = _provider()
        provider.set_teacher_class_authority(SYNTHETIC_PRINCIPAL_ID, (CLASS_REF_5A,))
        items = provider.list_assignable_classes(
            SYNTHETIC_TENANT_ID, SYNTHETIC_PRINCIPAL_ID
        )
        assert tuple(item.class_ref for item in items) == (CLASS_REF_5A,)
        provider.set_teacher_class_authority(SYNTHETIC_PRINCIPAL_ID, ())
        assert (
            provider.list_assignable_classes(
                SYNTHETIC_TENANT_ID, SYNTHETIC_PRINCIPAL_ID
            )
            == ()
        )

    def test_learner_membership_removal_is_visible_on_the_next_read(self) -> None:
        provider = _provider()
        provider.set_learner_membership(STUDENT_A_PRINCIPAL_ID, ())
        assert (
            provider.list_current_memberships(
                SYNTHETIC_TENANT_ID, STUDENT_A_PRINCIPAL_ID
            )
            == ()
        )

    def test_principal_scope_reduction_is_visible_on_the_next_read(self) -> None:
        provider = _provider()
        provider.set_principal_class_scope(
            PRINCIPAL_OS_HUMAN_PRINCIPAL_ID, (CLASS_REF_5A,)
        )
        items = provider.list_current_authorized_classes(
            SYNTHETIC_TENANT_ID, PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
        )
        assert tuple(item.class_ref for item in items) == (CLASS_REF_5A,)
        provider.set_principal_class_scope(PRINCIPAL_OS_HUMAN_PRINCIPAL_ID, ())
        assert (
            provider.list_current_authorized_classes(
                SYNTHETIC_TENANT_ID, PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
            )
            == ()
        )

    def test_adult_learner_revocation_is_visible_on_the_next_read(self) -> None:
        provider = _provider()
        provider.set_adult_learner_access(PARENT_OS_HUMAN_ADULT_A_ID, ())
        assert (
            provider.list_current_authorized_learners(
                SYNTHETIC_TENANT_ID, PARENT_OS_HUMAN_ADULT_A_ID
            )
            == ()
        )


class TestMalformedFixtureFailsClosed:
    def test_blank_class_ref_definition_is_rejected(self) -> None:
        with pytest.raises(CoherentSchoolContextFixtureError, match="blank"):
            DevelopmentCoherentSchoolContextProvider(
                class_definitions=(("", CLASS_LABEL_5A),),
                teacher_authority={},
                learner_memberships={},
                principal_scope={},
                adult_learner_access={},
            )

    def test_duplicate_class_ref_definition_is_rejected(self) -> None:
        with pytest.raises(CoherentSchoolContextFixtureError, match="duplicate"):
            DevelopmentCoherentSchoolContextProvider(
                class_definitions=(
                    (CLASS_REF_5A, CLASS_LABEL_5A),
                    (CLASS_REF_5A, CLASS_LABEL_5A),
                ),
                teacher_authority={},
                learner_memberships={},
                principal_scope={},
                adult_learner_access={},
            )

    def test_conflicting_class_display_definition_is_rejected(self) -> None:
        with pytest.raises(CoherentSchoolContextFixtureError, match="conflicting"):
            DevelopmentCoherentSchoolContextProvider(
                class_definitions=(
                    (CLASS_REF_5A, CLASS_LABEL_5A),
                    (CLASS_REF_5A, "Other 5A"),
                ),
                teacher_authority={},
                learner_memberships={},
                principal_scope={},
                adult_learner_access={},
            )

    def test_unknown_class_ref_in_relationship_is_rejected(self) -> None:
        with pytest.raises(CoherentSchoolContextFixtureError, match="unknown referenced"):
            DevelopmentCoherentSchoolContextProvider(
                class_definitions=((CLASS_REF_5A, CLASS_LABEL_5A),),
                teacher_authority={SYNTHETIC_PRINCIPAL_ID: ("class-missing",)},
                learner_memberships={},
                principal_scope={},
                adult_learner_access={},
            )

    def test_duplicate_teacher_class_refs_are_rejected(self) -> None:
        with pytest.raises(CoherentSchoolContextFixtureError, match="duplicate"):
            DevelopmentCoherentSchoolContextProvider(
                class_definitions=((CLASS_REF_5A, CLASS_LABEL_5A),),
                teacher_authority={
                    SYNTHETIC_PRINCIPAL_ID: (CLASS_REF_5A, CLASS_REF_5A)
                },
                learner_memberships={},
                principal_scope={},
                adult_learner_access={},
            )

    def test_malformed_uuid_identity_is_rejected(self) -> None:
        with pytest.raises(CoherentSchoolContextFixtureError, match="malformed"):
            DevelopmentCoherentSchoolContextProvider(
                class_definitions=((CLASS_REF_5A, CLASS_LABEL_5A),),
                teacher_authority={"not-a-uuid": (CLASS_REF_5A,)},  # type: ignore[dict-item]
                learner_memberships={},
                principal_scope={},
                adult_learner_access={},
            )

    def test_adult_access_to_unknown_learner_is_rejected(self) -> None:
        unknown_learner = uuid4()
        with pytest.raises(
            CoherentSchoolContextFixtureError, match="unknown learner fixture"
        ):
            DevelopmentCoherentSchoolContextProvider(
                class_definitions=((CLASS_REF_5A, CLASS_LABEL_5A),),
                teacher_authority={},
                learner_memberships={},
                principal_scope={},
                adult_learner_access={PARENT_OS_HUMAN_ADULT_A_ID: (unknown_learner,)},
            )

    def test_malformed_school_learner_ref_is_rejected(self) -> None:
        with pytest.raises(
            CoherentSchoolContextFixtureError, match="malformed optional"
        ):
            DevelopmentCoherentSchoolContextProvider(
                class_definitions=((CLASS_REF_5A, CLASS_LABEL_5A),),
                teacher_authority={},
                learner_memberships={
                    STUDENT_A_PRINCIPAL_ID: ((CLASS_REF_5A, "   "),)
                },
                principal_scope={},
                adult_learner_access={},
            )

    def test_malformed_read_identity_fails_closed_not_empty_allow(self) -> None:
        provider = _provider()
        with pytest.raises(CoherentSchoolContextFixtureError, match="malformed"):
            provider.list_assignable_classes(
                "not-a-tenant",  # type: ignore[arg-type]
                SYNTHETIC_PRINCIPAL_ID,
            )

    def test_rejected_mutation_preserves_previous_current_facts(self) -> None:
        provider = _provider()
        before = provider.list_assignable_classes(
            SYNTHETIC_TENANT_ID, SYNTHETIC_PRINCIPAL_ID
        )
        with pytest.raises(CoherentSchoolContextFixtureError):
            provider.set_teacher_class_authority(
                SYNTHETIC_PRINCIPAL_ID, (CLASS_REF_5A, CLASS_REF_5A)
            )
        after = provider.list_assignable_classes(
            SYNTHETIC_TENANT_ID, SYNTHETIC_PRINCIPAL_ID
        )
        assert after == before
        assert tuple(item.class_ref for item in after) == (CLASS_REF_5A, CLASS_REF_5B)
