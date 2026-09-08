"""AIEOS360-S01-I01 — HUMAN Student principal security substrate proofs."""

from __future__ import annotations

import uuid

import pytest

from aieos.development.learner_principals import (
    STUDENT_A_PRINCIPAL_ID,
    STUDENT_B_PRINCIPAL_ID,
    ensure_synthetic_student_principals,
)
from aieos.platform.security.authorization import (
    CurrentPrincipalClassificationAuthority,
    PrincipalKind,
)
from aieos.platform.security.authorization.decisions import PrincipalStatus
from aieos.platform.security.context import UnauthorizedError
from tests.platform.security.authorization.helpers import seed_principal

pytestmark = pytest.mark.aieos360_s01_i01


def _authority(engine) -> CurrentPrincipalClassificationAuthority:
    return CurrentPrincipalClassificationAuthority(engine)


class TestSyntheticStudentPrincipalsAreHuman:
    def test_i01_13_student_a_is_active_human(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        ensure_synthetic_student_principals(bootstrap_engine)
        auth = _authority(runtime_engine)
        assert (
            auth.require_current_human_principal(STUDENT_A_PRINCIPAL_ID)
            is PrincipalKind.HUMAN
        )
        assert (
            auth.resolve_current_principal_kind(STUDENT_A_PRINCIPAL_ID)
            is PrincipalKind.HUMAN
        )

    def test_i01_14_student_b_is_active_human(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        ensure_synthetic_student_principals(bootstrap_engine)
        auth = _authority(runtime_engine)
        assert (
            auth.require_current_human_principal(STUDENT_B_PRINCIPAL_ID)
            is PrincipalKind.HUMAN
        )

    def test_i01_15_workload_cannot_pass_human_learner_requirement(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        principal = uuid.uuid7()
        seed_principal(
            bootstrap_engine, principal, principal_kind=PrincipalKind.WORKLOAD
        )
        with pytest.raises(UnauthorizedError):
            _authority(runtime_engine).require_current_human_principal(principal)

    @pytest.mark.parametrize(
        "status",
        (PrincipalStatus.SUSPENDED, PrincipalStatus.DISABLED),
    )
    def test_i01_16_inactive_principal_cannot_pass_current_learner_requirement(
        self, bootstrap_engine, runtime_engine, status: PrincipalStatus
    ) -> None:
        principal = uuid.uuid7()
        seed_principal(
            bootstrap_engine,
            principal,
            principal_kind=PrincipalKind.HUMAN,
            status=status,
        )
        with pytest.raises(UnauthorizedError):
            _authority(runtime_engine).require_current_human_principal(principal)

    def test_i01_17_unclassified_principal_fails_closed_where_human_required(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        principal = uuid.uuid7()
        seed_principal(bootstrap_engine, principal, principal_kind=None)
        with pytest.raises(UnauthorizedError):
            _authority(runtime_engine).require_current_human_principal(principal)

    def test_principal_kind_student_does_not_exist(self) -> None:
        assert set(PrincipalKind) == {PrincipalKind.HUMAN, PrincipalKind.WORKLOAD}
        assert not hasattr(PrincipalKind, "STUDENT")
