"""
Endpoint untuk men-trigger dan memantau AnalysisJob serta mengakses file-file job.

`GET  /jobs`                       -> daftar seluruh job milik user (berjalan & selesai)
`POST /jobs/qc`                    -> membuat job baru & men-trigger Celery task PLINK QC
`GET  /jobs/{job_id}/status`       -> dipoll oleh React untuk menampilkan progress bar & log per tahap
`GET  /jobs/{job_id}/logs`         -> mengembalikan seluruh log proses di setiap tahap pipeline
`GET  /jobs/{job_id}/files`        -> daftar file input & file hasil/intermediate di setiap tahap job
`GET  /jobs/{job_id}/files/content`-> unduh atau pratinjau isi file dari job yang sedang berjalan / selesai
`GET  /jobs/{job_id}/results`      -> ringkasan statistik hasil GWAS untuk ResultDashboard
"""

from datetime import datetime, timezone
import gzip
from pathlib import Path
import shutil
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, Result, ResultType, UploadStatus, User
from app.schemas.jobs import JobStatusResponse, JobTriggerResponse, QCJobRequest, StepLogEntry
from app.services.manhattan_parser import compute_assoc_summary_stats
from app.tasks.celery_app import celery_app
from app.tasks.qc_tasks import run_qc_pipeline

router = APIRouter(prefix="/jobs", tags=["jobs"])

_STEP_DEFINITIONS = [
    ("inspection", "Inspeksi Fenotipe & Sampel", "Python / Stats"),
    ("normalization", "Normalisasi VCF", "bcftools"),
    ("qc", "Quality Control", "PLINK 2 / 1.9"),
    ("imputation", "Imputasi Genotipe", "Beagle 5.4"),
    ("structure", "Struktur Populasi & LD", "PCA / Centered Kinship"),
    ("gwas", "Analisis Asosiasi GWAS", "GEMMA / GLM"),
    ("multilocus", "Multi-Locus GWAS", "FarmCPU / BLINK"),
    ("postgwas", "Pasca-GWAS & Clumping", "Dynamic Thresholds"),
    ("visualization", "Visualisasi & Cache Plot", "Parquet Engine"),
    ("annotation", "Anotasi Gen Kandidat", "GFF3 Parser"),
]

_STAGE_LABELS = {
    "input": "Input Dataset (VCF & Fenotipe)",
    "inspection": "Tahap 1: Inspeksi Fenotipe & Sampel",
    "normalization": "Tahap 2: Normalisasi VCF (bcftools)",
    "qc": "Tahap 3: Quality Control (PLINK 2/1.9)",
    "imputation": "Tahap 4: Imputasi (Beagle)",
    "structure": "Tahap 5: Struktur Populasi, Kinship & LD Decay",
    "gwas": "Tahap 6: Analisis Asosiasi GWAS (GEMMA/PLINK2)",
    "multilocus": "Tahap 7: Multi-Locus GWAS (FarmCPU/BLINK)",
    "postgwas": "Tahap 8: Pasca-GWAS & Clumping",
    "visualization": "Tahap 9: Visualisasi & Plot Cache",
    "annotation": "Tahap 10: Anotasi Gen Kandidat GFF3",
}

_STAGE_ORDER = {
    "input": 0,
    "inspection": 1,
    "normalization": 2,
    "qc": 3,
    "imputation": 4,
    "structure": 5,
    "gwas": 6,
    "multilocus": 7,
    "postgwas": 8,
    "visualization": 9,
    "annotation": 10,
}

_PREVIEWABLE_SUFFIXES = (
    ".vcf",
    ".vcf.gz",
    ".log",
    ".txt",
    ".bim",
    ".fam",
    ".tsv",
    ".csv",
    ".json",
    ".eigenvec",
    ".eigenval",
    ".clumps",
    ".gff",
    ".gff3",
    ".gff3.gz",
    ".nosex",
)


def _is_admin(user: User) -> bool:
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    return role_val == "admin"


