"""CI03-G1 classification fault injection (isolated from PG18 integrated matrix)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.schema import build_preparation_content_schema_registry
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.api.app import (
    _UnavailablePrincipalClassificationAuthority,
    create_app,
)
from tests.domains.content.api.helpers_ci03_g1_i01 import (
    problem_code,
    rsa_material,
    seed_owner_bundle,
    seed_worksheet_http_ids,
)
from tests.fakes import (
    AllowClassroomAssessmentAuthorization,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    IDEMPOTENCY_RETENTION,
    StubSecurityContextResolver,
)

pytestmark = pytest.mark.ci03_g1_i01_fault


@pytest.fixture(scope="module")
def _rsa():
    return rsa_material()


class TestWorksheetPrivateReadFaultInjection:
    def test_classification_unavailable_503(
        self, runtime_engine, bootstrap_engine, _rsa
    ) -> None:
        private_key, public_jwk = _rsa
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        _, vid, _, content_id = seed_worksheet_http_ids(
            runtime_engine,
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            public_jwk=public_jwk,
            private_key=private_key,
        )
        app = create_app(
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
            assessment_authorization=AllowClassroomAssessmentAuthorization(),
            request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
            security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
            content_types=StaticContentTypeCatalog({"worksheet"}),
            cursor_signing_key=b"ci03-fault",
            schema_registry=build_preparation_content_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
            review_authorization=AllowReviewAuthorization(),
            review_comment_policy=AllowReviewCommentPolicy(),
            publication_authorization=AllowPublicationAuthorization(),
            publication_governance=AllowPublicationGovernance(),
            asset_reference_validation=AllowAssetReferenceValidation(),
            asset_current_governance=AllowAssetCurrentGovernance(),
            principal_classification_authority=_UnavailablePrincipalClassificationAuthority(),
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"/api/v1/contents/{content_id}/versions/{vid.value}",
            headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
        )
        assert response.status_code == 503
        assert problem_code(response) == "authorization_unavailable"