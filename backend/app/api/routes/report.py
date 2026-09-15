"""
api/routers/report.py

GET /results/{job_id}/report
Men-generate dan mengirim PDF ringkasan hasil GWAS.

Catatan skala:
- Generasi report ini dominan I/O-bound + satu pass streaming atas file .assoc
  (lihat report_generator.extract_top_snps), sehingga cukup cepat untuk
  dijalankan langsung di dalam request-response cycle FastAPI meski file
  assoc berukuran besar.
- Jika di kemudian hari report berkembang jadi jauh lebih berat (mis. ikut
  merender ulang Manhattan plot beresolusi tinggi ke dalam PDF), pindahkan
  logika ini ke Celery task terpisah mengikuti pola yang sama dengan job
  pipeline lain (POST untuk trigger + simpan hasil ke `results` sebagai
  result_type="summary_report_pdf" + GET terpisah untuk polling/download),
  supaya endpoint ini tetap non-blocking.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, JobStatus, Result
from app.services.report_generator import (
    build_report_context,
    extract_top_snps,
    parse_qc_summary_from_log,
    render_pdf_report,
)

router = APIRouter(prefix="/results", tags=["report"])


@router.get("/{job_id}/report")
def download_summary_report(
    job_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    if job.triggered_by != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Anda tidak memiliki akses ke job ini")

    if job.overall_status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="Laporan belum bisa dibuat karena job belum selesai diproses",
        )

    assoc_result = (
        db.query(Result)
        .filter(Result.job_id == job_id, Result.result_type == "gwas_assoc")
        .first()
    )
    if assoc_result is None:
        raise HTTPException(status_code=404, detail="File hasil GWAS (.assoc) tidak ditemukan")

    qc_summary = parse_qc_summary_from_log(None)

    top_snps = extract_top_snps(assoc_result.file_path, top_n=10)

    job_steps = []

    context = build_report_context(job, job_steps, qc_summary, top_snps)

    try:
        pdf_bytes = render_pdf_report(context)
    except Exception as exc:
        # Jangan bocorkan detail internal WeasyPrint ke non-technical user.
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
