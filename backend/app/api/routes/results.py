"""
Endpoint FastAPI untuk mengambil data hasil GWAS di setiap tahap pipeline (Fase 1 hingga Fase 5)
untuk dirender pada visualisasi interaktif frontend.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, JobStatus, Result, ResultType
from app.schemas.results import ManhattanDataResponse
from app.services.manhattan_parser import generate_manhattan_cache, load_manhattan_cache

router = APIRouter(prefix="/results", tags=["results"])


def _load_job_json_result(
    job_id: str,
    db: Session,
    result_type: ResultType,
    subfolder: str,
    filename: str,
) -> dict[str, Any]:
    """Helper untuk memuat hasil analisis JSON dari database Result atau filesystem."""
    # 1. Cek tabel Result
    res = (
        db.query(Result)
        .filter(Result.job_id == job_id, Result.result_type == result_type)
        .first()
    )
    if res and res.summary_stats:
        return res.summary_stats
    if res and res.file_path and Path(res.file_path).is_file():
        try:
            return json.loads(Path(res.file_path).read_text(encoding="utf-8"))
        except Exception:
            pass

    # 2. Cek direktori fisik job
    base_dir = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / subfolder
    target = base_dir / filename
    if target.is_file():
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 3. Cek skipped.json jika tahap dilewati
    skipped_file = base_dir / "skipped.json"
    if skipped_file.is_file():
        try:
            data = json.loads(skipped_file.read_text(encoding="utf-8"))
            data.setdefault("status", "skipped")
            return data
        except Exception:
            pass

    return {"status": "not_available", "job_id": job_id, "stage": subfolder}


@router.get(
    "/{job_id}/manhattan-data",
    response_model=ManhattanDataResponse,
    summary="Ambil data hasil GWAS untuk Manhattan Plot (format kolom, ringan)",
)
def get_manhattan_data(job_id: str, db: Session = Depends(get_db)) -> ManhattanDataResponse:
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()

    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job tidak ditemukan")

    if job.overall_status not in {JobStatus.COMPLETED, JobStatus.SUCCESS}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Job belum selesai (status saat ini: {job.overall_status.value}, "
                f"tahap: {job.current_step.value}). Gunakan endpoint status/polling "
                f"untuk memantau progres sebelum memanggil endpoint ini."
            ),
        )

    params = job.parameters or {}
    cache_path = params.get("manhattan_cache_path")

    # Auto-generate cache on the fly if job finished before cache generation was wired
    if (not cache_path or not Path(cache_path).is_file()) and job.assoc_output_path:
        assoc_path = Path(job.assoc_output_path)
        if assoc_path.is_file():
            fallback_cache = assoc_path.parent.parent / "manhattan_cache.parquet"
            cache_summary = generate_manhattan_cache(
                assoc_file_path=str(assoc_path),
                output_cache_path=str(fallback_cache),
            )
            cache_path = cache_summary["output_path"]
            job.parameters = {**params, "manhattan_cache_path": cache_path}
            db.commit()

    if not cache_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cache Manhattan Plot tidak ditemukan")

    try:
        df = load_manhattan_cache(cache_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File cache Manhattan Plot hilang dari storage",
        ) from exc

    return ManhattanDataResponse(
        job_id=job_id,
        total_points=len(df),
        chr=[int(v) for v in df["CHR"].tolist()],
        bp=[int(v) for v in df["BP"].tolist()],
        snp=[str(v) for v in df["SNP"].tolist()],
        log10_p=[round(float(v), 4) for v in df["LOG10_P"].tolist()],
    )


@router.get("/{job_id}/inspection", summary="Data inspeksi & rekonsiliasi fenotipe (Fase 1)")
def get_inspection_data(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.INSPECTION_REPORT, "inspection", "inspection.json"
    )


@router.get("/{job_id}/qc-comparison", summary="Data perbandingan QC sebelum/sesudah (Fase 2)")
def get_qc_comparison_data(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.QC_COMPARISON, "qc", "qc_comparison.json"
    )


@router.get("/{job_id}/pca", summary="Data dekomposisi PCA & Scree plot (Fase 3)")
def get_pca_data(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.PCA_DATA, "structure", "pca_data.json"
    )


@router.get("/{job_id}/kinship", summary="Data matriks kekerabatan Kinship & KING (Fase 3)")
def get_kinship_data(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.KINSHIP_DATA, "structure", "kinship_matrix.json"
    )


@router.get("/{job_id}/ld-decay", summary="Data kurva penurunan LD Decay (Fase 4D)")
def get_ld_decay_data(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.LD_DECAY, "structure", "ld_decay.json"
    )


@router.get("/{job_id}/model-recommendation", summary="Rekomendasi model GWAS GLM vs LMM (Fase 3)")
def get_model_recommendation_data(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.MODEL_RECOMMENDATION, "structure", "model_recommendation.json"
    )


@router.get("/{job_id}/thresholds", summary="Ambang batas signifikansi pasca-GWAS (Fase 4E)")
def get_thresholds_data(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.THRESHOLDS, "postgwas", "thresholds.json"
    )


@router.get("/{job_id}/lead-snps", summary="Daftar Lead SNPs independen hasil clumping (Fase 4F)")
def get_lead_snps_data(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.LEAD_SNPS, "postgwas", "lead_snps.json"
    )


@router.get("/{job_id}/peak-comparison", summary="Perbandingan puncak Single vs Multi-Locus GWAS (Fase 4H)")
def get_peak_comparison_data(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.PEAK_COMPARISON, "multilocus", "peak_comparison.json"
    )


@router.get("/{job_id}/candidate-genes", summary="Pemetaan anotasi gen kandidat GFF3 & GO (Fase 5)")
def get_candidate_genes_data(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.CANDIDATE_GENES, "annotation", "candidate_genes.json"
    )
