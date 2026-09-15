"""
Konfigurasi aplikasi berbasis environment variable.

Menggunakan pydantic-settings agar semua konfigurasi (DATABASE_URL, Redis, dsb)
terpusat, tervalidasi tipe datanya, dan mudah di-override lewat file `.env`
atau environment variable dari docker-compose.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # -- Database --
    DATABASE_URL: str = "postgresql+psycopg2://gwas_user:gwas_password@db:5432/gwas_db"

    # -- Celery / Redis --
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"

    # -- Storage --
    STORAGE_ROOT: str = "/data/storage"

    # -- Security (dipakai modul auth, disiapkan di sini agar terpusat) --
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
