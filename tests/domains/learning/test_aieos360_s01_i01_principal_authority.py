"""AIEOS360-S01-I01 — HUMAN Student principal security substrate proofs."""

from __future__ import annotations

import inspect
import uuid

import pytest
from sqlalchemy import text

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


def _load_principal(engine, principal_id) -> tuple[str, str | None]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT status, principal_kind
                FROM security.principals
                WHERE principal_id = :principal_id
                """
            ),
            {"principal_id": principal_id},
        ).one()
    return str(row.status), row.principal_kind


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

    def test_r1_07_helper_seeds_student_a_active_human(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        ensure_synthetic_student_principals(bootstrap_engine)
        status, kind = _load_principal(bootstrap_engine, STUDENT_A_PRINCIPAL_ID)
        assert status == PrincipalStatus.ACTIVE
        assert kind == str(PrincipalKind.HUMAN)
        assert (
            _authority(runtime_engine).require_current_human_principal(
                STUDENT_A_PRINCIPAL_ID
            )
            is PrincipalKind.HUMAN
        )

    def test_r1_08_helper_seeds_student_b_active_human(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        ensure_synthetic_student_principals(bootstrap_engine)
        status, kind = _load_principal(bootstrap_engine, STUDENT_B_PRINCIPAL_ID)
        assert status == PrincipalStatus.ACTIVE
        assert kind == str(PrincipalKind.HUMAN)
        assert (
            _authority(runtime_engine).require_current_human_principal(
                STUDENT_B_PRINCIPAL_ID
            )
            is PrincipalKind.HUMAN
        )

    def test_r1_09_helper_exposes_no_arbitrary_principal_ids_surface(self) -> None:
        parameters = inspect.signature(ensure_synthetic_student_principals).parameters
        assert tuple(parameters) == ("bootstrap_engine",)
        assert "principal_ids" not in parameters
        with pytest.raises(TypeError):
            ensure_synthetic_student_principals(  # type: ignore[call-arg]
                object(),
                principal_ids=(uuid.uuid4(),),
            )

    def test_r1_10_unrelated_principals_are_not_altered(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        workload = uuid.uuid7()
        disabled = uuid.uuid7()
        unrelated_human = uuid.uuid7()
        seed_principal(
            bootstrap_engine, workload, principal_kind=PrincipalKind.WORKLOAD
        )
        seed_principal(
            bootstrap_engine,
            disabled,
            principal_kind=PrincipalKind.HUMAN,
            status=PrincipalStatus.DISABLED,
        )
        seed_principal(
            bootstrap_engine,
            unrelated_human,
            principal_kind=PrincipalKind.HUMAN,
            status=PrincipalStatus.ACTIVE,
        )

        ensure_synthetic_student_principals(bootstrap_engine)

        workload_status, workload_kind = _load_principal(bootstrap_engine, workload)
        disabled_status, disabled_kind = _load_principal(bootstrap_engine, disabled)
        unrelated_status, unrelated_kind = _load_principal(
            bootstrap_engine, unrelated_human
        )
        assert workload_status == PrincipalStatus.ACTIVE
        assert workload_kind == str(PrincipalKind.WORKLOAD)
        assert disabled_status == PrincipalStatus.DISABLED
        assert disabled_kind == str(PrincipalKind.HUMAN)
        assert unrelated_status == PrincipalStatus.ACTIVE
        assert unrelated_kind == str(PrincipalKind.HUMAN)
        with pytest.raises(UnauthorizedError):
            _authority(runtime_engine).require_current_human_principal(workload)
        with pytest.raises(UnauthorizedError):
            _authority(runtime_engine).require_current_human_principal(disabled)
