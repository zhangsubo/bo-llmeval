"""隔离代码执行适配器。

代码 Worker 只通过本模块调用 Docker；不把模型代码交给 Web/API 进程执行。
"""

import logging
import re
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from evalplat.config import get_settings


logger = logging.getLogger(__name__)


# Pin the base image by digest so a mutable registry tag cannot silently alter
# the execution environment. Update deliberately when upgrading Python.
DEFAULT_SANDBOX_IMAGE = (
    "python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534"
)
# Keep the pre-digest tag temporarily for already persisted development jobs.
# Production callers pass an explicit allowlist with the configured digest.
ALLOWED_SANDBOX_IMAGES = frozenset({DEFAULT_SANDBOX_IMAGE, "python:3.11-slim"})
_IMMUTABLE_IMAGE_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
_SANDBOX_CONTAINER_PREFIX = "evalplat-sandbox-"
_SANDBOX_CLEANUP_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class SandboxPolicy:
    """Single source of truth for task precheck, enqueue and worker policy."""

    configured_image: str = DEFAULT_SANDBOX_IMAGE
    require_immutable: bool = False

    @property
    def allowed_images(self) -> frozenset[str]:
        # Legacy tags remain available only in explicit development mode. A
        # production policy accepts exactly the configured image.
        if self.require_immutable:
            return frozenset({self.configured_image})
        return frozenset({self.configured_image, *ALLOWED_SANDBOX_IMAGES})

    def validate(self, image: str | None) -> str:
        candidate = str(image or self.configured_image).strip()
        return validate_sandbox_image(
            candidate,
            allowed_images=self.allowed_images,
            require_immutable=self.require_immutable,
        )


def get_sandbox_policy(settings: object | None = None) -> SandboxPolicy:
    """Build the effective policy from Settings without importing it eagerly."""

    if settings is None:
        from evalplat.config import get_settings

        settings = get_settings()
    configured = str(getattr(settings, "sandbox_image", DEFAULT_SANDBOX_IMAGE) or DEFAULT_SANDBOX_IMAGE).strip()
    require_immutable = bool(getattr(settings, "sandbox_require_immutable_image", False))
    return SandboxPolicy(configured_image=configured, require_immutable=require_immutable)


def validate_sandbox_image(
    image: str,
    *,
    allowed_images: Iterable[str] | None = None,
    require_immutable: bool = False,
) -> str:
    """Return a controlled sandbox image name or reject untrusted images."""

    normalized = str(image or "").strip()
    allowlist = (
        ALLOWED_SANDBOX_IMAGES
        if allowed_images is None
        else frozenset(str(item).strip() for item in allowed_images if str(item).strip())
    )
    if normalized not in allowlist:
        raise ValueError("代码沙箱镜像不在受控 allowlist 中")
    if require_immutable and _IMMUTABLE_IMAGE_RE.search(normalized) is None:
        raise ValueError("生产环境必须使用 digest 固定的代码沙箱镜像")
    return normalized


_RUNNER_SOURCE = '''\
from pathlib import Path
import sys

sys.path.insert(0, "/workspace")

def _compile(path):
    source = Path(path).read_text(encoding="utf-8")
    return compile(source, path, "exec")

try:
    main_program = _compile("/workspace/main.py")
    tests_program = _compile("/workspace/tests.py")
    print("EVALPLAT_COMPILE_OK", flush=True)
except BaseException:
    print("EVALPLAT_COMPILE_FAILED", flush=True)
    raise

try:
    tests_source = Path("/workspace/tests.py").read_text(encoding="utf-8")
    namespace = {"__name__": "__main__", "__file__": "/workspace/main.py"}
    # Code-generation benchmarks define functions in main.py and reference
    # them from tests.py. Keep both programs in one namespace and run tests
    # only after the generated code has loaded successfully.
    exec(main_program, namespace)
    if tests_source.strip():
        namespace["__file__"] = "/workspace/tests.py"
        exec(tests_program, namespace)
    print("EVALPLAT_RUN_OK", flush=True)
except BaseException:
    print("EVALPLAT_RUN_FAILED", flush=True)
    raise
'''


