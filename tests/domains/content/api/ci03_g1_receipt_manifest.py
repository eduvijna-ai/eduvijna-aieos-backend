"""CI03-G1 PG18 evidence receipt manifest (case keys + expected fields)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from tests.domains.content.api.ci03_g1_provenance import CI03_G1_BASELINE_MIGRATION_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256


def _expected_migration_head_for_mode(mode: Mode) -> str:
    if mode == "baseline":
        return CI03_G1_BASELINE_MIGRATION_HEAD
    return EXPECTED_MIGRATION_HEAD

Mode = Literal["baseline", "candidate"]

NON_OWNER_FIXTURES = (
    "unrelated_human",
    "reviewer_decide",
    "publisher_only",
    "principal",
    "parent",
    "student",
)

WORKSHEET_SCHEMAS = ("worksheet@1", "worksheet@2")

SHARED_CASES = frozenset(
    {
        "suspended_owner",
        "revoked_membership_same_resource",
        "wrong_content_id_correlation",
        "missing_version",
        "anonymous",
        "forged_tenant",
        "cross_tenant",
        "nonworksheet_cross_principal",
        "owner_library_positive",
        "owner_review_queue_list_positive",
        "owner_review_queue_detail_positive",
        "reviewer_review_queue_detail_denied",
        "non_owner_library_denied",
    }
)


def _non_owner_cases(prefix: str) -> frozenset[str]:
    return frozenset(
        f"{prefix}_non_owner_{fixture}_{schema}"
        for fixture in NON_OWNER_FIXTURES
        for schema in WORKSHEET_SCHEMAS
    )


BASELINE_ONLY = frozenset(
    {
        "owner_positive_worksheet@1",
        "owner_positive_worksheet@2",
        "baseline_workload_owner",
    }
) | _non_owner_cases("baseline")

CANDIDATE_ONLY = frozenset(
    {
        "owner_positive_worksheet@1",
        "owner_positive_worksheet@2",
        "candidate_workload_owner",
    }
) | _non_owner_cases("candidate")

REQUIRED_BY_MODE: dict[Mode, frozenset[str]] = {
    "baseline": SHARED_CASES | BASELINE_ONLY,
    "candidate": SHARED_CASES | CANDIDATE_ONLY,
}


@dataclass(frozen=True)
class ReceiptExpectation:
    status: int
    problem_code: str | None = None
    no_store: str | None | object = ...
    private_field_names: frozenset[str] | None = None
    private_field_names_superset: frozenset[str] | None = None
    private_field_names_empty: bool = False
    require_private_fields_observed_empty: bool = False
    source_sha: str | None = None


CASES_REQUIRING_OBSERVED_EMPTY = frozenset(
    {
        "candidate_workload_owner",
        "suspended_owner",
        "revoked_membership_same_resource",
        "wrong_content_id_correlation",
        "missing_version",
        "anonymous",
        "forged_tenant",
        "cross_tenant",
        "reviewer_review_queue_detail_denied",
        "non_owner_library_denied",
    }
    | {f"candidate_non_owner_{f}_{s}" for f in NON_OWNER_FIXTURES for s in WORKSHEET_SCHEMAS}
)


def _owner_positive(schema: str, mode: Mode) -> ReceiptExpectation:
    base_fields = frozenset({"answer", "explanation"})
    if schema == "worksheet@2":
        base_fields = base_fields | {"learner_hint"}
    if mode == "baseline":
        return ReceiptExpectation(
            status=200,
            no_store=None,
            private_field_names_superset=base_fields,
        )
    return ReceiptExpectation(
        status=200,
        no_store="private, no-store",
        private_field_names_superset=base_fields,
    )


def _non_owner(fixture: str, schema: str, mode: Mode) -> ReceiptExpectation:
    if mode == "baseline":
        fields = frozenset({"answer", "explanation"})
        if schema == "worksheet@2":
            fields = fields | {"learner_hint"}
        return ReceiptExpectation(
            status=200,
            private_field_names_superset=fields,
        )
    return ReceiptExpectation(
        status=404,
        problem_code="content_version_not_found",
        private_field_names_empty=True,
    )


def expected_for_case(case: str, *, mode: Mode, source_sha: str) -> ReceiptExpectation | None:
    if case.startswith("owner_positive_worksheet@"):
        schema = case.removeprefix("owner_positive_")
        exp = _owner_positive(schema, mode)
        return ReceiptExpectation(
            status=exp.status,
            problem_code=exp.problem_code,
            no_store=exp.no_store,
            private_field_names_superset=exp.private_field_names_superset,
            source_sha=source_sha,
        )
    if case.startswith("baseline_non_owner_") or case.startswith("candidate_non_owner_"):
        prefix = "baseline_non_owner_" if case.startswith("baseline_") else "candidate_non_owner_"
        rest = case[len(prefix) :]
        marker = "_worksheet@"
        if marker not in rest:
            return None
        fixture, schema_tail = rest.split(marker, 1)
        schema = f"worksheet@{schema_tail}"
        exp = _non_owner(fixture, schema, mode)
        return ReceiptExpectation(
            status=exp.status,
            problem_code=exp.problem_code,
            private_field_names_superset=exp.private_field_names_superset,
            private_field_names_empty=exp.private_field_names_empty,
            source_sha=source_sha,
        )
    static: dict[str, ReceiptExpectation] = {
        "baseline_workload_owner": ReceiptExpectation(status=200),
        "candidate_workload_owner": ReceiptExpectation(
            status=404, problem_code="content_version_not_found", private_field_names_empty=True
        ),
        "suspended_owner": ReceiptExpectation(status=403, problem_code="forbidden"),
        "revoked_membership_same_resource": ReceiptExpectation(
            status=403, problem_code="forbidden"
        ),
        "wrong_content_id_correlation": ReceiptExpectation(
            status=404, problem_code="content_version_not_found"
        ),
        "missing_version": ReceiptExpectation(
            status=404, problem_code="content_version_not_found"
        ),
        "anonymous": ReceiptExpectation(status=401, problem_code="unauthenticated"),
        "forged_tenant": ReceiptExpectation(status=403, problem_code="forbidden"),
        "cross_tenant": ReceiptExpectation(
            status=404, problem_code="content_version_not_found", private_field_names_empty=True
        ),
        "nonworksheet_cross_principal": ReceiptExpectation(status=200, no_store=None),
        "owner_library_positive": ReceiptExpectation(status=200),
        "owner_review_queue_list_positive": ReceiptExpectation(status=200),
        "owner_review_queue_detail_positive": ReceiptExpectation(status=200),
        "reviewer_review_queue_detail_denied": ReceiptExpectation(
            status=404, problem_code="review_queue_item_not_found"
        ),
        "non_owner_library_denied": ReceiptExpectation(
            status=404, problem_code="library_item_not_found"
        ),
    }
    if case not in static:
        return None
    base = static[case]
    return ReceiptExpectation(
        status=base.status,
        problem_code=base.problem_code,
        no_store=base.no_store,
        private_field_names=base.private_field_names,
        private_field_names_superset=base.private_field_names_superset,
        private_field_names_empty=base.private_field_names_empty,
        source_sha=source_sha,
    )


def _field_names(record: dict[str, Any]) -> set[str]:
    raw = record.get("private_field_names")
    if raw is None:
        return set()
    if isinstance(raw, list):
        return {str(x) for x in raw}
    return set()


def _check_record(record: dict[str, Any], *, mode: Mode, source_sha: str) -> list[str]:
    errors: list[str] = []
    case = str(record.get("case", ""))
    exp = expected_for_case(case, mode=mode, source_sha=source_sha)
    if exp is None:
        errors.append(f"unexpected case key {case!r}")
        return errors
    if record.get("source_sha") != source_sha:
        errors.append(f"{case}: source_sha mismatch")
    if record.get("baseline_mode") != (mode == "baseline"):
        errors.append(f"{case}: baseline_mode mismatch")
    status = record.get("status")
    if status != exp.status:
        errors.append(f"{case}: status expected {exp.status} got {status}")
    if exp.problem_code is not None and record.get("problem_code") != exp.problem_code:
        errors.append(
            f"{case}: problem_code expected {exp.problem_code!r} got {record.get('problem_code')!r}"
        )
    if exp.no_store is not ...:
        no_store = record.get("no_store")
        if no_store != exp.no_store:
            errors.append(f"{case}: no_store expected {exp.no_store!r} got {no_store!r}")
    names = _field_names(record)
    if case in CASES_REQUIRING_OBSERVED_EMPTY or exp.private_field_names_empty:
        if "private_fields_observed" not in record:
            errors.append(f"{case}: missing private_fields_observed")
        else:
            observed = record.get("private_fields_observed")
            if not isinstance(observed, list):
                errors.append(f"{case}: private_fields_observed must be a list")
            elif observed:
                errors.append(
                    f"{case}: private_fields_observed must be empty, got {observed}"
                )
    if exp.private_field_names_empty and names:
        errors.append(f"{case}: expected no private field names, got {sorted(names)}")
    if exp.private_field_names is not None and names != set(exp.private_field_names):
        errors.append(f"{case}: private_field_names mismatch")
    if exp.private_field_names_superset is not None and not exp.private_field_names_superset <= names:
        errors.append(
            f"{case}: missing private fields {sorted(exp.private_field_names_superset - names)}"
        )
    if not str(record.get("db_server_version", "")).startswith("18."):
        errors.append(f"{case}: missing or invalid db_server_version")
    runtime_user = record.get("runtime_current_user") or record.get("runtime_role")
    if not runtime_user:
        errors.append(f"{case}: missing runtime_current_user")
    if record.get("runtime_rolsuper") is not False:
        errors.append(f"{case}: runtime_rolsuper must be false")
    if record.get("runtime_rolbypassrls") is not False:
        errors.append(f"{case}: runtime_rolbypassrls must be false")
    if str(record.get("row_security_active", "")).lower() not in {"on", "true"}:
        errors.append(f"{case}: row_security_active must be on")
    if record.get("content_contents_rls_enabled") is not True:
        errors.append(f"{case}: content.contents RLS must be enabled")
    if record.get("content_versions_rls_enabled") is not True:
        errors.append(f"{case}: content.content_versions RLS must be enabled")
    if record.get("runtime_security_write_denied") is not True:
        errors.append(f"{case}: runtime_security_write_denied must be true")
    expected_head = _expected_migration_head_for_mode(mode)
    if record.get("alembic_head") != expected_head:
        errors.append(f"{case}: alembic_head pin mismatch")
    if record.get("openapi_sha256") != EXPECTED_OPENAPI_SHA256:
        errors.append(f"{case}: openapi_sha256 pin mismatch")
    if record.get("git_head_sha") and record.get("git_head_sha") != source_sha:
        errors.append(f"{case}: git_head_sha must match source_sha")
    if not record.get("application_module"):
        errors.append(f"{case}: missing application_module path")
    return errors


def load_receipts(path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def verify_receipt_file(
    path: str,
    *,
    mode: Mode,
    source_sha: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    required = REQUIRED_BY_MODE[mode]
    records = load_receipts(path)
    by_case: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for record in records:
        case = str(record.get("case", ""))
        by_case.setdefault(case, []).append(record)
    for case, rows in by_case.items():
        if len(rows) > 1:
            errors.append(f"duplicate receipts for case {case!r} ({len(rows)})")
    missing = required - set(by_case)
    if missing:
        errors.append(f"missing required cases: {sorted(missing)}")
    extra = set(by_case) - required
    if extra:
        errors.append(f"unexpected case keys: {sorted(extra)}")
    for case in sorted(required):
        rows = by_case.get(case)
        if not rows:
            continue
        errors.extend(_check_record(rows[0], mode=mode, source_sha=source_sha))
    return errors, records


def summarize_verification(
    *,
    mode: Mode,
    source_sha: str,
    records: list[dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    return {
        "mode": mode,
        "source_sha": source_sha,
        "required_case_count": len(REQUIRED_BY_MODE[mode]),
        "receipt_count": len(records),
        "ok": not errors,
        "errors": errors,
        "receipts": records,
    }