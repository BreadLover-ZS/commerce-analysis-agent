from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    app_env: Literal["development", "production", "test"] = "development"
    agent_mode: Literal["demo", "live"] = "demo"
    database_url: str = "sqlite:///.data/commerce.db"
    data_dir: Path = Path(".data")
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    session_secret: SecretStr = SecretStr("local-demo-only-change-before-deployment")
    bootstrap_email: str = "demo@example.com"
    bootstrap_password: SecretStr = SecretStr("demo-commerce-2026")
    cookie_secure: bool = False
    session_hours: int = Field(12, ge=1, le=168)
    seed_demo: bool = True

    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    model_read_timeout_seconds: float = Field(60, ge=1, le=120)
    model_connect_timeout_seconds: float = Field(5, ge=1, le=30)
    tavily_api_key: SecretStr = SecretStr("")
    knowledge_provider: Literal["local", "ragflow"] = "local"
    ragflow_api_url: str = ""
    ragflow_api_key: SecretStr = SecretStr("")
    ragflow_datasets: dict[str, list[str]] = {}
    semantic_review: bool = True
    input_price_per_million: float | None = Field(None, ge=0)
    output_price_per_million: float | None = Field(None, ge=0)

    max_tool_calls: int = Field(16, ge=1, le=100)
    max_model_calls: int = Field(12, ge=1, le=100)
    max_tokens: int = Field(60000, ge=1000)
    max_runtime_seconds: int = Field(180, ge=5, le=1800)
    max_upload_bytes: int = Field(5 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    max_active_tasks: int = Field(3, ge=1, le=20)
    lease_seconds: int = Field(30, ge=5, le=300)
    max_attempts: int = Field(3, ge=1, le=5)
    worker_concurrency: int = Field(2, ge=1, le=8)
    diagnosis_wait_ttl_seconds: int = Field(86400, ge=60, le=604800)
    diagnosis_inventory_max_age_days: int = Field(7, ge=0, le=365)
    diagnosis_runtime: Literal["legacy", "v3", "v4", "v5", "v6", "v7", "v8", "v9"] = "v9"

    @model_validator(mode="after")
    def validate_deployment(self):
        if self.agent_mode == "live" and (
            not self.openai_api_key.get_secret_value() or not self.llm_model
        ):
            raise ValueError("live 模式需要 OPENAI_API_KEY 和 LLM_MODEL")
        if self.app_env == "production":
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("production 必须使用 PostgreSQL / psycopg")
            if not self.cookie_secure:
                raise ValueError("production 必须启用 COOKIE_SECURE 并通过 HTTPS 访问")
            if (
                len(self.session_secret.get_secret_value()) < 32
                or "local-demo" in self.session_secret.get_secret_value()
            ):
                raise ValueError("production 需要独立的 SESSION_SECRET（至少 32 字符）")
            if (
                len(self.bootstrap_password.get_secret_value()) < 12
                or self.bootstrap_password.get_secret_value() == "demo-commerce-2026"
            ):
                raise ValueError("production 必须设置独立的 BOOTSTRAP_PASSWORD（至少 12 字符）")
            if self.seed_demo:
                raise ValueError("production 禁止自动播种演示账户和业务数据")
        return self

    @property
    def checkpoint_url(self) -> str:
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def prepare_dirs(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///"):
            Path(self.database_url.removeprefix("sqlite:///")).parent.mkdir(
                parents=True, exist_ok=True
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
