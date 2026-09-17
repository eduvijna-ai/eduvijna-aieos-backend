"""AIEOS360-S03-I01 — Parent Intelligence capability + HUMAN adult classification."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.parent_intelligence.application.errors import (
    ParentIntelligenceCapabilityForbidden,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    AuthorizedLearnerAccess,
    CurrentParentLearnerAccessService,
)
from aieos.domains.parent_intelligence.application.ports import (
    AIEOS_PARENT_INTELLIGENCE_CAPABILITIES,
    PARENT_INTELLIGENCE_READ,
)
from aieos.platform.security.authorization import (
    AIEOS_CONTENT_CAPABILITIES,
    AIEOS_PARENT_INTELLIGENCE_CAPABILITIES as ADAPTER_CAPABILITIES,
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
    KernelParentIntelligenceAuthorization,
    SecurityAuthorityLearnerPrincipalIntegrity,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from aieos.platform.security.context import (
    AuthorizationUnavailableError,
    UnauthorizedError,
)
from tests.platform.security.authorization.helpers import seed_active_authority, seed_principal

pytestmark = pytest.mark.aieos360_s03_i01


@pytest.fixture
def tenant_id():
    return uuid.uuid7()


@pytest.fixture
def adult_principal_id():
    return uuid.uuid7()


@pytest.fixture
def learner_principal_id():
    return uuid.uuid7()


def _kernel(engine: Engine) -> AuthorizationKernel:
    return AuthorizationKernel(
        engine,
        known_capabilities=AIEOS_CONTENT_CAPABILITIES | ADAPTER_CAPABILITIES,
    )


def _auth(engine: Engine) -> KernelParentIntelligenceAuthorization:
    return KernelParentIntelligenceAuthorization(_kernel(engine))


class _RecordingReader:
    def __init__(self, result=()) -> None:
        self.result = result
        self.calls: list[tuple[object, object]] = []

    def list_current_authorized_learners(self, tenant_id, adult_principal_id):
        self.calls.append((tenant_id, adult_principal_id))
        return self.result


class _AllowIntegrity:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def validate_learner_subject(self, *, tenant_id, learner_principal_id):
        self.calls.append((tenant_id, learner_principal_id))


class TestCatalogAndAdapter:
    def test_adapter_catalog_matches_application_catalog(self) -> None:
        assert AIEOS_PARENT_INTELLIGENCE_CAPABILITIES == ADAPTER_CAPABILITIES
        assert AIEOS_PARENT_INTELLIGENCE_CAPABILITIES == frozenset(
            {PARENT_INTELLIGENCE_READ}
        )

    def test_wildcard_capability_rejected(self, tenant_id, adult_principal_id) -> None:
        class _Kernel:
            def decide_capability(self, **_kwargs):
                raise AssertionError("kernel must not be consulted for wildcards")

        auth = KernelParentIntelligenceAuthorization(_Kernel())  # type: ignore[arg-type]
        for capability in (
            "parent.intelligence.*",
            "parent.*",
            "*.read",
            "*",
        ):
            with pytest.raises(ParentIntelligenceCapabilityForbidden):
                auth.authorize(
                    tenant_id=tenant_id,
                    principal_id=adult_principal_id,
                    capability=capability,
                )

    def test_unknown_capability_denied(self, tenant_id, adult_principal_id) -> None:
        class _Kernel:
            def decide_capability(self, **_kwargs):
                raise AssertionError("kernel must not be consulted for unknown")

        auth = KernelParentIntelligenceAuthorization(_Kernel())  # type: ignore[arg-type]
        with pytest.raises(ParentIntelligenceCapabilityForbidden):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=adult_principal_id,
                capability="parent.intelligence.write",
            )

    def test_authorization_unavailable_propagates(
        self, tenant_id, adult_principal_id
    ) -> None:
        class _UnavailableKernel:
            def decide_capability(self, **_kwargs):
                raise AuthorizationUnavailableError("authorization unavailable")

        auth = KernelParentIntelligenceAuthorization(_UnavailableKernel())  # type: ignore[arg-type]
        with pytest.raises(AuthorizationUnavailableError):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=adult_principal_id,
                capability=PARENT_INTELLIGENCE_READ,
            )

    def test_unexpected_failure_sanitized(self, tenant_id, adult_principal_id) -> None:
        class _BrokenKernel:
            def decide_capability(self, **_kwargs):
                raise RuntimeError("db exploded")

        auth = KernelParentIntelligenceAuthorization(_BrokenKernel())  # type: ignore[arg-type]
        with pytest.raises(AuthorizationUnavailableError):
            auth.authorize(
                tenant_id=tenant_id,
                principal_id=adult_principal_id,
                capability=PARENT_INTELLIGENCE_READ,
            )


class TestKernelParentIntelligenceAuthorization:
    def test_allow_exact_known_capability(
        self,
        bootstrap_engine: Engine,
        runtime_engine: Engine,
        tenant_id,
        adult_principal_id,
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=adult_principal_id,
            capabilities=(PARENT_INTELLIGENCE_READ,),
            principal_kind=PrincipalKind.HUMAN,
        )
        _auth(runtime_engine).authorize(
            tenant_id=tenant_id,
            principal_id=adult_principal_id,
            capability=PARENT_INTELLIGENCE_READ,
        )

    def test_no_grant_denies(
        self,
        bootstrap_engine: Engine,
        runtime_engine: Engine,
        tenant_id,
        adult_principal_id,
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=adult_principal_id,
            capabilities=(),
            principal_kind=PrincipalKind.HUMAN,
        )
        with pytest.raises(ParentIntelligenceCapabilityForbidden):
            _auth(runtime_engine).authorize(
                tenant_id=tenant_id,
                principal_id=adult_principal_id,
                capability=PARENT_INTELLIGENCE_READ,
            )


class TestHumanClassificationWithAccess:
    def test_active_human_with_grant_returns_learners(
        self,
        bootstrap_engine: Engine,
        runtime_engine: Engine,
        tenant_id,
        adult_principal_id,
        learner_principal_id,
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=adult_principal_id,
            capabilities=(PARENT_INTELLIGENCE_READ,),
            principal_kind=PrincipalKind.HUMAN,
        )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=learner_principal_id,
            capabilities=(),
            principal_kind=PrincipalKind.HUMAN,
        )
        expected = (
            AuthorizedLearnerAccess(learner_principal_id=learner_principal_id),
        )
        reader = _RecordingReader(expected)
        items = CurrentParentLearnerAccessService(
            classification=CurrentPrincipalClassificationAuthority(runtime_engine),
            authorization=_auth(runtime_engine),
            reader=reader,
            integrity=SecurityAuthorityLearnerPrincipalIntegrity(runtime_engine),
        ).current_authorized_learners(tenant_id, adult_principal_id)
        assert items == expected
        assert reader.calls == [(tenant_id, adult_principal_id)]

    def test_workload_fails_closed_before_access_provider(
        self,
        bootstrap_engine: Engine,
        runtime_engine: Engine,
        tenant_id,
        adult_principal_id,
        learner_principal_id,
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=adult_principal_id,
            capabilities=(PARENT_INTELLIGENCE_READ,),
            principal_kind=PrincipalKind.WORKLOAD,
        )
        reader = _RecordingReader(
            (AuthorizedLearnerAccess(learner_principal_id=learner_principal_id),)
        )
        with pytest.raises(UnauthorizedError):
            CurrentParentLearnerAccessService(
                classification=CurrentPrincipalClassificationAuthority(
                    runtime_engine
                ),
                authorization=_auth(runtime_engine),
                reader=reader,
                integrity=_AllowIntegrity(),
            ).current_authorized_learners(tenant_id, adult_principal_id)
        assert reader.calls == []

    def test_inactive_adult_fails_closed(
        self,
        bootstrap_engine: Engine,
        runtime_engine: Engine,
        tenant_id,
        adult_principal_id,
        learner_principal_id,
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=adult_principal_id,
            capabilities=(PARENT_INTELLIGENCE_READ,),
            principal_kind=PrincipalKind.HUMAN,
        )
        seed_principal(
            bootstrap_engine,
            adult_principal_id,
            principal_kind=PrincipalKind.HUMAN,
            status="DISABLED",
        )
        reader = _RecordingReader(
            (AuthorizedLearnerAccess(learner_principal_id=learner_principal_id),)
        )
        with pytest.raises(UnauthorizedError):
            CurrentParentLearnerAccessService(
                classification=CurrentPrincipalClassificationAuthority(
                    runtime_engine
                ),
                authorization=_auth(runtime_engine),
                reader=reader,
                integrity=_AllowIntegrity(),
            ).current_authorized_learners(tenant_id, adult_principal_id)
        assert reader.calls == []

    def test_missing_adult_fails_closed(
        self, runtime_engine: Engine, tenant_id, adult_principal_id, learner_principal_id
    ) -> None:
        reader = _RecordingReader(
            (AuthorizedLearnerAccess(learner_principal_id=learner_principal_id),)
        )
        with pytest.raises(UnauthorizedError):
            CurrentParentLearnerAccessService(
                classification=CurrentPrincipalClassificationAuthority(
                    runtime_engine
                ),
                authorization=_auth(runtime_engine),
                reader=reader,
                integrity=_AllowIntegrity(),
            ).current_authorized_learners(tenant_id, adult_principal_id)
        assert reader.calls == []

    def test_null_classification_fails_closed(
        self,
        bootstrap_engine: Engine,
        runtime_engine: Engine,
        tenant_id,
        adult_principal_id,
        learner_principal_id,
    ) -> None:
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=adult_principal_id,
            capabilities=(PARENT_INTELLIGENCE_READ,),
            principal_kind=PrincipalKind.HUMAN,
        )
        seed_principal(
            bootstrap_engine,
            adult_principal_id,
            principal_kind=None,
        )
        reader = _RecordingReader(
            (AuthorizedLearnerAccess(learner_principal_id=learner_principal_id),)
        )
        with pytest.raises(UnauthorizedError):
            CurrentParentLearnerAccessService(
                classification=CurrentPrincipalClassificationAuthority(
                    runtime_engine
                ),
                authorization=_auth(runtime_engine),
                reader=reader,
                integrity=_AllowIntegrity(),
            ).current_authorized_learners(tenant_id, adult_principal_id)
        assert reader.calls == []
