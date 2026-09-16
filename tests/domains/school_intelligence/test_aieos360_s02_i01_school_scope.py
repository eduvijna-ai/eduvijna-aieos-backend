"""AIEOS360-S02-I01 — current Principal school-scope application contract."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from aieos.development.principal_school_context import (
    CLASS_REF_6A,
    CLASS_REF_6B,
    PRINCIPAL_OS_HUMAN_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
    development_principal_school_scope_reader,
)
from aieos.domains.school_intelligence.application.errors import (
    SchoolContextContractError,
    SchoolContextUnavailable,
    SchoolIntelligenceCapabilityForbidden,
)
from aieos.domains.school_intelligence.application.ports import SCHOOL_INTELLIGENCE_READ
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
    CurrentPrincipalSchoolScopeService,
    UnconfiguredSchoolContextPrincipalScopeReader,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from aieos.platform.security.context import UnauthorizedError

pytestmark = pytest.mark.aieos360_s02_i01


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

    def list_current_authorized_classes(self, tenant_id, principal_id):
        self.calls.append((tenant_id, principal_id))
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
            raise SchoolIntelligenceCapabilityForbidden(
                "school intelligence capability denied"
            )


def _service(*, classification=None, authorization=None, reader=None):
    return CurrentPrincipalSchoolScopeService(
        classification=classification or _HumanGate(),
        authorization=authorization or _Auth(),
        reader=reader or _RecordingReader(),
    )


class TestDevelopmentScopeMatrix:
    def test_matching_tenant_and_principal_returns_synthetic_set(self) -> None:
        reader = development_principal_school_scope_reader()
        items = reader.list_current_authorized_classes(
            SYNTHETIC_TENANT_ID, PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
        )
        assert items == (
            AuthorizedSchoolClassRef(class_ref=CLASS_REF_6A, display_label="Grade 6A"),
            AuthorizedSchoolClassRef(class_ref=CLASS_REF_6B, display_label="Grade 6B"),
        )
        assert reader.calls == [
            (SYNTHETIC_TENANT_ID, PRINCIPAL_OS_HUMAN_PRINCIPAL_ID)
        ]

    def test_wrong_principal_returns_empty_not_failure(self) -> None:
        reader = development_principal_school_scope_reader()
        assert reader.list_current_authorized_classes(
            SYNTHETIC_TENANT_ID, uuid4()
        ) == ()

    def test_wrong_tenant_returns_empty_not_failure(self) -> None:
        reader = development_principal_school_scope_reader()
        assert reader.list_current_authorized_classes(
            uuid4(), PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
        ) == ()


class TestCompositionOrder:
    def test_human_capability_and_scope_return_authorized_classes(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        expected = (
            AuthorizedSchoolClassRef(class_ref="class-6a", display_label="Grade 6A"),
        )
        reader = _RecordingReader(expected)
        classification = _HumanGate()
        authorization = _Auth()
        items = _service(
            classification=classification,
            authorization=authorization,
            reader=reader,
        ).current_authorized_classes(tenant_id, principal_id)
        assert items == expected
        assert classification.calls == [principal_id]
        assert authorization.calls == [
            (tenant_id, principal_id, SCHOOL_INTELLIGENCE_READ)
        ]
        assert reader.calls == [(tenant_id, principal_id)]

    def test_workload_fails_before_scope_is_exposed(self) -> None:
        reader = _RecordingReader(
            (AuthorizedSchoolClassRef(class_ref="class-6a", display_label="Grade 6A"),)
        )
        authorization = _Auth()
        with pytest.raises(UnauthorizedError):
            _service(
                classification=_HumanGate(kind=PrincipalKind.WORKLOAD),
                authorization=authorization,
                reader=reader,
            ).current_authorized_classes(uuid4(), uuid4())
        assert authorization.calls == []
        assert reader.calls == []

    def test_capability_deny_prevents_scope_return(self) -> None:
        reader = _RecordingReader(
            (AuthorizedSchoolClassRef(class_ref="class-6a", display_label="Grade 6A"),)
        )
        with pytest.raises(SchoolIntelligenceCapabilityForbidden):
            _service(
                authorization=_Auth(allow=False),
                reader=reader,
            ).current_authorized_classes(uuid4(), uuid4())
        assert reader.calls == []

    def test_current_scope_change_is_reflected_with_no_cache(self) -> None:
        first = (
            AuthorizedSchoolClassRef(class_ref="class-6a", display_label="Grade 6A"),
        )
        second = (
            AuthorizedSchoolClassRef(class_ref="class-6b", display_label="Grade 6B"),
        )
        reader = _RecordingReader(first)
        service = _service(reader=reader)
        tenant_id = uuid4()
        principal_id = uuid4()
        assert service.current_authorized_classes(tenant_id, principal_id) == first
        reader.result = second
        assert service.current_authorized_classes(tenant_id, principal_id) == second
        assert reader.calls == [
            (tenant_id, principal_id),
            (tenant_id, principal_id),
        ]

    def test_revoked_empty_scope_is_distinct_from_unavailable(self) -> None:
        reader = _RecordingReader(())
        items = _service(reader=reader).current_authorized_classes(uuid4(), uuid4())
        assert items == ()
        assert reader.calls


class TestFailClosedProvider:
    def test_unconfigured_reader_fails_closed_not_empty_success(self) -> None:
        with pytest.raises(SchoolContextUnavailable):
            _service(
                reader=UnconfiguredSchoolContextPrincipalScopeReader()
            ).current_authorized_classes(uuid4(), uuid4())

    def test_provider_exception_becomes_unavailable(self) -> None:
        with pytest.raises(SchoolContextUnavailable):
            _service(
                reader=_RecordingReader(exc=RuntimeError("erp secret boom"))
            ).current_authorized_classes(uuid4(), uuid4())

    def test_none_provider_response_fails_contract(self) -> None:
        with pytest.raises(SchoolContextContractError):
            _service(reader=_RecordingReader(None)).current_authorized_classes(
                uuid4(), uuid4()
            )

    def test_malformed_item_fails_contract(self) -> None:
        with pytest.raises(SchoolContextContractError):
            _service(reader=_RecordingReader([object()])).current_authorized_classes(
                uuid4(), uuid4()
            )

    def test_duplicate_class_ref_fails_contract(self) -> None:
        with pytest.raises(SchoolContextContractError):
            _service(
                reader=_RecordingReader(
                    (
                        AuthorizedSchoolClassRef(
                            class_ref="class-6a", display_label="Grade 6A"
                        ),
                        AuthorizedSchoolClassRef(
                            class_ref="class-6a", display_label="Grade 6A copy"
                        ),
                    )
                )
            ).current_authorized_classes(uuid4(), uuid4())

    def test_blank_class_ref_fails_contract(self) -> None:
        with pytest.raises(SchoolContextContractError):
            _service(
                reader=_RecordingReader(
                    (AuthorizedSchoolClassRef(class_ref="  ", display_label="Grade 6A"),)
                )
            ).current_authorized_classes(uuid4(), uuid4())

    def test_blank_display_label_fails_contract(self) -> None:
        with pytest.raises(SchoolContextContractError):
            _service(
                reader=_RecordingReader(
                    (AuthorizedSchoolClassRef(class_ref="class-6a", display_label="  "),)
                )
            ).current_authorized_classes(uuid4(), uuid4())

    def test_structurally_invalid_shape_fails_contract(self) -> None:
        @dataclass
        class _Bad:
            class_ref: int
            display_label: str

        with pytest.raises(SchoolContextContractError):
            _service(
                reader=_RecordingReader([_Bad(class_ref=1, display_label="Grade 6A")])
            ).current_authorized_classes(uuid4(), uuid4())
