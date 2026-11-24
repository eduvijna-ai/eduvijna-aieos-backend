"""CI03-G1 PG18 provenance collection (runtime SQL + repo pins)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from tools.release.common import (
    EXPECTED_MIGRATION_HEAD,
    EXPECTED_OPENAPI_SHA256,
    assert_openapi_digest,
    derive_and_validate_migration_head,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def git_head_sha(repo_root: Path | None = None) -> str:
    root = repo_root or REPO_ROOT
    return (
        subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
        )
        .strip()
    )


def application_gate_module_path() -> str:
    import aieos.domains.content.application.http_append as mod

    path = getattr(mod, "__file__", None)
    if path is None:
        raise RuntimeError("http_append module has no __file__")
    return str(Path(path).resolve())


def collect_runtime_db_provenance(runtime_engine: Engine) -> dict[str, Any]:
    with runtime_engine.connect() as conn:
        server_version = str(
            conn.execute(text("SHOW server_version")).scalar_one()
        )
        current_user = str(conn.execute(text("SELECT current_user")).scalar_one())
        row_security_active = str(
            conn.execute(text("SHOW row_security")).scalar_one()
        )
        role = conn.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user"
            )
        ).one()
        contents_rls = conn.execute(
            text(
                "SELECT c.relrowsecurity "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'content' AND c.relname = 'contents'"
            )
        ).scalar_one()
        versions_rls = conn.execute(
            text(
                "SELECT c.relrowsecurity "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'content' AND c.relname = 'content_versions'"
            )
        ).scalar_one()
    return {
        "db_server_version": server_version,
        "pg_major": server_version.split(".", 1)[0],
        "runtime_current_user": current_user,
        "runtime_role": current_user,
        "row_security_active": row_security_active,
        "runtime_rolsuper": bool(role.rolsuper),
        "runtime_rolbypassrls": bool(role.rolbypassrls),
        "content_contents_rls_enabled": bool(contents_rls),
        "content_versions_rls_enabled": bool(versions_rls),
    }


def runtime_security_write_denied(runtime_engine: Engine) -> bool:
    try:
        with runtime_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO security.principals "
                    "(principal_id, principal_kind, status) "
                    "VALUES ('00000000-0000-7000-8000-000000000099', 'HUMAN', 'ACTIVE')"
                )
            )
    except DBAPIError:
        return True
    return False


def collect_repo_provenance(repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root or REPO_ROOT
    alembic_head = derive_and_validate_migration_head(root)
    openapi_sha = assert_openapi_digest(root)
    if alembic_head != EXPECTED_MIGRATION_HEAD:
        raise RuntimeError(f"unexpected alembic head {alembic_head}")
    if openapi_sha != EXPECTED_OPENAPI_SHA256:
        raise RuntimeError("unexpected openapi digest")
    return {
        "alembic_head": alembic_head,
        "openapi_sha256": openapi_sha,
        "application_module": application_gate_module_path(),
        "git_head_sha": git_head_sha(root),
    }


def collect_matrix_provenance(runtime_engine: Engine) -> dict[str, Any]:
    if not runtime_security_write_denied(runtime_engine):
        raise RuntimeError("runtime engine must not write security.principals")
    return {
        **collect_runtime_db_provenance(runtime_engine),
        **collect_repo_provenance(),
        "runtime_security_write_denied": True,
    }