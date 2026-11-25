"""Disposable PG18 for PK-HINT governed-assignment tests (separate from ci03_g2 G2 matrix)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tests.conftest import (
    BOOTSTRAP_USER,
    DB_NAME,
    DB_PASSWORD,
    POSTGRES_IMAGE,
    alembic_config,
    bootstrap_url,
    clear_asset_audit_rows_for_schema_downgrade,
    migrator_url,
    provision_identities,
    provision_runtime_grants,
    runtime_url,
    wait_for_engine,
)

HINT_GOVERNED_CONTAINER = "aieos-hint-governed-pg"
HINT_GOVERNED_HOST_PORT = os.environ.get("AIEOS_HINT_GOVERNED_PG_PORT", "55434")


def _start_hint_governed_postgres() -> str:
    subprocess.run(
        ["docker", "rm", "-f", HINT_GOVERNED_CONTAINER],
        check=False,
        capture_output=True,
    )
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            HINT_GOVERNED_CONTAINER,
            "-e",
            f"POSTGRES_USER={BOOTSTRAP_USER}",
            "-e",
            f"POSTGRES_PASSWORD={DB_PASSWORD}",
            "-e",
            f"POSTGRES_DB={DB_NAME}",
            "-p",
            f"{HINT_GOVERNED_HOST_PORT}:5432",
            POSTGRES_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        raise RuntimeError(f"docker run failed: {result.stderr}")
    return HINT_GOVERNED_HOST_PORT


@pytest.fixture(scope="session")
def hint_governed_postgres18() -> Iterator[dict[str, str]]:
    """Owned PG18 for hint assignment-bound functional tests; not shared with G2 ci03_g2."""
    port = _start_hint_governed_postgres()
    b_url = bootstrap_url(port)
    m_url = migrator_url(port)
    r_url = runtime_url(port)
    bootstrap = wait_for_engine(b_url)
    with bootstrap.connect() as conn:
        version = conn.execute(text("SHOW server_version")).scalar_one()
        if not str(version).startswith("18."):
            raise RuntimeError(f"PostgreSQL 18 required; got {version}")
    provision_identities(bootstrap)
    cfg = alembic_config(m_url)
    command.upgrade(cfg, "head")
    clear_asset_audit_rows_for_schema_downgrade(bootstrap)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap)
    try:
        yield {
            "bootstrap_url": b_url,
            "migrator_url": m_url,
            "runtime_url": r_url,
            "server_version": str(version),
            "port": port,
            "container_name": HINT_GOVERNED_CONTAINER,
        }
    finally:
        bootstrap.dispose()
        subprocess.run(
            ["docker", "rm", "-f", HINT_GOVERNED_CONTAINER],
            check=False,
            capture_output=True,
        )


@pytest.fixture(scope="session")
def hint_governed_bootstrap_engine(
    hint_governed_postgres18: dict[str, str],
) -> Iterator[Engine]:
    engine = create_engine(hint_governed_postgres18["bootstrap_url"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def hint_governed_runtime_engine(
    hint_governed_postgres18: dict[str, str],
) -> Iterator[Engine]:
    engine = create_engine(hint_governed_postgres18["runtime_url"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def ci03_hint_governed_substrate(hint_governed_bootstrap_engine: Engine) -> Iterator[None]:
    provision_runtime_grants(hint_governed_bootstrap_engine)
    yield