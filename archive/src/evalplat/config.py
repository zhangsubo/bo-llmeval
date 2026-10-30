"""应用配置：集中式 Settings（pydantic-settings）。

纪律：全项目禁止散落 ``os.environ.get``，一切环境变量经本模块的
``Settings`` 读取并做类型校验（RESEARCH.md Don't Hand-Roll）。
"""

from functools import lru_cache
import re
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用运行所需的环境变量集合。

    - database_url：主库连接串（必填，本地开发指向 compose db 服务）
    - test_database_url：测试库连接串（指向 compose db-test 服务）
    - app_master_key：应用主密钥（必填；供 Phase 2/10 加密 API Key，本阶段不使用）
    - log_level：日志级别（默认 INFO）
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env 中允许存在本阶段未消费的键
    )

    database_url: str
    test_database_url: str = (
        "postgresql+psycopg://evalplat:evalplat@localhost:5433/evalplat_test"
    )
    app_master_key: str
    app_env: str = "development"
    log_level: str = "INFO"
    report_dir: str = "./data/reports"
    dataset_dir: str = "./data/datasets"
    evalscope_dir: str = "./data/evalscope"
    tokenizer_dir: str = "./data/tokenizers"
    history_cleanup_stale_seconds: int = 900
    worker_poll_interval_seconds: float = 1.0
    worker_max_concurrency: int = 4
    # The development default is intentionally easy to use. Production
    # deployments should provide a digest-pinned image and enable the policy
    # below so mutable tags cannot change the execution environment.
    sandbox_image: str = "python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534"
    sandbox_require_immutable_image: bool = False
    # Docker-socket workers need a host-visible path for child-container bind
    # mounts. The Compose file supplies this in deployments that use a socket.
    sandbox_host_dir: str | None = None
    sandbox_work_dir: str = "/sandbox"

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        """Reject development credentials when running as production.

        Development defaults keep a fresh checkout usable, but they must not
        silently become a deployable configuration. The check is centralized
        here so API, worker, and migration processes share the same policy.
        """

        environment = self.app_env.strip().lower()
        if environment not in {"development", "test", "production"}:
            raise ValueError("APP_ENV 必须为 development、test 或 production")
        self.app_env = environment
        if self.sandbox_host_dir and not Path(self.sandbox_host_dir).expanduser().is_absolute():
            raise ValueError("SANDBOX_HOST_DIR 必须使用绝对路径")
        if environment != "production":
            return self

        try:
            Fernet(self.app_master_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("生产环境 APP_MASTER_KEY 必须是有效的 Fernet key") from exc

        lowered_url = self.database_url.lower()
        insecure_markers = (
            "evalplat:evalplat@",
            "dev-only-insecure",
            "change-me",
            "replace-with-",
        )
        if any(marker in lowered_url for marker in insecure_markers):
            raise ValueError("生产环境 DATABASE_URL 不能使用开发占位凭据")
        if not self.sandbox_require_immutable_image:
            raise ValueError("生产环境必须启用 SANDBOX_REQUIRE_IMMUTABLE_IMAGE")
        if re.search(r"@sha256:[0-9a-f]{64}$", self.sandbox_image.strip()) is None:
            raise ValueError("生产环境 SANDBOX_IMAGE 必须使用 digest 固定的镜像")
        if not self.sandbox_host_dir or not Path(self.sandbox_host_dir).expanduser().is_absolute():
            raise ValueError("生产环境必须配置绝对路径 SANDBOX_HOST_DIR")
        return self


@lru_cache
def get_settings() -> Settings:
    """进程级缓存的 Settings 单例。"""
    return Settings()
