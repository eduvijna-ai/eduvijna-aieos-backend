"""Owned disposable PG18 for CI03-G2 proofs that must not mutate the session substrate."""

from __future__ import annotations

import subprocess
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class DisposableCi03Substrate:
    bootstrap_url: str
    migrator_url: str
    runtime_url: str
    bootstrap_engine: Engine
    runtime_engine: Engine
    container_name: str
    port: str


def _docker_mapped_port(container_name: str) -> str:
    mapped = subprocess.check_output(
        ["docker", "port", container_name, "5432/tcp"],
        text=True,
    ).strip()
    return mapped.rsplit(":", 1)[-1]


@contextmanager
def owned_disposable_ci03_pg18(
    *,
    alembic_revision: str = "head",
) -> Iterator[DisposableCi03Substrate]:
    """Ephemeral PG18 container; torn down after the proof body even on failure."""
    container = f"aieos-ci03-g2-own-{uuid.uuid7().hex[:12]}"
    subprocess.run(
        ["docker", "rm", "-f", container],
        check=False,
        capture_output=True,
    )
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container,
            "-e",
            f"POSTGRES_USER={BOOTSTRAP_USER}",
            "-e",
            f"POSTGRES_PASSWORD={DB_PASSWORD}",
            "-e",
            f"POSTGRES_DB={DB_NAME}",
            "-P",
            POSTGRES_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    port = _docker_mapped_port(container)
    b_url = bootstrap_url(port)
    m_url = migrator_url(port)
    r_url = runtime_url(port)
    bootstrap = wait_for_engine(b_url)
    try:
        with bootstrap.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar_one()
            if not str(version).startswith("18."):
                raise RuntimeError(f"PostgreSQL 18 required; got {version}")
        provision_identities(bootstrap)
        cfg = alembic_config(m_url)
        command.upgrade(cfg, "head")
        clear_asset_audit_rows_for_schema_downgrade(bootstrap)
        command.downgrade(cfg, "base")
        command.upgrade(cfg, alembic_revision)
        provision_runtime_grants(bootstrap)
        runtime = create_engine(r_url)
        try:
            yield DisposableCi03Substrate(
                bootstrap_url=b_url,
                migrator_url=m_url,
                runtime_url=r_url,
                bootstrap_engine=bootstrap,
                runtime_engine=runtime,
                container_name=container,
                port=port,
            )
        finally:
            runtime.dispose()
    finally:
        bootstrap.dispose()
        subprocess.run(
            ["docker", "rm", "-f", container],
            check=False,
            capture_output=True,
        )