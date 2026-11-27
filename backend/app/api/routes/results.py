"""
Endpoint FastAPI untuk mengambil data hasil GWAS di setiap tahap pipeline (Fase 1 hingga Fase 5)
untuk dirender pada visualisasi interaktif frontend.
"""

from __future__ import annotations

from functools import lru_cache
import json
import logging
import os
from pathlib import Path
from typing import Any
import uuid

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_job_owner
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, Result, ResultType
from app.schemas.results import ManhattanDataResponse
from app.services.chromosome_service import load_split_map
from app.services.manhattan_parser import (
    generate_manhattan_cache,
    load_manhattan_cache,
    load_manhattan_chr_labels,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/results", tags=["results"], dependencies=[Depends(require_job_owner)])


def _load_job_json_result(
    job_id: uuid.UUID | str,
    db: Session,
    result_type: ResultType,
    subfolder: str | list[str],
    filename: str | list[str],
) -> dict[str, Any] | list[Any]:
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


_MULTILOCUS_MODEL_FILES = {
    "FARMCPU": "farmcpu_results.tsv",
    "BLINK": "blink_results.tsv",
    "MLMM": "mlmm_results.tsv",
}


def _resolve_multilocus_manhattan_cache(job_id: uuid.UUID, model_key: str) -> str:
    job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
    ml_dir = job_root / "multilocus"
    tsv_path = ml_dir / _MULTILOCUS_MODEL_FILES[model_key]

    summary_file = ml_dir / "multilocus_summary.json"
    if not tsv_path.is_file() and summary_file.is_file():
        try:
            summary_data = json.loads(summary_file.read_text(encoding="utf-8"))
            entry = (summary_data.get("results") or {}).get(model_key) or {}
            cand = entry.get("tsv_file")
            if cand and Path(cand).is_file():
                tsv_path = Path(cand)
        except Exception:
            pass

    if not tsv_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File hasil model multi-locus {model_key} tidak ditemukan",
        )

    model_cache = job_root / "visualization" / "multilocus" / model_key.lower() / "manhattan_cache.parquet"
    labels_path = model_cache.with_name("manhattan_chr_labels.json")
    if not model_cache.is_file() or not labels_path.is_file():
        summary = generate_manhattan_cache(
            assoc_file_path=str(tsv_path),
            output_cache_path=str(model_cache),
            split_map=load_split_map(job_id),
        )
        return str(summary["output_path"])
    return str(model_cache)


@router.get(
    "/{job_id}/manhattan-data",
    response_model=ManhattanDataResponse,
    summary="Ambil data hasil GWAS untuk Manhattan Plot (format kolom, ringan)",
)
def get_manhattan_data(
    job_id: uuid.UUID,
    model: str | None = Query(default=None, description="Model opsional: GEMMA, FARMCPU, BLINK, atau MLMM"),
    db: Session = Depends(get_db),
) -> ManhattanDataResponse:
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

    clean_model = model.strip().upper() if isinstance(model, str) and model.strip() else None
    if clean_model is not None and clean_model not in {"GEMMA", "GLM", "LMM"}:
        if clean_model not in _MULTILOCUS_MODEL_FILES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Model '{clean_model}' tidak didukung. Gunakan FARMCPU, BLINK, atau MLMM.",
            )
        cache_path = _resolve_multilocus_manhattan_cache(job.id, clean_model)
    else:
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
                    split_map=load_split_map(job.id),
                )
                cache_path = cache_summary["output_path"]
                job.parameters = {**params, "manhattan_cache_path": cache_path}
                db.commit()

        if not cache_path:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cache Manhattan Plot tidak ditemukan")

        labels_path = Path(cache_path).with_name("manhattan_chr_labels.json")
        if not labels_path.is_file() and job.assoc_output_path:
            assoc_path = Path(job.assoc_output_path)
            if assoc_path.is_file():
                generate_manhattan_cache(
                    assoc_file_path=str(assoc_path),
                    output_cache_path=str(cache_path),
                    split_map=load_split_map(job.id),
                )

    try:
        df = load_manhattan_cache(cache_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File cache Manhattan Plot hilang dari storage",
        ) from exc

    chr_labels = load_manhattan_chr_labels(cache_path)

    return ManhattanDataResponse(
        job_id=str(job_id),
        total_points=len(df),
        chr=[int(v) if isinstance(v, int) and not isinstance(v, bool) else (int(float(v)) if pd.notna(v) and str(v).replace(".", "", 1).isdigit() else 0) for v in df["CHR"].tolist()],
        bp=[int(v) if isinstance(v, int) and not isinstance(v, bool) else (int(float(v)) if pd.notna(v) and str(v).replace(".", "", 1).isdigit() else 0) for v in df["BP"].tolist()],
        snp=[str(v) for v in df["SNP"].tolist()],
        log10_p=[round(float(v), 4) if pd.notna(v) else 0.0 for v in df["LOG10_P"].tolist()],
        chr_labels=chr_labels,
        model=clean_model,
    )


