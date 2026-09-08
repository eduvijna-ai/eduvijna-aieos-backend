"""AIEOS360-S01-I01 — current learner membership application contract."""

from __future__ import annotations

from uuid import uuid4

import pytest

from aieos.development.learner_principals import (
    CLASS_REF_5A,
    CLASS_REF_5B,
    STUDENT_A_PRINCIPAL_ID,
    STUDENT_A_SCHOOL_LEARNER_REF,
    STUDENT_B_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
)
from aieos.development.learner_school_context import (
    development_learner_membership_authority,
    development_learner_membership_reader,
)
from aieos.development.teacher_os_review_scenario import SYNTHETIC_PRINCIPAL_ID
from aieos.domains.learning.application.errors import (
    LearnerClassMembershipDenied,
    SchoolContextContractError,
    SchoolContextUnavailable,
)
from aieos.domains.learning.application.learner_membership import (
    CurrentLearnerClassMembership,
    ListCurrentLearnerMembershipsService,
    SchoolContextLearnerMembershipAuthorityService,
    UnconfiguredSchoolContextLearnerMembershipReader,
)

pytestmark = pytest.mark.aieos360_s01_i01


class _RecordingReader:
    def __init__(
        self,
        result: object = (),
        *,
        exc: BaseException | None = None,
    ) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[tuple[object, object]] = []

    def list_current_memberships(self, tenant_id, learner_principal_id):
        self.calls.append((tenant_id, learner_principal_id))
        if self.exc is not None:
            raise self.exc
        return self.result


class TestDevelopmentMembershipMatrix:
    def test_i01_01_student_a_class_5a_pass(self) -> None:
        item = development_learner_membership_authority().require_current_membership(
            SYNTHETIC_TENANT_ID, STUDENT_A_PRINCIPAL_ID, CLASS_REF_5A
        )
        assert item.class_ref == CLASS_REF_5A
        assert item.school_learner_ref == STUDENT_A_SCHOOL_LEARNER_REF

    def test_i01_02_student_a_class_5b_deny(self) -> None:
        with pytest.raises(LearnerClassMembershipDenied):
            development_learner_membership_authority().require_current_membership(
                SYNTHETIC_TENANT_ID, STUDENT_A_PRINCIPAL_ID, CLASS_REF_5B
            )

    def test_i01_03_student_b_class_5a_deny(self) -> None:
        with pytest.raises(LearnerClassMembershipDenied):
            development_learner_membership_authority().require_current_membership(
                SYNTHETIC_TENANT_ID, STUDENT_B_PRINCIPAL_ID, CLASS_REF_5A
            )

    def test_i01_04_student_b_class_5b_pass(self) -> None:
        item = development_learner_membership_authority().require_current_membership(
            SYNTHETIC_TENANT_ID, STUDENT_B_PRINCIPAL_ID, CLASS_REF_5B
        )
        assert item.class_ref == CLASS_REF_5B
        assert item.school_learner_ref is None

    def test_i01_05_wrong_tenant_deny(self) -> None:
        with pytest.raises(LearnerClassMembershipDenied):
            development_learner_membership_authority().require_current_membership(
                uuid4(), STUDENT_A_PRINCIPAL_ID, CLASS_REF_5A
            )

    def test_i01_06_unknown_learner_deny(self) -> None:
        with pytest.raises(LearnerClassMembershipDenied):
            development_learner_membership_authority().require_current_membership(
                SYNTHETIC_TENANT_ID, uuid4(), CLASS_REF_5A
            )

    def test_i01_12_teacher_principal_not_automatically_member(self) -> None:
        with pytest.raises(LearnerClassMembershipDenied):
            development_learner_membership_authority().require_current_membership(
                SYNTHETIC_TENANT_ID, SYNTHETIC_PRINCIPAL_ID, CLASS_REF_5A
            )


class TestFailClosedProvider:
    def test_i01_07_provider_unavailable_fails_closed(self) -> None:
        service = ListCurrentLearnerMembershipsService(
            _RecordingReader(exc=RuntimeError("erp secret boom"))
        )
        with pytest.raises(SchoolContextUnavailable):
            service.list(uuid4(), uuid4())

        unconfigured = SchoolContextLearnerMembershipAuthorityService(
            UnconfiguredSchoolContextLearnerMembershipReader()
        )
        with pytest.raises(SchoolContextUnavailable):
            unconfigured.require_current_membership(
                uuid4(), uuid4(), CLASS_REF_5A
            )

    def test_i01_08_malformed_provider_response_fails_closed(self) -> None:
        service = ListCurrentLearnerMembershipsService(_RecordingReader(None))
        with pytest.raises(SchoolContextContractError):
            service.list(uuid4(), uuid4())

        service = ListCurrentLearnerMembershipsService(_RecordingReader([object()]))
        with pytest.raises(SchoolContextContractError):
            service.list(uuid4(), uuid4())

        class _BadRef:
            class_ref = "class-5a"
            school_learner_ref = 123

        service = ListCurrentLearnerMembershipsService(_RecordingReader([_BadRef()]))
        with pytest.raises(SchoolContextContractError):
            service.list(uuid4(), uuid4())

    def test_i01_09_blank_class_ref_fails_closed(self) -> None:
        service = ListCurrentLearnerMembershipsService(
            _RecordingReader(
                (CurrentLearnerClassMembership(class_ref="  "),)
            )
        )
        with pytest.raises(SchoolContextContractError):
            service.list(uuid4(), uuid4())

        with pytest.raises(SchoolContextContractError):
            development_learner_membership_authority().require_current_membership(
                SYNTHETIC_TENANT_ID, STUDENT_A_PRINCIPAL_ID, "   "
            )

    def test_i01_10_duplicate_class_ref_fails_closed(self) -> None:
        service = ListCurrentLearnerMembershipsService(
            _RecordingReader(
                (
                    CurrentLearnerClassMembership(class_ref="dup"),
                    CurrentLearnerClassMembership(class_ref="dup"),
                )
            )
        )
        with pytest.raises(SchoolContextContractError):
            service.list(uuid4(), uuid4())


class TestSchoolLearnerRefIsNotIdentity:
    def test_i01_11_school_learner_ref_is_optional_opaque_metadata(self) -> None:
        authority = development_learner_membership_authority()
        item = authority.require_current_membership(
            SYNTHETIC_TENANT_ID, STUDENT_A_PRINCIPAL_ID, CLASS_REF_5A
        )
        assert item.school_learner_ref == STUDENT_A_SCHOOL_LEARNER_REF

        # Correlation metadata is not an authentication or authorization key.
        with pytest.raises(LearnerClassMembershipDenied):
            authority.require_current_membership(
                SYNTHETIC_TENANT_ID, uuid4(), CLASS_REF_5A
            )
        listed = development_learner_membership_reader().list_current_memberships(
            SYNTHETIC_TENANT_ID, STUDENT_A_PRINCIPAL_ID
        )
        assert listed[0].school_learner_ref == STUDENT_A_SCHOOL_LEARNER_REF
        assert authority.require_current_membership(
            SYNTHETIC_TENANT_ID,
            STUDENT_A_PRINCIPAL_ID,
            CLASS_REF_5A,
        ).class_ref == CLASS_REF_5A
