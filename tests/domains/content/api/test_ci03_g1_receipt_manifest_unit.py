"""Unit tests for CI03-G1 receipt manifest verification."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tests.domains.content.api.ci03_g1_receipt_manifest import (
    REQUIRED_BY_MODE,
    verify_receipt_file,
)
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.ci03_g1_i01_unit

BASE_SHA = "5bbc09bc895407b71a61f9ba86a7209ff9c10ffa"


def _provenance(source_sha: str) -> dict[str, object]:
    return {
        "db_server_version": "18.1",
        "pg_major": "18",
        "runtime_current_user": "aieos_runtime",
        "runtime_role": "aieos_runtime",
        "row_security_active": "on",
        "runtime_rolsuper": False,
        "runtime_rolbypassrls": False,
        "content_contents_rls_enabled": True,
        "content_versions_rls_enabled": True,
        "runtime_security_write_denied": True,
        "alembic_head": EXPECTED_MIGRATION_HEAD,
        "openapi_sha256": EXPECTED_OPENAPI_SHA256,
        "application_module": "/repo/src/aieos/domains/content/application/http_append.py",
        "git_head_sha": source_sha,
    }


def _line(case: str, *, mode: str, source_sha: str, **extra: object) -> str:
    body: dict[str, object] = {
        "case": case,
        "source_sha": source_sha,
        "baseline_mode": mode == "baseline",
        "status": 200,
        **_provenance(source_sha),
        **extra,
    }
    return json.dumps(body, sort_keys=True)


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_verify_rejects_missing_required_case() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "receipts.jsonl"
        one = next(iter(REQUIRED_BY_MODE["candidate"]))
        _write(path, [_line(one, mode="candidate", source_sha=BASE_SHA)])
        errors, _ = verify_receipt_file(
            str(path), mode="candidate", source_sha=BASE_SHA
        )
        assert any("missing required cases" in e for e in errors)


def test_verify_rejects_duplicate_case() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "receipts.jsonl"
        case = "missing_version"
        _write(
            path,
            [
                _line(
                    case,
                    mode="candidate",
                    source_sha=BASE_SHA,
                    status=404,
                    problem_code="content_version_not_found",
                    private_fields_observed=[],
                    private_field_names=[],
                ),
                _line(
                    case,
                    mode="candidate",
                    source_sha=BASE_SHA,
                    status=404,
                    problem_code="content_version_not_found",
                    private_fields_observed=[],
                    private_field_names=[],
                ),
            ],
        )
        errors, _ = verify_receipt_file(
            str(path), mode="candidate", source_sha=BASE_SHA
        )
        assert any("duplicate" in e for e in errors)


def test_verify_rejects_wrong_source_sha() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "receipts.jsonl"
        _write(
            path,
            [
                _line(
                    "missing_version",
                    mode="candidate",
                    source_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    status=404,
                    problem_code="content_version_not_found",
                    private_fields_observed=[],
                    private_field_names=[],
                )
            ],
        )
        errors, _ = verify_receipt_file(
            str(path), mode="candidate", source_sha=BASE_SHA
        )
        assert any("source_sha mismatch" in e for e in errors)


def test_verify_rejects_denied_without_observed_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "receipts.jsonl"
        _write(
            path,
            [
                _line(
                    "cross_tenant",
                    mode="candidate",
                    source_sha=BASE_SHA,
                    status=404,
                    problem_code="content_version_not_found",
                    private_field_names=[],
                )
            ],
        )
        errors, _ = verify_receipt_file(
            str(path), mode="candidate", source_sha=BASE_SHA
        )
        assert any("missing private_fields_observed" in e for e in errors)