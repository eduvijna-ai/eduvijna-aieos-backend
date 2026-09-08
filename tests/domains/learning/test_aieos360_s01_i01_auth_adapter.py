"""AIEOS360-S01-I01 — development mapped authenticator (Teacher OS unchanged)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from aieos.development.auth_adapters import (
    DevelopmentMappedPrincipalAuthenticator,
    DevelopmentPrincipalAuthenticator,
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


def _mapped() -> DevelopmentMappedPrincipalAuthenticator:
    return DevelopmentMappedPrincipalAuthenticator(
        {
            DEVELOPMENT_STUDENT_A_TOKEN: STUDENT_A_PRINCIPAL_ID,
            DEVELOPMENT_STUDENT_B_TOKEN: STUDENT_B_PRINCIPAL_ID,
        }
    )


class TestDevelopmentMappedPrincipalAuthenticator:
    def test_known_alias_returns_configured_principal(self) -> None:
        auth = _mapped()
        identity = auth.authenticate(_Request(f"Bearer {DEVELOPMENT_STUDENT_A_TOKEN}"))
        assert identity.principal_id == STUDENT_A_PRINCIPAL_ID
        identity_b = auth.authenticate(
            _Request(f"Bearer {DEVELOPMENT_STUDENT_B_TOKEN}")
        )
        assert identity_b.principal_id == STUDENT_B_PRINCIPAL_ID

    def test_unknown_token_unauthenticated(self) -> None:
        with pytest.raises(UnauthenticatedError):
            _mapped().authenticate(_Request("Bearer unknown-token"))

    def test_arbitrary_uuid_bearer_is_not_a_principal(self) -> None:
        with pytest.raises(UnauthenticatedError):
            _mapped().authenticate(_Request(f"Bearer {uuid4()}"))

    def test_missing_authorization_unauthenticated(self) -> None:
        with pytest.raises(UnauthenticatedError):
            _mapped().authenticate(_Request(None))


class TestTeacherDevelopmentAuthenticatorUnchanged:
    def test_fixed_principal_ignores_bearer(self) -> None:
        principal_id = uuid4()
        auth = DevelopmentPrincipalAuthenticator(principal_id)
        identity = auth.authenticate(_Request("Bearer anything"))
        assert identity.principal_id == principal_id
