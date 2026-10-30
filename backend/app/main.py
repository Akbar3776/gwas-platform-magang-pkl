from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    auth,
    capabilities,
    export,
    jobs,
    pipeline,
    reference_panels,
    report,
    results,
    upload,
)
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.db.base import Base
from app.db.engine.database import SessionLocal, engine
from app.db.engine.models import ReferencePanel

configure_logging()
logger = logging.getLogger(__name__)


def _ensure_sqlite_columns(bind_engine) -> None:
    """Ensure newly introduced columns are present in SQLite local database."""
    if bind_engine.dialect.name != "sqlite":
        return
    try:
        with bind_engine.begin() as conn:
            cursor = conn.exec_driver_sql("PRAGMA table_info(datasets)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if "annotation_file_path" not in existing_cols:
                conn.exec_driver_sql("ALTER TABLE datasets ADD COLUMN annotation_file_path VARCHAR(1024)")
            if "extra_metadata" not in existing_cols:
                conn.exec_driver_sql("ALTER TABLE datasets ADD COLUMN extra_metadata JSON")
    except Exception as exc:
        logger.warning("Gagal memverifikasi/menambah kolom SQLite: %s", exc)


def _init_database() -> None:
    """Ensure tables exist and seed default reference panels when starting up."""
    try:
        Base.metadata.create_all(bind=engine)
        _ensure_sqlite_columns(engine)
        db = SessionLocal()
        try:
            if db.query(ReferencePanel).count() == 0:
                db.add_all(
                    [
                        ReferencePanel(
                            name="1000 Genomes Phase 3",
                            population="ALL",
                            genome_build="GRCh38",
                            description="Panel referensi global 1000 Genomes Phase 3 (GRCh38)",
                            vcf_paths={"ALL": "/data/reference-panels/1000g_phase3_grch38/reference.vcf.gz"},
                            genetic_map_paths={},
                            is_active=True,
                        ),
                        ReferencePanel(
                            name="1000 Genomes Phase 3",
                            population="ALL",
                            genome_build="GRCh37",
                            description="Panel referensi global 1000 Genomes Phase 3 (GRCh37/hg19)",
                            vcf_paths={"ALL": "/data/reference-panels/1000g_phase3_grch37/reference.vcf.gz"},
                            genetic_map_paths={},
                            is_active=True,
                        ),
                    ]
                )
                db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Database auto-init dilewati (DB mungkin belum aktif): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_database()
    yield


app = FastAPI(
    title="GWAS Platform API",
    description="Orkestrator pipeline GWAS end-to-end (normalisasi -> QC -> imputasi -> GWAS -> visualisasi)",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=getattr(settings, "CORS_ORIGINS", ["http://localhost:5173"]),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"

app.include_router(auth.router, prefix=API_PREFIX, tags=["Auth"])
app.include_router(upload.router, prefix=API_PREFIX, tags=["Datasets & Upload"])
app.include_router(jobs.router, prefix=API_PREFIX, tags=["Jobs"])
app.include_router(pipeline.router, prefix=API_PREFIX, tags=["Pipeline"])
app.include_router(capabilities.router, prefix=API_PREFIX, tags=["Pipeline Capabilities"])
app.include_router(results.router, prefix=API_PREFIX, tags=["Results"])
app.include_router(export.router, prefix=API_PREFIX, tags=["Export"])
app.include_router(report.router, prefix=API_PREFIX, tags=["Report"])
app.include_router(reference_panels.router, prefix=API_PREFIX, tags=["Reference Panels"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}