@router.get("/{job_id}/inspection", summary="Data inspeksi & rekonsiliasi fenotipe (Fase 1)")
def get_inspection_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _load_job_json_result(
        job_id, db, ResultType.INSPECTION_REPORT, "inspection", ["inspection_report.json", "inspection.json"]
    )


def _enrich_qc_split_parts(data: Any, job_id: uuid.UUID) -> Any:
    """Populate split_chrom_parts and Fase 0 contig-filter counts when present."""
    if not isinstance(data, dict) or data.get("status") in {"skipped", "not_available"}:
        return data
    enriched = dict(data)

    if "split_chrom_parts" not in enriched or not isinstance(enriched["split_chrom_parts"], dict):
        smap = load_split_map(job_id)
        split_chroms = smap.get("split_chroms") if isinstance(smap, dict) else None
        if isinstance(split_chroms, dict) and split_chroms:
            parts_counts = {
                str(k): int(v.get("num_parts") or len(v.get("parts") or []))
                for k, v in split_chroms.items()
                if isinstance(v, dict)
            }
            enriched["split_chrom_parts"] = parts_counts
            if not enriched.get("split_chromosomes"):
                enriched["split_chromosomes"] = sorted(parts_counts.keys())

    removed = enriched.get("removed")
    if isinstance(removed, dict) and "non_primary_variants" not in removed:
        stats_file = (
            Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "normalization" / "qc_overflow_stats.json"
        )
        if stats_file.is_file():
            try:
                norm_stats = json.loads(stats_file.read_text(encoding="utf-8"))
                if isinstance(norm_stats, dict) and "contig_filter_pattern" in norm_stats:
                    new_removed = dict(removed)
                    new_removed["non_primary_variants"] = int(
                        norm_stats.get("non_primary_removed_variants", 0)
                    )
                    new_removed["non_primary_contigs"] = int(
                        norm_stats.get("non_primary_removed_contigs", 0)
                    )
                    if "invalid_pos" not in new_removed:
                        new_removed["invalid_pos"] = int(norm_stats.get("invalid_pos_removed", 0))
                    enriched["removed"] = new_removed
            except Exception:
                pass

    return enriched


@router.get("/{job_id}/qc-comparison", summary="Data perbandingan QC sebelum/sesudah (Fase 2)")
def get_qc_comparison_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    data = _load_job_json_result(
        job_id, db, ResultType.QC_COMPARISON, "qc", ["qc_comparison.json", "qc_summary.json"]
    )
    if isinstance(data, dict):
        before = data.get("before") or {}
        after = data.get("after") or {}
        is_stale_or_identical = (
            (before.get("n_snps") == after.get("n_snps") and (before.get("n_snps") or 0) > 0)
            or "maf_hist" not in before
            or not before.get("maf_hist", {}).get("edges")
        )
        if is_stale_or_identical:
            qc_dir = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "qc"
            if qc_dir.is_dir() and ((qc_dir / "before_qc.afreq").is_file() or (qc_dir / "before_qc.frq").is_file()):
                from app.services.qc_service import build_qc_comparison_from_plink_outputs
                try:
                    healed = _enrich_qc_split_parts(build_qc_comparison_from_plink_outputs(qc_dir), job_id)
                    comp_file = qc_dir / "qc_comparison.json"
                    comp_file.write_text(json.dumps(healed, indent=2), encoding="utf-8")
                    return healed
                except Exception as err:
                    logger.warning("Gagal meregenerasi perbandingan QC untuk job %s: %s", job_id, err)
    return _enrich_qc_split_parts(data, job_id)


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


