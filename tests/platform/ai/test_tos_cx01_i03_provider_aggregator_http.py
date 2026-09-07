"""TOS-CX01-I03 read-only Provider Aggregator HTTP proofs.

No PostgreSQL. Injected composition only.
"""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.platform.ai.composition import (
    PROVIDER_MODE_DEVELOPMENT_FAKE,
    PROVIDER_MODE_REAL,
    build_provider_runtime_projection,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.api.app import create_app
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
    make_test_schema_registry,
)

CURSOR_KEY = b"tos-cx01-i03-provider-aggregator"
PATH = "/api/v1/platform/ai/providers"
SECRET_NEEDLES = (
    "api_key",
    "AIEOS_GROQ_API_KEY",
    "AIEOS_OPENAI_API_KEY",
    "gsk_",
    "offline-dummy-key",
    "Bearer",
    "authorization",
)


class _UnusedUow:
    def __call__(self, tenant_id):
        raise AssertionError("Provider Aggregator must not open UoW")


def _app(
    *,
    provider_id: str,
    model_id: str,
    environ: dict[str, str],
    unauthenticated: bool = False,
):
    tenant_id = uuid4()
    principal_id = uuid4()
    projection = build_provider_runtime_projection(
        provider_id=provider_id,
        model_id=model_id,
        gateway_composed=True,
        environ=environ,
    )
    return create_app(
        uow_factory=_UnusedUow(),
        teaching_uow_factory=_UnusedUow(),
        assessment_uow_factory=_UnusedUow(),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=FixedPrincipalAuthenticator(
            principal_id, unauthenticated=unauthenticated
        ),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=timedelta(hours=24),
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        model_gateway=FakeStructuredModelGateway(),
        ai_provider_id=provider_id,
        ai_model_id=model_id,
        provider_runtime=projection,
    ), tenant_id


def _get(app, tenant_id):
    client = TestClient(app, raise_server_exceptions=False)
    return client.get(PATH, headers={"X-AIEOS-Tenant-ID": str(tenant_id)})


def _assert_safe(body: dict[str, object]) -> None:
    blob = json.dumps(body)
    lowered = blob.lower()
    for needle in SECRET_NEEDLES:
        assert needle.lower() not in lowered
    assert "token" not in body
    assert "secret" not in body
    for provider in body.get("providers") or []:
        assert "api_key" not in provider
        assert "key" not in provider
        assert "token" not in provider


def test_requires_trusted_request_context() -> None:
    app, tenant_id = _app(
        provider_id="fake",
        model_id="fake-model",
        environ={},
        unauthenticated=True,
    )
    response = _get(app, tenant_id)
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "unauthenticated"
    _assert_safe(body)


def test_groq_active_projection() -> None:
    app, tenant_id = _app(
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        environ={"AIEOS_GROQ_API_KEY": "offline-dummy-key"},
    )
    response = _get(app, tenant_id)
    assert response.status_code == 200
    body = response.json()
    assert body["active_provider_id"] == "groq"
    assert body["active_model_id"] == "openai/gpt-oss-120b"
    assert body["mode"] == PROVIDER_MODE_REAL
    providers = {item["provider_id"]: item for item in body["providers"]}
    assert providers["groq"]["active"] is True
    assert providers["groq"]["configured"] is True
    assert providers["groq"]["model_id"] == "openai/gpt-oss-120b"
    assert providers["openai"]["active"] is False
    assert providers["fake"]["active"] is False
    assert providers["fake"]["development_only"] is True
    routes = {item["capability_id"]: item for item in body["capability_routes"]}
    assert routes["education.generate_preparation_kit"]["provider_id"] == "groq"
    assert routes["teacher_os.assistant_respond"]["model_id"] == "openai/gpt-oss-120b"
    assert routes["education.generate_worksheet"]["display_name"] == "Worksheet Generation"
    _assert_safe(body)


def test_openai_active_projection() -> None:
    app, tenant_id = _app(
        provider_id="openai",
        model_id="gpt-5.6-terra",
        environ={"AIEOS_OPENAI_API_KEY": "offline-dummy-key"},
    )
    response = _get(app, tenant_id)
    body = response.json()
    assert response.status_code == 200
    assert body["active_provider_id"] == "openai"
    assert body["mode"] == PROVIDER_MODE_REAL
    providers = {item["provider_id"]: item for item in body["providers"]}
    assert providers["openai"]["active"] is True
    assert providers["groq"]["active"] is False
    _assert_safe(body)


def test_fake_active_projection() -> None:
    app, tenant_id = _app(provider_id="fake", model_id="fake-model", environ={})
    response = _get(app, tenant_id)
    body = response.json()
    assert response.status_code == 200
    assert body["active_provider_id"] == "fake"
    assert body["mode"] == PROVIDER_MODE_DEVELOPMENT_FAKE
    providers = {item["provider_id"]: item for item in body["providers"]}
    assert providers["fake"]["active"] is True
    assert providers["groq"]["configured"] is False
    _assert_safe(body)


def test_no_mutation_endpoint() -> None:
    app, tenant_id = _app(provider_id="fake", model_id="fake-model", environ={})
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-AIEOS-Tenant-ID": str(tenant_id)}
    assert client.post(PATH, headers=headers).status_code == 405
    assert client.put(PATH, headers=headers).status_code == 405
    assert client.patch(PATH, headers=headers).status_code == 405
    assert client.delete(PATH, headers=headers).status_code == 405
