"""
api/routers/export.py

GET /results/{job_id}/export
Membungkus file-file penting hasil satu job (log PLINK, .assoc GEMMA, VCF hasil
imputasi) menjadi satu file .zip dan mengirimkannya sebagai StreamingResponse.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, JobStatus, Result
from app.services.export_service import ExportFile, generate_zip_stream

router = APIRouter(prefix="/results", tags=["export"])

RESULT_TYPE_ARCNAME = {
    "plink_qc_log": "logs/plink_qc.log",
    "gemma_log": "logs/gemma.log",
    "gwas_assoc": "results/gwas_results.assoc.txt",
    "gwas_normalized_tsv": "results/gwas_results.normalized.tsv",
    "imputed_vcf": "data/imputed.vcf.gz",
    "manhattan_data": "results/manhattan_cache.parquet",
    "qc_comparison": "results/qc_comparison.json",
    "pca_data": "results/pca_data.json",
    "kinship_data": "results/kinship_matrix.json",
    "ld_decay": "results/ld_decay.json",
    "model_recommendation": "results/model_recommendation.json",
    "thresholds": "results/thresholds.json",
    "lead_snps": "results/lead_snps.json",
    "peak_comparison": "results/peak_comparison.json",
    "candidate_genes": "results/candidate_genes.json",
}


def _is_admin(user) -> bool:
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    return role_val == "admin"


def _collect_job_export_files(job: AnalysisJob, results: list[Result]) -> list[ExportFile]:
    files: list[ExportFile] = []
    seen_arcnames: set[str] = set()

    for r in results:
        rtype = r.result_type.value if hasattr(r.result_type, "value") else str(r.result_type)
        arcname = RESULT_TYPE_ARCNAME.get(rtype, f"other/{rtype}")
        if r.file_path and Path(r.file_path).is_file() and arcname not in seen_arcnames:
            files.append({"arcname": arcname, "path": r.file_path})
            seen_arcnames.add(arcname)

    # Fallback: discover files directly from job attributes if Result rows were not populated
    params = job.parameters or {}
    fallback_candidates = [
        ("results/gwas_results.assoc.txt", job.assoc_output_path),
        ("logs/plink_qc.log", params.get("qc_log_path")),
        ("data/imputed.vcf.gz", f"{job.imputed_prefix}.vcf.gz" if job.imputed_prefix else None),
        ("logs/gemma.log", str(Path(job.work_dir) / "gemma.log") if job.work_dir else None),
    ]
    for arcname, candidate_path in fallback_candidates:
        if candidate_path and arcname not in seen_arcnames and Path(candidate_path).is_file():
            files.append({"arcname": arcname, "path": str(candidate_path)})
            seen_arcnames.add(arcname)

    return files


@router.get("/{job_id}/export")
def export_job_results(
    job_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")

    if job.triggered_by != user.id and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Anda tidak memiliki akses ke job ini")

    if job.overall_status not in {JobStatus.COMPLETED, JobStatus.SUCCESS}:
        raise HTTPException(
            status_code=409,
            detail="Hasil belum tersedia karena job belum selesai diproses",
        )

    results = db.query(Result).filter(Result.job_id == job_id).all()
    result_files = _collect_job_export_files(job, results)
    if not result_files:
        raise HTTPException(status_code=404, detail="Tidak ada file hasil untuk job ini")

    zip_stream = generate_zip_stream(result_files)
    filename = f"gwas_results_{job_id}.zip"

    return StreamingResponse(
        zip_stream,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
