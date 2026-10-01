from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_CLEANING_TEST_DATABASE_NAME = "ai_management_cleaning_test"
_CLEANING_TEST_DATABASE_USER = "ai_management_cleaning_test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    internal_api_token: str = "development-internal-token"
    admin_api_token: str = "development-admin-token"
    user_hmac_secret: str = "development-user-hmac-secret"
    public_base_url: str = "http://127.0.0.1:8000"
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8000, ge=1, le=65535)

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
    dify_course_dataset_enabled: bool = True
    # Isolation switch used by the disposable cleaning-test deployment.  The
    # check is enforced at the API dataset boundary, not only by Streamlit.
    cleaning_general_only: bool = False
    cleaning_test_dataset_id: str = "19206ce0-80a4-4a93-aa86-63e3385d2b2a"
    cleaning_test_isolation_id: str = ""
    cleaning_test_database_name: str = _CLEANING_TEST_DATABASE_NAME
    cleaning_test_database_user: str = _CLEANING_TEST_DATABASE_USER
    cleaning_test_redis_db: int = Field(default=6, ge=0, le=15)

    bailian_api_key: str = ""
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    cleaning_model: str = "qwen-plus"
    cleaning_prompt_version: str = "commercial-cleaning-v2"
    # Bump only when stored-cleaning semantics change (sanitization, strategy
    # contract, chunking or publication policy).  It is deliberately distinct
    # from a build version so routine releases do not re-index every source.
    cleaning_spec_revision: str = "commercial-cleaning-v3"
    cleaning_batch_candidate_threshold: int = Field(default=100, ge=1, le=50_000)
    cleaning_batch_token_threshold: int = Field(default=200_000, ge=1)
    cleaning_batch_max_requests: int = Field(default=40_000, ge=1, le=50_000)
    cleaning_batch_max_bytes: int = Field(default=400 * 1024 * 1024, ge=1024, le=500 * 1024 * 1024)
    # Batch is opt-in.  New deployments and test runs use the bounded
    # realtime worker unless an operator explicitly enables Bailian Batch;
    # an existing task keeps the mode stored on its record.
    bailian_batch_enabled: bool = False
    # This second switch is meaningful only for the disposable, general-only
    # cleaning-test deployment.  It prevents a generic Batch environment
    # override from silently converting the isolated acceptance boundary into
    # a paid Batch run.
    cleaning_test_batch_acceptance_enabled: bool = False
    cleaning_candidate_max_tokens: int = Field(default=12_000, ge=1000)
    # Bound provider output for both realtime and Batch JSON requests. The
    # planning guard reserves this maximum for each allowed provider call.
    cleaning_model_max_tokens: int = Field(default=8192, ge=512, le=32768)
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
    # Realtime calls use an independent, non-discounted price snapshot.
    cleaning_realtime_price_version: str = ""
    cleaning_realtime_price_effective_date: str = ""
    cleaning_realtime_price_valid_until: str = ""
    cleaning_realtime_input_price_per_million: float = Field(default=0, ge=0)
    cleaning_realtime_output_price_per_million: float = Field(default=0, ge=0)
    cleaning_daily_budget_yuan: float = Field(default=10.0, ge=0)
    # Content-model cleaning only: short source entries are a human-review
    # condition.  This is server-side policy; it is snapshotted per batch by
    # the cleaning admission path and never accepted from upload metadata.
    cleaning_review_required_enabled: bool = True
    cleaning_review_min_chars: int = Field(default=200, ge=0)
    cleaning_review_force_strategies: str = ""
    # Acceptance-only cumulative cap for paid cleaning calls.  It is a
    # machine-side safety rail, not a long-term product quota.  Deploy scripts
    # must set a default without overwriting an operator's local override.
    acceptance_cleaning_budget_max_yuan: float = Field(
        default=5.0, alias="JX_ACCEPTANCE_CLEANING_BUDGET_MAX_YUAN", ge=0
    )
    # Explicit opt-in for the disposable ys acceptance ledger.  This remains
    # independent from CLEANING_GENERAL_ONLY because ys validates the
    # general dataset at a higher boundary while still exercising all normal
    # application routes.
    cleaning_acceptance_budget_enabled: bool = False
    # The course catalogue is an upload-only workflow.  It may auto-approve
    # its own model task, but only while the configured price snapshot is
    # valid and the estimated upper bound stays below this internal cap.
    course_catalog_auto_publish: bool = True
    course_catalog_auto_approve_cost_max: float = Field(default=1.0, ge=0)
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
    ooxml_max_entries: int = Field(default=4096, ge=1, le=100_000)
    ooxml_max_total_uncompressed_mb: int = Field(default=512, ge=1, le=4096)
    ooxml_max_entry_mb: int = Field(default=256, ge=1, le=2048)
    ooxml_max_compression_ratio: int = Field(default=200, ge=1, le=10_000)
    common_metadata_max_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    common_metadata_max_depth: int = Field(default=6, ge=1, le=32)
    common_metadata_max_keys: int = Field(default=128, ge=1, le=10_000)
    common_metadata_max_list_items: int = Field(default=256, ge=1, le=10_000)
    common_metadata_max_string_chars: int = Field(default=8192, ge=1, le=1_000_000)
    catalog_review_max_mb: int = Field(default=50, ge=1, le=512)
    review_markdown_max_mb: int = Field(default=20, ge=1, le=512)
    max_active_cleaning_batches: int = Field(default=5, ge=1, le=1000)
    cleaning_worker_concurrency: int = Field(default=4, ge=1, le=32)
    # OCR is deliberately opt-in.  The page limit only applies to scanned
    # PDFs which need the on-demand RapidOCR path; text-layer PDFs are not
    # limited.  The backend name is explicit so a rollback can disable OCR
    # without changing the HTTP contract or worker code.
    pdf_ocr_enabled: bool = False
    pdf_ocr_backend: Literal["rapidocr_onnxruntime", "disabled"] = "rapidocr_onnxruntime"
    pdf_ocr_max_pages: int = Field(default=50, ge=1, le=50)
    pdf_ocr_task_timeout_seconds: int = Field(default=3600, ge=600, le=3600)
    pdf_ocr_idle_seconds: int = Field(default=600, ge=60, le=3600)
    pdf_ocr_render_dpi: int = Field(default=200, ge=72, le=600)
    pdf_ocr_retry_dpi: int = Field(default=300, ge=72, le=600)
    pdf_ocr_auto_confidence: float = Field(default=0.90, ge=0, le=1)
    pdf_ocr_min_confidence: float = Field(default=0.75, ge=0, le=1)
    pdf_ocr_max_low_conf_ratio: float = Field(default=0.10, ge=0, le=1)
    pdf_ocr_cpu_threads: int = Field(default=2, ge=1, le=16)
    pdf_ocr_model_root: Path = Path("var/ocr-models/rapidocr")
    pdf_ocr_model_manifest: Path = Path("var/ocr-models/rapidocr/manifest.json")
    upload_disk_reserve_mb: int = Field(default=1024, ge=128)
    # Server-side diagnostic artifact. Deployments decide whether it is kept;
    # the path is never exposed through HTTP and must be a dedicated directory.
    save_conversion_markdown: bool = False
    conversion_markdown_root: Path = Path("var/conversion-markdown")
    conversion_markdown_retention_days: int = Field(default=7, ge=1, le=30)
    failed_file_retention_hours: int = Field(default=24, ge=1)
    cleaning_status_retention_days: int = Field(default=7, ge=1)
    cleaning_source_error_retention_days: int = Field(default=7, ge=0, le=30)
    cleaning_markdown_split_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    system_log_retention_days: int = Field(default=30, ge=1, le=3650)
    click_counter_retention_days: int = Field(default=30, ge=2, le=365)
    upload_root: Path = Path("var/uploads")
    cleaned_root: Path = Path("var/cleaned")
    audit_root: Path = Path("var/audit")
    acceptance_campaign_ledger_path: Path = Field(
        default=Path("var/audit/acceptance-campaign-ledger.json"),
        alias="JX_ACCEPTANCE_CAMPAIGN_LEDGER_PATH",
    )
    model_review_retention_days: int = Field(default=30, ge=1, le=3650)
    model_review_max_bytes: int = Field(
        default=512 * 1024 * 1024, ge=1 * 1024 * 1024, le=10 * 1024 * 1024 * 1024
    )
    build_version: str = "development"

    @property
    def knowledge_base_url(self) -> str:
        return (self.dify_knowledge_base_url or self.dify_base_url).rstrip("/")

    @property
    def dify_url(self) -> str:
        return self.dify_base_url.rstrip("/")

    @model_validator(mode="after")
    def validate_ocr_limits(self) -> "Settings":
        if self.pdf_ocr_enabled and self.pdf_ocr_backend == "disabled":
            raise ValueError("PDF_OCR_ENABLED=true 时 PDF_OCR_BACKEND 不能为 disabled")
        minimum_timeout = max(600, self.pdf_ocr_max_pages * 60)
        if self.pdf_ocr_task_timeout_seconds < minimum_timeout:
            raise ValueError(
                "PDF_OCR_TASK_TIMEOUT_SECONDS 必须 >= "
                f"max(600, PDF_OCR_MAX_PAGES*60) ({minimum_timeout})"
            )
        if self.pdf_ocr_retry_dpi < self.pdf_ocr_render_dpi:
            raise ValueError("PDF_OCR_RETRY_DPI 不能低于 PDF_OCR_RENDER_DPI")
        if self.pdf_ocr_min_confidence > self.pdf_ocr_auto_confidence:
            raise ValueError("PDF_OCR_MIN_CONFIDENCE 不能高于 PDF_OCR_AUTO_CONFIDENCE")
        return self

    @model_validator(mode="after")
    def validate_cleaning_test_isolation(self) -> "Settings":
        if self.cleaning_acceptance_budget_enabled and self.app_env != "test":
            raise ValueError("清洗验收累计预算只能在 APP_ENV=test 下启用")
        if self.cleaning_acceptance_budget_enabled and self.acceptance_cleaning_budget_max_yuan > 5:
            raise ValueError("隔离清洗累计模型预算不能超过 5 元")
        if self.cleaning_acceptance_budget_enabled and self.cleaning_daily_budget_yuan > 4:
            raise ValueError("隔离清洗日预算不能超过 4 元")
        if (
            self.cleaning_acceptance_budget_enabled
            and self.cleaning_daily_budget_yuan > self.acceptance_cleaning_budget_max_yuan
        ):
            raise ValueError("隔离清洗日预算不能超过累计模型预算")
        if not self.cleaning_general_only:
            return self
        if (
            not self.cleaning_test_dataset_id
            or self.dify_general_dataset_id != self.cleaning_test_dataset_id
        ):
            raise ValueError(
                "CLEANING_GENERAL_ONLY requires DIFY_GENERAL_DATASET_ID "
                "to equal CLEANING_TEST_DATASET_ID"
            )
        if self.app_env != "test":
            raise ValueError("CLEANING_GENERAL_ONLY 只能在 APP_ENV=test 下运行")
        if self.cleaning_test_isolation_id != "ai-management-cleaning-test":
            raise ValueError("CLEANING_GENERAL_ONLY requires the fixed test isolation ID")

        from urllib.parse import parse_qs, urlparse

        database = urlparse(self.database_url)
        redis = urlparse(self.redis_url)
        database_name = database.path.lstrip("/").split("?", 1)[0]
        redis_path = redis.path.strip("/").split("?", 1)[0]
        if (
            self.cleaning_test_database_name != _CLEANING_TEST_DATABASE_NAME
            or self.cleaning_test_database_user != _CLEANING_TEST_DATABASE_USER
            or database.scheme not in {"postgresql", "postgresql+psycopg"}
            or database_name != _CLEANING_TEST_DATABASE_NAME
            or database.username != _CLEANING_TEST_DATABASE_USER
        ):
            raise ValueError("CLEANING_GENERAL_ONLY requires the dedicated test database and user")
        if (
            redis.scheme not in {"redis", "rediss"}
            or redis_path != str(self.cleaning_test_redis_db)
            or self.cleaning_test_redis_db != 6
            # redis-py gives a ``db`` query argument precedence over the URL
            # path.  Reject it entirely in the fail-closed test profile so
            # every Cache/Celery consumer has one unambiguous DB target.
            or "db" in parse_qs(redis.query, keep_blank_values=True)
        ):
            raise ValueError("CLEANING_GENERAL_ONLY requires Redis DB 6")
        if self.bailian_batch_enabled != self.cleaning_test_batch_acceptance_enabled:
            raise ValueError("CLEANING_GENERAL_ONLY requires explicit Batch acceptance opt-in")
        return self

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if (self.cleanup_hour, self.cleanup_minute) != (2, 0):
            raise ValueError("课程目录夜间刷新必须为 Asia/Shanghai 02:00")
        non_loopback = self.bind_host not in {"127.0.0.1", "::1", "localhost"}
        if non_loopback and (
            self.internal_api_token.startswith("development-")
            or self.admin_api_token.startswith("development-")
            or self.user_hmac_secret.startswith("development-")
            or min(
                len(self.internal_api_token), len(self.admin_api_token), len(self.user_hmac_secret)
            )
            < 32
        ):
            raise ValueError("非回环监听必须使用长度至少32的高熵认证配置")
        if self.app_env != "production":
            return self
        if self.save_conversion_markdown:
            raise ValueError("生产环境禁止保存转换前策略 Markdown")
        missing: list[str] = []
        if self.internal_api_token.startswith("development-") or len(self.internal_api_token) < 32:
            missing.append("INTERNAL_API_TOKEN")
        if self.user_hmac_secret.startswith("development-") or len(self.user_hmac_secret) < 32:
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
            "bailian_api_key",
        ):
            if not getattr(self, field_name):
                missing.append(field_name.upper())
        if self.dify_course_dataset_enabled and not self.dify_course_dataset_id:
            missing.append("DIFY_COURSE_DATASET_ID")
        if missing:
            raise ValueError(f"生产环境缺少配置：{', '.join(missing)}")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
