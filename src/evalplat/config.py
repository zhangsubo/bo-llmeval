"""应用配置：集中式 Settings（pydantic-settings）。

纪律：全项目禁止散落 ``os.environ.get``，一切环境变量经本模块的
``Settings`` 读取并做类型校验（RESEARCH.md Don't Hand-Roll）。
"""

from functools import lru_cache

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
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """进程级缓存的 Settings 单例。"""
    return Settings()
