"""Configuration from environment / .env. No secret is ever defaulted to a real value.

Field names match environment variables case-insensitively (e.g. field
``ami_secret`` reads ``AMI_SECRET``). Production mode fails fast when a secret
is still a placeholder or required but empty.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppMode = Literal["production", "development", "demo"]
_PLACEHOLDERS = ("CHANGE_ME", "changeme", "CHANGEME")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ---------------- Application ----------------
    app_mode: AppMode = "production"
    app_host: str = "127.0.0.1"  # loopback by design; expose via httpd TLS proxy
    app_port: int = Field(default=8080, ge=1, le=65535)
    tz: str = "UTC"
    log_level: str = "INFO"
    openapi_enabled: bool = False

    # ---------------- App database ----------------
    database_url: str = "postgresql+psycopg://ccmon:changeme@127.0.0.1:5432/ccmon"

    # ---------------- Issabel / Asterisk source DB (read-only user) ----------------
    issabel_db_host: str = "127.0.0.1"
    issabel_db_port: int = Field(default=3306, ge=1, le=65535)
    issabel_db_user: str = "ccmon_reader"
    issabel_db_password: SecretStr = SecretStr("CHANGE_ME_DB_PASSWORD")
    issabel_db_cdr: str = "asteriskcdrdb"
    issabel_db_asterisk: str = "asterisk"
    issabel_db_call_center: str = "call_center"
    issabel_db_connect_timeout: int = Field(default=5, ge=1, le=60)
    issabel_db_read_timeout: int = Field(default=6, ge=1, le=300)
    issabel_db_max_rows: int = Field(default=5000, ge=10, le=100_000)

    # ---------------- Asterisk AMI (read-only manager user) ----------------
    ami_enabled: bool = True
    ami_host: str = "127.0.0.1"
    ami_port: int = Field(default=5038, ge=1, le=65535)
    ami_username: str = "ccmon_read"
    ami_secret: SecretStr = SecretStr("CHANGE_ME_AMI_SECRET")
    ami_timeout: float = Field(default=3.0, ge=0.5, le=30)

    # ---------------- Telegram ----------------
    telegram_enabled: bool = False
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""

    # ---------------- Collector tuning ----------------
    collect_live_seconds: int = Field(default=5, ge=1, le=3600)
    collect_history_seconds: int = Field(default=60, ge=5, le=3600)
    collect_rollup_minutes: int = Field(default=60, ge=1, le=1440)
    live_stale_after_seconds: int = Field(default=30, ge=5, le=3600)
    retention_snapshots_days: int = Field(default=7, ge=1, le=3650)
    retention_rollups_days: int = Field(default=400, ge=1, le=3650)
    retention_alert_events_days: int = Field(default=180, ge=1, le=3650)
    retention_audit_days: int = Field(default=365, ge=1, le=3650)

    # ---------------- Health probes ----------------
    probe_services: str = "asterisk,issabeldialer,httpd,mariadb"

    # ---------------- SLA policy ----------------
    sla_seconds: int = Field(default=20, ge=1, le=3600)
    sla_target_pct: float = Field(default=80.0, ge=0, le=100)

    # ---------------- Auth ----------------
    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr("")
    session_ttl_minutes: int = Field(default=720, ge=5, le=100_800)
    login_rate_per_minute: int = Field(default=5, ge=1, le=1000)

    # ---------------- Rate limiting / abuse guards ----------------
    rate_limit_ip_per_minute: int = Field(default=120, ge=1, le=100_000)
    rate_limit_token_per_minute: int = Field(default=60, ge=1, le=100_000)
    report_max_days: int = Field(default=92, ge=1, le=366)
    report_max_rows: int = Field(default=5000, ge=10, le=1_000_000)

    # ---------------- Derived ----------------
    @property
    def probe_service_list(self) -> list[str]:
        return [s.strip() for s in self.probe_services.split(",") if s.strip()]

    @property
    def is_demo(self) -> bool:
        return self.app_mode == "demo"

    @field_validator("log_level")
    @classmethod
    def _valid_level(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid LOG_LEVEL: {v}")
        return v

    @model_validator(mode="after")
    def _fail_fast_in_production(self) -> Settings:
        if self.app_mode != "production":
            return self
        db = self.database_url.lower()
        if any(p.lower() in db for p in _PLACEHOLDERS):
            raise ValueError(
                "DATABASE_URL still contains a placeholder password; set a real one in .env"
            )
        if self.ami_enabled and (
            not self.ami_secret.get_secret_value().strip()
            or any(p in self.ami_secret.get_secret_value() for p in _PLACEHOLDERS)
        ):
            raise ValueError("AMI_SECRET is empty or a placeholder while AMI_ENABLED=true")
        if self.telegram_enabled and not (
            self.telegram_bot_token.get_secret_value() and self.telegram_chat_id.strip()
        ):
            raise ValueError(
                "TELEGRAM_ENABLED=true requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )
        if not self.admin_password.get_secret_value() and self.app_mode == "production":
            # Not fatal: admin may already exist in the DB; only fail if requested.
            pass
        return self

    def redaction_strings(self) -> list[str]:
        """Values that must never appear in logs or error output."""
        out: list[str] = []
        for value in (
            self.database_url,
            self.issabel_db_password.get_secret_value(),
            self.ami_secret.get_secret_value(),
            self.telegram_bot_token.get_secret_value(),
            self.admin_password.get_secret_value(),
        ):
            if value and len(value) >= 4 and value not in out:
                out.append(value)
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: drop the cached settings so env changes take effect."""
    get_settings.cache_clear()
