"""
Endpoint FastAPI untuk mengambil data hasil GWAS di setiap tahap pipeline (Fase 1 hingga Fase 5)
untuk dirender pada visualisasi interaktif frontend.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import uuid

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, JobStatus, Result, ResultType
from app.schemas.results import ManhattanDataResponse
from app.services.manhattan_parser import generate_manhattan_cache, load_manhattan_cache

router = APIRouter(prefix="/results", tags=["results"])


def _load_job_json_result(
    job_id: uuid.UUID | str,
    db: Session,
    result_type: ResultType,
    subfolder: str | list[str],
    filename: str | list[str],
) -> dict[str, Any]:
    """Helper untuk memuat hasil analisis JSON dari database Result atau filesystem."""
    try:
        parsed_id = job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))
    except (ValueError, TypeError):
        parsed_id = None

    # 1. Cek tabel Result
    if parsed_id is not None:
        res = (
            db.query(Result)
            .filter(Result.job_id == parsed_id, Result.result_type == result_type)
            .first()
        )
        if res and res.file_path and Path(res.file_path).is_file():
            try:
                return json.loads(Path(res.file_path).read_text(encoding="utf-8"))
            except Exception:
                pass
        if res and res.summary_stats:
            return res.summary_stats

    subfolders = [subfolder] if isinstance(subfolder, str) else list(subfolder)
    filenames = [filename] if isinstance(filename, str) else list(filename)

    # 2. Cek direktori fisik job
    job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
    for folder in subfolders:
        base_dir = job_root / folder
        for fn in filenames:
            target = base_dir / fn
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

    return {"status": "not_available", "job_id": str(job_id), "stage": subfolders[0]}


@router.get(
    "/{job_id}/manhattan-data",
    response_model=ManhattanDataResponse,
    summary="Ambil data hasil GWAS untuk Manhattan Plot (format kolom, ringan)",
)
def get_manhattan_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> ManhattanDataResponse:
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

    # Cek file cache yang sudah ada di direktori visualization sebelum regenerate
    if not cache_path or not Path(cache_path).is_file():
        vis_cache = Path(settings.STORAGE_ROOT) / "jobs" / str(job.id) / "visualization" / "manhattan_cache.parquet"
        if vis_cache.is_file():
            cache_path = str(vis_cache)
            job.parameters = {**params, "manhattan_cache_path": cache_path}
            db.commit()

    # Auto-generate cache on the fly if job finished before cache generation was wired
    if (not cache_path or not Path(cache_path).is_file()) and job.assoc_output_path:
        assoc_path = Path(job.assoc_output_path)
        if assoc_path.is_file():
            fallback_cache = assoc_path.parent.parent / "visualization" / "manhattan_cache.parquet"
            if not fallback_cache.parent.is_dir():
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
        job_id=str(job_id),
        total_points=len(df),
        chr=[int(float(v)) if pd.notna(v) and str(v).replace(".", "", 1).isdigit() else 1 for v in df["CHR"].tolist()],
        bp=[int(float(v)) if pd.notna(v) and str(v).replace(".", "", 1).isdigit() else 0 for v in df["BP"].tolist()],
        snp=[str(v) for v in df["SNP"].tolist()],
        log10_p=[round(float(v), 4) if pd.notna(v) else 0.0 for v in df["LOG10_P"].tolist()],
    )


@router.get("/{job_id}/inspection", summary="Data inspeksi & rekonsiliasi fenotipe (Fase 1)")
def get_inspection_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.INSPECTION_REPORT, "inspection", ["inspection_report.json", "inspection.json"]
    )


@router.get("/{job_id}/qc-comparison", summary="Data perbandingan QC sebelum/sesudah (Fase 2)")
def get_qc_comparison_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.QC_COMPARISON, "qc", ["qc_comparison.json", "qc_summary.json"]
    )


@router.get("/{job_id}/pca", summary="Data dekomposisi PCA & Scree plot (Fase 3)")
def get_pca_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.PCA_DATA, "structure", ["pca.json", "pca_data.json"]
    )


@router.get("/{job_id}/kinship", summary="Data matriks kekerabatan Kinship & KING (Fase 3)")
def get_kinship_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.KINSHIP_DATA, "structure", ["kinship.json", "kinship_matrix.json"]
    )


@router.get("/{job_id}/ld-decay", summary="Data kurva penurunan LD Decay (Fase 4D)")
def get_ld_decay_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.LD_DECAY, "structure", "ld_decay.json"
    )


@router.get("/{job_id}/model-recommendation", summary="Rekomendasi model GWAS GLM vs LMM (Fase 3)")
def get_model_recommendation_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.MODEL_RECOMMENDATION, "structure", ["model_recommendation.json", "model_rec.json"]
    )


@router.get("/{job_id}/thresholds", summary="Ambang batas signifikansi pasca-GWAS (Fase 4E)")
def get_thresholds_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.THRESHOLDS, "postgwas", "thresholds.json"
    )


@router.get("/{job_id}/lead-snps", summary="Daftar Lead SNPs independen hasil clumping (Fase 4F)")
def get_lead_snps_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    data = _load_job_json_result(
        job_id, db, ResultType.LEAD_SNPS, "postgwas", "lead_snps.json"
    )
    # Bila lead snps kosong atau tidak ditemukan tapi file asosiasi normalisasi ada di gwas/postgwas
    raw_snps = data if isinstance(data, list) else data.get("snps", [])
    if not raw_snps or len(raw_snps) == 0:
        job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
        norm_assoc = job_root / "gwas" / "gwas_results.normalized.tsv"
        if not norm_assoc.is_file():
            norm_assoc = job_root / "postgwas" / "gwas_results.normalized.tsv"
        if norm_assoc.is_file():
            from app.services.postgwas_service import run_greedy_clumping
            thresh_data = _load_job_json_result(job_id, db, ResultType.THRESHOLDS, "postgwas", "thresholds.json")
            sel_p = float(thresh_data.get("selected_p") or 5e-8) if isinstance(thresh_data, dict) else 5e-8
            regenerated = run_greedy_clumping(assoc_file=norm_assoc, selected_p=sel_p)
            if regenerated:
                postgwas_dir = job_root / "postgwas"
                if postgwas_dir.is_dir():
                    try:
                        lead_f = postgwas_dir / "lead_snps.json"
                        lead_f.write_text(json.dumps(regenerated, indent=2), encoding="utf-8")
                    except Exception:
                        pass
                return {"count": len(regenerated), "snps": regenerated}
    return data


@router.get("/{job_id}/peak-comparison", summary="Perbandingan puncak Single vs Multi-Locus GWAS (Fase 4H)")
def get_peak_comparison_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    data = _load_job_json_result(
        job_id, db, ResultType.PEAK_COMPARISON, ["postgwas", "multilocus"], "peak_comparison.json"
    )
    # Jika data kosong (total_peaks == 0), periksa apakah ada lead_snps single atau multi-locus yang dapat dibandingkan
    tot_peaks = 0
    if isinstance(data, dict):
        summary_dict = data.get("summary") or {}
        tot_peaks = int(summary_dict.get("total_peaks") or 0)
        if tot_peaks == 0:
            tot_peaks = len(data.get("consistent", [])) + len(data.get("multi_only", [])) + len(data.get("single_only", []))

    if tot_peaks == 0:
        lead_data = get_lead_snps_data(job_id, db)
        single_leads = lead_data if isinstance(lead_data, list) else lead_data.get("snps", [])
        ml_data = get_multilocus_results_data(job_id, db)
        multi_leads = ml_data.get("lead_snps", []) if isinstance(ml_data, dict) else []

        if single_leads or multi_leads:
            from app.tasks.multilocus_tasks import generate_peak_comparison
            job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
            out_csv = job_root / "postgwas" / "Ringkasan_Puncak_GWAS.csv"
            out_json = job_root / "postgwas" / "peak_comparison.json"
            recomp = generate_peak_comparison(
                single_lead_snps=single_leads,
                multi_lead_snps=multi_leads,
                output_csv=out_csv,
                output_json=out_json,
            )
            return recomp
    return data


@router.get("/{job_id}/multilocus-results", summary="Hasil detail analisis Multi-Locus GWAS (FarmCPU, BLINK, MLMM) (Fase 4C)")
def get_multilocus_results_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    data = _load_job_json_result(
        job_id, db, ResultType.MULTILOCUS_RESULTS, "multilocus", "multilocus_summary.json"
    )
    if isinstance(data, dict):
        leads = data.get("lead_snps") or []
        if not leads:
            job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "multilocus"
            if job_root.is_dir():
                import csv
                extracted_leads: list[dict[str, Any]] = []
                for m_name in ["farmcpu", "blink", "mlmm"]:
                    tsv_f = job_root / f"{m_name}_results.tsv"
                    if tsv_f.is_file():
                        try:
                            with tsv_f.open("r", encoding="utf-8") as tf:
                                reader = csv.DictReader(tf, delimiter="\t")
                                rows = sorted(reader, key=lambda x: float(x.get("p", 1.0)))[:5]
                                for r in rows:
                                    extracted_leads.append({
                                        "snp": r.get("rs"),
                                        "chr": str(r.get("chr", "")).lstrip("chrCHR"),
                                        "pos": int(r.get("pos", 0)),
                                        "p": float(r.get("p", 1.0)),
                                        "method": m_name.upper(),
                                    })
                        except Exception:
                            pass
                if extracted_leads:
                    data["lead_snps"] = extracted_leads
                    data["n_lead_snps"] = len(extracted_leads)
    return data


@router.get("/{job_id}/candidate-genes", summary="Pemetaan anotasi gen kandidat GFF3 & GO (Fase 5)")
def get_candidate_genes_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.CANDIDATE_GENES, "annotation", "candidate_genes.json"
    )
