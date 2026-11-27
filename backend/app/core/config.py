"""
Konfigurasi aplikasi berbasis environment variable.

Menggunakan pydantic-settings agar semua konfigurasi (DATABASE_URL, Redis, dsb)
terpusat, tervalidasi tipe datanya, dan mudah di-override lewat file `.env`
atau environment variable dari docker-compose.
"""

import os
from pathlib import Path
import re

from pydantic import model_validator

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _HAS_PYDANTIC_SETTINGS = True
except ImportError:
    from pydantic import BaseModel as BaseSettings  # type: ignore[misc,assignment]
    _HAS_PYDANTIC_SETTINGS = False

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_BACKEND_ENV_FILE = _BACKEND_DIR / ".env"

if _BACKEND_ENV_FILE.is_file():
    for raw_line in _BACKEND_ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_DEFAULT_STORAGE = (
    str(_BACKEND_DIR / "data" / "storage") if os.name == "nt" else "/data/storage"
)
DEFAULT_NON_PRIMARY_CONTIG_REGEX = (
    r"(?i)^(scaffold|contig|ctg|unplaced|chrun)|_(random|unplaced|alt)$"
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

    # -- Upload Constraints --
    MAX_UPLOAD_SIZE_BYTES: int = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", "0"))
    MAX_CHUNK_SIZE_BYTES: int = int(os.getenv("MAX_CHUNK_SIZE_BYTES", str(16 * 1024 * 1024)))

    # -- Security (dipakai modul auth, disiapkan di sini agar terpusat) --
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
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

    # -- Bioinformatics Tools Paths --
    PLINK_BINARY_PATH: str = os.getenv("PLINK_BINARY_PATH", "")
    PLINK2_BINARY_PATH: str = os.getenv("PLINK2_BINARY_PATH", "")
    BCFTOOLS_BINARY_PATH: str = os.getenv("BCFTOOLS_BINARY_PATH", "")
    GEMMA_BINARY_PATH: str = os.getenv("GEMMA_BINARY_PATH", "")
    IMPUTATION_BINARY_PATH: str = os.getenv("IMPUTATION_BINARY_PATH", "")
    BEAGLE_JAR_PATH: str = os.getenv("BEAGLE_JAR_PATH", "")
    BEAGLE_LIB_DIR: str = os.getenv("BEAGLE_LIB_DIR", "")
    BEAGLE_LIB_BIN_DIR: str = os.getenv("BEAGLE_LIB_BIN_DIR", "")
    R_BINARY_PATH: str = os.getenv("R_BINARY_PATH", "")

    # -- Workflow 2026-11-21: Split Kromosom, Region Kandidat & KASP --
    CHROM_SPLIT_THRESHOLD_BP: int = int(os.getenv("CHROM_SPLIT_THRESHOLD_BP", "2147483647"))
    CHROM_SPLIT_UNIT_BP: int = int(os.getenv("CHROM_SPLIT_UNIT_BP", "1000000000"))
    CANDIDATE_REGION_MAX_TOTAL_BP: int = int(os.getenv("CANDIDATE_REGION_MAX_TOTAL_BP", "50000000"))
    KASP_FLANK_BP: int = int(os.getenv("KASP_FLANK_BP", "150"))
    NON_PRIMARY_CONTIG_REGEX: str = os.getenv(
        "NON_PRIMARY_CONTIG_REGEX",
        DEFAULT_NON_PRIMARY_CONTIG_REGEX,
    )

    # -- Batasan Unggahan Pemetaan GO & Teks Terbatas (G01) --
    GO_MAPPING_MAX_UPLOAD_BYTES: int = int(os.getenv("GO_MAPPING_MAX_UPLOAD_BYTES", "52428800"))
    GO_MAPPING_MAX_DECOMPRESSED_BYTES: int = int(os.getenv("GO_MAPPING_MAX_DECOMPRESSED_BYTES", "134217728"))
    GO_MAPPING_MAX_LINES: int = int(os.getenv("GO_MAPPING_MAX_LINES", "1000000"))
    GO_MAPPING_MIN_VALID_RATIO: float = float(os.getenv("GO_MAPPING_MIN_VALID_RATIO", "0.5"))
    UPLOAD_TEXT_MAX_LINE_BYTES: int = int(os.getenv("UPLOAD_TEXT_MAX_LINE_BYTES", "65536"))

    @model_validator(mode="after")
    def validate_workflow_settings(self) -> "Settings":
        if self.CHROM_SPLIT_THRESHOLD_BP <= 0:
            raise ValueError("CHROM_SPLIT_THRESHOLD_BP harus lebih besar dari 0.")
        if self.CHROM_SPLIT_UNIT_BP <= 0:
            raise ValueError("CHROM_SPLIT_UNIT_BP harus lebih besar dari 0.")
        if self.CANDIDATE_REGION_MAX_TOTAL_BP <= 0:
            raise ValueError("CANDIDATE_REGION_MAX_TOTAL_BP harus lebih besar dari 0.")
        if self.CHROM_SPLIT_UNIT_BP > self.CHROM_SPLIT_THRESHOLD_BP:
            raise ValueError(
                "CHROM_SPLIT_UNIT_BP tidak boleh melebihi CHROM_SPLIT_THRESHOLD_BP."
            )
        if self.KASP_FLANK_BP < 60:
            raise ValueError("KASP_FLANK_BP minimal 60 bp per sisi (syarat EasyKASP).")
        if self.NON_PRIMARY_CONTIG_REGEX:
            try:
                re.compile(self.NON_PRIMARY_CONTIG_REGEX)
            except re.error as exc:
                raise ValueError(f"NON_PRIMARY_CONTIG_REGEX tidak valid: {exc}") from exc
        if self.GO_MAPPING_MAX_UPLOAD_BYTES <= 0:
            raise ValueError("GO_MAPPING_MAX_UPLOAD_BYTES harus lebih besar dari 0.")
        if self.GO_MAPPING_MAX_DECOMPRESSED_BYTES <= 0:
            raise ValueError("GO_MAPPING_MAX_DECOMPRESSED_BYTES harus lebih besar dari 0.")
        if self.GO_MAPPING_MAX_LINES <= 0:
            raise ValueError("GO_MAPPING_MAX_LINES harus lebih besar dari 0.")
        if self.UPLOAD_TEXT_MAX_LINE_BYTES <= 0:
            raise ValueError("UPLOAD_TEXT_MAX_LINE_BYTES harus lebih besar dari 0.")
        if not (0.0 < self.GO_MAPPING_MIN_VALID_RATIO <= 1.0):
            raise ValueError("GO_MAPPING_MIN_VALID_RATIO harus berada dalam rentang (0, 1].")
        return self

    if _HAS_PYDANTIC_SETTINGS:
        model_config = SettingsConfigDict(
            env_file=(str(_BACKEND_ENV_FILE), ".env"),
            extra="ignore",
        )


settings = Settings()
if os.name == "nt" and settings.STORAGE_ROOT.startswith("/data"):
    settings.STORAGE_ROOT = _DEFAULT_STORAGE
