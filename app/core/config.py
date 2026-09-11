"""Ilova sozlamalari — barchasi .env fayldan o'qiladi."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "stars-service"
    env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = False
    api_prefix: str = "/v1"
    cors_origins: list[str] = ["*"]

    # --- Security ---
    admin_api_key: str = "change-me-super-secret-admin-key"
    webhook_secret: str = "change-me-webhook-secret"

    # SSRF himoyasi: mijoz webhook_url'ni ichki manzilga yo'naltira olmaydi.
    # Faqat dev/test uchun true qiling (localhost listener bilan sinash).
    allow_private_webhook_urls: bool = False

    # PUL XAVFSIZLIGI. To'lov so'rovi yuborilgandan KEYIN timeout/uzilish
    # bo'lsa, tranzaksiya provayder tomonida o'tgan-o'tmaganini bilmaymiz —
    # `provider_ref` ham bo'sh qoladi. Avtomatik qayta urinish bunday holatda
    # IKKI MARTA to'lovga olib kelishi mumkin.
    #   False (standart) -> buyurtma `failed` bo'ladi, odam tekshiradi. Xavfsiz.
    #   True             -> avtomatik qayta uriniladi. Tezroq, lekin riskli.
    retry_on_network_timeout: bool = False

    # --- PostgreSQL ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "stars"
    postgres_password: str = "stars_password"
    postgres_db: str = "stars_service"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # --- Redis ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # --- Rate limit ---
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120

    # --- Order processing ---
    order_max_attempts: int = 3
    order_retry_backoff_seconds: int = 30
    order_job_timeout_seconds: int = 180
    webhook_max_attempts: int = 6
    webhook_timeout_seconds: int = 15

    # --- Providers ---
    default_provider: str = "mock"

    fragment_enabled: bool = False
    fragment_seed: str = ""
    fragment_cookies: str = ""
    fragment_kyc: bool = False
    fragment_show_sender: bool = False

    botapi_enabled: bool = False
    bot_token: str = ""
    botapi_base_url: str = "https://api.telegram.org"

    # --- Business limits ---
    min_stars: int = 50
    max_stars: int = 1_000_000
    allowed_premium_months: list[int] = Field(default=[3, 6, 12])

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Alembic uchun (psycopg2 emas, asyncpg async engine ishlatiladi —
        bu faqat log/diagnostika uchun ko'rsatiladi)."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
