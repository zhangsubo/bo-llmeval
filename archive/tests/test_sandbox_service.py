"""代码沙箱参数和结果处理测试。"""

from unittest.mock import patch

from evalplat.services.sandbox_service import run_python_sandbox, validate_sandbox_image


def test_sandbox_uses_network_isolation_and_no_shell():
    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    with patch("evalplat.services.sandbox_service.subprocess.run", return_value=Result()) as run:
        result = run_python_sandbox("print('ok')")

    command = run.call_args_list[0].args[0]
    assert "--network" in command
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert "--name" in command
    container_name = command[command.index("--name") + 1]
    assert container_name.startswith("evalplat-sandbox-")
    assert run.call_args_list[0].kwargs["check"] is False
    assert run.call_args_list[1].args[0] == ["docker", "rm", "-f", container_name]
    assert run.call_args_list[1].kwargs["check"] is False
    assert result["status"] == "passed"


def test_sandbox_rejects_oversized_code():
    import pytest

    with pytest.raises(ValueError):
        run_python_sandbox("x" * (512 * 1024 + 1))


def test_sandbox_rejects_uncontrolled_image():
    import pytest

    with pytest.raises(ValueError, match="allowlist"):
        run_python_sandbox("print('ok')", image="attacker/image:latest")


def test_sandbox_accepts_configured_custom_image(monkeypatch):
    """The worker and direct runner must share the configured image policy."""
    custom_image = "registry.example.com/evalplat/python:3.11@sha256:" + "b" * 64
    monkeypatch.setattr(
        "evalplat.services.sandbox_service.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "sandbox_image": custom_image,
                "sandbox_require_immutable_image": True,
                "sandbox_host_dir": None,
                "sandbox_work_dir": "/sandbox",
            },
        )(),
    )

    class Result:
        returncode = 0
        stdout = "EVALPLAT_COMPILE_OK\nEVALPLAT_RUN_OK\n"
        stderr = ""

    with patch("evalplat.services.sandbox_service.subprocess.run", return_value=Result()) as run:
        result = run_python_sandbox("print('ok')", image=custom_image)

    command = run.call_args_list[0].args[0]
    assert custom_image in command
    assert result["status"] == "passed"


def test_sandbox_production_policy_requires_digest_pinned_image():
    import pytest

    with pytest.raises(ValueError, match="digest"):
        validate_sandbox_image(
            "python:3.11-slim",
            allowed_images={"python:3.11-slim"},
            require_immutable=True,
        )

    pinned = "registry.example.com/python@sha256:" + "a" * 64
    assert validate_sandbox_image(
        pinned,
        allowed_images={pinned},
        require_immutable=True,
    ) == pinned


def test_sandbox_default_allowlist_keeps_legacy_development_tag():
    assert validate_sandbox_image("python:3.11-slim") == "python:3.11-slim"


def test_sandbox_runner_executes_tests_file():
    """The sandbox must run the supplied tests, not only import the solution."""
    from pathlib import Path

    class Result:
        returncode = 0
        stdout = "EVALPLAT_COMPILE_OK\nEVALPLAT_RUN_OK\n"
        stderr = ""

    def fake_run(command, **_kwargs):
        if command[:3] == ["docker", "rm", "-f"]:
            return Result()
        mount = next(value for index, value in enumerate(command) if value == "--mount" for value in [command[index + 1]])
        source = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
        runner = (source / "runner.py").read_text(encoding="utf-8")
        assert "tests.py" in runner
        assert "EVALPLAT_RUN_OK" in runner
        return Result()

    with patch("evalplat.services.sandbox_service.subprocess.run", side_effect=fake_run):
        result = run_python_sandbox("def answer(): return 42", "assert True")

    assert result["status"] == "passed"
    assert result["compile_status"] == "passed"
    assert result["run_status"] == "passed"


