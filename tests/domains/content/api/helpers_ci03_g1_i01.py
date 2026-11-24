"""CI03-G1-I01 shared PG18 harness: JWT + kernel tenant authority + real classification."""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import text

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt import PyJWK
from sqlalchemy.engine import Engine

from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.ports import (
    CONTENT_PUBLISH,
    CONTENT_REVIEW_DECIDE,
    CONTENT_VERSION_CREATE,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.parent_intelligence.application.ports import PARENT_INTELLIGENCE_READ
from aieos.domains.school_intelligence.application.ports import SCHOOL_INTELLIGENCE_READ
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.schema import (
    WORKSHEET_CONTENT_TYPE,
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
    build_preparation_content_schema_registry,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.runtime.activation import (
    MutationActivationDecision,
    MutationActivationStatus,
    install_mutation_activation_interlock,
)
from aieos.platform.security.auth_config import (
    AIEOS_PRINCIPAL_ID_CLAIM,
    AuthRuntimeConfig,
)
from aieos.platform.security.authority import CurrentAuthoritySecurityContextResolver
from aieos.platform.security.authorization import (
    AIEOS_CONTENT_CAPABILITIES,
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
    KernelAIGenerationAuthorization,
    KernelCurrentTenantAccessAuthority,
    KernelPublicationAuthorization,
    KernelReviewAuthorization,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from aieos.platform.security.jwt_bearer import JwtBearerRequestIdentityAuthenticator
from tests.domains.assessment.helpers_dev08_i02 import MutableSchoolContextClassReader
from tests.fakes import (
    AllowClassroomAssessmentAuthorization,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationGovernance,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    make_test_schema_registry,
)
from tests.platform.security.authorization.helpers import seed_active_authority

ISSUER = "https://issuer.ci03-g1.example.test/"
AUDIENCE = "aieos-api"
JWKS_URI = "https://issuer.ci03-g1.example.test/.well-known/jwks.json"
KID = "ci03-g1-i01-test-key"
CURSOR_KEY = b"ci03-g1-i01-real-auth-cursor"
CLASS_REF = "class-ci03-g1"

PRIVATE_FIELD_NAMES = frozenset({"answer", "explanation", "learner_hint"})


class _EnabledMutationGate:
    def check(self) -> MutationActivationDecision:
        return MutationActivationDecision(True, MutationActivationStatus.ENABLED)


class _StaticJwkClient:
    def __init__(self, public_jwk: dict[str, Any]) -> None:
        self._key = PyJWK.from_dict(public_jwk)

    def get_signing_key_from_jwt(self, token: str) -> PyJWK:
        return self._key


def rsa_material() -> tuple[Any, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def _b64u(value: int, *, length: int) -> str:
        raw = value.to_bytes(length, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    n_len = (public_numbers.n.bit_length() + 7) // 8
    public_jwk = {
        "kty": "RSA",
        "kid": KID,
        "use": "sig",
        "alg": "RS256",
        "n": _b64u(public_numbers.n, length=n_len),
        "e": _b64u(public_numbers.e, length=3),
    }
    return private_key, public_jwk


def mint_token(private_key: Any, *, principal_id: UUID) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "ci03-subject",
        "client_id": "aieos-client",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 3600,
        AIEOS_PRINCIPAL_ID_CLAIM: str(principal_id),
    }
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": KID, "typ": "at+jwt"},
    )


def auth_headers(tenant_id: UUID, token: str | None, **extra: str) -> dict[str, str]:
    headers = {"X-AIEOS-Tenant-ID": str(tenant_id), **extra}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if "Idempotency-Key" not in headers:
        headers["Idempotency-Key"] = f"ci03-{uuid.uuid7()}"
    return headers


def private_field_names(payload: object) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    questions = payload.get("questions")
    if not isinstance(questions, list):
        return set()
    found: set[str] = set()
    for item in questions:
        if not isinstance(item, dict):
            continue
        found.update(k for k in PRIVATE_FIELD_NAMES if k in item)
    return found


def problem_code(response) -> str | None:
    if not response.headers.get("content-type", "").startswith("application/problem+json"):
        return None
    try:
        return response.json().get("code")
    except Exception:  # noqa: BLE001
        return None


def seed_human_member(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    capabilities: tuple[str, ...] = (),
    principal_kind: PrincipalKind = PrincipalKind.HUMAN,
) -> None:
    seed_active_authority(
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        principal_kind=principal_kind,
        capabilities=capabilities,
    )


def build_real_auth_app(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    owner_principal_id: UUID,
    public_jwk: dict[str, Any],
    private_key: Any,
    include_worksheet: bool = True,
) -> tuple[FastAPI, Any]:
    kernel = AuthorizationKernel(runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES)
    authority = KernelCurrentTenantAccessAuthority(kernel)
    authenticator = JwtBearerRequestIdentityAuthenticator(
        AuthRuntimeConfig(issuer=ISSUER, audience=AUDIENCE, jwks_uri=JWKS_URI),
        jwk_client=_StaticJwkClient(public_jwk),
    )
    content_types = {WORKSHEET_CONTENT_TYPE, "test.generic"} if include_worksheet else {"test.generic"}
    reader = MutableSchoolContextClassReader(
        tenant_id=tenant_id,
        teacher_principal_id=owner_principal_id,
        class_refs=(CLASS_REF,),
    )
    app = create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=authenticator,
        security_resolver=CurrentAuthoritySecurityContextResolver(authority),
        content_types=StaticContentTypeCatalog(content_types),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=(
            build_preparation_content_schema_registry()
            if include_worksheet
            else make_test_schema_registry()
        ),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=KernelReviewAuthorization(kernel),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=KernelPublicationAuthorization(kernel),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        school_context_class_reader=reader,
        principal_classification_authority=CurrentPrincipalClassificationAuthority(
            runtime_engine
        ),
        ai_generation_authorization=KernelAIGenerationAuthorization(kernel),
    )
    install_mutation_activation_interlock(app, _EnabledMutationGate())
    return app, private_key


def client_for(
    app: FastAPI, private_key: Any, *, tenant_id: UUID, principal_id: UUID
) -> TestClient:
    token = mint_token(private_key, principal_id=principal_id)
    client = TestClient(app, raise_server_exceptions=False)

    def _with_auth(headers: dict[str, str]) -> dict[str, str]:
        return auth_headers(tenant_id, token, **headers)

    client._ci03_auth_headers = _with_auth  # type: ignore[attr-defined]
    client._ci03_tenant_id = tenant_id  # type: ignore[attr-defined]
    client._ci03_token = token  # type: ignore[attr-defined]
    return client


def owner_worksheet_fixtures(
    client: TestClient,
    *,
    hint_bearing_v2: bool = False,
    hint_free_v2: bool = False,
) -> tuple[str, str, str]:
    """Returns content_id, v1_version_id, optional_v2_version_id (empty when unused)."""
    tenant_id: UUID = client._ci03_tenant_id  # type: ignore[attr-defined]
    hdr = client._ci03_auth_headers  # type: ignore[attr-defined]
    created = client.post(
        "/api/v1/contents",
        json={
            "content_type": WORKSHEET_CONTENT_TYPE,
            "title": "CI03",
            "description": "",
            "locale": "en-IN",
        },
        headers=hdr({}),
    )
    assert created.status_code == 201
    content_id = created.json()["content_id"]
    etag = created.headers["ETag"]
    v1 = client.post(
        f"/api/v1/contents/{content_id}/versions",
        json={
            "schema_id": WORKSHEET_SCHEMA_ID,
            "schema_version": WORKSHEET_SCHEMA_VERSION,
            "payload": _synthetic_worksheet_v1_payload(),
        },
        headers=hdr({"If-Match": etag}),
    )
    assert v1.status_code == 201
    v1_id = v1.json()["version_id"]
    etag = v1.headers["ETag"]
    v2_id = ""
    if hint_bearing_v2:
        from tests.domains.education.test_aieos_v1_hint_t1 import _worksheet_v2_payload

        hint = client.post(
            f"/api/v1/contents/{content_id}/worksheet-learner-hint-versions",
            json={
                "authoring_class_ref": CLASS_REF,
                "payload": _worksheet_v2_payload(),
                "asset_refs": [],
            },
            headers=hdr({"If-Match": etag}),
        )
        assert hint.status_code == 201
        v2_id = hint.json()["version_id"]
    elif hint_free_v2:
        from aieos.domains.education.schema import WORKSHEET_SCHEMA_VERSION_V2
        from tests.domains.education.test_aieos_v1_hint_t1 import _hint_free_v2_payload

        v2 = client.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": WORKSHEET_SCHEMA_ID,
                "schema_version": WORKSHEET_SCHEMA_VERSION_V2,
                "payload": _hint_free_v2_payload(),
            },
            headers=hdr({"If-Match": etag}),
        )
        assert v2.status_code == 201
        v2_id = v2.json()["version_id"]
    return content_id, v1_id, v2_id


