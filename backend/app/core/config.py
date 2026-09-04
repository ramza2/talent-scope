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

    llm_base_url: str = "https://alzi-llm.openlink.kr"
    llm_api_key: str = "change-me"
    llm_model: str = "Qwen3-14B"

    vlm_base_url: str = "https://alzi-vlm.openlink.kr"
    vlm_api_key: str = "change-me"
    vlm_model: str = "Qwen2.5-VL-7B-Instruct"

    embedding_base_url: str = "change-me"
    embedding_api_key: str = "change-me"
    embedding_model: str = "bge-m3"

    talentscope_host: str = "localhost"

    # Embedding dimension fixed by db/schema.sql (BGE-M3)
    embedding_dimensions: int = 1024

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local", "test"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
