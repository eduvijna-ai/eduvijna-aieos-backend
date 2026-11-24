#!/usr/bin/env bash
# CI03-G1-I01R2 — real JWT/kernel PG18 baseline + candidate evidence (fail closed).
set -euo pipefail

BASE_SHA="${CI03_G1_BASE_SHA:-5bbc09bc895407b71a61f9ba86a7209ff9c10ffa}"
HEAD_SHA="$(git rev-parse HEAD)"
ROOT="$(pwd)"
RECEIPTS_ROOT="$(mktemp -d)"
WORKTREE="${RECEIPTS_ROOT}/base-worktree"
MATRIX_REL="tests/domains/content/api/test_ci03_g1_pg18_matrix.py"
HELPERS_REL="tests/domains/content/api/helpers_ci03_g1_i01.py"
COPIED_TESTS=(
  "${HELPERS_REL}"
  "${MATRIX_REL}"
)

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

count_receipts() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    echo 0
    return
  fi
  wc -l < "${path}" | tr -d ' '
}

run_candidate() {
  export CI03_G1_BASELINE_MODE=0
  export CI03_G1_SOURCE_SHA="${HEAD_SHA}"
  export CI03_G1_RECEIPTS_PATH="${RECEIPTS_ROOT}/candidate.jsonl"
  : > "${CI03_G1_RECEIPTS_PATH}"
  uv run pytest -q "${MATRIX_REL}" --tb=short
  local count
  count="$(count_receipts "${CI03_G1_RECEIPTS_PATH}")"
  if [[ "${count}" -lt 5 ]]; then
    echo "CI03_G1_CANDIDATE_RECEIPTS=FAIL count=${count}"
    exit 1
  fi
  echo "CI03_G1_CANDIDATE_RECEIPTS=OK count=${count} sha=${HEAD_SHA}"
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
  export CI03_G1_RECEIPTS_PATH="${RECEIPTS_ROOT}/baseline.jsonl"
  : > "${CI03_G1_RECEIPTS_PATH}"
  uv run pytest -q "${MATRIX_REL}" --tb=short
  local count
  count="$(count_receipts "${CI03_G1_RECEIPTS_PATH}")"
  popd >/dev/null
  if [[ "${count}" -lt 3 ]]; then
    echo "CI03_G1_BASELINE_RECEIPTS=FAIL count=${count}"
    exit 1
  fi
  echo "CI03_G1_BASELINE_RECEIPTS=OK count=${count} sha=${BASE_SHA}"
}

run_candidate
run_baseline

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "## CI03-G1-I01R2 PG18 evidence"
    echo ""
    echo "- candidate_sha: ${HEAD_SHA}"
    echo "- baseline_sha: ${BASE_SHA}"
    echo "- candidate_receipts: $(count_receipts "${RECEIPTS_ROOT}/candidate.jsonl")"
    echo "- baseline_receipts: $(count_receipts "${RECEIPTS_ROOT}/baseline.jsonl")"
    echo "- matrix: real JWT + kernel tenant + classification + disposable PG18"
  } >>"${GITHUB_STEP_SUMMARY}"
fi

echo "CI03_G1_EVIDENCE=COMPLETE"