@lru_cache(maxsize=64)
def _cached_greedy_clumping(
    norm_assoc_str: str,
    mtime_ns: int,
    size_bytes: int,
    sel_p: float,
    sug_p: float = 1e-5,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    from app.services.postgwas_service import extract_suggestive_peaks, run_greedy_clumping

    assoc_file = Path(norm_assoc_str)
    snps = run_greedy_clumping(assoc_file=assoc_file, selected_p=sel_p) or []
    suggestive = extract_suggestive_peaks(
        assoc_file=assoc_file,
        selected_p=sel_p,
        suggestive_p=sug_p,
        exclude_snps={s["snp"] for s in snps if s.get("snp")},
    )
    return (
        tuple(dict(item) for item in snps),
        tuple(dict(item) for item in suggestive),
    )


@router.get("/{job_id}/lead-snps", summary="Daftar Lead SNPs independen hasil clumping (Fase 4F)")
def get_lead_snps_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    data = _load_job_json_result(
        job_id, db, ResultType.LEAD_SNPS, "postgwas", "lead_snps.json"
    )
    raw_snps = data if isinstance(data, list) else (data.get("snps", []) if isinstance(data, dict) else [])
    has_suggestive_field = isinstance(data, dict) and ("suggestive" in data or "sugestif" in data)
    needs_fallback = (
        (isinstance(data, dict) and data.get("status") == "not_available")
        or (not raw_snps and not has_suggestive_field)
    )
    if needs_fallback:
        job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
        candidates = [
            job_root / "gwas" / "gwas_results.normalized.tsv",
            job_root / "postgwas" / "gwas_results.normalized.tsv",
            job_root / "gwas" / "gwas_lmm.assoc.txt",
            job_root / "gwas" / "gwas_glm.assoc.txt",
        ]
        norm_assoc = next((p for p in candidates if p.is_file()), None)
        if norm_assoc is not None:
            thresh_data = _load_job_json_result(job_id, db, ResultType.THRESHOLDS, "postgwas", "thresholds.json")
            sel_p = float(thresh_data.get("selected_p") or 5e-8) if isinstance(thresh_data, dict) else 5e-8
            sug_p = float(thresh_data.get("suggestive") or 1e-5) if isinstance(thresh_data, dict) else 1e-5
            st = norm_assoc.stat()
            cached_snps, cached_sug = _cached_greedy_clumping(
                str(norm_assoc), st.st_mtime_ns, st.st_size, sel_p, sug_p
            )
            regenerated = [dict(item) for item in cached_snps]
            suggestive_list = [dict(item) for item in cached_sug]
            if regenerated or suggestive_list:
                return {
                    "count": len(regenerated),
                    "snps": regenerated,
                    "suggestive_count": len(suggestive_list),
                    "suggestive": suggestive_list,
                }

    if isinstance(data, list):
        return {"count": len(data), "snps": data, "suggestive_count": 0, "suggestive": []}
    if isinstance(data, dict):
        sug_list = data.get("suggestive")
        if not isinstance(sug_list, list):
            sug_list = data.get("sugestif") if isinstance(data.get("sugestif"), list) else []
        return {
            **data,
            "count": len(raw_snps),
            "snps": raw_snps,
            "suggestive_count": len(sug_list),
            "suggestive": sug_list,
        }
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
            from app.tasks.multilocus_tasks import _single_model_label, generate_peak_comparison
            job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
            job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
            out_csv = job_root / "postgwas" / "Ringkasan_Puncak_GWAS.csv"
            out_json = job_root / "postgwas" / "peak_comparison.json"
            recomp = generate_peak_comparison(
                single_lead_snps=single_leads,
                multi_lead_snps=multi_leads,
                output_csv=out_csv,
                output_json=out_json,
                single_model_label=_single_model_label(job.parameters if job else None),
                split_map=load_split_map(job_id),
            )
            return recomp
    return data


@router.get("/{job_id}/multilocus-results", summary="Hasil detail analisis Multi-Locus GWAS (FarmCPU, BLINK, MLMM) (Fase 4C)")
def get_multilocus_results_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    data = _load_job_json_result(
        job_id, db, ResultType.MULTILOCUS_RESULTS, "multilocus", "multilocus_summary.json"
    )
    if isinstance(data, dict) and data.get("status") not in {"skipped", "not_available"}:
        leads = data.get("lead_snps") or []
        needs_enrichment = not leads or any("pve_pct" not in s for s in leads if isinstance(s, dict))
        if needs_enrichment:
            job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "multilocus"
            if job_root.is_dir():
                from app.tasks.multilocus_tasks import (
                    METHOD_DISPLAY_NAMES,
                    extract_multilocus_leads_from_tsv,
                    write_significant_pve_csv,
                )

                split_map = load_split_map(job_id)
                job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
                trait_label = str(
                    (((job.parameters if job else None) or {}).get("phenotype") or {}).get("trait")
                    or "Trait"
                )
                extracted_leads: list[dict[str, Any]] = []
                results_map = dict(data.get("results") or {})
                for m_name in ["farmcpu", "blink", "mlmm"]:
                    tsv_f = job_root / f"{m_name}_results.tsv"
                    if tsv_f.is_file():
                        m_upper = m_name.upper()
                        m_snps = extract_multilocus_leads_from_tsv(
                            tsv_f,
                            method_name=m_upper,
                            trait_name=trait_label,
                            max_top=5,
                            split_map=split_map,
                        )
                        if m_snps:
                            extracted_leads.extend(m_snps)
                            display_m = METHOD_DISPLAY_NAMES.get(m_upper, m_upper)
                            pve_csv = job_root / f"{display_m}_Significant_SNPs_PVE.csv"
                            try:
                                write_significant_pve_csv(pve_csv, m_snps, trait_name=trait_label)
                            except Exception:
                                pass
                            if m_upper in results_map and isinstance(results_map[m_upper], dict):
                                results_map[m_upper] = {
                                    **results_map[m_upper],
                                    "pve_csv_file": str(pve_csv),
                                    "significant_snps": m_snps,
                                }
                if extracted_leads:
                    data["lead_snps"] = extracted_leads
                    data["n_lead_snps"] = len(extracted_leads)
                    data["results"] = results_map
                    try:
                        (job_root / "multilocus_summary.json").write_text(
                            json.dumps(data, indent=2), encoding="utf-8"
                        )
                    except Exception:
                        pass
    return data


def _resolve_dataset_for_job(db: Session, job: AnalysisJob | None) -> Dataset | None:
    if job is None or job.dataset_id is None:
        return None
    return db.query(Dataset).filter(Dataset.id == job.dataset_id).first()


def _resolve_dataset_gff_path(db: Session, job: AnalysisJob | None) -> Path | None:
    dataset = _resolve_dataset_for_job(db, job)
    if dataset is None:
        return None
    if dataset.annotation_file_path and Path(dataset.annotation_file_path).is_file():
        return Path(dataset.annotation_file_path)
    ds_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id)
    for cand_name in ("annotation.gff3.gz", "annotation.gff3", "annotation.gff.gz", "annotation.gff"):
        ds_anno = ds_dir / cand_name
        if ds_anno.is_file():
            return ds_anno
    return None


