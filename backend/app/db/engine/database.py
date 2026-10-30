"""
Setup koneksi database: engine SQLAlchemy 2.0, session factory, dan
dependency `get_db` untuk di-inject ke route FastAPI.

Catatan desain:
- `pool_pre_ping=True` agar koneksi mati (misal karena DB restart/idle timeout)
  otomatis dideteksi dan di-reconnect, bukan menyebabkan error acak.
- `pool_size`/`max_overflow` disesuaikan untuk beban dari FastAPI (request pendek)
  DAN Celery worker (yang juga meng-import module ini untuk update status job).
"""

from collections.abc import Generator
from contextlib import contextmanager
import logging
from pathlib import Path
import socket
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep

logger = logging.getLogger(__name__)


def _build_engine():
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql"):
        parsed = urlparse(db_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        if host in {"localhost", "127.0.0.1", "db"}:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.3)
            try:
                sock.connect((host, port))
                sock.close()
            except OSError:
                sqlite_path = (Path(__file__).resolve().parents[3] / "gwas_local.db").as_posix()
                logger.warning(
                    "PostgreSQL (%s:%s) tidak terjangkau; beralih otomatis ke SQLite lokal (%s)",
                    host,
                    port,
                    sqlite_path,
                )
                return create_engine(
                    f"sqlite:///{sqlite_path}",
                    connect_args={"check_same_thread": False},
                    future=True,
                )

    return create_engine(
        db_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        future=True,
    )


engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    """
    Dependency FastAPI: satu session per request, otomatis ditutup setelah selesai.

    Contoh pemakaian di route:
        @router.get("/datasets")
        def list_datasets(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def update_job_status(job_id: str, status: str, step: str, error: str | None = None) -> None:
    with get_db_session() as db:
        job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
        if job is None:
            return
        job.overall_status = JobStatus(status.lower())
        job.current_step = PipelineStep(step.lower())
        if error is not None:
            job.error_message = error
        db.commit()
