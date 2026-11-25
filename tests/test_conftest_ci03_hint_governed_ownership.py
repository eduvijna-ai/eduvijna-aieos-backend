"""C01R2 ownership lifecycle tests; Docker is mocked, not PG18 proof."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tests import conftest_ci03_hint_governed as hg

CID = "a" * 64


def _result(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_new_container_names_are_distinct():
    a = hg.new_hint_governed_container_name()
    b = hg.new_hint_governed_container_name()
    assert a != b
    assert a.startswith(hg.CONTAINER_NAME_PREFIX)
    assert b.startswith(hg.CONTAINER_NAME_PREFIX)


def test_port_collision_fails_closed_without_docker_rm():
    with (
        patch.object(hg, "_loopback_port_available", return_value=False),
        patch.object(hg.subprocess, "run") as run,
        pytest.raises(RuntimeError, match="already bound"),
    ):
        hg.create_owned_hint_postgres_container("collision", explicit_host_port="55434")
    run.assert_not_called()


def test_docker_create_failure_does_not_remove_container():
    with (
        patch.object(hg.subprocess, "run", return_value=_result(returncode=125)) as run,
        pytest.raises(RuntimeError, match="create failed"),
    ):
        hg.create_owned_hint_postgres_container("collision", explicit_host_port=None)
    assert run.call_count == 1
    assert run.call_args.args[0][:2] == ["docker", "create"]


@pytest.mark.parametrize("port", ["0", "65536", "-1", "abc", " 55434"])
def test_invalid_explicit_port_does_not_create(port):
    with (
        patch.object(hg.subprocess, "run") as run,
        pytest.raises(RuntimeError, match="invalid host port"),
    ):
        hg.create_owned_hint_postgres_container("invalid", explicit_host_port=port)
    run.assert_not_called()


@pytest.mark.parametrize("port,expected", [(None, "127.0.0.1::5432"), ("55434", "127.0.0.1:55434:5432")])
def test_create_uses_loopback_only(port, expected):
    with (
        patch.object(hg, "_loopback_port_available", return_value=True),
        patch.object(hg.subprocess, "run", return_value=_result(CID)) as run,
    ):
        assert hg.create_owned_hint_postgres_container("owned", explicit_host_port=port) == CID
    cmd = run.call_args.args[0]
    assert cmd[cmd.index("-p") + 1] == expected
    assert "-P" not in cmd
    assert "--rm" not in cmd


@pytest.mark.parametrize("mapping", ["0.0.0.0:55434", "[::]:55434", "127.0.0.1:0", "127.0.0.1:55434\n0.0.0.0:55434"])
def test_invalid_mapping_fails_closed(mapping):
    with (
        patch.object(hg.subprocess, "run", return_value=_result(mapping)),
        pytest.raises(RuntimeError),
    ):
        hg.docker_mapped_host_port(CID)


def test_teardown_removes_only_proven_container_id():
    with patch.object(hg.subprocess, "run", return_value=_result()) as run:
        hg.remove_owned_container(CID)
    run.assert_called_once_with(
        ["docker", "rm", "-f", "-v", CID], check=False, capture_output=True
    )


def test_invalid_cleanup_id_never_invokes_docker():
    with (
        patch.object(hg.subprocess, "run") as run,
        pytest.raises(RuntimeError, match="invalid cleanup"),
    ):
        hg.remove_owned_container("foreign-container")
    run.assert_not_called()


@pytest.fixture
def lifecycle(monkeypatch):
    monkeypatch.delenv("AIEOS_HINT_GOVERNED_PG_PORT", raising=False)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalar_one.return_value = "18.1"
    mocks = {
        "create": MagicMock(return_value=CID),
        "start": MagicMock(),
        "mapping": MagicMock(return_value="55123"),
        "wait": MagicMock(return_value=engine),
        "identities": MagicMock(),
        "config": MagicMock(),
        "upgrade": MagicMock(),
        "legacy_cleanup": MagicMock(),
        "downgrade": MagicMock(),
        "grants": MagicMock(),
        "remove": MagicMock(),
    }
    for key, attr in {
        "create": "create_owned_hint_postgres_container",
        "start": "start_owned_container",
        "mapping": "docker_mapped_host_port",
        "wait": "wait_for_engine",
        "identities": "provision_identities",
        "config": "alembic_config",
        "legacy_cleanup": "clear_asset_audit_rows_for_schema_downgrade",
        "grants": "provision_runtime_grants",
        "remove": "remove_owned_container",
    }.items():
        monkeypatch.setattr(hg, attr, mocks[key])
    monkeypatch.setattr(hg.command, "upgrade", mocks["upgrade"])
    monkeypatch.setattr(hg.command, "downgrade", mocks["downgrade"])
    return engine, mocks


@pytest.mark.parametrize("stage", ["start", "mapping", "wait", "identities", "upgrade", "downgrade", "grants"])
def test_owned_substrate_cleans_up_on_initialization_failure(lifecycle, stage):
    engine, mocks = lifecycle
    mocks[stage].side_effect = RuntimeError("injected failure")
    with pytest.raises(RuntimeError, match="injected failure"):
        with hg.owned_hint_governed_postgres18():
            pytest.fail("failed initialization must not yield")
    mocks["remove"].assert_called_once_with(CID)
    if stage not in {"start", "mapping", "wait"}:
        engine.dispose.assert_called_once()


def test_successful_lifecycle(lifecycle):
    engine, mocks = lifecycle
    with hg.owned_hint_governed_postgres18() as substrate:
        assert substrate["container_id"] == CID
        assert substrate["port"] == "55123"
        assert substrate["server_version"] == "18.1"
        mocks["remove"].assert_not_called()
    mocks["start"].assert_called_once_with(CID)
    mocks["mapping"].assert_called_once_with(CID)
    mocks["grants"].assert_called_once_with(engine)
    engine.dispose.assert_called_once()
    mocks["remove"].assert_called_once_with(CID)


def test_cleanup_failure_is_not_silently_accepted(lifecycle):
    engine, mocks = lifecycle
    mocks["remove"].side_effect = RuntimeError("cleanup failed")
    with pytest.raises(RuntimeError, match="owned container cleanup failed"):
        with hg.owned_hint_governed_postgres18():
            pass
    engine.dispose.assert_called_once()


def test_cleanup_failure_preserves_primary_exception(lifecycle):
    _, mocks = lifecycle
    mocks["upgrade"].side_effect = RuntimeError("migration failed")
    mocks["remove"].side_effect = RuntimeError("cleanup failed")
    with pytest.raises(RuntimeError, match="migration failed") as exc:
        with hg.owned_hint_governed_postgres18():
            pass
    assert "owned container cleanup failed" in exc.value.__notes__[0]


def test_disposal_failure_still_removes_container(lifecycle):
    engine, mocks = lifecycle
    engine.dispose.side_effect = RuntimeError("dispose failed")
    with pytest.raises(RuntimeError, match="engine disposal failed"):
        with hg.owned_hint_governed_postgres18():
            pass
    mocks["remove"].assert_called_once_with(CID)
