"""CI03-G1 candidate-only service unit tests (not shared baseline matrix)."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from aieos.domains.content.application.errors import ContentVersionNotFound
from aieos.domains.content.application.http_append import GetContentVersionService
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.security.authorization import CurrentPrincipalClassificationAuthority
from aieos.platform.security.context import AuthorizationUnavailableError
from tests.domains.content.api.helpers_ci03_g1_i01 import (
    rsa_material,
    seed_owner_bundle,
    seed_worksheet_http_ids,
)

pytestmark = pytest.mark.ci03_g1_i01_unit


@pytest.fixture(scope="module")
def _rsa():
    return rsa_material()


class TestGetContentVersionServiceUnit:
    def test_worksheet_gate_unavailable_classification(
        self, runtime_engine, bootstrap_engine, _rsa
    ) -> None:
        service = GetContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            principal_classification=None,
        )
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        cid, vid, _, _ = seed_worksheet_http_ids(
            runtime_engine,
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        with pytest.raises(AuthorizationUnavailableError):
            service.get(tenant_id, owner_id, cid, vid)

    def test_mismatched_version_tenant_content_identity(
        self, runtime_engine, bootstrap_engine, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        cid, vid, _, _ = seed_worksheet_http_ids(
            runtime_engine,
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        service = GetContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            principal_classification=CurrentPrincipalClassificationAuthority(
                runtime_engine
            ),
        )
        with pytest.raises(ContentVersionNotFound):
            service.get(
                tenant_id,
                owner_id,
                cid,
                ContentVersionId(uuid.uuid7()),
            )
        other_tenant = uuid.uuid7()
        with pytest.raises(ContentVersionNotFound):
            service.get(other_tenant, owner_id, cid, vid)
        with pytest.raises(ContentVersionNotFound):
            service.get(
                tenant_id,
                owner_id,
                ContentId(uuid.uuid7()),
                vid,
            )