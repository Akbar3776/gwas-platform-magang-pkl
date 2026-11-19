"""
api/routers/report.py

GET /results/{job_id}/report
Men-generate dan mengirim PDF ringkasan hasil GWAS.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user, require_job_owner
from app.db.engine.database import get_db
from app.db.engine.models import JobStatus, Result, ResultType
from app.services.report_generator import (
    build_report_context,
    extract_top_snps,
    parse_qc_summary_from_log,
    render_pdf_report,
)

router = APIRouter(prefix="/results", tags=["report"])


def _format_utc_mtime(path: Path) -> str:
    try:
        dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return dt.strftime("%d %B %Y, %H:%M UTC")
    except OSError:
        return "-"


def _resolve_gwas_model_label(params: dict[str, Any], job_root: Path) -> str:
    raw_model = str(((params.get("gwas") or {}).get("model")) or "").strip().lower()
    if raw_model == "auto":
        raw_model = str(params.get("recommended_model") or "").strip().lower()

    gemma_log = job_root / "gwas" / "gemma.log"
    if gemma_log.is_file():
        try:
            text = gemma_log.read_text(encoding="utf-8", errors="replace")
            if "Model Statistik: GLM" in text or "Model Statistik: LM " in text or " -lm " in text:
                return "GEMMA -lm"
            if "Baseline LMM untuk " in text:
                return f"{raw_model.upper()} + GEMMA -lmm" if raw_model in {"farmcpu", "blink", "mlmm"} else "GEMMA -lmm"
            if "Model Statistik: LMM" in text or " -lmm " in text:
                return "GEMMA -lmm"
        except OSError:
            pass

    if raw_model in {"glm", "lm"}:
        return "GEMMA -lm"
    if raw_model == "lmm":
        return "GEMMA -lmm"
    if raw_model in {"farmcpu", "blink", "mlmm"}:
        return f"{raw_model.upper()} + GEMMA -lmm"
    return "GEMMA"


def build_job_steps(
    job: Any,
    job_root: Path | None = None,
    assoc_path: str | Path | None = None,
) -> list[dict[str, str]]:
    """Membangun riwayat status tiap tahap pipeline dari artefak aktual di disk."""
    root = job_root if job_root is not None else (Path(settings.STORAGE_ROOT) / "jobs" / str(job.id))
    params = getattr(job, "parameters", None) or {}
    failed_step = str(getattr(job, "failed_step", "") or "").strip().lower()

    gwas_artifacts = [
        root / "gwas" / "gwas_results.normalized.tsv",
        root / "gwas" / "output" / "association.assoc.txt",
    ]
    if assoc_path:
        gwas_artifacts.append(Path(assoc_path))

    stages: list[tuple[str, str, list[Path]]] = [
        (
            "inspection",
            "Inspeksi Data & Fenotipe",
            [root / "inspection" / "inspection_report.json", root / "inspection" / "pheno_plink.txt"],
        ),
        (
            "normalization",
            "Normalisasi VCF (bcftools)",
            [root / "normalization" / "normalized.vcf.gz"],
        ),
        (
            "qc",
            "Quality Control (PLINK)",
            [root / "qc" / "plink_qc.bed"],
        ),
        (
            "imputation",
            "Imputasi Genotipe (Beagle)",
            [root / "imputation" / "imputed.vcf.gz"],
        ),
        (
            "structure",
            "Struktur Populasi & LD Decay",
            [root / "structure" / "kinship.cXX.txt", root / "structure" / "pca_results.eigenvec"],
        ),
        (
            "gwas",
            f"Analisis Asosiasi ({_resolve_gwas_model_label(params, root)})",
            gwas_artifacts,
        ),
        (
            "multilocus",
            "Analisis Multi-Locus (FarmCPU / BLINK / MLMM)",
            [root / "multilocus" / "multilocus_summary.json"],
        ),
        (
            "postgwas",
            "Pasca-GWAS & Clumping",
            [root / "postgwas" / "lead_snps.json", root / "postgwas" / "thresholds.json"],
        ),
        (
            "visualization",
            "Visualisasi & Cache Plot",
            [root / "visualization" / "manhattan_cache.parquet"],
        ),
        (
            "annotation",
            "Anotasi Gen Kandidat GFF3",
            [root / "annotation" / "candidate_genes.json"],
        ),
    ]

    steps: list[dict[str, str]] = []
    for stage_key, label, artifacts in stages:
        skipped_marker = root / stage_key / "skipped.json"
        if skipped_marker.is_file():
            steps.append({
                "step_name": label,
                "status": "SKIPPED",
                "completed_at": _format_utc_mtime(skipped_marker),
            })
            continue
        if failed_step == stage_key:
            steps.append({
                "step_name": label,
                "status": "FAILED",
                "completed_at": "-",
            })
            continue
        done_artifact = next((p for p in artifacts if p.is_file()), None)
        if done_artifact is not None:
            steps.append({
                "step_name": label,
                "status": "COMPLETED",
                "completed_at": _format_utc_mtime(done_artifact),
            })
            continue
        steps.append({
            "step_name": label,
            "status": "NOT_RUN",
            "completed_at": "-",
        })
    return steps


@router.get("/{job_id}/report")
def download_summary_report(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    job = require_job_owner(job_id=job_id, user=user, db=db)

    if job.overall_status not in {JobStatus.COMPLETED, JobStatus.SUCCESS}:
        raise HTTPException(
            status_code=409,
            detail="Laporan belum bisa dibuat karena job belum selesai diproses",
        )

    assoc_result = (
        db.query(Result)
        .filter(Result.job_id == job.id, Result.result_type == ResultType.GWAS_ASSOC)
        .first()
    )
    assoc_path = (
        assoc_result.file_path
        if (assoc_result is not None and assoc_result.result_type == ResultType.GWAS_ASSOC)
        else job.assoc_output_path
    )
    if not assoc_path or not Path(assoc_path).is_file():
        raise HTTPException(status_code=404, detail="File hasil GWAS (.assoc) tidak ditemukan")

    params = job.parameters or {}
    qc_summary = params.get("qc_summary")
    if not qc_summary:
        qc_log_path = params.get("qc_log_path")
        log_excerpt = None
        if qc_log_path and Path(qc_log_path).is_file():
            log_excerpt = Path(qc_log_path).read_text(encoding="utf-8", errors="replace")
        qc_summary = parse_qc_summary_from_log(log_excerpt)

    top_snps = extract_top_snps(assoc_path, top_n=10)
    job_steps = build_job_steps(job, assoc_path=assoc_path)

    context = build_report_context(job, job_steps, qc_summary, top_snps)

    try:
        pdf_bytes = render_pdf_report(context)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Gagal membuat laporan PDF. Silakan coba lagi atau hubungi admin.",
        ) from exc

    filename = f"gwas_summary_report_{job_id}.pdf"

    def _pdf_iterator():
        yield pdf_bytes

    return StreamingResponse(
        _pdf_iterator(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
