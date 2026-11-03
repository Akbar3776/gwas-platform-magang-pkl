"""Common helpers, task utilities, and cancellation checks for GWAS pipeline tasks."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
from typing import Any
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, JobStatus, Result, ResultType

logger = logging.getLogger(__name__)


class JobCancelledError(RuntimeError):
    """Raised when an active task detects that the job was cancelled by the user."""


def stage_dir(job_id: str, stage: str) -> Path:
    """Return and create the working directory for a given pipeline stage."""
    target = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / stage
    target.mkdir(parents=True, exist_ok=True)
    return target


def append_log(log_file: Path, message: str, with_timestamp: bool = False) -> None:
    """Safely append a message to the specified stage log file."""
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8", errors="replace") as f:
            if with_timestamp:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                f.write(f"[{ts}] {message.rstrip()}\n")
            else:
                f.write(f"{message.rstrip()}\n")
    except OSError as exc:
        logger.warning("Gagal menulis ke log file %s: %s", log_file, exc)


def ensure_not_cancelled(job_id: str, db: Any = None) -> None:
    """Check if the job has been cancelled by the user; raise JobCancelledError if so."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        try:
            target_id = uuid.UUID(str(job_id))
        except (ValueError, TypeError):
            target_id = job_id
        job = db.get(AnalysisJob, target_id)
        if job is None:
            return
        status_val = job.overall_status.value if hasattr(job.overall_status, "value") else str(job.overall_status)
        if status_val == JobStatus.CANCELLED.value:
            raise JobCancelledError(f"Job {job_id} telah dibatalkan oleh pengguna.")
    finally:
        if close_db:
            db.close()


def mark_job(
    target: Any,
    job_or_none: Any = None,
    *,
    status: JobStatus | None = None,
    overall_status: JobStatus | None = None,
    current_step: Any = None,
    error: str | None = None,
    error_message: str | None = None,
    work_dir: str | None = None,
    extra_params: dict[str, Any] | None = None,
    **fields: Any,
) -> None:
    """
    Update job fields safely.
    Supports either mark_job(job_id, status=...) or mark_job(db, job, overall_status=...).
    """
    db = None
    job = None
    close_db = False

    # Check if target is a DB session: mark_job(db, job, ...)
    if hasattr(target, "commit") and (hasattr(target, "get") or hasattr(target, "query")):
        db = target
        job = job_or_none
    elif isinstance(target, AnalysisJob):
        job = target
        db = SessionLocal()
        close_db = True
    else:
        # target is job_id (str or UUID)
        job_id = target
        db = SessionLocal()
        close_db = True
        try:
            job_uuid = uuid.UUID(str(job_id)) if isinstance(job_id, str) else job_id
            job = db.get(AnalysisJob, job_uuid)
        except Exception:
            job = None

    if job is None:
        if close_db and db:
            db.close()
        return

    try:
        new_status = overall_status or status
        if new_status is not None:
            job.overall_status = new_status
        if current_step is not None:
            job.current_step = current_step
        new_err = error_message or error
        if new_err is not None:
            job.error_message = new_err
            job.error_log = new_err
        if work_dir is not None:
            job.work_dir = work_dir
        if extra_params is not None:
            job.parameters = {**(job.parameters or {}), **extra_params}

        for k, v in fields.items():
            if hasattr(job, k):
                setattr(job, k, v)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Gagal memperbarui job: %s", exc)
    finally:
        if close_db and db:
            db.close()


def upsert_result(
    db: Session,
    job_id: uuid.UUID | str,
    result_type: ResultType,
    file_path: str | None = None,
    summary_stats: dict[str, Any] | None = None,
) -> Result:
    """Insert or update a Result row for a given job and result_type."""
    parsed_job_id = uuid.UUID(str(job_id)) if isinstance(job_id, str) else job_id
    existing = (
        db.query(Result)
        .filter(Result.job_id == parsed_job_id, Result.result_type == result_type)
        .first()
    )
    if existing is not None:
        if file_path is not None:
            existing.file_path = file_path
        if summary_stats is not None:
            existing.summary_stats = summary_stats
        return existing

    new_result = Result(
        job_id=parsed_job_id,
        result_type=result_type,
        file_path=file_path,
        summary_stats=summary_stats,
    )
    db.add(new_result)
    return new_result


def which_tool(name: str, default: str | None = None) -> str | None:
    """Resolve CLI executable path from environment variable, local backend folders, or system PATH."""
    clean_name = name.strip()
    norm_upper = clean_name.upper()

    # 1. Direct environment variable lookup
    env_keys = [clean_name, f"{norm_upper}_BINARY_PATH"]
    if norm_upper in {"PLINK2", "PLINK"}:
        env_keys.extend(["PLINK2_BINARY_PATH", "PLINK_BINARY_PATH"])
    elif norm_upper == "BCFTOOLS":
        env_keys.extend(["BCFTOOLS_BINARY_PATH", "BCFTOOLS_PATH"])

    for k in env_keys:
        val = os.getenv(k, "").strip()
        if val:
            if Path(val).is_file():
                return str(Path(val).resolve())
            w = shutil.which(val)
            if w:
                return w

    # 2. Local backend directories lookup
    backend_root = Path(__file__).resolve().parent.parent.parent
    local_candidates: list[Path] = []
    if norm_upper in {"PLINK2", "PLINK"}:
        local_candidates.extend([
            backend_root / "plink2_win64_20261012" / "plink2.exe",
            backend_root / "plink" / "plink2.exe",
            backend_root / "plink" / "plink.exe",
            backend_root / "bin" / "plink2.exe",
            backend_root / "bin" / "plink.exe",
        ])
    elif norm_upper == "BCFTOOLS":
        local_candidates.extend([
            backend_root / "bcftools" / "bcftools.cmd",
            backend_root / "bcftools" / "bcftools.bat",
            backend_root / "tools" / "bcftools" / "bcftools.cmd",
            backend_root / "tools" / "bcftools" / "bcftools.bat",
            backend_root / "bin" / "bcftools",
        ])
    elif norm_upper == "GEMMA":
        local_candidates.extend([
            backend_root / "gemma" / "gemma.cmd",
            backend_root / "gemma" / "gemma.bat",
            backend_root / "gemma" / "gemma",
        ])

    for cand in local_candidates:
        if cand.is_file():
            return str(cand.resolve())

    # 3. System PATH fallback
    system_candidate = default if default is not None else clean_name
    if Path(system_candidate).is_file():
        return str(Path(system_candidate).resolve())
    return shutil.which(system_candidate)


def write_json(path: Path | str, obj: Any) -> None:
    """Write an object as formatted JSON."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(target.suffix + ".tmp")
    with temp_target.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    temp_target.replace(target)


def read_json(path: Path | str) -> Any:
    """Read a JSON file."""
    with Path(path).open("r", encoding="utf-8", errors="replace") as f:
        return json.load(f)
