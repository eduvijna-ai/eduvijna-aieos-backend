"""Disposable PG18 for PK-HINT governed-assignment tests (separate from ci03_g2 G2 matrix)."""

from __future__ import annotations

import os
import socket
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

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

CONTAINER_NAME_PREFIX = "aieos-hint-governed-"


def new_hint_governed_container_name() -> str:
    return f"{CONTAINER_NAME_PREFIX}{uuid.uuid7().hex}"


def _loopback_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def run_owned_hint_postgres_container(
    container_name: str,
    *,
    explicit_host_port: str | None,
) -> str:
    """Start a new container; return its ID. Never removes pre-existing containers."""
    cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        container_name,
        "-e",
        f"POSTGRES_USER={BOOTSTRAP_USER}",
        "-e",
        f"POSTGRES_PASSWORD={DB_PASSWORD}",
        "-e",
        f"POSTGRES_DB={DB_NAME}",
    ]
    if explicit_host_port is not None:
        port = int(explicit_host_port)
        if not _loopback_port_available(port):
            raise RuntimeError(
                f"hint governed PG18: port {port} already bound on 127.0.0.1"
            )
        cmd.extend(["-p", f"127.0.0.1:{port}:5432"])
    else:
        cmd.append("-P")
    cmd.append(POSTGRES_IMAGE)
    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip() or "docker run failed"
        raise RuntimeError(f"hint governed PG18: {msg}")
    container_id = result.stdout.strip()
    if not container_id:
        raise RuntimeError("hint governed PG18: docker run returned no container ID")
    return container_id


def docker_mapped_host_port(container_id: str) -> str:
    mapped = subprocess.check_output(
        ["docker", "port", container_id, "5432/tcp"],
        text=True,
    ).strip()
    return mapped.rsplit(":", 1)[-1]


def remove_owned_container(container_id: str) -> None:
    """Stop/remove only the container ID created by this fixture invocation."""
    subprocess.run(
        ["docker", "rm", "-f", container_id],
        check=False,
        capture_output=True,
    )


@contextmanager
def owned_hint_governed_postgres18() -> Iterator[dict[str, str]]:
    """Owned disposable PG18; teardown by proven container ID only."""
    explicit_port = os.environ.get("AIEOS_HINT_GOVERNED_PG_PORT")
    container_name = new_hint_governed_container_name()
    container_id: str | None = None
    bootstrap: Engine | None = None
    try:
        container_id = run_owned_hint_postgres_container(
            container_name,
            explicit_host_port=explicit_port,
        )
        port = (
            explicit_port
            if explicit_port is not None
            else docker_mapped_host_port(container_id)
        )
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
        yield {
            "bootstrap_url": b_url,
            "migrator_url": m_url,
            "runtime_url": r_url,
            "server_version": str(version),
            "port": port,
            "container_id": container_id,
            "container_name": container_name,
        }
    finally:
        if bootstrap is not None:
            try:
                bootstrap.dispose()
            except Exception:  # noqa: BLE001 — preserve primary during teardown
                pass
        if container_id is not None:
            remove_owned_container(container_id)


@pytest.fixture(scope="session")
def hint_governed_postgres18() -> Iterator[dict[str, str]]:
    """Owned PG18 for hint assignment-bound functional tests; not shared with G2 ci03_g2."""
    with owned_hint_governed_postgres18() as substrate:
        yield substrate


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