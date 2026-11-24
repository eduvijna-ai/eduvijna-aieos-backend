"""Disposable PG18 substrate for CI03-G2-I01 tests (does not share session DB)."""

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
    SCHEMA_OWNER_ROLE,
    SECURITY_SCHEMA_OWNER_ROLE,
    ASSET_SCHEMA_OWNER_ROLE,
    MIGRATOR_USER,
    RUNTIME_USER,
    MIGRATION_RUNTIME_USER,
    WORKFLOW_DISPATCHER_USER,
    EVENT_DISPATCHER_USER,
    alembic_config,
    bootstrap_url,
    clear_asset_audit_rows_for_schema_downgrade,
    event_dispatcher_url,
    migrator_url,
    migration_runtime_url,
    provision_identities,
    provision_runtime_grants,
    runtime_url,
    wait_for_engine,
    workflow_dispatcher_url,
)

CI03_G2_I01_CONTAINER = "aieos-ci03-g2-i01-pg"
CI03_G2_I01_HOST_PORT = os.environ.get("AIEOS_CI03_G2_I01_PG_PORT", "55433")


def _start_ci03_postgres() -> str:
    subprocess.run(
        ["docker", "rm", "-f", CI03_G2_I01_CONTAINER],
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
            CI03_G2_I01_CONTAINER,
            "-e",
            f"POSTGRES_USER={BOOTSTRAP_USER}",
            "-e",
            f"POSTGRES_PASSWORD={DB_PASSWORD}",
            "-e",
            f"POSTGRES_DB={DB_NAME}",
            "-p",
            f"{CI03_G2_I01_HOST_PORT}:5432",
            POSTGRES_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        raise RuntimeError(f"docker run failed: {result.stderr}")
    return CI03_G2_I01_HOST_PORT


@pytest.fixture(scope="session")
def postgres18() -> Iterator[dict[str, str]]:
    """Isolated PG18 for reviewer-assignment evidence; separate from GCI shared DB."""
    external = os.environ.get("AIEOS_CI03_G2_I01_DATABASE_URL")
    started_container = False
    if external:
        b_url = os.environ.get(
            "AIEOS_CI03_G2_I01_BOOTSTRAP_DATABASE_URL", external
        )
        m_url = external
        r_url = os.environ.get("AIEOS_CI03_G2_I01_RUNTIME_DATABASE_URL", external)
        mig_url = os.environ.get(
            "AIEOS_CI03_G2_I01_MIGRATION_RUNTIME_DATABASE_URL", r_url
        )
        d_url = os.environ.get(
            "AIEOS_CI03_G2_I01_WORKFLOW_DISPATCHER_DATABASE_URL", r_url
        )
        e_url = os.environ.get(
            "AIEOS_CI03_G2_I01_EVENT_DISPATCHER_DATABASE_URL", r_url
        )
        port = CI03_G2_I01_HOST_PORT
    else:
        port = _start_ci03_postgres()
        started_container = True
        b_url = bootstrap_url(port)
        m_url = migrator_url(port)
        r_url = runtime_url(port)
        mig_url = migration_runtime_url(port)
        d_url = workflow_dispatcher_url(port)
        e_url = event_dispatcher_url(port)

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
            "migration_runtime_url": mig_url,
            "workflow_dispatcher_url": d_url,
            "event_dispatcher_url": e_url,
            "server_version": str(version),
            "port": port,
            "schema_owner_role": SCHEMA_OWNER_ROLE,
            "security_schema_owner_role": SECURITY_SCHEMA_OWNER_ROLE,
            "asset_schema_owner_role": ASSET_SCHEMA_OWNER_ROLE,
            "migrator_user": MIGRATOR_USER,
            "runtime_user": RUNTIME_USER,
            "migration_runtime_user": MIGRATION_RUNTIME_USER,
            "workflow_dispatcher_user": WORKFLOW_DISPATCHER_USER,
            "event_dispatcher_user": EVENT_DISPATCHER_USER,
        }
    finally:
        bootstrap.dispose()
        if started_container:
            subprocess.run(
                ["docker", "rm", "-f", CI03_G2_I01_CONTAINER],
                check=False,
                capture_output=True,
            )


@pytest.fixture(scope="session")
def bootstrap_engine(postgres18: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(postgres18["bootstrap_url"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def migrator_engine(postgres18: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(postgres18["migrator_url"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def runtime_engine(postgres18: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(postgres18["runtime_url"])
    try:
        yield engine
    finally:
        engine.dispose()