def test_sandbox_uses_daemon_visible_workspace_when_configured(tmp_path, monkeypatch):
    """Docker-socket workers must pass the host-visible bind source."""
    host_root = tmp_path / "host-sandboxes"
    worker_root = tmp_path / "worker-sandboxes"
    host_root.mkdir()
    worker_root.mkdir()
    monkeypatch.setattr(
        "evalplat.services.sandbox_service.get_settings",
        lambda: type(
            "Settings",
            (),
            {"sandbox_host_dir": str(host_root), "sandbox_work_dir": str(worker_root)},
        )(),
        raising=False,
    )

    class Result:
        returncode = 0
        stdout = "EVALPLAT_COMPILE_OK\nEVALPLAT_RUN_OK\n"
        stderr = ""

    def fake_run(command, **_kwargs):
        if command[:3] == ["docker", "rm", "-f"]:
            return Result()
        mount = command[command.index("--mount") + 1]
        source = mount.split("src=", 1)[1].split(",dst=", 1)[0]
        assert source.startswith(str(host_root))
        assert not source.startswith(str(worker_root))
        return Result()

    with patch("evalplat.services.sandbox_service.subprocess.run", side_effect=fake_run):
        result = run_python_sandbox("print('ok')")

    assert result["status"] == "passed"


def test_sandbox_rejects_relative_daemon_workspace(monkeypatch):
    monkeypatch.setattr(
        "evalplat.services.sandbox_service.get_settings",
        lambda: type(
            "Settings",
            (),
            {"sandbox_host_dir": "./relative-sandboxes", "sandbox_work_dir": "/sandbox"},
        )(),
        raising=False,
    )

    with patch("evalplat.services.sandbox_service.subprocess.run") as run:
        import pytest

        with pytest.raises(ValueError, match="SANDBOX_HOST_DIR"):
            run_python_sandbox("print('ok')")
        run.assert_not_called()


def test_sandbox_timeout_removes_named_container():
    """A CLI timeout must explicitly remove the daemon-side container."""
    import subprocess

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    timeout = subprocess.TimeoutExpired(["docker", "run"], 1, output=b"partial", stderr=b"still running")
    with patch(
        "evalplat.services.sandbox_service.subprocess.run",
        side_effect=[timeout, Result()],
    ) as run:
        result = run_python_sandbox("while True: pass", timeout_seconds=1)

    assert result["status"] == "timeout"
    run_command = run.call_args_list[0].args[0]
    container_name = run_command[run_command.index("--name") + 1]
    assert run.call_args_list[1].args[0] == ["docker", "rm", "-f", container_name]


def test_sandbox_cli_unavailable_still_removes_named_container():
    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    with patch(
        "evalplat.services.sandbox_service.subprocess.run",
        side_effect=[FileNotFoundError("docker"), Result()],
    ) as run:
        result = run_python_sandbox("print('ok')")

    assert result["status"] == "unavailable"
    run_command = run.call_args_list[0].args[0]
    container_name = run_command[run_command.index("--name") + 1]
    assert run.call_args_list[1].args[0] == ["docker", "rm", "-f", container_name]


def test_sandbox_unexpected_cli_error_still_removes_named_container():
    import pytest

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    with patch(
        "evalplat.services.sandbox_service.subprocess.run",
        side_effect=[RuntimeError("docker client failed"), Result()],
    ) as run:
        with pytest.raises(RuntimeError, match="docker client failed"):
            run_python_sandbox("print('ok')")

    run_command = run.call_args_list[0].args[0]
    container_name = run_command[run_command.index("--name") + 1]
    assert run.call_args_list[1].args[0] == ["docker", "rm", "-f", container_name]


def test_sandbox_logs_cleanup_failure(caplog):
    import logging

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    class CleanupFailure:
        returncode = 1
        stdout = ""
        stderr = "permission denied"

    with patch(
        "evalplat.services.sandbox_service.subprocess.run",
        side_effect=[Result(), CleanupFailure()],
    ) as run:
        with caplog.at_level(logging.WARNING, logger="evalplat.services.sandbox_service"):
            result = run_python_sandbox("print('ok')")

    assert result["status"] == "passed"
    run_command = run.call_args_list[0].args[0]
    container_name = run_command[run_command.index("--name") + 1]
    assert any(
        record.message == "failed to clean up sandbox container"
        and record.container_name == container_name
        and record.returncode == 1
        for record in caplog.records
    )


def test_runner_loads_generated_code_before_tests(tmp_path):
    from evalplat.services.sandbox_service import _RUNNER_SOURCE

    (tmp_path / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests.py").write_text("assert add(1, 2) == 3\n", encoding="utf-8")
    (tmp_path / "runner.py").write_text(
        _RUNNER_SOURCE.replace("/workspace", str(tmp_path)), encoding="utf-8"
    )

    import subprocess

    result = subprocess.run(
        ["python", str(tmp_path / "runner.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "EVALPLAT_COMPILE_OK" in result.stdout
    assert "EVALPLAT_RUN_OK" in result.stdout
