"""OpenAPI contract for PK-V1-HINT-T0-B guarded hint HTTP."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.api.app import create_app
from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from tests.dbutil import REPO_ROOT
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowClassroomAssessmentAuthorization,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
)

pytestmark = pytest.mark.aieos_v1_hint_t1

SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("OpenAPI export must not touch persistence")


def _schema() -> dict:
    app = create_app(
        uow_factory=_UnusedUowFactory(),
        teaching_uow_factory=_UnusedUowFactory(),
        assessment_uow_factory=_UnusedUowFactory(),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=FixedPrincipalAuthenticator(uuid4()),
        security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"hint-t0-b-openapi-export-key",
        schema_registry=ContentSchemaRegistry(),
        idempotency_retention=timedelta(hours=24),
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )
    return build_openapi(app)


def test_openapi_declares_t0_b_operations() -> None:
    schema = _schema()
    paths = schema["paths"]
    hint_append = (
        "/api/v1/contents/{content_id}/worksheet-learner-hint-versions"
    )
    attest = (
        "/api/v1/contents/{content_id}/versions/{version_id}"
        "/actions/record-worksheet-hint-attestation"
    )
    assert hint_append in paths
    assert attest in paths
    assert paths[hint_append]["post"]["operationId"] == "content_worksheet_hint_version_append"
    assert (
        paths[attest]["post"]["operationId"]
        == "content_worksheet_hint_attestation_record"
    )
    dumped = canonical_openapi_json(schema)
    assert "WorksheetLearnerHintVersionAppendRequest" in dumped
    assert "WorksheetLearnerHintAttestationRecordRequest" in dumped
    assert "WorksheetLearnerHintAttestationRecordResponse" in dumped


def test_openapi_snapshot_matches_checked_in_contract() -> None:
    generated = canonical_openapi_json(_schema())
    assert SNAPSHOT.is_file()
    assert SNAPSHOT.read_text(encoding="utf-8") == generated


def _header_param(operation: dict, name: str) -> dict | None:
    for param in operation.get("parameters", []):
        if param.get("in") == "header" and param.get("name") == name:
            return param
    return None


def test_openapi_t0_b_mutation_headers_required_and_success_metadata() -> None:
    schema = _schema()
    paths = schema["paths"]
    append_op = paths["/api/v1/contents/{content_id}/worksheet-learner-hint-versions"][
        "post"
    ]
    attest_op = paths[
        "/api/v1/contents/{content_id}/versions/{version_id}"
        "/actions/record-worksheet-hint-attestation"
    ]["post"]
    for op in (append_op, attest_op):
        idem = _header_param(op, "Idempotency-Key")
        if_match = _header_param(op, "If-Match")
        assert idem is not None and idem.get("required") is True
        assert if_match is not None and if_match.get("required") is True
    append_201 = append_op["responses"]["201"]["headers"]
    assert "ETag" in append_201
    assert "Location" in append_201
    attest_201 = attest_op["responses"]["201"]["headers"]
    assert "ETag" in attest_201