def _read_log_tail(candidates: list[Path], max_lines: int = 2000) -> tuple[str, int]:
    merged_chunks: list[str] = []
    seen_files: set[str] = set()
    for path in candidates:
        if path and path.is_file():
            resolved = str(path.resolve())
            if resolved in seen_files:
                continue
            seen_files.add(resolved)
            try:
                raw = path.read_text(encoding="utf-8", errors="replace").strip()
                if raw and raw not in "\n".join(merged_chunks):
                    merged_chunks.append(raw)
            except OSError:
                continue
    if not merged_chunks:
        return "", 0
    combined = "\n\n".join(merged_chunks)
    lines = combined.splitlines()
    tail = "\n".join(lines[-max_lines:]) if len(lines) > max_lines else combined
    return tail, len(lines)


def _collect_step_logs(job: AnalysisJob) -> tuple[list[StepLogEntry], str | None]:
    base_dir = Path(settings.STORAGE_ROOT) / "jobs" / str(job.id)
    params = job.parameters or {}
    work_dir = Path(job.work_dir) if job.work_dir else None

    candidates_map: dict[str, list[Path]] = {
        "inspection": [
            base_dir / "inspection" / "inspection.log",
            *([work_dir / "inspection.log", work_dir.parent / "inspection" / "inspection.log"] if work_dir else []),
        ],
        "normalization": [
            base_dir / "normalization" / "bcftools.log",
            *([work_dir / "bcftools.log", work_dir.parent / "normalization" / "bcftools.log"] if work_dir else []),
        ],
        "qc": [
            *([Path(params["qc_log_path"])] if params.get("qc_log_path") else []),
            *([Path(f"{job.qc_output_prefix}.log")] if job.qc_output_prefix else []),
            base_dir / "qc" / "plink_qc.log",
            *([work_dir / "plink_qc.log", work_dir.parent / "qc" / "plink_qc.log"] if work_dir else []),
        ],
        "imputation": [
            *([Path(job.imputed_prefix).parent / "beagle.log", Path(f"{job.imputed_prefix}.log")] if job.imputed_prefix else []),
            base_dir / "imputation" / "beagle.log",
            base_dir / "imputation" / "imputed.log",
            *([work_dir / "beagle.log", work_dir.parent / "imputation" / "beagle.log"] if work_dir else []),
        ],
        "structure": [
            base_dir / "structure" / "structure.log",
            *([work_dir / "structure.log", work_dir.parent / "structure" / "structure.log"] if work_dir else []),
        ],
        "gwas": [
            *([Path(job.assoc_output_path).parent.parent / "gemma.log"] if job.assoc_output_path else []),
            base_dir / "gwas" / "gemma.log",
            base_dir / "gwas" / "output" / "kinship.log.txt",
            base_dir / "gwas" / "output" / "association.log.txt",
            *([work_dir / "gemma.log", work_dir.parent / "gwas" / "gemma.log"] if work_dir else []),
        ],
        "multilocus": [
            base_dir / "multilocus" / "multilocus.log",
            *([work_dir / "multilocus.log", work_dir.parent / "multilocus" / "multilocus.log"] if work_dir else []),
        ],
        "postgwas": [
            base_dir / "postgwas" / "postgwas.log",
            *([work_dir / "postgwas.log", work_dir.parent / "postgwas" / "postgwas.log"] if work_dir else []),
        ],
        "visualization": [
            *([Path(job.assoc_output_path).parent.parent / "visualization.log"] if job.assoc_output_path else []),
            base_dir / "visualization" / "visualization.log",
            base_dir / "gwas" / "visualization.log",
            *([work_dir / "visualization.log", work_dir.parent / "visualization" / "visualization.log"] if work_dir else []),
        ],
        "annotation": [
            base_dir / "annotation" / "annotation.log",
            *([work_dir / "annotation.log", work_dir.parent / "annotation" / "annotation.log"] if work_dir else []),
        ],
    }

    step_keys = [item[0] for item in _STEP_DEFINITIONS]
    current_key = (job.current_step.value if hasattr(job.current_step, "value") else str(job.current_step)).lower()
    overall = (job.overall_status.value if hasattr(job.overall_status, "value") else str(job.overall_status)).lower()
    failed_key = (job.failed_step or current_key).lower()
    current_idx = step_keys.index(current_key) if current_key in step_keys else 0
    failed_idx = step_keys.index(failed_key) if failed_key in step_keys else current_idx

    entries: list[StepLogEntry] = []
    last_log_line: str | None = params.get("last_log_line")

    for idx, (step_key, label, tool) in enumerate(_STEP_DEFINITIONS):
        log_text, line_count = _read_log_tail(candidates_map.get(step_key, []))

        is_step_skipped = (
            (base_dir / step_key / "skipped.json").is_file()
            or (work_dir and (work_dir.parent / step_key / "skipped.json").is_file())
            or "[SKIPPED]" in log_text
        )

        if is_step_skipped:
            step_status = "skipped"
        elif job.pipeline_type == "qc_only" and step_key != "qc" and not log_text:
            step_status = "skipped"
        elif overall in {"completed", "success"}:
            step_status = "done" if (log_text or job.pipeline_type != "qc_only") else "skipped"
        elif overall == "failed":
            if idx < failed_idx or (idx != failed_idx and log_text):
                step_status = "done"
            elif idx == failed_idx:
                step_status = "failed"
                err_detail = job.error_log or job.error_message
                if err_detail and err_detail not in log_text:
                    log_text = f"{log_text}\n[ERROR] {err_detail}".strip()
                    line_count = len(log_text.splitlines())
            else:
                step_status = "pending"
        elif overall == "cancelled":
            if idx < current_idx:
                step_status = "done"
            elif idx == current_idx:
                step_status = "cancelled"
                cancel_msg = job.error_message or "Job dihentikan oleh pengguna."
                if cancel_msg not in log_text:
                    log_text = f"{log_text}\n[CANCELLED] {cancel_msg}".strip()
                    line_count = len(log_text.splitlines())
            else:
                step_status = "skipped"
        elif overall == "pending":
            step_status = "pending"
        else:
            if idx < current_idx:
                step_status = "done"
            elif idx == current_idx:
                step_status = "active"
            else:
                step_status = "pending"

        if log_text:
            non_empty = [ln.strip() for ln in log_text.splitlines() if ln.strip()]
            if non_empty:
                last_log_line = non_empty[-1]

        entries.append(
            StepLogEntry(
                step=step_key,
                label=label,
                tool=tool,
                status=step_status,
                line_count=line_count,
                log_text=log_text,
            )
        )

    return entries, last_log_line


