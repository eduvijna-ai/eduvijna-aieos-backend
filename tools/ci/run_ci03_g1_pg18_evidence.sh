#!/usr/bin/env bash
# CI03-G1-VE01 — real JWT/kernel PG18 baseline + candidate evidence (manifest verify).
set -euo pipefail

BASE_SHA="${CI03_G1_BASE_SHA:-5bbc09bc895407b71a61f9ba86a7209ff9c10ffa}"
HEAD_SHA="$(git rev-parse HEAD)"
ROOT="$(pwd)"
RECEIPTS_ROOT="$(mktemp -d)"
WORKTREE="${RECEIPTS_ROOT}/base-worktree"
MATRIX_REL="tests/domains/content/api/test_ci03_g1_pg18_matrix.py"
HELPERS_REL="tests/domains/content/api/helpers_ci03_g1_i01.py"
MANIFEST_REL="tests/domains/content/api/ci03_g1_receipt_manifest.py"
PROVENANCE_REL="tests/domains/content/api/ci03_g1_provenance.py"
VERIFY_REL="tools/ci/verify_ci03_g1_receipts.py"
COPIED_TESTS=(
  "${HELPERS_REL}"
  "${MATRIX_REL}"
  "${MANIFEST_REL}"
  "${PROVENANCE_REL}"
)

ARTIFACT_DIR="${GITHUB_WORKSPACE:-${ROOT}}/ci03-g1-evidence"
BASELINE_JSONL="${ARTIFACT_DIR}/baseline.jsonl"
CANDIDATE_JSONL="${ARTIFACT_DIR}/candidate.jsonl"
SUMMARY_JSON="${ARTIFACT_DIR}/evidence_summary.json"

cleanup() {
  if [[ -d "${WORKTREE}" ]]; then
    git -C "${ROOT}" worktree remove --force "${WORKTREE}" 2>/dev/null || true
  fi
  rm -rf "${RECEIPTS_ROOT}"
}
trap cleanup EXIT

if [[ -n "${GITHUB_EVENT_PULL_REQUEST_HEAD_SHA:-}" ]]; then
  test "${HEAD_SHA}" = "${GITHUB_EVENT_PULL_REQUEST_HEAD_SHA}"
fi

if ! git cat-file -t "${BASE_SHA}" >/dev/null 2>&1; then
  echo "CI03_G1_EVIDENCE=HOLD missing_base_sha"
  exit 1
fi

mkdir -p "${ARTIFACT_DIR}"

run_candidate() {
  export CI03_G1_BASELINE_MODE=0
  export CI03_G1_SOURCE_SHA="${HEAD_SHA}"
  export github_run_id="${GITHUB_RUN_ID:-local}"
  export github_run_attempt="${GITHUB_RUN_ATTEMPT:-1}"
  export CI03_G1_RECEIPTS_PATH="${RECEIPTS_ROOT}/candidate.jsonl"
  : > "${CI03_G1_RECEIPTS_PATH}"
  uv run pytest -q "${MATRIX_REL}" --tb=short
  cp "${CI03_G1_RECEIPTS_PATH}" "${CANDIDATE_JSONL}"
  echo "CI03_G1_CANDIDATE_PYTEST=OK sha=${HEAD_SHA}"
}

run_baseline() {
  git -C "${ROOT}" worktree add --detach "${WORKTREE}" "${BASE_SHA}"
  for rel in "${COPIED_TESTS[@]}"; do
    install -D "${ROOT}/${rel}" "${WORKTREE}/${rel}"
  done
  pushd "${WORKTREE}" >/dev/null
  uv sync --locked --group dev
  export CI03_G1_BASELINE_MODE=1
  export CI03_G1_SOURCE_SHA="${BASE_SHA}"
  export github_run_id="${GITHUB_RUN_ID:-local}"
  export github_run_attempt="${GITHUB_RUN_ATTEMPT:-1}"
  export CI03_G1_RECEIPTS_PATH="${RECEIPTS_ROOT}/baseline.jsonl"
  : > "${CI03_G1_RECEIPTS_PATH}"
  uv run pytest -q "${MATRIX_REL}" --tb=short
  popd >/dev/null
  cp "${CI03_G1_RECEIPTS_PATH}" "${BASELINE_JSONL}"
  echo "CI03_G1_BASELINE_PYTEST=OK sha=${BASE_SHA}"
}

run_candidate
run_baseline

uv run python "${VERIFY_REL}" \
  --baseline "${BASELINE_JSONL}" \
  --candidate "${CANDIDATE_JSONL}" \
  --baseline-sha "${BASE_SHA}" \
  --candidate-sha "${HEAD_SHA}" \
  --summary-out "${SUMMARY_JSON}"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "## CI03-G1-VE01 PG18 evidence"
    echo ""
    echo "- candidate_sha: \`${HEAD_SHA}\`"
    echo "- baseline_sha: \`${BASE_SHA}\`"
    echo "- summary: \`${SUMMARY_JSON}\`"
    echo "- learner_safe_regression_suite (quality-gate): \`tests/domains/education/test_aieos_v1_hint_t0_b_http.py\`, \`tests/domains/education/test_aieos_v1_hint_t1_http.py\`, \`tests/domains/learning/test_aieos_v1_hint_t1b.py\`"
    echo ""
    echo "### Receipt summary"
    echo '```json'
    head -c 120000 "${SUMMARY_JSON}" || true
    echo '```'
  } >>"${GITHUB_STEP_SUMMARY}"
fi

echo "CI03_G1_EVIDENCE=COMPLETE artifacts=${ARTIFACT_DIR}"