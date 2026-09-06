"""TOS-DEV10-I03S1 CurrentPrincipalClassificationAuthority proofs."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from aieos.platform.security.authorization import (
    CurrentPrincipalClassificationAuthority,
    PrincipalKind,
)
from aieos.platform.security.authorization.decisions import PrincipalStatus
from aieos.platform.security.context import UnauthorizedError
from aieos.platform.security.identity import TrustedRequestIdentity
from tests.platform.security.authorization.helpers import seed_principal

pytestmark = pytest.mark.tos_dev10_i03s1


def _authority(engine) -> CurrentPrincipalClassificationAuthority:
    return CurrentPrincipalClassificationAuthority(engine)


class TestResolveAndRequire:
    def test_human_resolves_and_require_human(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        principal = uuid.uuid7()
        seed_principal(bootstrap_engine, principal, principal_kind=PrincipalKind.HUMAN)
        auth = _authority(runtime_engine)
        assert auth.resolve_current_principal_kind(principal) is PrincipalKind.HUMAN
        assert auth.require_current_human_principal(principal) is PrincipalKind.HUMAN

    def test_workload_resolves_and_require_workload(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        principal = uuid.uuid7()
        seed_principal(
            bootstrap_engine, principal, principal_kind=PrincipalKind.WORKLOAD
        )
        auth = _authority(runtime_engine)
        assert auth.resolve_current_principal_kind(principal) is PrincipalKind.WORKLOAD
        assert (
            auth.require_current_workload_principal(principal) is PrincipalKind.WORKLOAD
        )

    def test_null_kind_fails_closed_for_require_human(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        principal = uuid.uuid7()
        seed_principal(bootstrap_engine, principal, principal_kind=None)
        auth = _authority(runtime_engine)
        with pytest.raises(UnauthorizedError):
            auth.require_current_human_principal(principal)
        with pytest.raises(UnauthorizedError):
            auth.resolve_current_principal_kind(principal)

    def test_workload_fails_require_human(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        principal = uuid.uuid7()
        seed_principal(
            bootstrap_engine, principal, principal_kind=PrincipalKind.WORKLOAD
        )
        with pytest.raises(UnauthorizedError):
            _authority(runtime_engine).require_current_human_principal(principal)

    def test_human_fails_require_workload(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        principal = uuid.uuid7()
        seed_principal(bootstrap_engine, principal, principal_kind=PrincipalKind.HUMAN)
        with pytest.raises(UnauthorizedError):
            _authority(runtime_engine).require_current_workload_principal(principal)

    def test_unknown_principal_fails_closed(self, runtime_engine) -> None:
        with pytest.raises(UnauthorizedError):
            _authority(runtime_engine).resolve_current_principal_kind(uuid.uuid7())

    @pytest.mark.parametrize(
        "status",
        (PrincipalStatus.SUSPENDED, PrincipalStatus.DISABLED),
    )
    def test_inactive_principal_fails_closed(
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


class TestIdentityAndRevalidation:
    def test_trusted_request_identity_remains_principal_id_only(self) -> None:
        assert set(TrustedRequestIdentity.__dataclass_fields__) == {"principal_id"}
        identity = TrustedRequestIdentity(principal_id=uuid.uuid7())
        assert not hasattr(identity, "principal_kind")

    def test_jwt_module_does_not_map_kind_claims(self) -> None:
        from pathlib import Path

        from tests.dbutil import REPO_ROOT

        jwt_src = (
            REPO_ROOT / "src" / "aieos" / "platform" / "security" / "jwt_bearer.py"
        )
        text = jwt_src.read_text(encoding="utf-8")
        assert "principal_kind" not in text
        assert "PrincipalKind" not in text

    def test_kind_change_in_sor_observed_on_next_check(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        principal = uuid.uuid7()
        seed_principal(bootstrap_engine, principal, principal_kind=PrincipalKind.HUMAN)
        auth = _authority(runtime_engine)
        assert auth.require_current_human_principal(principal) is PrincipalKind.HUMAN

        with bootstrap_engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE security.principals
                    SET principal_kind = 'WORKLOAD',
                        updated_at = clock_timestamp()
                    WHERE principal_id = :id
                    """),
                {"id": principal},
            )

        with pytest.raises(UnauthorizedError):
            auth.require_current_human_principal(principal)
        assert (
            auth.require_current_workload_principal(principal) is PrincipalKind.WORKLOAD
        )