def _compute_elapsed_seconds(job: AnalysisJob) -> float | None:
    params = job.parameters or {}
    if params.get("elapsed_seconds") is not None:
        return float(params["elapsed_seconds"])
    if job.started_at is None:
        return None
    start = job.started_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    end = job.completed_at or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return round(max((end - start).total_seconds(), 0.0), 2)


def _discover_job_files(job: AnalysisJob, dataset: Dataset | None) -> list[dict]:
    """Temukan seluruh file input dan file di setiap tahap job (termasuk saat job masih berjalan)."""
    discovered: list[dict] = []
    seen_paths: set[str] = set()

    def _add_file(path: Path, stage: str, key_id: str, display_name: str | None = None):
        if not path or not path.is_file():
            return
        resolved = str(path.resolve())
        if resolved in seen_paths:
            return
        seen_paths.add(resolved)
        stat = path.stat()
        fname = display_name or path.name
        lower_name = fname.lower()
        previewable = any(lower_name.endswith(sfx) for sfx in _PREVIEWABLE_SUFFIXES)
        discovered.append(
            {
                "file_key": key_id,
                "filename": fname,
                "stage": stage,
                "stage_label": _STAGE_LABELS.get(stage, stage),
                "size_bytes": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "previewable": previewable,
                "_abs_path": path,
            }
        )

    # 1. Input dataset files
    if dataset:
        if dataset.storage_path:
            _add_file(Path(dataset.storage_path), "input", "dataset_input", dataset.original_filename)
        if dataset.phenotype_file_path:
            p_pheno = Path(dataset.phenotype_file_path)
            if p_pheno.is_file():
                _add_file(p_pheno, "input", "dataset_pheno", p_pheno.name)
        anno_path = getattr(dataset, "annotation_file_path", None) or getattr(dataset, "annotation_gff_path", None)
        if anno_path:
            p_gff = Path(anno_path)
            if p_gff.is_file():
                _add_file(p_gff, "input", "dataset_annotation", p_gff.name)

    # 2. Job workspace files across stages
    job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job.id)
    roots_to_scan = [job_root]
    known_stages = {
        "inspection",
        "normalization",
        "qc",
        "imputation",
        "structure",
        "gwas",
        "multilocus",
        "postgwas",
        "visualization",
        "annotation",
    }
    if job.work_dir:
        wd = Path(job.work_dir)
        if wd.name in known_stages:
            roots_to_scan.append(wd.parent)
        else:
            roots_to_scan.append(wd)

    for root in roots_to_scan:
        if not root.is_dir():
            continue
        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(root).as_posix()
            parts = rel.split("/")
            folder = parts[0].lower() if parts else ""
            fname_lower = file_path.name.lower()
            if folder == "visualization" or "visualization" in fname_lower or "manhattan" in fname_lower:
                stage = "visualization"
            elif folder in known_stages:
                stage = folder
            elif "inspection" in fname_lower or "keep_ids" in fname_lower or "pheno" in fname_lower:
                stage = "inspection"
            elif "plink" in fname_lower:
                stage = "qc"
            elif "beagle" in fname_lower or "imputed" in fname_lower:
                stage = "imputation"
            elif "pca" in fname_lower or "eigen" in fname_lower or "kinship" in fname_lower or "ld_decay" in fname_lower:
                stage = "structure"
            elif "gemma" in fname_lower or "assoc" in fname_lower:
                stage = "gwas"
            elif "multilocus" in fname_lower or "farmcpu" in fname_lower or "blink" in fname_lower:
                stage = "multilocus"
            elif "clump" in fname_lower or "lead_snp" in fname_lower or "threshold" in fname_lower:
                stage = "postgwas"
            elif "gene" in fname_lower or "gff" in fname_lower or "go" in fname_lower:
                stage = "annotation"
            else:
                stage = "normalization"
            _add_file(file_path, stage, f"job:{rel}")

    # 3. Fallback: sertakan file inspeksi pra-run pada dataset bila belum ada di folder job/inspection
    if dataset:
        ds_inspect_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id) / "inspection"
        if ds_inspect_dir.is_dir():
            existing_names = {item["filename"] for item in discovered if item["stage"] == "inspection"}
            for file_path in sorted(ds_inspect_dir.glob("*")):
                if file_path.is_file() and file_path.name not in existing_names:
                    _add_file(file_path, "inspection", f"ds_inspect:{file_path.name}")

    discovered.sort(key=lambda item: (_STAGE_ORDER.get(item["stage"], 99), item["filename"].lower()))
    return discovered


