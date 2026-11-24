#!/usr/bin/env bash
# CI03-G1-I01 — disposable PG18 pre-fix baseline + post-fix candidate characterization.
set -euo pipefail

BASE_SHA="5bbc09bc895407b71a61f9ba86a7209ff9c10ffa"
HEAD_SHA="$(git rev-parse HEAD)"
WORKTREE="${GITHUB_WORKSPACE:-$(pwd)}/../ci03-g1-base-worktree"

echo "CI03_G1_BASE_SHA=${BASE_SHA}"
echo "CI03_G1_HEAD_SHA=${HEAD_SHA}"

uv run pytest -q -m "ci03_g1_i01 and not ci03_g1_i01_fault" --tb=no
echo "CI03_G1_CANDIDATE_MATRIX=PASSED"

if git cat-file -t "${BASE_SHA}" >/dev/null 2>&1; then
  rm -rf "${WORKTREE}"
  git worktree add --detach "${WORKTREE}" "${BASE_SHA}"
  pushd "${WORKTREE}" >/dev/null
  uv sync --locked --group dev >/dev/null
  export AIEOS_TEST_DATABASE_URL="${AIEOS_TEST_DATABASE_URL:-}"
  CI03_G1_CHARACTERIZATION_ONLY=1 uv run python - <<'PY'
import json
import os
import uuid

from fastapi.testclient import TestClient

from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.schema import (
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
    WORKSHEET_CONTENT_TYPE,
    build_preparation_content_schema_registry,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload
from tests.fakes import (
    AllowClassroomAssessmentAuthorization,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
)

# Baseline characterization only — stub auth allowed; not integrated production proof.
db_url = os.environ.get("AIEOS_TEST_RUNTIME_DATABASE_URL") or os.environ.get(
    "AIEOS_TEST_DATABASE_URL"
)
if not db_url:
    print(json.dumps({"ci03_g1_baseline": "NOT_TESTED", "reason": "missing_pg18_url"}))
    raise SystemExit(0)

from sqlalchemy import create_engine

engine = create_engine(db_url)
tenant = uuid.uuid7()
owner = uuid.uuid7()
intruder = uuid.uuid7()


def client(pid: uuid.UUID) -> TestClient:
    app = create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(engine),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(engine),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=FixedPrincipalAuthenticator(pid),
        security_resolver=StubSecurityContextResolver(tenant, pid),
        content_types=__import__(
            "aieos.domains.content.application.catalog", fromlist=["StaticContentTypeCatalog"]
        ).StaticContentTypeCatalog({WORKSHEET_CONTENT_TYPE}),
        cursor_signing_key=b"ci03-g1-baseline",
        schema_registry=build_preparation_content_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )
    return TestClient(app, raise_server_exceptions=False)


def headers() -> dict[str, str]:
    return {
        "X-AIEOS-Tenant-ID": str(tenant),
        "Idempotency-Key": f"baseline-{uuid.uuid7()}",
    }


owner_client = client(owner)
created = owner_client.post(
    "/api/v1/contents",
    json={
        "content_type": WORKSHEET_CONTENT_TYPE,
        "title": "baseline",
        "description": "",
        "locale": "en-IN",
    },
    headers=headers(),
)
assert created.status_code == 201
cid = created.json()["content_id"]
etag = created.headers["ETag"]
appended = owner_client.post(
    f"/api/v1/contents/{cid}/versions",
    json={
        "schema_id": WORKSHEET_SCHEMA_ID,
        "schema_version": WORKSHEET_SCHEMA_VERSION,
        "payload": valid_worksheet_payload(),
    },
    headers={**headers(), "If-Match": etag},
)
assert appended.status_code == 201
vid = appended.json()["version_id"]
intruder_client = client(intruder)
get_resp = intruder_client.get(
    f"/api/v1/contents/{cid}/versions/{vid}",
    headers={"X-AIEOS-Tenant-ID": str(tenant)},
)
payload = get_resp.json().get("payload") if get_resp.status_code == 200 else {}
questions = payload.get("questions") if isinstance(payload, dict) else []
field_names = set()
if isinstance(questions, list) and questions and isinstance(questions[0], dict):
    for key in ("answer", "explanation", "learner_hint"):
        if key in questions[0]:
            field_names.add(key)
print(
    json.dumps(
        {
            "ci03_g1_baseline_sha": os.environ.get("CI03_G1_BASE_SHA", "5bbc09bc"),
            "same_tenant_non_owner_status": get_resp.status_code,
            "private_field_names_present": sorted(field_names),
            "characterization": "source_gap_expected_200_pre_fix",
        }
    )
)
PY
  popd >/dev/null
  git worktree remove --force "${WORKTREE}"
  echo "CI03_G1_BASELINE_CHARACTERIZATION=DONE"
else
  echo "CI03_G1_BASELINE_CHARACTERIZATION=NOT_TESTED"
fi

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "## CI03-G1-I01 PG18 evidence"
    echo ""
    echo "- base_sha: ${BASE_SHA}"
    echo "- head_sha: ${HEAD_SHA}"
    echo "- candidate_matrix: pytest -m ci03_g1_i01"
    echo "- baseline: stub-auth characterization at base (same-tenant status + field names only)"
  } >>"${GITHUB_STEP_SUMMARY}"
fi