def _resolve_dataset_go_mapping_path(db: Session, job: AnalysisJob | None) -> Path | None:
    from app.services.go_enrichment_service import resolve_dataset_go_mapping_path

    return resolve_dataset_go_mapping_path(_resolve_dataset_for_job(db, job))


def _resolve_dataset_ortholog_index_path(db: Session, job: AnalysisJob | None) -> Path | None:
    from app.services.ortholog_service import resolve_dataset_ortholog_index_path

    return resolve_dataset_ortholog_index_path(_resolve_dataset_for_job(db, job))


def _resolve_dataset_fasta_path(db: Session, job: AnalysisJob | None) -> Path | None:
    dataset = _resolve_dataset_for_job(db, job)
    if dataset is None:
        return None
    ref_meta = (dataset.extra_metadata or {}).get("reference_fasta") if dataset.extra_metadata else None
    if isinstance(ref_meta, dict) and ref_meta.get("path") and Path(ref_meta["path"]).is_file():
        return Path(ref_meta["path"])
    ds_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id)
    for cand_name in ("reference.fa", "reference.fasta", "reference.fna"):
        ref_f = ds_dir / cand_name
        if ref_f.is_file():
            return ref_f
    return None


def _normalize_candidate_genes_payload(
    data: dict[str, Any],
    *,
    job: AnalysisJob | None = None,
    gff_path: Path | None = None,
    go_mapping_path: Path | None = None,
) -> dict[str, Any]:
    """Pastikan struktur candidate_genes kompatibel antara backend lama (`rows`) dan frontend (`mappings`)."""
    raw_rows = data.get("mappings") or data.get("rows") or []
    normalized_rows: list[dict[str, Any]] = []
    unique_genes: set[str] = set(data.get("candidate_genes_list") or [])
    counts = dict(data.get("counts") or {"Genik": 0, "Near-gen": 0, "Intergenik": 0})
    recalc_counts = not bool(data.get("counts"))

    for row in raw_rows:
        r = dict(row)
        pos = int(r.get("pos") or 0)
        cat = r.get("category") or "Intergenik"
        if recalc_counts and cat in counts:
            counts[cat] += 1

        nearest_id = r.get("nearest_gene") or (r.get("closest_gene") or {}).get("gene_id") or "-"
        nearest_name = r.get("nearest_gene_name") or (r.get("closest_gene") or {}).get("gene_name") or nearest_id
        dist_bp = r.get("distance_bp")
        if dist_bp is None:
            dist_bp = r.get("distance_to_closest_gene")
        dist_val = int(dist_bp) if dist_bp is not None and int(dist_bp) >= 0 else None

        closest_src = (r.get("closest_gene") or {}).get("source")
        gff_src = r.get("gff_source") or closest_src or "GFF3"
        raw_src = r.get("snp_source") or r.get("source")
        if not raw_src or raw_src == "GFF3" or (closest_src and raw_src == closest_src and "snp_source" not in r):
            snp_src = "GEMMA"
        else:
            snp_src = str(raw_src)
        r["source"] = snp_src
        r["snp_source"] = snp_src
        r["gff_source"] = gff_src

        r["nearest_gene"] = nearest_id
        r["nearest_gene_name"] = nearest_name
        r["distance_bp"] = dist_val if dist_val is not None else -1
        r["distance_to_closest_gene"] = dist_val

        if "closest_gene" not in r:
            if nearest_id and nearest_id != "-":
                r["closest_gene"] = {
                    "gene_id": nearest_id,
                    "gene_name": nearest_name,
                    "strand": r.get("strand", "-"),
                    "description": r.get("description", ""),
                }
            else:
                r["closest_gene"] = None

        if cat in {"Genik", "Near-gen"} and nearest_id and nearest_id != "-":
            unique_genes.add(nearest_id)

        win_list: list[dict[str, Any]] = []
        for gw in r.get("genes_in_window") or []:
            g = dict(gw)
            gid = g.get("gene_id") or g.get("id") or ""
            gname = g.get("gene_name") or g.get("name") or gid
            g["id"] = gid
            g["gene_id"] = gid
            g["name"] = gname
            g["gene_name"] = gname
            if "distance" not in g or g["distance"] is None:
                g_start = int(g.get("start") or 0)
                g_end = int(g.get("end") or 0)
                if g_start <= pos <= g_end:
                    g["distance"] = 0
                elif pos < g_start:
                    g["distance"] = g_start - pos
                else:
                    g["distance"] = pos - g_end
            win_list.append(g)

        r["genes_in_window"] = win_list
        r["genes_in_window_count"] = len(win_list)
        normalized_rows.append(r)

    win_bp = int(data.get("window_bp") or (int(data.get("window_kb") or 100) * 1000))
    win_kb = int(data.get("window_kb") or max(1, round(win_bp / 1000)))
    sorted_genes = sorted(unique_genes)

    return {
        **data,
        "status": "completed",
        "window_bp": win_bp,
        "window_kb": win_kb,
        "total_lead_snps": len(normalized_rows),
        "counts": counts,
        "candidate_genes_list": sorted_genes,
        "rows": normalized_rows,
        "mappings": normalized_rows,
        "go_enrichment": data.get("go_enrichment"),
    }


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def refresh_go_if_stale(
    db: Session,
    job: AnalysisJob | None,
    cand_payload: dict[str, Any] | None,
    anno_dir: Path,
    *,
    cached_go: dict[str, Any] | None = None,
    gff_path: Path | None = None,
    go_mapping_path: Path | None = None,
    ortholog_index_path: Path | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """Menyegarkan hasil GO job secara lazy dan non-destruktif bila fingerprint input berubah."""
    from app.services import go_enrichment_service as go_svc
    from app.services.go_ontology_service import load_go_ontology

    if gff_path is None:
        gff_path = _resolve_dataset_gff_path(db, job)
    if go_mapping_path is None:
        go_mapping_path = _resolve_dataset_go_mapping_path(db, job)
    if ortholog_index_path is None:
        ortholog_index_path = _resolve_dataset_ortholog_index_path(db, job)

    go_file = anno_dir / "go_enrichment.json"
    current_go: dict[str, Any] | None = cached_go if isinstance(cached_go, dict) else None
    if current_go is None and go_file.is_file():
        try:
            loaded = json.loads(go_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current_go = loaded
        except Exception:
            current_go = None
    if current_go is None and isinstance(cand_payload, dict) and isinstance(cand_payload.get("go_enrichment"), dict):
        current_go = cand_payload["go_enrichment"]

    params = job.parameters if job is not None else None
    feature_type = go_svc.resolve_job_feature_type(params)
    contig_pattern, contig_pattern_source = go_svc.resolve_job_contig_pattern(params)
    trait_label = str(((params or {}).get("phenotype") or {}).get("trait") or "Candidate Genes")

    expected_fp = go_svc.compute_go_fingerprint(
        mapping_sha256=go_svc.file_sha256_cached(go_mapping_path),
        gff_sha256=go_svc.file_sha256_cached(gff_path),
        obo=load_go_ontology(),
        ortholog_sha256=go_svc.file_sha256_cached(ortholog_index_path),
        feature_type=feature_type,
        contig_pattern=contig_pattern,
        allow_synthetic=go_svc._allow_synthetic_annotation(),
    )
    stored_fp = (
        ((current_go.get("go_stats") or {}).get("fingerprint"))
        if isinstance(current_go, dict)
        else None
    )
    if isinstance(current_go, dict) and stored_fp == expected_fp and stored_fp is not None:
        return current_go, False

    gff_available = bool(gff_path is not None and gff_path.is_file())
    if not gff_available and isinstance(current_go, dict):
        bg_src = str(((current_go.get("go_stats") or {}).get("background") or {}).get("source") or "")
        made_with_gff = bg_src.startswith("gff_") or (
            not current_go.get("go_stats") and bool(current_go.get("go_table"))
        )
        if made_with_gff:
            return {**current_go, "stale": True, "stale_reason": "gff_unavailable"}, False

    if not isinstance(cand_payload, dict):
        return current_go, False
    mapped_rows = cand_payload.get("rows") or cand_payload.get("mappings") or []
    cand_genes = cand_payload.get("candidate_genes_list") or []
    if not mapped_rows and not cand_genes:
        return current_go, False

    try:
        new_go = go_svc.run_go_enrichment_analysis(
            mapped_rows=mapped_rows,
            candidate_genes_list=cand_genes,
            trait_name=trait_label,
            output_dir=anno_dir,
            gff_path=gff_path,
            go_mapping_path=go_mapping_path,
            feature_type=feature_type,
            contig_filter_pattern=contig_pattern,
            contig_pattern_source=contig_pattern_source,
            ortholog_index_path=ortholog_index_path,
        )
    except Exception:
        logger.exception("Gagal menyegarkan hasil GO untuk job=%s", getattr(job, "id", None))
        if isinstance(current_go, dict):
            return {**current_go, "stale": True, "stale_reason": "recompute_failed"}, False
        return None, False

    if go_file.is_file():
        try:
            prev_payload = json.loads(go_file.read_text(encoding="utf-8"))
        except Exception:
            prev_payload = current_go
        if isinstance(prev_payload, dict):
            _write_json_atomic(anno_dir / "go_enrichment.previous.json", prev_payload)
    elif isinstance(current_go, dict):
        _write_json_atomic(anno_dir / "go_enrichment.previous.json", current_go)

    _write_json_atomic(go_file, new_go)
    cand_file = anno_dir / "candidate_genes.json"
    if cand_file.is_file() or isinstance(cand_payload, dict):
        try:
            base_cand = (
                json.loads(cand_file.read_text(encoding="utf-8"))
                if cand_file.is_file()
                else dict(cand_payload)
            )
            if isinstance(base_cand, dict):
                base_cand["go_enrichment"] = new_go
                _write_json_atomic(cand_file, base_cand)
        except Exception:
            logger.exception("Gagal memperbarui candidate_genes.json untuk job=%s", getattr(job, "id", None))

    return new_go, True


@router.get("/{job_id}/candidate-genes", summary="Pemetaan anotasi gen kandidat GFF3 & GO (Fase 5)")
def get_candidate_genes_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    data = _load_job_json_result(
        job_id, db, ResultType.CANDIDATE_GENES, "annotation", "candidate_genes.json"
    )
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    gff_candidate = _resolve_dataset_gff_path(db, job)
    go_mapping_candidate = _resolve_dataset_go_mapping_path(db, job)
    anno_dir = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "annotation"
    if isinstance(data, dict) and (data.get("rows") or data.get("mappings")):
        norm = _normalize_candidate_genes_payload(
            data,
            job=job,
            gff_path=gff_candidate,
            go_mapping_path=go_mapping_candidate,
        )
        refreshed_go, _ = refresh_go_if_stale(
            db,
            job,
            norm,
            anno_dir,
            gff_path=gff_candidate,
            go_mapping_path=go_mapping_candidate,
        )
        if refreshed_go is not None:
            norm["go_enrichment"] = refreshed_go
        return norm

    # Pemulihan on-the-fly jika dataset memiliki file GFF3 dan job memiliki Lead SNPs
    if job is not None and job.dataset_id is not None:
        if gff_candidate is not None:
            lead_res = get_lead_snps_data(job_id, db)
            gemma_snps = lead_res if isinstance(lead_res, list) else lead_res.get("snps", [])
            ml_candidates: list[dict[str, Any]] = []
            ml_summary_f = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "multilocus" / "multilocus_summary.json"
            if ml_summary_f.is_file():
                try:
                    ml_data = json.loads(ml_summary_f.read_text(encoding="utf-8"))
                    ml_candidates.extend(ml_data.get("lead_snps") or [])
                    for m_entry in (ml_data.get("results") or {}).values():
                        if isinstance(m_entry, dict):
                            ml_candidates.extend(m_entry.get("significant_snps") or [])
                except Exception:
                    pass
            from app.services.annotation_service import (
                map_lead_snps_to_genes,
                merge_annotation_input_snps,
                parse_gff3_genes,
            )

            lead_snps = merge_annotation_input_snps(gemma_snps, ml_candidates)
            if lead_snps:
                try:
                    import csv

                    params = job.parameters or {}
                    raw_anno = params.get("annotation") or {}
                    feat_type = raw_anno.get("feature_type", "gene") if isinstance(raw_anno, dict) else "gene"
                    win_kb = int(
                        (raw_anno.get("window_kb") if isinstance(raw_anno, dict) else None)
                        or params.get("chosen_ld_decay_kb")
                        or 100
                    )
                    win_bp = win_kb * 1000

                    genes_by_chr, _ = parse_gff3_genes(gff_candidate, feature_type=feat_type)
                    mapped_rows, category_counts = map_lead_snps_to_genes(
                        lead_snps=lead_snps,
                        genes_by_chr=genes_by_chr,
                        window_bp=win_bp,
                    )
                    payload = _normalize_candidate_genes_payload({
                        "status": "completed",
                        "window_bp": win_bp,
                        "window_kb": win_kb,
                        "counts": category_counts,
                        "rows": mapped_rows,
                    }, job=job, gff_path=gff_candidate, go_mapping_path=go_mapping_candidate)

                    anno_dir.mkdir(parents=True, exist_ok=True)
                    cand_file = anno_dir / "candidate_genes.json"
                    _write_json_atomic(cand_file, payload)
                    refreshed_go, _ = refresh_go_if_stale(
                        db,
                        job,
                        payload,
                        anno_dir,
                        gff_path=gff_candidate,
                        go_mapping_path=go_mapping_candidate,
                    )
                    if refreshed_go is not None:
                        payload["go_enrichment"] = refreshed_go

                    go_genes = payload.get("candidate_genes_list") or []
                    (anno_dir / "Daftar_Gen_Untuk_GO.txt").write_text(
                        "\n".join(go_genes) + ("\n" if go_genes else ""), encoding="utf-8"
                    )

                    ekstraksi_csv = anno_dir / "Ekstraksi_Gen_Mentah.csv"
                    with ekstraksi_csv.open("w", encoding="utf-8", newline="") as ef:
                        writer = csv.writer(ef)
                        writer.writerow(["SNP", "Sumber", "Kromosom", "Posisi", "P_Value", "Gen_Terdekat", "Nama_Gen", "Jarak_bp", "Kategori", "Strand", "Deskripsi", "Gen_Dalam_Jendela"])
                        for r in payload["rows"]:
                            win_ids = ", ".join((g.get("id") or g.get("gene_id") or "") for g in r.get("genes_in_window", []))
                            writer.writerow([
                                r.get("snp"),
                                r.get("source") or "GEMMA",
                                r.get("chr"),
                                r.get("pos"),
                                r.get("p"),
                                r.get("nearest_gene"),
                                r.get("nearest_gene_name"),
                                r.get("distance_bp"),
                                r.get("category"),
                                r.get("strand"),
                                r.get("description"),
                                win_ids,
                            ])
                    return payload
                except Exception:
                    pass

    return data


@router.get("/{job_id}/go-enrichment", summary="Analisis Gene Ontology (GO) Enrichment & Tabel GO (Fase 5.3)")
def get_go_enrichment_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    anno_dir = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "annotation"
    go_file = anno_dir / "go_enrichment.json"
    cached_go: dict[str, Any] | None = None
    if go_file.is_file():
        try:
            loaded = json.loads(go_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and (loaded.get("go_table") or loaded.get("status")):
                cached_go = loaded
        except Exception:
            cached_go = None

    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    cand_raw = _load_job_json_result(
        job_id, db, ResultType.CANDIDATE_GENES, "annotation", "candidate_genes.json"
    )
    if isinstance(cand_raw, dict) and (cand_raw.get("rows") or cand_raw.get("mappings")):
        cand_norm = _normalize_candidate_genes_payload(cand_raw, job=job)
        refreshed, _ = refresh_go_if_stale(db, job, cand_norm, anno_dir, cached_go=cached_go)
        if isinstance(refreshed, dict):
            return refreshed
    else:
        cand = get_candidate_genes_data(job_id, db)
        if isinstance(cand, dict) and isinstance(cand.get("go_enrichment"), dict):
            return cand["go_enrichment"]

    if isinstance(cached_go, dict):
        return cached_go
    return {"status": "not_available", "job_id": str(job_id), "go_table": [], "enrichment_terms": []}


def _sanitize_primers_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize primers payload paths to relative filenames and ensure nearby_variants shape."""
    out = dict(payload)
    if isinstance(out.get("candidate_regions"), dict):
        cr = dict(out["candidate_regions"])
        for key in ("csv", "bed", "fasta"):
            val = cr.get(key)
            if isinstance(val, str) and val.strip():
                cr[key] = Path(val).name
        out["candidate_regions"] = cr

    if isinstance(out.get("markers"), list):
        sanitized_markers: list[Any] = []
        for marker in out["markers"]:
            if not isinstance(marker, dict):
                sanitized_markers.append(marker)
                continue
            item = dict(marker)
            raw_nv = item.get("nearby_variants")
            nearby_list = list(raw_nv) if isinstance(raw_nv, list) else []
            item["nearby_variants"] = nearby_list
            if "nearby_variants_count" not in item or item["nearby_variants_count"] is None:
                item["nearby_variants_count"] = len(nearby_list)
            else:
                item["nearby_variants_count"] = int(item["nearby_variants_count"])
            sanitized_markers.append(item)
        out["markers"] = sanitized_markers

    if isinstance(out.get("kasp"), dict):
        kasp = dict(out["kasp"])
        if isinstance(kasp.get("files"), dict):
            kasp_files = dict(kasp["files"])
            for fkey, fval in list(kasp_files.items()):
                if isinstance(fval, str) and fval.strip():
                    kasp_files[fkey] = Path(fval).name
            kasp["files"] = kasp_files
        if isinstance(kasp.get("markers"), list):
            kasp["markers"] = list(kasp["markers"])
        out["kasp"] = kasp

    return out


@router.get("/{job_id}/primers", summary="Sekuens Flanking & Desain Primer PCR Primer3 / KASP (Fase 5.4 / 6–8)")
def get_primers_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    anno_dir = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "annotation"
    primers_file = anno_dir / "primers.json"
    if primers_file.is_file():
        try:
            loaded = json.loads(primers_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and (
                loaded.get("markers")
                or loaded.get("status") in {"completed", "no_reference", "no_reference_for_chr", "skipped"}
            ):
                return _sanitize_primers_payload(loaded)
        except Exception:
            pass

    # Bangkitkan on-the-fly dari candidate_genes atau lead_snps untuk job yang sudah selesai
    cand = get_candidate_genes_data(job_id, db)
    source_snps: list[dict[str, Any]] = []
    if isinstance(cand, dict) and cand.get("rows"):
        source_snps = list(cand["rows"])
    else:
        lead_res = get_lead_snps_data(job_id, db)
        source_snps = lead_res if isinstance(lead_res, list) else list(lead_res.get("snps") or [])

    if source_snps:
        try:
            from app.services.primer_service import generate_flanking_and_primers

            job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
            trait_label = str(
                (((job.parameters if job else None) or {}).get("phenotype") or {}).get("trait")
                or "trait"
            )
            bim_file = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "qc" / "plink_qc.bim"
            payload = generate_flanking_and_primers(
                lead_snps=source_snps,
                trait_name=trait_label,
                max_snps=20,
                fasta_path=_resolve_dataset_fasta_path(db, job),
                output_dir=anno_dir,
                bim_path=bim_file if bim_file.is_file() else None,
                split_map=load_split_map(job_id),
            )
            anno_dir.mkdir(parents=True, exist_ok=True)
            primers_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return _sanitize_primers_payload(payload)
        except Exception:
            pass

    return {"status": "not_available", "job_id": str(job_id), "total_markers": 0, "markers": []}
