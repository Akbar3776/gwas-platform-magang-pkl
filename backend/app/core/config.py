"""
Konfigurasi aplikasi berbasis environment variable.

Menggunakan pydantic-settings agar semua konfigurasi (DATABASE_URL, Redis, dsb)
terpusat, tervalidasi tipe datanya, dan mudah di-override lewat file `.env`
atau environment variable dari docker-compose.
"""

import os
from pathlib import Path

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _HAS_PYDANTIC_SETTINGS = True
except ImportError:
    from pydantic import BaseModel as BaseSettings  # type: ignore[misc,assignment]
    _HAS_PYDANTIC_SETTINGS = False

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_BACKEND_ENV_FILE = _BACKEND_DIR / ".env"

if not _HAS_PYDANTIC_SETTINGS and _BACKEND_ENV_FILE.is_file():
    for raw_line in _BACKEND_ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_DEFAULT_STORAGE = (
    str(_BACKEND_DIR / "data" / "storage") if os.name == "nt" else "/data/storage"
)
_raw_storage = os.getenv("STORAGE_ROOT", _DEFAULT_STORAGE)
if os.name == "nt" and _raw_storage.startswith("/data"):
    _raw_storage = _DEFAULT_STORAGE
os.environ.setdefault("UPLOAD_DIR", _raw_storage)


class Settings(BaseSettings):
    # -- Environment --
    ENV: str = os.getenv("ENV", "development")

    # -- Database --
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://gwas_user:gwas_password@db:5432/gwas_db",
    )

    # -- Celery / Redis --
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")

    # -- Storage --
    STORAGE_ROOT: str = _raw_storage

    # -- Security (dipakai modul auth, disiapkan di sini agar terpusat) --
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost",
    ]

    # -- Retention --
    RETENTION_DAYS: int = int(os.getenv("RETENTION_DAYS", "30"))
    RETENTION_HARD_DELETE: bool = os.getenv("RETENTION_HARD_DELETE", "false").lower() == "true"

    if _HAS_PYDANTIC_SETTINGS:
        model_config = SettingsConfigDict(
            env_file=(str(_BACKEND_ENV_FILE), ".env"),
            extra="ignore",
        )


settings = Settings()
if os.name == "nt" and settings.STORAGE_ROOT.startswith("/data"):
    settings.STORAGE_ROOT = _DEFAULT_STORAGE
