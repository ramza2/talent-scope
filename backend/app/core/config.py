"""Application settings loaded from environment variables / `.env`."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_secret_key: str = "change-me"
    database_url: str = (
        "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope"
    )
    redis_url: str = "redis://127.0.0.1:6379/0"

    s3_endpoint: str = "http://127.0.0.1:9000"
    s3_access_key: str = "change-me"
    s3_secret_key: str = "change-me"
    s3_bucket: str = "talent-scope"
    # Force path-style for MinIO / local S3-compatible endpoints.
    s3_force_path_style: bool = True

    # Upload limits (docs/15 — 413 on exceed)
    upload_max_file_size_mb: int = 50
    upload_max_files_per_session: int = 20
    upload_session_ttl_hours: int = 24

    # Document processing (LibreOffice headless)
    libreoffice_bin: str = "soffice"
    libreoffice_timeout_seconds: int = 120

    llm_base_url: str = "https://alzi-llm.openlink.kr"
    llm_api_key: str = "change-me"
    llm_model: str = "Qwen3-14B"

    vlm_base_url: str = "https://alzi-vlm.openlink.kr"
    vlm_api_key: str = "change-me"
    vlm_model: str = "Qwen2.5-VL-7B-Instruct"

    # Upload identify / AI runtime limits
    ai_request_timeout_seconds: float = 60.0
    identify_max_pages: int = 5
    identify_max_vlm_pages: int = 3
    identify_max_text_chars: int = 20000
    identify_pdf_render_dpi: int = 120

    # Detailed profile analysis limits
    analysis_max_pages_per_document: int = 10
    analysis_max_vlm_pages: int = 5
    analysis_max_total_text_chars: int = 40000
    analysis_pdf_render_dpi: int = 120
    analysis_code_context_max_chars: int = 12000
    analysis_max_quote_chars: int = 400
    analysis_max_source_refs: int = 5

    embedding_base_url: str = "change-me"
    embedding_api_key: str = "change-me"
    embedding_model: str = "bge-m3"

    talentscope_host: str = "localhost"

    # Embedding dimension fixed by db/schema.sql (BGE-M3 / VECTOR(1024))
    embedding_dimensions: int = 1024
    # Keep false until a real OpenAI-compatible embedding endpoint is configured.
    embedding_enabled: bool = False
    embedding_request_timeout_seconds: float = 60.0
    embedding_max_input_chars: int = 8000
    embedding_max_retries: int = 3
    embedding_retry_backoff_seconds: int = 60

    # Browser server-session auth (docs/15)
    session_cookie_name: str = "ts_session"
    session_ttl_seconds: int = 28800
    csrf_cookie_name: str = "ts_csrf"
    redis_key_prefix: str = "talentscope"

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local", "test"}

    @property
    def cookie_secure(self) -> bool:
        """Secure cookies in non-dev environments (HTTPS)."""
        return not self.is_development


@lru_cache
def get_settings() -> Settings:
    return Settings()
