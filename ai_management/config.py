from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"] = "development"
    internal_api_token: str = "development-internal-token"
    admin_api_token: str = "development-admin-token"
    user_hmac_secret: str = "development-user-hmac-secret"
    public_base_url: str = "http://127.0.0.1:8000"

    database_url: str = "sqlite:///./ai_management.db"
    redis_url: str = "redis://127.0.0.1:6379/5"

    dify_base_url: str = "http://127.0.0.1/v1"
    dify_business_app_api_key: str = ""
    dify_moments_app_api_key: str = ""
    dify_writing_app_api_key: str = ""
    dify_summary_profile_app_api_key: str = ""
    dify_knowledge_base_url: str = ""
    dify_knowledge_api_key: str = ""
    dify_general_dataset_id: str = ""
    dify_course_dataset_id: str = ""

    history_retention_days: int = Field(default=7, ge=1, le=365)
    cleanup_hour: int = Field(default=3, ge=0, le=23)
    cleanup_minute: int = Field(default=30, ge=0, le=59)
    summary_user_turns: int = Field(default=6, ge=1, le=100)
    summary_approx_chars: int = Field(default=6000, ge=100)
    summary_batch_max_chars: int = Field(default=40000, ge=1000, le=55000)
    summary_batch_max_messages: int = Field(default=100, ge=2, le=500)
    context_max_chars: int = Field(default=24000, ge=1000)
    context_max_messages: int = Field(default=50, ge=4, le=500)
    max_query_chars: int = Field(default=12000, ge=100)

    max_upload_file_mb: int = Field(default=50, ge=1)
    max_upload_files: int = Field(default=10, ge=1, le=100)
    max_upload_batch_mb: int = Field(default=500, ge=1)
    failed_file_retention_hours: int = Field(default=24, ge=1)
    cleaning_status_retention_days: int = Field(default=7, ge=1)
    system_log_retention_days: int = Field(default=30, ge=1, le=3650)
    click_counter_retention_days: int = Field(default=30, ge=2, le=365)
    upload_root: Path = Path("var/uploads")
    build_version: str = "development"

    @property
    def knowledge_base_url(self) -> str:
        return (self.dify_knowledge_base_url or self.dify_base_url).rstrip("/")

    @property
    def dify_url(self) -> str:
        return self.dify_base_url.rstrip("/")

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.app_env != "production":
            return self
        missing: list[str] = []
        if (
            self.internal_api_token.startswith("development-")
            or len(self.internal_api_token) < 32
        ):
            missing.append("INTERNAL_API_TOKEN")
        if (
            self.user_hmac_secret.startswith("development-")
            or len(self.user_hmac_secret) < 32
        ):
            missing.append("USER_HMAC_SECRET")
        if self.admin_api_token.startswith("development-") or len(self.admin_api_token) < 32:
            missing.append("ADMIN_API_TOKEN")
        if not self.database_url.startswith("postgresql"):
            missing.append("DATABASE_URL(生产必须使用PostgreSQL)")
        for field_name in (
            "dify_business_app_api_key",
            "dify_moments_app_api_key",
            "dify_writing_app_api_key",
            "dify_summary_profile_app_api_key",
            "dify_knowledge_api_key",
            "dify_general_dataset_id",
            "dify_course_dataset_id",
        ):
            if not getattr(self, field_name):
                missing.append(field_name.upper())
        if missing:
            raise ValueError(f"生产环境缺少配置：{', '.join(missing)}")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
