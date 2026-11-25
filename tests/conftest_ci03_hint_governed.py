"""Disposable PG18 for PK-HINT governed-assignment tests (separate from ci03_g2 G2 matrix)."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
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


def _validated_port(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,5}", value):
        raise RuntimeError("hint governed PG18: invalid host port")
    port = int(value)
    if not 1 <= port <= 65535:
        raise RuntimeError("hint governed PG18: invalid host port")
    return port


def create_owned_hint_postgres_container(
    container_name: str,
    *,
    explicit_host_port: str | None,
) -> str:
    """Create, but do not start, a new disposable container."""
    host_port = ""
    if explicit_host_port is not None:
        port = _validated_port(explicit_host_port)
        if not _loopback_port_available(port):
            raise RuntimeError(
                f"hint governed PG18: port {port} already bound on 127.0.0.1"
            )
        host_port = str(port)
    result = subprocess.run(
        [
            "docker", "create", "--name", container_name,
            "-e", f"POSTGRES_USER={BOOTSTRAP_USER}",
            "-e", f"POSTGRES_PASSWORD={DB_PASSWORD}",
            "-e", f"POSTGRES_DB={DB_NAME}",
            "-p", f"127.0.0.1:{host_port}:5432", POSTGRES_IMAGE,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("hint governed PG18: docker create failed")
    container_id = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise RuntimeError("hint governed PG18: invalid created container ID")
    return container_id


def start_owned_container(container_id: str) -> None:
    result = subprocess.run(
        ["docker", "start", container_id], check=False, capture_output=True
    )
    if result.returncode != 0:
        raise RuntimeError("hint governed PG18: docker start failed")


def docker_mapped_host_port(container_id: str) -> str:
    result = subprocess.run(
        ["docker", "port", container_id, "5432/tcp"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("hint governed PG18: docker port failed")
    match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", result.stdout.strip())
    if match is None:
        raise RuntimeError("hint governed PG18: expected exactly one loopback mapping")
    return str(_validated_port(match.group(1)))


def remove_owned_container(container_id: str) -> None:
    """Remove only the owned container and its anonymous volumes."""
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise RuntimeError("hint governed PG18: invalid cleanup container ID")
    result = subprocess.run(
        ["docker", "rm", "-f", "-v", container_id],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError("hint governed PG18: owned container cleanup failed")


@contextmanager
def owned_hint_governed_postgres18() -> Iterator[dict[str, str]]:
    """Owned disposable PG18; teardown by proven container ID only."""
    explicit_port = os.environ.get("AIEOS_HINT_GOVERNED_PG_PORT")
    container_name = new_hint_governed_container_name()
    container_id: str | None = None
    bootstrap: Engine | None = None
    try:
        container_id = create_owned_hint_postgres_container(
            container_name,
            explicit_host_port=explicit_port,
        )
        start_owned_container(container_id)
        port = docker_mapped_host_port(container_id)
        if explicit_port is not None and int(port) != _validated_port(explicit_port):
            raise RuntimeError("hint governed PG18: explicit port mapping mismatch")
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
        primary = sys.exception()
        failures: list[str] = []
        if bootstrap is not None:
            try:
                bootstrap.dispose()
            except Exception:  # noqa: BLE001 — sanitize teardown diagnostics
                failures.append("bootstrap engine disposal failed")
        if container_id is not None:
            try:
                remove_owned_container(container_id)
            except Exception:  # noqa: BLE001 — sanitize teardown diagnostics
                failures.append("owned container cleanup failed")
        if failures:
            message = "hint governed PG18: " + "; ".join(failures)
            if primary is not None:
                primary.add_note(message)
            else:
                raise RuntimeError(message)


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
