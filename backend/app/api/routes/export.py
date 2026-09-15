"""
api/routers/export.py

GET /results/{job_id}/export
Membungkus file-file penting hasil satu job (log PLINK, .assoc GEMMA, VCF hasil
imputasi) menjadi satu file .zip dan mengirimkannya sebagai StreamingResponse.

Catatan integrasi:
- Sesuaikan import `db.session`, `db.models`, `core.security` dengan struktur
  proyek FastAPI kamu yang sebenarnya (di sini ditulis sesuai konvensi pada
  design.md — PostgreSQL menyimpan metadata & path, bukan isi file).
- `Result.result_type` diasumsikan mengikuti nilai yang sama dengan yang dipakai
  Celery worker saat menulis baris ke tabel `results` (mis. "plink_qc_log",
  "gemma_log", "gwas_assoc", "imputed_vcf").
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, JobStatus, Result
from app.services.export_service import ExportFile, generate_zip_stream

router = APIRouter(prefix="/results", tags=["export"])

# Mapping result_type internal -> nama file yang rapi & mudah dipahami peneliti
# saat mereka membuka ZIP hasil download.
RESULT_TYPE_ARCNAME = {
    "plink_qc_log": "logs/plink_qc.log",
    "gemma_log": "logs/gemma.log",
    "gwas_assoc": "results/gwas_results.assoc.txt",
    "imputed_vcf": "data/imputed.vcf.gz",
}


@router.get("/{job_id}/export")
def export_job_results(
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
            detail="Hasil belum tersedia karena job belum selesai diproses",
        )

    results = db.query(Result).filter(Result.job_id == job_id).all()
    if not results:
        raise HTTPException(status_code=404, detail="Tidak ada file hasil untuk job ini")

    result_files: list[ExportFile] = [
        {
            "arcname": RESULT_TYPE_ARCNAME.get(r.result_type.value, f"other/{r.result_type.value}"),
            "path": r.file_path,
        }
        for r in results
    ]

    zip_stream = generate_zip_stream(result_files)
    filename = f"gwas_results_{job_id}.zip"

    return StreamingResponse(
        zip_stream,
        media_type="application/zip",
        headers={
            # Ukuran akhir ZIP tidak diketahui di awal karena di-generate dinamis,
            # jadi Content-Length sengaja tidak di-set — browser akan memakai
            # chunked transfer encoding dan tetap menampilkan progress download.
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
