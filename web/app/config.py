"""ROADVISION_WEB_* ortam değişkenlerinden okunan servis ayarları."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Web API yapılandırması.

    Alan adları ``ROADVISION_WEB_`` önekiyle ortam değişkenine eşlenir:
    ``dsn`` → ``ROADVISION_WEB_DSN`` vb. DSN, salt-okunur ``roadvision_web``
    rolüne aittir; masaüstünün sahip DSN'i burada KULLANILMAZ.
    """

    model_config = SettingsConfigDict(env_prefix="ROADVISION_WEB_", extra="ignore")

    dsn: str
    pool_min: int = 1
    pool_max: int = 4

    # Faz 1 — kimlik (WEB_PLANI.md §8). cookie_secure varsayılanı yereldeki
    # http geliştirme için False'tur; Faz 2'de nginx+TLS arkasında True
    # yapılmalıdır. API zaten yalnız 127.0.0.1'e yayınlanır.
    cookie_secure: bool = False
    session_ttl_hours: int = 12
    session_idle_minutes: int = 30
    login_rate_per_minute: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
