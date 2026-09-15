"""
Endpoint FastAPI untuk mengambil data hasil GWAS yang siap dirender sebagai
Manhattan Plot di React. Data yang dikembalikan sudah di-parsing dan
di-downsample sebelumnya oleh Celery task `run_gemma_association`
(lihat `app/services/manhattan_parser.py`), sehingga endpoint ini HANYA
membaca file cache Parquet yang sudah kecil -- bukan memproses ulang
file `.assoc.txt` mentah pada setiap request.
"""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, JobStatus
from app.schemas.results import ManhattanDataResponse

router = APIRouter(prefix="/results", tags=["results"])


@router.get(
    "/{job_id}/manhattan-data",
    response_model=ManhattanDataResponse,
    summary="Ambil data hasil GWAS untuk Manhattan Plot (format kolom, ringan)",
)
def get_manhattan_data(job_id: str, db: Session = Depends(get_db)) -> ManhattanDataResponse:
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()

    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job tidak ditemukan")

    if job.overall_status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Job belum selesai (status saat ini: {job.overall_status.value}, "
                f"tahap: {job.current_step.value}). Gunakan endpoint status/polling "
                f"untuk memantau progres sebelum memanggil endpoint ini."
            ),
        )

    cache_path = (job.parameters or {}).get("manhattan_cache_path")
    if not cache_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cache Manhattan Plot tidak ditemukan")

    try:
        # File cache sudah berukuran kecil hasil downsampling, sehingga aman
        # dibaca langsung dalam satu siklus request-response tanpa streaming.
        df = pd.read_parquet(cache_path, columns=["CHR", "BP", "SNP", "LOG10_P"])
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File cache Manhattan Plot hilang dari storage",
        ) from exc

    return ManhattanDataResponse(
        job_id=job_id,
        total_points=len(df),
        chr=df["CHR"].tolist(),
        bp=df["BP"].tolist(),
        snp=df["SNP"].tolist(),
        log10_p=[round(float(v), 4) for v in df["LOG10_P"].tolist()],
    )
