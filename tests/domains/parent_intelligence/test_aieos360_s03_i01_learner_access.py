"""AIEOS360-S03-I01 — current Parent learner-access application contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import Engine

from aieos.development.parent_learner_access import (
    LEARNER_CHILD_1_ID,
    LEARNER_CHILD_2_ID,
    LEARNER_CHILD_3_ID,
    PARENT_OS_HUMAN_ADULT_A_ID,
    PARENT_OS_HUMAN_ADULT_B_ID,
    SYNTHETIC_TENANT_ID,
    DevelopmentParentIntelligencePermit,
    development_parent_learner_access_reader,
)
from aieos.domains.parent_intelligence.application.errors import (
    ParentIntelligenceCapabilityForbidden,
    ParentLearnerAccessContractError,
    ParentLearnerAccessUnavailable,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    AuthorizedLearnerAccess,
    CurrentParentLearnerAccessService,
    UnconfiguredSchoolContextParentLearnerAccessReader,
)
from aieos.domains.parent_intelligence.application.ports import PARENT_INTELLIGENCE_READ
from aieos.platform.security.authorization import (
    SecurityAuthorityLearnerPrincipalIntegrity,
)
from aieos.platform.security.authorization.decisions import (
    MembershipStatus,
    PrincipalKind,
    PrincipalStatus,
)
from aieos.platform.security.context import UnauthorizedError
from tests.platform.security.authorization.helpers import (
    seed_active_authority,
    seed_membership,
    seed_principal,
    seed_tenant,
)

pytestmark = pytest.mark.aieos360_s03_i01


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

    def list_current_authorized_learners(self, tenant_id, adult_principal_id):
        self.calls.append((tenant_id, adult_principal_id))
        if self.exc is not None:
            raise self.exc
        return self.result


class _HumanGate:
    def __init__(
        self,
        *,
        kind: PrincipalKind = PrincipalKind.HUMAN,
        exc: BaseException | None = None,
    ) -> None:
        self.kind = kind
        self.exc = exc
        self.calls: list[object] = []

    def require_current_human_principal(self, principal_id):
        self.calls.append(principal_id)
        if self.exc is not None:
            raise self.exc
        if self.kind is not PrincipalKind.HUMAN:
            raise UnauthorizedError("principal not authorized")
        return PrincipalKind.HUMAN


class _Auth:
    def __init__(self, *, allow: bool = True, exc: BaseException | None = None) -> None:
        self.allow = allow
        self.exc = exc
        self.calls: list[tuple[object, object, str]] = []

    def authorize(self, *, tenant_id, principal_id, capability):
        self.calls.append((tenant_id, principal_id, capability))
        if self.exc is not None:
            raise self.exc
        if not self.allow:
            raise ParentIntelligenceCapabilityForbidden(
                "parent intelligence capability denied"
            )


class _Integrity:
    def __init__(
        self,
        *,
        deny: set[UUID] | None = None,
        exc_for: dict[UUID, BaseException] | None = None,
    ) -> None:
        self.deny = deny or set()
        self.exc_for = exc_for or {}
        self.calls: list[tuple[UUID, UUID]] = []

    def validate_learner_subject(self, *, tenant_id, learner_principal_id):
        self.calls.append((tenant_id, learner_principal_id))
        if learner_principal_id in self.exc_for:
            raise self.exc_for[learner_principal_id]
        if learner_principal_id in self.deny:
            raise ParentLearnerAccessContractError(
                "Parent Learner Access provider returned an unknown learner"
            )


def _service(*, classification=None, authorization=None, reader=None, integrity=None):
    return CurrentParentLearnerAccessService(
        classification=classification or _HumanGate(),
        authorization=authorization or _Auth(),
        reader=reader or _RecordingReader(),
        integrity=integrity or _Integrity(),
    )


class TestDevelopmentAccessMatrix:
    def test_matching_tenant_and_adult_returns_synthetic_set(self) -> None:
        reader = development_parent_learner_access_reader()
        items = reader.list_current_authorized_learners(
            SYNTHETIC_TENANT_ID, PARENT_OS_HUMAN_ADULT_A_ID
        )
        assert items == (
            AuthorizedLearnerAccess(learner_principal_id=LEARNER_CHILD_1_ID),
            AuthorizedLearnerAccess(learner_principal_id=LEARNER_CHILD_2_ID),
        )
        assert {field.name for field in items[0].__dataclass_fields__.values()} == {
            "learner_principal_id"
        }
        adult_b = reader.list_current_authorized_learners(
            SYNTHETIC_TENANT_ID, PARENT_OS_HUMAN_ADULT_B_ID
        )
        assert adult_b == (
            AuthorizedLearnerAccess(learner_principal_id=LEARNER_CHILD_3_ID),
        )

    def test_wrong_adult_returns_empty_not_failure(self) -> None:
        reader = development_parent_learner_access_reader()
        assert reader.list_current_authorized_learners(SYNTHETIC_TENANT_ID, uuid4()) == ()

    def test_wrong_tenant_returns_empty_not_failure(self) -> None:
        reader = development_parent_learner_access_reader()
        assert (
            reader.list_current_authorized_learners(
                uuid4(), PARENT_OS_HUMAN_ADULT_A_ID
            )
            == ()
        )

    def test_development_permit_rejects_wildcard_and_unknown(self) -> None:
        permit = DevelopmentParentIntelligencePermit()
        permit.authorize(
            tenant_id=SYNTHETIC_TENANT_ID,
            principal_id=PARENT_OS_HUMAN_ADULT_A_ID,
            capability=PARENT_INTELLIGENCE_READ,
        )
        with pytest.raises(ParentIntelligenceCapabilityForbidden):
            permit.authorize(
                tenant_id=SYNTHETIC_TENANT_ID,
                principal_id=PARENT_OS_HUMAN_ADULT_A_ID,
                capability="parent.intelligence.*",
            )
        with pytest.raises(ParentIntelligenceCapabilityForbidden):
            permit.authorize(
                tenant_id=SYNTHETIC_TENANT_ID,
                principal_id=PARENT_OS_HUMAN_ADULT_A_ID,
                capability="parent.intelligence.write",
            )


class TestCompositionOrder:
    def test_human_capability_and_access_return_authorized_learners(self) -> None:
        tenant_id = uuid4()
        adult_id = uuid4()
        learner_id = uuid4()
        expected = (AuthorizedLearnerAccess(learner_principal_id=learner_id),)
        reader = _RecordingReader(expected)
        classification = _HumanGate()
        authorization = _Auth()
        integrity = _Integrity()
        items = _service(
            classification=classification,
            authorization=authorization,
            reader=reader,
            integrity=integrity,
        ).current_authorized_learners(tenant_id, adult_id)
        assert items == expected
        assert classification.calls == [adult_id]
        assert authorization.calls == [
            (tenant_id, adult_id, PARENT_INTELLIGENCE_READ)
        ]
        assert reader.calls == [(tenant_id, adult_id)]
        assert integrity.calls == [(tenant_id, learner_id)]

    def test_workload_fails_before_access_is_exposed(self) -> None:
        reader = _RecordingReader(
            (AuthorizedLearnerAccess(learner_principal_id=uuid4()),)
        )
        authorization = _Auth()
        with pytest.raises(UnauthorizedError):
            _service(
                classification=_HumanGate(kind=PrincipalKind.WORKLOAD),
                authorization=authorization,
                reader=reader,
            ).current_authorized_learners(uuid4(), uuid4())
        assert authorization.calls == []
        assert reader.calls == []

    def test_capability_deny_prevents_access_return(self) -> None:
        reader = _RecordingReader(
            (AuthorizedLearnerAccess(learner_principal_id=uuid4()),)
        )
        with pytest.raises(ParentIntelligenceCapabilityForbidden):
            _service(
                authorization=_Auth(allow=False),
                reader=reader,
            ).current_authorized_learners(uuid4(), uuid4())
        assert reader.calls == []

    def test_successful_empty_set_is_distinct_from_unavailable(self) -> None:
        reader = _RecordingReader(())
        items = _service(reader=reader).current_authorized_learners(uuid4(), uuid4())
        assert items == ()
        assert reader.calls

    def test_access_revoked_between_calls_is_not_cached(self) -> None:
        learner_id = uuid4()
        first = (AuthorizedLearnerAccess(learner_principal_id=learner_id),)
        reader = _RecordingReader(first)
        service = _service(reader=reader)
        tenant_id = uuid4()
        adult_id = uuid4()
        assert service.current_authorized_learners(tenant_id, adult_id) == first
        reader.result = ()
        assert service.current_authorized_learners(tenant_id, adult_id) == ()
        assert reader.calls == [(tenant_id, adult_id), (tenant_id, adult_id)]

    def test_synthetic_reader_revocation_between_calls(self) -> None:
        reader = development_parent_learner_access_reader()
        tenant_id = SYNTHETIC_TENANT_ID
        adult_id = PARENT_OS_HUMAN_ADULT_A_ID
        service = _service(reader=reader)
        first = service.current_authorized_learners(tenant_id, adult_id)
        assert {item.learner_principal_id for item in first} == {
            LEARNER_CHILD_1_ID,
            LEARNER_CHILD_2_ID,
        }
        reader.set_current_authorized_learners(adult_id, ())
        assert service.current_authorized_learners(tenant_id, adult_id) == ()


class TestFailClosedProvider:
    def test_unconfigured_reader_fails_closed_not_empty_success(self) -> None:
        with pytest.raises(ParentLearnerAccessUnavailable):
            _service(
                reader=UnconfiguredSchoolContextParentLearnerAccessReader()
            ).current_authorized_learners(uuid4(), uuid4())

    def test_provider_exception_becomes_unavailable(self) -> None:
        with pytest.raises(ParentLearnerAccessUnavailable) as excinfo:
            _service(
                reader=_RecordingReader(exc=RuntimeError("erp secret boom"))
            ).current_authorized_learners(uuid4(), uuid4())
        assert "erp secret" not in str(excinfo.value)

    def test_none_provider_response_fails_contract(self) -> None:
        with pytest.raises(ParentLearnerAccessContractError):
            _service(reader=_RecordingReader(None)).current_authorized_learners(
                uuid4(), uuid4()
            )

    def test_non_iterable_provider_response_fails_contract(self) -> None:
        with pytest.raises(ParentLearnerAccessContractError):
            _service(reader=_RecordingReader(object())).current_authorized_learners(
                uuid4(), uuid4()
            )

    def test_malformed_learner_uuid_fails_contract(self) -> None:
        @dataclass
        class _Bad:
            learner_principal_id: str

        with pytest.raises(ParentLearnerAccessContractError):
            _service(
                reader=_RecordingReader([_Bad(learner_principal_id="not-a-uuid")])
            ).current_authorized_learners(uuid4(), uuid4())

    def test_duplicate_learner_id_fails_contract(self) -> None:
        learner_id = uuid4()
        with pytest.raises(ParentLearnerAccessContractError):
            _service(
                reader=_RecordingReader(
                    (
                        AuthorizedLearnerAccess(learner_principal_id=learner_id),
                        AuthorizedLearnerAccess(learner_principal_id=learner_id),
                    )
                )
            ).current_authorized_learners(uuid4(), uuid4())

    def test_one_invalid_learner_invalidates_entire_set(self) -> None:
        valid = uuid4()
        invalid = uuid4()
        with pytest.raises(ParentLearnerAccessContractError):
            _service(
                reader=_RecordingReader(
                    (
                        AuthorizedLearnerAccess(learner_principal_id=valid),
                        AuthorizedLearnerAccess(learner_principal_id=invalid),
                    )
                ),
                integrity=_Integrity(deny={invalid}),
            ).current_authorized_learners(uuid4(), uuid4())

    def test_provider_order_is_normalized_deterministically(self) -> None:
        first = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        second = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        items = _service(
            reader=_RecordingReader(
                (
                    AuthorizedLearnerAccess(learner_principal_id=second),
                    AuthorizedLearnerAccess(learner_principal_id=first),
                )
            )
        ).current_authorized_learners(uuid4(), uuid4())
        assert items == (
            AuthorizedLearnerAccess(learner_principal_id=first),
            AuthorizedLearnerAccess(learner_principal_id=second),
        )

    def test_access_result_contains_no_presentation_metadata(self) -> None:
        learner_id = uuid4()
        items = _service(
            reader=_RecordingReader(
                (AuthorizedLearnerAccess(learner_principal_id=learner_id),)
            )
        ).current_authorized_learners(uuid4(), uuid4())
        assert items[0].learner_principal_id == learner_id
        assert not hasattr(items[0], "presentation_label")
        assert not hasattr(items[0], "display_name")
        assert not hasattr(items[0], "child_name")


class TestLearnerSubjectIntegrity:
    def test_unknown_learner_fails_contract(
        self, runtime_engine: Engine
    ) -> None:
        unknown = uuid4()
        with pytest.raises(ParentLearnerAccessContractError):
            _service(
                reader=_RecordingReader(
                    (AuthorizedLearnerAccess(learner_principal_id=unknown),)
                ),
                integrity=SecurityAuthorityLearnerPrincipalIntegrity(runtime_engine),
            ).current_authorized_learners(uuid4(), uuid4())

    def test_workload_learner_fails_contract(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid4()
        learner_id = uuid4()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=learner_id,
            capabilities=(),
            principal_kind=PrincipalKind.WORKLOAD,
        )
        with pytest.raises(ParentLearnerAccessContractError):
            _service(
                reader=_RecordingReader(
                    (AuthorizedLearnerAccess(learner_principal_id=learner_id),)
                ),
                integrity=SecurityAuthorityLearnerPrincipalIntegrity(runtime_engine),
            ).current_authorized_learners(tenant_id, uuid4())

    def test_null_kind_learner_fails_contract(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid4()
        learner_id = uuid4()
        seed_tenant(bootstrap_engine, tenant_id)
        seed_principal(bootstrap_engine, learner_id, principal_kind=None)
        seed_membership(
            bootstrap_engine, tenant_id=tenant_id, principal_id=learner_id
        )
        with pytest.raises(ParentLearnerAccessContractError):
            _service(
                reader=_RecordingReader(
                    (AuthorizedLearnerAccess(learner_principal_id=learner_id),)
                ),
                integrity=SecurityAuthorityLearnerPrincipalIntegrity(runtime_engine),
            ).current_authorized_learners(tenant_id, uuid4())

    def test_learner_without_requested_tenant_membership_fails_contract(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid4()
        learner_id = uuid4()
        seed_tenant(bootstrap_engine, tenant_id)
        seed_principal(
            bootstrap_engine, learner_id, principal_kind=PrincipalKind.HUMAN
        )
        with pytest.raises(ParentLearnerAccessContractError):
            _service(
                reader=_RecordingReader(
                    (AuthorizedLearnerAccess(learner_principal_id=learner_id),)
                ),
                integrity=SecurityAuthorityLearnerPrincipalIntegrity(runtime_engine),
            ).current_authorized_learners(tenant_id, uuid4())

    def test_cross_tenant_learner_fails_contract(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        requested_tenant = uuid4()
        other_tenant = uuid4()
        learner_id = uuid4()
        seed_tenant(bootstrap_engine, requested_tenant)
        seed_active_authority(
            bootstrap_engine,
            tenant_id=other_tenant,
            principal_id=learner_id,
            capabilities=(),
            principal_kind=PrincipalKind.HUMAN,
        )
        with pytest.raises(ParentLearnerAccessContractError):
            _service(
                reader=_RecordingReader(
                    (AuthorizedLearnerAccess(learner_principal_id=learner_id),)
                ),
                integrity=SecurityAuthorityLearnerPrincipalIntegrity(runtime_engine),
            ).current_authorized_learners(requested_tenant, uuid4())

    def test_expired_membership_fails_contract(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid4()
        learner_id = uuid4()
        seed_tenant(bootstrap_engine, tenant_id)
        seed_principal(
            bootstrap_engine, learner_id, principal_kind=PrincipalKind.HUMAN
        )
        seed_membership(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=learner_id,
            status=MembershipStatus.ACTIVE,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        with pytest.raises(ParentLearnerAccessContractError):
            _service(
                reader=_RecordingReader(
                    (AuthorizedLearnerAccess(learner_principal_id=learner_id),)
                ),
                integrity=SecurityAuthorityLearnerPrincipalIntegrity(runtime_engine),
            ).current_authorized_learners(tenant_id, uuid4())

    def test_suspended_human_learner_is_accepted_as_data_subject(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid4()
        learner_id = uuid4()
        seed_tenant(bootstrap_engine, tenant_id)
        seed_principal(
            bootstrap_engine,
            learner_id,
            principal_kind=PrincipalKind.HUMAN,
            status=PrincipalStatus.SUSPENDED,
        )
        seed_membership(
            bootstrap_engine, tenant_id=tenant_id, principal_id=learner_id
        )
        items = _service(
            reader=_RecordingReader(
                (AuthorizedLearnerAccess(learner_principal_id=learner_id),)
            ),
            integrity=SecurityAuthorityLearnerPrincipalIntegrity(runtime_engine),
        ).current_authorized_learners(tenant_id, uuid4())
        assert items == (
            AuthorizedLearnerAccess(learner_principal_id=learner_id),
        )

    def test_disabled_human_learner_is_accepted_as_data_subject(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid4()
        learner_id = uuid4()
        seed_tenant(bootstrap_engine, tenant_id)
        seed_principal(
            bootstrap_engine,
            learner_id,
            principal_kind=PrincipalKind.HUMAN,
            status=PrincipalStatus.DISABLED,
        )
        seed_membership(
            bootstrap_engine, tenant_id=tenant_id, principal_id=learner_id
        )
        items = _service(
            reader=_RecordingReader(
                (AuthorizedLearnerAccess(learner_principal_id=learner_id),)
            ),
            integrity=SecurityAuthorityLearnerPrincipalIntegrity(runtime_engine),
        ).current_authorized_learners(tenant_id, uuid4())
        assert items == (
            AuthorizedLearnerAccess(learner_principal_id=learner_id),
        )