@router.get("")
def list_user_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Mengembalikan daftar seluruh AnalysisJob milik user (sedang berjalan maupun selesai)."""
    query = db.query(AnalysisJob).filter(AnalysisJob.deleted_at.is_(None))
    if not _is_admin(user):
        query = query.filter(AnalysisJob.triggered_by == user.id)
    jobs_list = query.order_by(AnalysisJob.created_at.desc()).limit(50).all()

    items = []
    for job in jobs_list:
        dataset = db.get(Dataset, job.dataset_id)
        files = _discover_job_files(job, dataset)
        _, last_log_line = _collect_step_logs(job)
        overall_val = job.overall_status.value if hasattr(job.overall_status, "value") else str(job.overall_status)
        step_val = job.current_step.value if hasattr(job.current_step, "value") else str(job.current_step)
        items.append(
            {
                "job_id": str(job.id),
                "dataset_id": str(job.dataset_id),
                "dataset_filename": dataset.original_filename if dataset else "Dataset",
                "pipeline_type": job.pipeline_type,
                "current_step": step_val,
                "overall_status": overall_val,
                "elapsed_seconds": _compute_elapsed_seconds(job),
                "last_log_line": last_log_line,
                "file_count": len(files),
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            }
        )
    return items


@router.post("/qc", response_model=JobTriggerResponse, status_code=202)
def trigger_qc_job(
    payload: QCJobRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobTriggerResponse:
    """
    Membuat `AnalysisJob` baru berstatus PENDING dan langsung men-trigger
    task Celery `run_plink_qc` secara asinkron.
    """
    dataset = db.get(Dataset, payload.dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset tidak ditemukan.")

    if dataset.owner_id != user.id and not _is_admin(user):
        raise HTTPException(status_code=404, detail="Dataset tidak ditemukan.")

    if dataset.upload_status != UploadStatus.UPLOADED and dataset.upload_status != UploadStatus.READY:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Dataset berstatus '{dataset.upload_status.value}'. "
                "Pastikan upload sudah selesai (status uploaded/ready) sebelum menjalankan QC."
            ),
        )

    job = AnalysisJob(
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="qc_only",
        current_step=PipelineStep.QC,
        overall_status=JobStatus.PENDING,
        parameters={
            "maf": payload.maf,
            "geno": payload.geno,
            "mind": payload.mind,
            "hwe": payload.hwe,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    task = run_qc_pipeline.delay(None, str(job.id), str(dataset.id), job.parameters)

    return JobTriggerResponse(
        job_id=job.id,
        overall_status=job.overall_status.value,
        celery_task_id=task.id,
    )


@router.get("/{job_id}/status", response_model=JobStatusResponse)
def get_job_status(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobStatusResponse:
    """
    Endpoint polling untuk frontend. React memanggil ini secara berkala selagi
    `overall_status` masih PENDING/RUNNING, lalu berhenti polling begitu status
    menjadi COMPLETED/FAILED/CANCELLED.
    """
    job = db.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    if job.triggered_by != user.id and not _is_admin(user):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    step_logs, last_log_line = _collect_step_logs(job)
    elapsed_seconds = _compute_elapsed_seconds(job)

    return JobStatusResponse(
        job_id=job.id,
        pipeline_type=job.pipeline_type,
        current_step=job.current_step.value,
        overall_status=job.overall_status.value,
        error_message=job.error_message,
        error_log=job.error_log,
        last_log_line=last_log_line,
        elapsed_seconds=elapsed_seconds,
        step_logs=step_logs,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/{job_id}/logs")
def get_job_logs(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """
    Mengembalikan seluruh log eksekusi per tahap untuk sebuah AnalysisJob.
    """
    job = db.get(AnalysisJob, job_id)
    if job is None or (job.triggered_by != user.id and not _is_admin(user)):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    step_logs, last_log_line = _collect_step_logs(job)
    return {
        "job_id": str(job.id),
        "pipeline_type": job.pipeline_type,
        "overall_status": job.overall_status.value,
        "current_step": job.current_step.value,
        "elapsed_seconds": _compute_elapsed_seconds(job),
        "last_log_line": last_log_line,
        "step_logs": [entry.model_dump() for entry in step_logs],
    }


@router.get("/{job_id}/files")
def list_job_files(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """
    Mengembalikan daftar seluruh file yang terkait dengan job (baik input dataset
    maupun file yang dihasilkan pada setiap tahap saat job sedang berjalan / selesai).
    """
    job = db.get(AnalysisJob, job_id)
    if job is None or (job.triggered_by != user.id and not _is_admin(user)):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    dataset = db.get(Dataset, job.dataset_id)
    raw_files = _discover_job_files(job, dataset)
    clean_files = [
        {k: v for k, v in item.items() if k != "_abs_path"}
        for item in raw_files
    ]
    return {
        "job_id": str(job.id),
        "overall_status": job.overall_status.value if hasattr(job.overall_status, "value") else str(job.overall_status),
        "current_step": job.current_step.value if hasattr(job.current_step, "value") else str(job.current_step),
        "files": clean_files,
    }


@router.get("/{job_id}/files/content")
def get_job_file_content(
    job_id: uuid.UUID,
    file_key: str = Query(..., description="file_key dari endpoint /jobs/{job_id}/files"),
    preview: bool = Query(default=False, description="Jika true, kembalikan pratinjau teks (150 baris pertama)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mengunduh atau melihat pratinjau isi file spesifik dari sebuah job."""
    job = db.get(AnalysisJob, job_id)
    if job is None or (job.triggered_by != user.id and not _is_admin(user)):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    dataset = db.get(Dataset, job.dataset_id)
    raw_files = _discover_job_files(job, dataset)
    matched = next((item for item in raw_files if item["file_key"] == file_key), None)
    if matched is None:
        raise HTTPException(status_code=404, detail="File tidak ditemukan pada job ini.")

    target_path: Path = matched["_abs_path"]
    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="File fisik tidak ditemukan di storage.")

    if preview:
        open_fn = gzip.open if target_path.name.endswith(".gz") else open
        lines = []
        try:
            with open_fn(target_path, "rt", encoding="utf-8", errors="replace") as f:
                for _ in range(150):
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line.rstrip("\r\n"))
            return PlainTextResponse("\n".join(lines))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"File tidak dapat ditampilkan sebagai teks: {exc}") from exc

    return FileResponse(
        path=str(target_path),
        filename=matched["filename"],
        media_type="application/octet-stream",
    )


