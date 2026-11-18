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


def is_under_storage(path: Path) -> bool:
    """Return True when resolved path is strictly inside settings.STORAGE_ROOT."""
    root = Path(settings.STORAGE_ROOT).resolve()
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
        return resolved != root
    except (OSError, ValueError):
        return False


def remove_tree_under_storage(path: Path) -> int:
    """Safely remove a directory strictly under STORAGE_ROOT and return freed bytes."""
    if not is_under_storage(path) or not path.is_dir():
        return 0
    try:
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        shutil.rmtree(path)
        return size
    except OSError as exc:
        logger.warning("Gagal menghapus direktori storage %s: %s", path, exc)
        return 0


def _is_deleted_value(value: Any) -> bool:
    """Only treat an explicit datetime/timestamp (not MagicMock placeholders) as deleted."""
    return isinstance(value, (datetime, str)) and bool(value)


def remove_job_storage(job_id: uuid.UUID | str, work_dir: str | None = None) -> int:
    """Remove STORAGE_ROOT/jobs/<job_id> and any additional work_dir inside STORAGE_ROOT."""
    freed = remove_tree_under_storage(Path(settings.STORAGE_ROOT) / "jobs" / str(job_id))
    if work_dir and isinstance(work_dir, (str, Path)):
        wd = Path(work_dir)
        storage_root = Path(settings.STORAGE_ROOT).resolve()
        try:
            wd_resolved = wd.resolve()
        except OSError:
            return freed
        if wd_resolved.parent != storage_root and wd.exists():
            freed += remove_tree_under_storage(wd)
    return freed


def cleanup_if_job_deleted(job_id: str | None, db: Any = None) -> int:
    """Remove residual job storage directory if the job record is marked deleted in DB."""
    if not job_id:
        return 0
    try:
        target_id = uuid.UUID(str(job_id))
    except (ValueError, TypeError):
        return 0
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        try:
            row = (
                db.query(AnalysisJob.deleted_at, AnalysisJob.work_dir)
                .filter(AnalysisJob.id == target_id)
                .first()
            )
        except Exception:
            job = db.get(AnalysisJob, target_id)
            row = (
                (getattr(job, "deleted_at", None), getattr(job, "work_dir", None))
                if job is not None
                else None
            )
        if row is None:
            return 0
        deleted_at = row[0] if isinstance(row, tuple) else getattr(row, "deleted_at", None)
        work_dir = row[1] if isinstance(row, tuple) else getattr(row, "work_dir", None)
        if not _is_deleted_value(deleted_at):
            return 0
        return remove_job_storage(target_id, work_dir)
    except Exception as exc:
        logger.warning("Cek hapus storage job %s gagal: %s", job_id, exc)
        return 0
    finally:
        if close_db:
            db.close()


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