def run_python_sandbox(
    code: str,
    tests: str = "",
    *,
    image: str | None = None,
    timeout_seconds: int = 10,
    memory_mb: int = 256,
    cpus: float = 1.0,
    pids_limit: int = 64,
) -> dict[str, object]:
    """在短生命周期、禁网 Docker 容器中运行 Python 代码。"""
    settings = get_settings()
    image = get_sandbox_policy(settings).validate(image)
    if not code or len(code.encode("utf-8")) > 512 * 1024:
        raise ValueError("代码不能为空且不得超过 512 KiB")
    if len(tests.encode("utf-8")) > 512 * 1024:
        raise ValueError("测试代码不得超过 512 KiB")
    if timeout_seconds <= 0 or timeout_seconds > 300:
        raise ValueError("timeout_seconds 必须在 1-300 之间")
    if memory_mb <= 0 or cpus <= 0 or pids_limit <= 0:
        raise ValueError("沙箱资源限制必须为正数")

    with _sandbox_workspace(
        host_dir=getattr(settings, "sandbox_host_dir", None),
        work_dir=getattr(settings, "sandbox_work_dir", "/sandbox"),
    ) as (root, docker_root):
        (root / "main.py").write_text(code, encoding="utf-8")
        (root / "tests.py").write_text(tests, encoding="utf-8")
        (root / "runner.py").write_text(_RUNNER_SOURCE, encoding="utf-8")
        # Keep a stable handle for cleanup if the docker CLI is killed by the
        # subprocess timeout while the daemon-side container is still alive.
        container_name = f"{_SANDBOX_CONTAINER_PREFIX}{uuid.uuid4().hex}"
        command = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(pids_limit),
            "--memory", f"{memory_mb}m",
            "--cpus", str(cpus),
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--mount", f"type=bind,src={docker_root},dst=/workspace,readonly",
            image,
            "python", "/workspace/runner.py",
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "status": "timeout",
                "compile_status": "pending",
                "run_status": "failed",
                "stdout": _tail_text(exc.stdout),
                "stderr": _tail_text(exc.stderr),
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "error_category": "timeout",
                "error_summary": "代码执行超时",
            }
        except OSError as exc:
            return {
                "status": "unavailable",
                "compile_status": "pending",
                "run_status": "not_run",
                "stdout": "",
                "stderr": "沙箱运行时不可用",
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "error_category": "sandbox_unavailable",
                "error_summary": "代码沙箱不可用",
            }
        finally:
            _cleanup_sandbox_container(container_name)

    stdout = _tail_text(completed.stdout)
    stderr = _tail_text(completed.stderr)
    compile_ok = "EVALPLAT_COMPILE_OK" in stdout
    run_ok = "EVALPLAT_RUN_OK" in stdout
    compile_status = "passed" if compile_ok or completed.returncode == 0 else "failed"
    run_status = "passed" if run_ok or completed.returncode == 0 else "failed" if compile_ok else "not_run"
    status = "passed" if run_status == "passed" else "failed"
    return {
        "status": status,
        "compile_status": compile_status,
        "run_status": run_status,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "test_results": {
            "compile": compile_status,
            "run": run_status,
        },
        **({"error_category": "test_failure", "error_summary": "代码测试失败"} if status == "failed" else {}),
    }


@contextmanager
def _sandbox_workspace(*, host_dir: str | None = None, work_dir: str = "/sandbox"):
    """Create files where both the worker and the Docker daemon can see them.

    A worker using the host Docker socket cannot bind-mount a path that only
    exists inside the worker container.  Deployments set ``SANDBOX_HOST_DIR``
    to the daemon-visible path and mount that same directory as
    ``SANDBOX_WORK_DIR`` (default ``/sandbox``) in the worker.  Local calls
    without that mapping retain the ordinary temporary-directory behavior.
    """

    host_dir = str(host_dir or "").strip()
    if not host_dir:
        with tempfile.TemporaryDirectory(prefix="evalplat-sandbox-") as temp_dir:
            root = Path(temp_dir)
            yield root, str(root)
        return

    host_path = Path(host_dir).expanduser()
    if not host_path.is_absolute():
        raise ValueError("SANDBOX_HOST_DIR 必须使用绝对路径")
    work_path = Path(work_dir).resolve()
    work_path.mkdir(parents=True, exist_ok=True)
    host_root = host_path.resolve()
    with tempfile.TemporaryDirectory(prefix="evalplat-sandbox-", dir=work_path) as temp_dir:
        root = Path(temp_dir)
        relative = root.relative_to(work_path)
        docker_root = host_root / relative
        yield root, str(docker_root)


def _tail_text(value: object, limit: int = 10_000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value)[-limit:]


def _cleanup_sandbox_container(container_name: str) -> bool:
    """Best-effort removal of a daemon-side sandbox container.

    ``docker run --rm`` normally removes a container after a clean exit, but
    killing the CLI process on timeout does not guarantee that behavior. The
    explicit ``rm -f`` is therefore performed on every subprocess path. A
    missing container is an expected outcome when ``--rm`` already cleaned it;
    all other failures are logged with the generated name for investigation.
    """

    try:
        completed = subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=_SANDBOX_CLEANUP_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive around CLI/runtime failures
        logger.warning(
            "failed to clean up sandbox container",
            extra={
                "container_name": container_name,
                "error": str(exc)[:500],
            },
        )
        return False

    returncode = getattr(completed, "returncode", None)
    if returncode == 0:
        return True

    stdout = _tail_text(getattr(completed, "stdout", ""), 1_000)
    stderr = _tail_text(getattr(completed, "stderr", ""), 1_000)
    if "no such container" in f"{stdout}\n{stderr}".lower():
        return True
    logger.warning(
        "failed to clean up sandbox container",
        extra={
            "container_name": container_name,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        },
    )
    return False