@router.get("/{job_id}/results")
def get_job_results_summary(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """
    Mengembalikan ringkasan statistik hasil GWAS (total_snps, significant_snps, lambda_gc)
    yang ditampilkan oleh ResultDashboard.jsx.
    """
    job = db.get(AnalysisJob, job_id)
    if job is None or (job.triggered_by != user.id and not _is_admin(user)):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    if job.overall_status not in {JobStatus.COMPLETED, JobStatus.SUCCESS}:
        raise HTTPException(
            status_code=409,
            detail="Hasil belum tersedia karena job belum selesai diproses.",
        )

    params = job.parameters or {}
    summary_stats = params.get("summary_stats")

    if not summary_stats:
        assoc_res = (
            db.query(Result)
            .filter(Result.job_id == job.id, Result.result_type == ResultType.GWAS_ASSOC)
            .first()
        )
        if assoc_res and assoc_res.summary_stats:
            summary_stats = assoc_res.summary_stats
        elif job.assoc_output_path and Path(job.assoc_output_path).is_file():
            summary_stats = compute_assoc_summary_stats(job.assoc_output_path)
            job.parameters = {**params, "summary_stats": summary_stats}
            db.commit()
        else:
            summary_stats = {"total_snps": 0, "significant_snps": 0, "lambda_gc": 1.0}

    return {
        "job_id": str(job.id),
        "overall_status": job.overall_status.value,
        "total_snps": summary_stats.get("total_snps", 0),
        "significant_snps": summary_stats.get("significant_snps", 0),
        "lambda_gc": summary_stats.get("lambda_gc", 1.0),
    }


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Menghentikan eksekusi AnalysisJob yang sedang berjalan."""
    job = db.get(AnalysisJob, job_id)
    if job is None or job.deleted_at is not None or (job.triggered_by != user.id and not _is_admin(user)):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    terminal_statuses = {JobStatus.COMPLETED, JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.CANCELLED}
    if job.overall_status in terminal_statuses:
        return {
            "job_id": str(job.id),
            "overall_status": job.overall_status.value,
            "cancelled": False,
            "detail": f"Job sudah berstatus {job.overall_status.value}.",
        }

    if job.celery_task_id and hasattr(celery_app, "control"):
        try:
            celery_app.control.revoke(job.celery_task_id, terminate=True)
        except Exception:
            pass

    now = datetime.now(timezone.utc)
    job.overall_status = JobStatus.CANCELLED
    job.completed_at = now
    job.error_message = "Job dihentikan oleh pengguna."
    db.commit()

    return {
        "job_id": str(job.id),
        "overall_status": job.overall_status.value,
        "cancelled": True,
    }


@router.post("/{job_id}/retry")
def retry_job(
    job_id: uuid.UUID,
    from_scratch: bool = Query(
        default=False,
        description="Jika true, ulangi seluruh pipeline dari Tahap 1; jika false (default), lanjutkan dari tahap terakhir yang terhenti.",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """
    Menjalankan ulang (retry) atau melanjutkan (resume) AnalysisJob yang berhenti (gagal/dibatalkan).
    """
    from app.tasks.pipeline_orchestrator import STAGE_TO_JOB_STATE, trigger_retry_pipeline

    job = db.get(AnalysisJob, job_id)
    if job is None or job.deleted_at is not None or (job.triggered_by != user.id and not _is_admin(user)):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    dataset = db.get(Dataset, job.dataset_id)
    if dataset is None or dataset.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Dataset untuk job ini tidak lagi tersedia.")

    # Jika masih ada task lama terdaftar, revoke terlebih dahulu
    if job.celery_task_id and hasattr(celery_app, "control"):
        try:
            celery_app.control.revoke(job.celery_task_id, terminate=True)
        except Exception:
            pass

    previous_failed_step = job.failed_step

    # Reset atribut error dan waktu selesai
    job.error_message = None
    job.error_log = None
    job.failed_step = None
    job.completed_at = None
    if from_scratch or job.started_at is None:
        job.started_at = datetime.now(timezone.utc)

    task_id, start_idx, start_stage = trigger_retry_pipeline(
        job_id=str(job.id),
        dataset_id=str(dataset.id),
        raw_vcf_path=str(getattr(dataset, "storage_path", "") or ""),
        job_parameters=job.parameters or {},
        failed_step=previous_failed_step,
        from_scratch=from_scratch,
    )

    new_status, new_step = STAGE_TO_JOB_STATE.get(
        start_stage,
        (JobStatus.RUNNING_INSPECTION, PipelineStep.INSPECTION),
    )
    job.overall_status = new_status
    job.current_step = new_step
    job.celery_task_id = task_id
    db.commit()

    return {
        "job_id": str(job.id),
        "celery_task_id": task_id,
        "overall_status": job.overall_status.value,
        "current_step": job.current_step.value,
        "resumed_from_step": start_stage,
        "resumed_from_stage_number": start_idx + 1,
        "from_scratch": from_scratch,
        "retried": True,
    }


@router.delete("/{job_id}")
def delete_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Menghapus AnalysisJob beserta file artefak di direktori kerja job."""
    job = db.get(AnalysisJob, job_id)
    if job is None or job.deleted_at is not None or (job.triggered_by != user.id and not _is_admin(user)):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")

    terminal_statuses = {JobStatus.COMPLETED, JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.CANCELLED}
    if job.overall_status not in terminal_statuses:
        if job.celery_task_id and hasattr(celery_app, "control"):
            try:
                celery_app.control.revoke(job.celery_task_id, terminate=True)
            except Exception:
                pass
        job.overall_status = JobStatus.CANCELLED
        job.error_message = "Job dihentikan dan dihapus oleh pengguna."

    now = datetime.now(timezone.utc)
    job.deleted_at = now
    if job.completed_at is None:
        job.completed_at = now

    job_dir = Path(settings.STORAGE_ROOT) / "jobs" / str(job.id)
    if job_dir.is_dir():
        shutil.rmtree(job_dir, ignore_errors=True)

    db.commit()
    return {
        "job_id": str(job.id),
        "deleted": True,
    }
