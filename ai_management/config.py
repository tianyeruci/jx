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

    bailian_api_key: str = ""
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    cleaning_model: str = "qwen-plus"
    cleaning_prompt_version: str = "commercial-cleaning-v1"
    # Bump only when stored-cleaning semantics change (sanitization, strategy
    # contract, chunking or publication policy).  It is deliberately distinct
    # from a build version so routine releases do not re-index every source.
    cleaning_spec_revision: str = "commercial-cleaning-v3"
    cleaning_batch_candidate_threshold: int = Field(default=100, ge=1, le=50_000)
    cleaning_batch_token_threshold: int = Field(default=200_000, ge=1)
    cleaning_batch_max_requests: int = Field(default=40_000, ge=1, le=50_000)
    cleaning_batch_max_bytes: int = Field(
        default=400 * 1024 * 1024, ge=1024, le=500 * 1024 * 1024
    )
    cleaning_candidate_max_tokens: int = Field(default=12_000, ge=1000)
    # Dify validates this independently from the model prompt candidate cap.
    # Its custom hierarchical segment API accepts only 50..4000.
    dify_parent_max_tokens: int = Field(default=2000, ge=50, le=4000)
    cleaning_realtime_rpm: int = Field(default=60, ge=1)
    cleaning_realtime_tpm: int = Field(default=500_000, ge=10_000)
    cleaning_price_version: str = ""
    cleaning_price_effective_date: str = ""
    cleaning_price_valid_until: str = ""
    cleaning_input_price_per_million: float = Field(default=0, ge=0)
    cleaning_output_price_per_million: float = Field(default=0, ge=0)
    dify_publish_window_start_hour: int = Field(default=0, ge=0, le=23)
    dify_publish_window_end_hour: int = Field(default=24, ge=1, le=24)
    dify_shard_max_items: int = Field(default=200, ge=1, le=200)
    dify_shard_max_bytes: int = Field(default=1_000_000, ge=1024)

    history_retention_days: int = Field(default=7, ge=1, le=365)
    # The catalogue refresh time is a product decision, not a deployment
    # preference.  Keeping it literal prevents an unnoticed environment
    # override from moving the course snapshot away from 02:00 Asia/Shanghai.
    cleanup_hour: int = Field(default=2, ge=0, le=23)
    cleanup_minute: int = Field(default=0, ge=0, le=59)
    summary_user_turns: int = Field(default=6, ge=1, le=100)
    summary_approx_chars: int = Field(default=6000, ge=100)
    summary_batch_max_chars: int = Field(default=40000, ge=1000, le=55000)
    summary_batch_max_messages: int = Field(default=100, ge=2, le=500)
    context_max_chars: int = Field(default=24000, ge=1000)
    context_max_messages: int = Field(default=50, ge=4, le=500)
    max_query_chars: int = Field(default=12000, ge=100)

    max_upload_file_mb: int = Field(default=200, ge=1, le=200)
    max_upload_files: int = Field(default=10, ge=1, le=100)
    max_upload_batch_mb: int = Field(default=500, ge=1)
    max_active_cleaning_batches: int = Field(default=5, ge=1, le=1000)
    cleaning_worker_concurrency: int = Field(default=4, ge=1, le=32)
    upload_disk_reserve_mb: int = Field(default=1024, ge=128)
    # Test environments may keep conversion output for server-side diagnosis;
    # production defaults to false and never exposes this path through API.
    save_conversion_markdown: bool = False
    conversion_markdown_root: Path = Path("var/conversion-markdown")
    failed_file_retention_hours: int = Field(default=24, ge=1)
    cleaning_status_retention_days: int = Field(default=7, ge=1)
    cleaning_source_error_retention_days: int = Field(default=7, ge=0, le=30)
    cleaning_markdown_split_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    system_log_retention_days: int = Field(default=30, ge=1, le=3650)
    click_counter_retention_days: int = Field(default=30, ge=2, le=365)
    upload_root: Path = Path("var/uploads")
    cleaned_root: Path = Path("var/cleaned")
    audit_root: Path = Path("var/audit")
    build_version: str = "development"

    @property
    def knowledge_base_url(self) -> str:
        return (self.dify_knowledge_base_url or self.dify_base_url).rstrip("/")

    @property
    def dify_url(self) -> str:
        return self.dify_base_url.rstrip("/")

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if (self.cleanup_hour, self.cleanup_minute) != (2, 0):
            raise ValueError("课程目录夜间刷新必须为 Asia/Shanghai 02:00")
        if self.app_env != "production":
            return self
        if self.save_conversion_markdown:
            raise ValueError("生产环境禁止保存转换前策略 Markdown")
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
            "bailian_api_key",
        ):
            if not getattr(self, field_name):
                missing.append(field_name.upper())
        if missing:
            raise ValueError(f"生产环境缺少配置：{', '.join(missing)}")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
