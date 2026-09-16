"""AIEOS360-S02-I01 — School Intelligence capability + HUMAN classification."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.school_intelligence.application.errors import (
    SchoolIntelligenceCapabilityForbidden,
)
from aieos.domains.school_intelligence.application.ports import (
    AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES,
    SCHOOL_INTELLIGENCE_READ,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
    CurrentPrincipalSchoolScopeService,
)
from aieos.platform.security.authorization import (
    AIEOS_CONTENT_CAPABILITIES,
    AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES as ADAPTER_CAPABILITIES,
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
    KernelSchoolIntelligenceAuthorization,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from aieos.platform.security.context import (
    AuthorizationUnavailableError,
    UnauthorizedError,
)
from tests.platform.security.authorization.helpers import seed_active_authority, seed_principal

pytestmark = pytest.mark.aieos360_s02_i01


@pytest.fixture
def tenant_id():
    return uuid.uuid7()


@pytest.fixture
def principal_id():
    return uuid.uuid7()


def _kernel(engine: Engine) -> AuthorizationKernel:
    return AuthorizationKernel(
        engine,
        known_capabilities=AIEOS_CONTENT_CAPABILITIES | ADAPTER_CAPABILITIES,
    )


def _auth(engine: Engine) -> KernelSchoolIntelligenceAuthorization:
    return KernelSchoolIntelligenceAuthorization(_kernel(engine))


class _RecordingReader:
    def __init__(self, result=()) -> None:
        self.result = result
        self.calls: list[tuple[object, object]] = []

    def list_current_authorized_classes(self, tenant_id, principal_id):
        self.calls.append((tenant_id, principal_id))
        return self.result


class TestCatalogAndAdapter:
    def test_adapter_catalog_matches_application_catalog(self) -> None:
        assert AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES == ADAPTER_CAPABILITIES
        assert AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES == frozenset(
            {SCHOOL_INTELLIGENCE_READ}
        )

    def test_wildcard_capability_rejected(self, tenant_id, principal_id) -> None:
        class _Kernel:
            def decide_capability(self, **_kwargs):
                raise AssertionError("kernel must not be consulted for wildcards")

        auth = KernelSchoolIntelligenceAuthorization(_Kernel())  # type: ignore[arg-type]
        with pytest.raises(SchoolIntelligenceCapabilityForbidden):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability="school.intelligence.*",
            )
        with pytest.raises(SchoolIntelligenceCapabilityForbidden):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability="school.*",
            )
        with pytest.raises(SchoolIntelligenceCapabilityForbidden):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability="*.read",
            )

    def test_unknown_capability_denied(self, tenant_id, principal_id) -> None:
        class _Kernel:
            def decide_capability(self, **_kwargs):
                raise AssertionError("kernel must not be consulted for unknown")

        auth = KernelSchoolIntelligenceAuthorization(_Kernel())  # type: ignore[arg-type]
        with pytest.raises(SchoolIntelligenceCapabilityForbidden):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability="school.intelligence.write",
            )

    def test_authorization_unavailable_propagates(self, tenant_id, principal_id) -> None:
        class _UnavailableKernel:
            def decide_capability(self, **_kwargs):
                raise AuthorizationUnavailableError("authorization unavailable")

        auth = KernelSchoolIntelligenceAuthorization(_UnavailableKernel())  # type: ignore[arg-type]
        with pytest.raises(AuthorizationUnavailableError):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=SCHOOL_INTELLIGENCE_READ,
            )

    def test_unexpected_failure_sanitized(self, tenant_id, principal_id) -> None:
        class _BrokenKernel:
            def decide_capability(self, **_kwargs):
                raise RuntimeError("db exploded")

        auth = KernelSchoolIntelligenceAuthorization(_BrokenKernel())  # type: ignore[arg-type]
        with pytest.raises(AuthorizationUnavailableError):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=SCHOOL_INTELLIGENCE_READ,
            )


class TestKernelSchoolIntelligenceAuthorization:
    def test_allow_exact_known_capability(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(SCHOOL_INTELLIGENCE_READ,),
            principal_kind=PrincipalKind.HUMAN,
        )
        _auth(runtime_engine).authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=SCHOOL_INTELLIGENCE_READ,
        )

    def test_no_grant_denies(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(),
            principal_kind=PrincipalKind.HUMAN,
        )
        with pytest.raises(SchoolIntelligenceCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=SCHOOL_INTELLIGENCE_READ,
            )


class TestHumanClassificationWithScope:
    def test_active_human_with_grant_returns_scope(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(SCHOOL_INTELLIGENCE_READ,),
            principal_kind=PrincipalKind.HUMAN,
        )
        expected = (
            AuthorizedSchoolClassRef(class_ref="class-6a", display_label="Grade 6A"),
        )
        reader = _RecordingReader(expected)
        items = CurrentPrincipalSchoolScopeService(
            classification=CurrentPrincipalClassificationAuthority(runtime_engine),
            authorization=_auth(runtime_engine),
            reader=reader,
        ).current_authorized_classes(tenant_id, principal_id)
        assert items == expected
        assert reader.calls == [(tenant_id, principal_id)]

    def test_workload_fails_closed_before_scope(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(SCHOOL_INTELLIGENCE_READ,),
            principal_kind=PrincipalKind.WORKLOAD,
        )
        reader = _RecordingReader(
            (AuthorizedSchoolClassRef(class_ref="class-6a", display_label="Grade 6A"),)
        )
        with pytest.raises(UnauthorizedError):
            CurrentPrincipalSchoolScopeService(
                classification=CurrentPrincipalClassificationAuthority(runtime_engine),
                authorization=_auth(runtime_engine),
                reader=reader,
            ).current_authorized_classes(tenant_id, principal_id)
        assert reader.calls == []

    def test_inactive_principal_fails_closed(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(SCHOOL_INTELLIGENCE_READ,),
            principal_kind=PrincipalKind.HUMAN,
        )
        seed_principal(
            bootstrap_engine,
            principal_id,
            principal_kind=PrincipalKind.HUMAN,
            status="DISABLED",
        )
        reader = _RecordingReader(
            (AuthorizedSchoolClassRef(class_ref="class-6a", display_label="Grade 6A"),)
        )
        with pytest.raises(UnauthorizedError):
            CurrentPrincipalSchoolScopeService(
                classification=CurrentPrincipalClassificationAuthority(runtime_engine),
                authorization=_auth(runtime_engine),
                reader=reader,
            ).current_authorized_classes(tenant_id, principal_id)
        assert reader.calls == []

    def test_missing_principal_fails_closed(
        self, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        reader = _RecordingReader(
            (AuthorizedSchoolClassRef(class_ref="class-6a", display_label="Grade 6A"),)
        )
        with pytest.raises(UnauthorizedError):
            CurrentPrincipalSchoolScopeService(
                classification=CurrentPrincipalClassificationAuthority(runtime_engine),
                authorization=_auth(runtime_engine),
                reader=reader,
            ).current_authorized_classes(tenant_id, principal_id)
        assert reader.calls == []

    def test_null_classification_fails_closed(
        self, bootstrap_engine: Engine, runtime_engine: Engine, tenant_id, principal_id
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capabilities=(SCHOOL_INTELLIGENCE_READ,),
            principal_kind=PrincipalKind.HUMAN,
        )
        seed_principal(
            bootstrap_engine,
            principal_id,
            principal_kind=None,
        )
        reader = _RecordingReader(
            (AuthorizedSchoolClassRef(class_ref="class-6a", display_label="Grade 6A"),)
        )
        with pytest.raises(UnauthorizedError):
            CurrentPrincipalSchoolScopeService(
                classification=CurrentPrincipalClassificationAuthority(runtime_engine),
                authorization=_auth(runtime_engine),
                reader=reader,
            ).current_authorized_classes(tenant_id, principal_id)
        assert reader.calls == []