def _synthetic_worksheet_v1_payload() -> dict:
    from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload

    return valid_worksheet_payload()


def GET_VERSION(client: TestClient, content_id: str, version_id: str):
    tenant_id: UUID = client._ci03_tenant_id  # type: ignore[attr-defined]
    token: str = client._ci03_token  # type: ignore[attr-defined]
    return client.get(
        f"/api/v1/contents/{content_id}/versions/{version_id}",
        headers=auth_headers(tenant_id, token),
    )


def seed_owner_bundle(bootstrap_engine: Engine, tenant_id: UUID, owner_id: UUID) -> None:
    seed_human_member(
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=owner_id,
        capabilities=(CONTENT_VERSION_CREATE,),
    )


def baseline_mode() -> bool:
    return os.environ.get("CI03_G1_BASELINE_MODE", "0").strip() == "1"


def source_git_sha() -> str:
    return os.environ.get("CI03_G1_SOURCE_SHA", "unknown")


def emit_receipt(case: str, **fields: object) -> None:
    path = os.environ.get("CI03_G1_RECEIPTS_PATH")
    if not path:
        return
    record: dict[str, object] = {
        "case": case,
        "source_sha": source_git_sha(),
        "baseline_mode": baseline_mode(),
        **fields,
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def private_fields_recursive(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in PRIVATE_FIELD_NAMES:
                found.add(key)
            found |= private_fields_recursive(nested)
    elif isinstance(value, list):
        for item in value:
            found |= private_fields_recursive(item)
    return found


def assert_no_private_fields_recursive(body: object) -> None:
    assert not private_fields_recursive(body)


def set_content_owner(
    bootstrap_engine: Engine, content_id: str, owner_principal_id: UUID
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE content.contents SET owner_principal_id = :pid "
                "WHERE content_id = :cid"
            ),
            {"pid": owner_principal_id, "cid": content_id},
        )


def seed_worksheet_http_ids(
    runtime_engine: Engine,
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    owner_id: UUID,
    public_jwk: dict[str, Any],
    private_key: Any,
    hint_bearing_v2: bool = False,
) -> tuple[ContentId, ContentVersionId, ContentVersionId | None, str]:
    seed_owner_bundle(bootstrap_engine, tenant_id, owner_id)
    app, _ = build_real_auth_app(
        runtime_engine,
        tenant_id=tenant_id,
        owner_principal_id=owner_id,
        public_jwk=public_jwk,
        private_key=private_key,
    )
    client = client_for(app, private_key, tenant_id=tenant_id, principal_id=owner_id)
    content_id, v1_id, v2_id = owner_worksheet_fixtures(
        client, hint_bearing_v2=hint_bearing_v2
    )
    v2 = ContentVersionId(UUID(v2_id)) if v2_id else None
    return (
        ContentId(UUID(content_id)),
        ContentVersionId(UUID(v1_id)),
        v2,
        content_id,
    )


def seed_principal_fixture(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    fixture_label: str,
) -> None:
    caps: tuple[str, ...]
    if fixture_label == "principal":
        caps = (SCHOOL_INTELLIGENCE_READ,)
    elif fixture_label == "parent":
        caps = (PARENT_INTELLIGENCE_READ,)
    elif fixture_label == "student":
        caps = ()
    elif fixture_label == "reviewer_decide":
        caps = (CONTENT_REVIEW_DECIDE,)
    elif fixture_label == "publisher_only":
        caps = (CONTENT_PUBLISH,)
    else:
        caps = ()
    seed_human_member(
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        capabilities=caps,
    )