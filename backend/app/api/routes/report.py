"""
api/routers/report.py

GET /results/{job_id}/report
Men-generate dan mengirim PDF ringkasan hasil GWAS.
"""

from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, JobStatus, Result, ResultType
from app.services.report_generator import (
    build_report_context,
    extract_top_snps,
    parse_qc_summary_from_log,
    render_pdf_report,
)

router = APIRouter(prefix="/results", tags=["report"])


def _is_admin(user) -> bool:
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    return role_val == "admin"


@router.get("/{job_id}/report")
def download_summary_report(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    job = db.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    if job.triggered_by != user.id and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Anda tidak memiliki akses ke job ini")

    if job.overall_status not in {JobStatus.COMPLETED, JobStatus.SUCCESS}:
        raise HTTPException(
            status_code=409,
            detail="Laporan belum bisa dibuat karena job belum selesai diproses",
        )

    assoc_result = (
        db.query(Result)
        .filter(Result.job_id == job_id, Result.result_type == ResultType.GWAS_ASSOC)
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

    completed_label = (
        job.completed_at.strftime("%d %B %Y, %H:%M UTC") if job.completed_at else "-"
    )
    job_steps = [
        {"step_name": "Normalisasi VCF (bcftools)", "status": "COMPLETED", "completed_at": completed_label},
        {"step_name": "Quality Control (PLINK)", "status": "COMPLETED", "completed_at": completed_label},
        {"step_name": "Imputasi Genotipe (Beagle)", "status": "COMPLETED", "completed_at": completed_label},
        {"step_name": "Analisis Asosiasi (GEMMA)", "status": "COMPLETED", "completed_at": completed_label},
        {"step_name": "Visualisasi & Cache Plot", "status": "COMPLETED", "completed_at": completed_label},
    ]

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