def ensure_not_cancelled(job_id: str, db: Any = None, run_id: str | None = None) -> None:
    """Check if the job has been cancelled or superseded by a newer run_id; raise JobCancelledError if so."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        try:
            target_id = uuid.UUID(str(job_id))
        except (ValueError, TypeError):
            target_id = job_id
        try:
            job = db.get(AnalysisJob, target_id, populate_existing=True)
        except TypeError:
            job = db.get(AnalysisJob, target_id)
        if job is None:
            return
        if _is_deleted_value(getattr(job, "deleted_at", None)):
            remove_job_storage(job_id, getattr(job, "work_dir", None))
            raise JobCancelledError(f"Job {job_id} telah dihapus oleh pengguna.")
        status_val = job.overall_status.value if hasattr(job.overall_status, "value") else str(job.overall_status)
        if status_val == JobStatus.CANCELLED.value:
            raise JobCancelledError(f"Job {job_id} telah dibatalkan oleh pengguna.")
        if run_id is not None:
            job_params = getattr(job, "parameters", None)
            stored_run_id = job_params.get("run_id") if isinstance(job_params, dict) else None
            if stored_run_id and str(stored_run_id) != str(run_id):
                raise JobCancelledError(f"Eksekusi usang: job {job_id} sudah di-retry.")
    finally:
        if close_db:
            db.close()


def _job_guard_state(db: Any, job_id: Any) -> tuple[JobStatus | None, bool, str | None]:
    try:
        target_id = uuid.UUID(str(job_id))
    except (ValueError, TypeError):
        return None, False, None
    q = db.query(AnalysisJob.overall_status, AnalysisJob.deleted_at, AnalysisJob.work_dir).filter(
        AnalysisJob.id == target_id
    )
    if hasattr(q, "with_for_update") and hasattr(q, "first"):
        row = q.with_for_update().first()
        if row is None:
            return None, False, None
        return row[0], _is_deleted_value(row[1]), row[2]
    job = db.get(AnalysisJob, target_id)
    if job is None:
        return None, False, None
    return (
        getattr(job, "overall_status", None),
        _is_deleted_value(getattr(job, "deleted_at", None)),
        getattr(job, "work_dir", None),
    )


def current_status(db: Any, job_id: Any) -> JobStatus | None:
    status_val, _, _ = _job_guard_state(db, job_id)
    return status_val


def guard_status_change(db: Any, job_id: Any, new_status: JobStatus | None) -> bool:
    """
    Kembalikan True jika status boleh ditulis.
    - Job sudah dihapus -> bersihkan sisa folder job, return False untuk FAILED/CANCELLED, atau raise JobCancelledError
    - Job sudah CANCELLED + new_status FAILED/CANCELLED -> False (biarkan CANCELLED, jangan raise)
    - Job sudah CANCELLED + new_status lain (RUNNING_*, COMPLETED, ...) -> raise JobCancelledError
    - Selain itu -> True
    """
    status_val, is_deleted, work_dir = _job_guard_state(db, job_id)
    if is_deleted:
        remove_job_storage(job_id, work_dir)
        if new_status is None or new_status in {JobStatus.FAILED, JobStatus.CANCELLED}:
            return False
        raise JobCancelledError(f"Job {job_id} telah dihapus oleh pengguna.")
    if new_status is None:
        return True
    if status_val != JobStatus.CANCELLED:
        return True
    if new_status in {JobStatus.FAILED, JobStatus.CANCELLED}:
        return False
    raise JobCancelledError(f"Job {job_id} telah dibatalkan oleh pengguna.")


def is_job_cancelled(job_id: str, run_id: str | None = None) -> bool:
    """Return True if persisted job is CANCELLED, deleted, or has a different run_id; DB errors return False."""
    try:
        with SessionLocal() as db:
            try:
                target_id = uuid.UUID(str(job_id))
            except (ValueError, TypeError):
                return False
            row = (
                db.query(AnalysisJob.overall_status, AnalysisJob.parameters, AnalysisJob.deleted_at)
                .filter(AnalysisJob.id == target_id)
                .first()
            )
            if row is None:
                return False
            status_val = row[0]
            params = row[1]
            deleted_at = row[2] if len(row) > 2 else None
            if _is_deleted_value(deleted_at) or status_val == JobStatus.CANCELLED:
                return True
            if run_id is not None and isinstance(params, dict):
                stored_run_id = params.get("run_id")
                if stored_run_id and str(stored_run_id) != str(run_id):
                    return True
            return False
    except Exception as exc:
        logger.warning("Cek pembatalan gagal untuk job %s: %s", job_id, exc)
        return False


_UNSET: Any = object()


def mark_job(
    target: Any,
    job_or_none: Any = None,
    *,
    status: JobStatus | None = None,
    overall_status: JobStatus | None = None,
    current_step: Any = None,
    error: Any = _UNSET,
    error_message: Any = _UNSET,
    work_dir: str | None = None,
    extra_params: dict[str, Any] | None = None,
    session_factory: Any = None,
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
        db = (session_factory or SessionLocal)()
        close_db = True
    else:
        # target is job_id (str or UUID)
        job_id = target
        db = (session_factory or SessionLocal)()
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
        if new_status is not None and guard_status_change(db, job.id, new_status):
            job.overall_status = new_status
        if current_step is not None:
            job.current_step = current_step
        err_val = error_message if error_message is not _UNSET else error
        if err_val is not _UNSET:
            job.error_message = err_val
            job.error_log = err_val
        if work_dir is not None:
            job.work_dir = work_dir
        if extra_params is not None:
            job.parameters = {**(job.parameters or {}), **extra_params}

        for k, v in fields.items():
            if hasattr(job, k):
                setattr(job, k, v)
        db.commit()
    except JobCancelledError:
        db.rollback()
        raise
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


_IS_WINDOWS = os.name == "nt"
_WINDOWS_ONLY_SUFFIXES = {".cmd", ".bat", ".exe"}


def _is_usable_local_tool(path: Path) -> bool:
    """Kandidat lokal hanya dipakai bila bisa dijalankan di OS saat ini."""
    if not path.is_file():
        return False
    if _IS_WINDOWS:
        return True
    suffix = path.suffix.lower()
    if suffix in _WINDOWS_ONLY_SUFFIXES:
        return False
    if suffix == "":
        return os.access(path, os.X_OK)
    return True  # mis. .jar dijalankan lewat `java -jar`, tidak perlu bit executable


def which_tool(name: str, default: str | None = None) -> str | None:
    """Resolve CLI executable path from environment variable, local backend folders, or system PATH."""
    clean_name = name.strip()
    norm_upper = clean_name.upper()

    # 1. Direct environment variable lookup
    env_keys = [clean_name, f"{norm_upper}_BINARY_PATH"]
    if norm_upper == "PLINK2":
        env_keys.extend(["PLINK2", "PLINK2_PATH"])
    elif norm_upper == "PLINK":
        env_keys.extend(["PLINK", "PLINK_PATH"])
    elif norm_upper == "BCFTOOLS":
        env_keys.extend(["BCFTOOLS_BINARY_PATH", "BCFTOOLS_PATH"])
    elif norm_upper == "GEMMA":
        env_keys.extend(["GEMMA_BINARY_PATH", "GEMMA_PATH"])
    elif norm_upper == "BEAGLE_JAR":
        env_keys.extend(["BEAGLE_JAR_PATH", "IMPUTATION_JAR_PATH"])
    elif norm_upper in {"BEAGLE", "IMPUTATION"}:
        env_keys.extend(["IMPUTATION_BINARY_PATH", "BEAGLE_JAR_PATH", "BEAGLE_BINARY_PATH"])
    elif norm_upper in {"RSCRIPT", "R"}:
        env_keys.extend(["R_BINARY_PATH", "RSCRIPT_BINARY_PATH"])

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
    if norm_upper == "PLINK2":
        local_candidates.extend([
            backend_root / "plink2_win64_20261012" / "plink2.exe",
            backend_root / "plink" / "plink2.exe",
            backend_root / "bin" / "plink2.exe",
        ])
    elif norm_upper == "PLINK":
        local_candidates.extend([
            backend_root / "plink" / "plink.exe",
            backend_root / "bin" / "plink.exe",
            backend_root / "plink2_win64_20261012" / "plink2.exe",
            backend_root / "plink" / "plink2.exe",
            backend_root / "bin" / "plink2.exe",
        ])
    elif norm_upper == "BCFTOOLS":
        local_candidates.extend([
            backend_root / "bcftools" / "bcftools.cmd",
            backend_root / "bcftools" / "bcftools.bat",
            backend_root / "bin" / "bcftools",
        ])
    elif norm_upper == "GEMMA":
        local_candidates.extend([
            backend_root / "gemma" / "gemma.cmd",
            backend_root / "gemma" / "gemma.bat",
            backend_root / "gemma" / "gemma",
        ])
    elif norm_upper == "BEAGLE_JAR":
        local_candidates.extend([
            backend_root / "tools" / "beagle" / "beagle.jar",
            backend_root / "beagle" / "beagle.jar",
            Path("/opt/tools/beagle.jar"),
        ])
    elif norm_upper in {"BEAGLE", "IMPUTATION"}:
        local_candidates.extend([
            backend_root / "tools" / "beagle" / "beagle.cmd",
            backend_root / "tools" / "beagle" / "beagle.bat",
            backend_root / "tools" / "beagle" / "beagle.jar",
            backend_root / "beagle" / "beagle.jar",
            Path("/opt/tools/beagle.jar"),
        ])
    elif norm_upper in {"RSCRIPT", "R"}:
        local_candidates.extend([
            backend_root / "tools" / "rscript.cmd",
            backend_root / "tools" / "rscript.bat",
        ])

    for cand in local_candidates:
        if _is_usable_local_tool(cand):
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
