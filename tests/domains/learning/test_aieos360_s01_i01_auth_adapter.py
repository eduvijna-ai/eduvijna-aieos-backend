"""AIEOS360-S01-I01R1 — fixed Student development authenticator."""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from aieos.development import auth_adapters
from aieos.development.auth_adapters import (
    DevelopmentPrincipalAuthenticator,
    DevelopmentStudentPrincipalAuthenticator,
)
from aieos.development.learner_principals import (
    DEVELOPMENT_STUDENT_A_TOKEN,
    DEVELOPMENT_STUDENT_B_TOKEN,
    STUDENT_A_PRINCIPAL_ID,
    STUDENT_B_PRINCIPAL_ID,
)
from aieos.platform.security.context import UnauthenticatedError

pytestmark = pytest.mark.aieos360_s01_i01


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Request:
    def __init__(self, authorization: str | None) -> None:
        self.headers = _Headers()
        if authorization is not None:
            self.headers["Authorization"] = authorization


class TestDevelopmentStudentPrincipalAuthenticator:
    def test_r1_01_dev_student_a_maps_to_student_a(self) -> None:
        identity = DevelopmentStudentPrincipalAuthenticator().authenticate(
            _Request(f"Bearer {DEVELOPMENT_STUDENT_A_TOKEN}")
        )
        assert identity.principal_id == STUDENT_A_PRINCIPAL_ID

    def test_r1_02_dev_student_b_maps_to_student_b(self) -> None:
        identity = DevelopmentStudentPrincipalAuthenticator().authenticate(
            _Request(f"Bearer {DEVELOPMENT_STUDENT_B_TOKEN}")
        )
        assert identity.principal_id == STUDENT_B_PRINCIPAL_ID

    def test_r1_03_unknown_token_unauthenticated(self) -> None:
        with pytest.raises(UnauthenticatedError):
            DevelopmentStudentPrincipalAuthenticator().authenticate(
                _Request("Bearer unknown-token")
            )

    def test_r1_04_arbitrary_uuid_bearer_unauthenticated(self) -> None:
        with pytest.raises(UnauthenticatedError):
            DevelopmentStudentPrincipalAuthenticator().authenticate(
                _Request(f"Bearer {uuid4()}")
            )

    def test_r1_05_caller_cannot_configure_arbitrary_principal_mapping(self) -> None:
        assert not hasattr(auth_adapters, "DevelopmentMappedPrincipalAuthenticator")
        parameters = inspect.signature(
            DevelopmentStudentPrincipalAuthenticator.__init__
        ).parameters
        assert tuple(parameters) == ("self",)
        with pytest.raises(TypeError):
            DevelopmentStudentPrincipalAuthenticator(  # type: ignore[call-arg]
                {DEVELOPMENT_STUDENT_A_TOKEN: uuid4()}
            )
        with pytest.raises(TypeError):
            DevelopmentStudentPrincipalAuthenticator(  # type: ignore[call-arg]
                principal_id=uuid4()
            )
        with pytest.raises(UnauthenticatedError):
            DevelopmentStudentPrincipalAuthenticator().authenticate(
                _Request(f"Bearer {STUDENT_A_PRINCIPAL_ID}")
            )

    def test_missing_authorization_unauthenticated(self) -> None:
        with pytest.raises(UnauthenticatedError):
            DevelopmentStudentPrincipalAuthenticator().authenticate(_Request(None))


class TestTeacherDevelopmentAuthenticatorUnchanged:
    def test_r1_06_fixed_principal_ignores_bearer(self) -> None:
        principal_id = uuid4()
        auth = DevelopmentPrincipalAuthenticator(principal_id)
        identity = auth.authenticate(_Request("Bearer anything"))
        assert identity.principal_id == principal_id
        identity_student_alias = auth.authenticate(
            _Request(f"Bearer {DEVELOPMENT_STUDENT_A_TOKEN}")
        )
        assert identity_student_alias.principal_id == principal_id
