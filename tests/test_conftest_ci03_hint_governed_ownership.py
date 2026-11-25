"""R2 lifecycle ownership for hint-governed disposable PG18 (mocked Docker orchestration)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tests import conftest_ci03_hint_governed as hg


def test_new_container_names_are_distinct() -> None:
    a = hg.new_hint_governed_container_name()
    b = hg.new_hint_governed_container_name()
    assert a != b
    assert a.startswith(hg.CONTAINER_NAME_PREFIX)
    assert b.startswith(hg.CONTAINER_NAME_PREFIX)


def test_port_collision_fails_closed_without_docker_rm() -> None:
    with patch.object(hg.socket, "socket") as mock_socket_cls:
        sock = MagicMock()
        sock.bind.side_effect = OSError("address already in use")
        mock_socket_cls.return_value.__enter__.return_value = sock
        with (
            patch("subprocess.run") as mock_run,
            pytest.raises(RuntimeError, match="already bound"),
        ):
            hg.run_owned_hint_postgres_container(
                "aieos-hint-governed-collision",
                explicit_host_port="55434",
            )
        mock_run.assert_not_called()


def test_docker_run_failure_does_not_remove_container() -> None:
    with (
        patch("subprocess.run") as mock_run,
        pytest.raises(RuntimeError, match="name already in use"),
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=125, stdout="", stderr="name already in use"
        )
        hg.run_owned_hint_postgres_container(
            "aieos-hint-governed-fail",
            explicit_host_port=None,
        )
    assert all(
        "rm" not in (call.args[0] if call.args else [])
        for call in mock_run.call_args_list
    )


def test_teardown_removes_only_proven_container_id() -> None:
    with patch("subprocess.run") as mock_run:
        hg.remove_owned_container("abc123deadbeef")
    mock_run.assert_called_once_with(
        ["docker", "rm", "-f", "abc123deadbeef"],
        check=False,
        capture_output=True,
    )


def test_owned_substrate_cleans_up_on_engine_wait_failure() -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="cid-init-fail\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch("subprocess.check_output", return_value="127.0.0.1:55123\n"),
        patch.object(hg, "wait_for_engine", side_effect=RuntimeError("not ready")),
        pytest.raises(RuntimeError, match="not ready"),
    ):
        with hg.owned_hint_governed_postgres18():
            pass

    rm_calls = [c for c in calls if len(c) >= 3 and c[0:3] == ["docker", "rm", "-f"]]
    assert rm_calls == [["docker", "rm", "-f", "cid-init-fail"]]
    pre_rm = [c for c in calls if len(c) >= 2 and c[0:2] == ["docker", "rm"]]
    assert len(pre_rm) == 1


def test_successful_run_uses_dynamic_publish_when_no_explicit_port() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="container-id-xyz\n", stderr=""
        )
        cid = hg.run_owned_hint_postgres_container(
            "aieos-hint-governed-ok",
            explicit_host_port=None,
        )
    assert cid == "container-id-xyz"
    cmd = mock_run.call_args[0][0]
    assert "-P" in cmd
    assert "-p" not in cmd