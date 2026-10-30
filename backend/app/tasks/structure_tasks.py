"""Celery task for Population Structure, Kinship, and LD Decay (Fase 3 + 4D)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, ResultType
from app.schemas.pipeline import PopulationType, StructureConfig
from app.services.structure_service import (
    compute_centered_kinship,
    compute_king_robust_table,
    compute_ld_decay_curve,
    determine_model_recommendation,
    read_plink_binary,
    run_pca_decomposition,
)
from app.tasks._common import (
    JobCancelledError,
    append_log,
    ensure_not_cancelled,
    mark_job,
    stage_dir,
    upsert_result,
    which_tool,
    write_json,
)
from app.tasks.celery_app import celery_app, get_task_logger

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.structure_tasks.run_structure_analysis",
    queue="structure",
    max_retries=0,
)
def run_structure_analysis(
    self,
    imputation_or_qc_result: dict[str, Any],
    job_id: str,
    dataset_id: str,
    structure_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Menjalankan analisis Struktur Populasi (LD pruning + PCA), Kekerabatan (Kinship + KING),
    dan evaluasi Linkage Disequilibrium Decay (Fase 3 + 4D).
    """
    db = SessionLocal()
    ctx: dict[str, Any] = dict(imputation_or_qc_result or {})
    try:
        ensure_not_cancelled(job_id, db)
        job = db.get(AnalysisJob, job_id)
        dataset = db.get(Dataset, dataset_id)
        if job is None:
            raise ValueError(f"AnalysisJob {job_id} tidak ditemukan")
        if dataset is None:
            raise ValueError(f"Dataset {dataset_id} tidak ditemukan")

        workspace = stage_dir(job_id, "structure")
        log_file = workspace / "structure.log"

        # Resolusi input genotipe biner PLINK
        bfile_prefix = (
            ctx.get("genotype_bfile_prefix")
            or ctx.get("qc_bfile_prefix")
            or ctx.get("output_prefix")
        )
        if not bfile_prefix:
            # Cari file .bed di tahap QC atau imputasi
            candidate_qc = workspace.parent / "qc" / "plink_qc"
            if candidate_qc.with_suffix(".bed").is_file():
                bfile_prefix = str(candidate_qc)
            else:
                candidate_imp = workspace.parent / "imputation" / "imputed"
                if candidate_imp.with_suffix(".bed").is_file():
                    bfile_prefix = str(candidate_imp)

        if not bfile_prefix or not Path(f"{bfile_prefix}.bed").is_file():
            raise FileNotFoundError(f"File PLINK biner tidak ditemukan untuk analisis struktur: {bfile_prefix}")

        # Konfigurasi struktur
        raw_struct = (job.parameters or {}).get("structure") or structure_params or {}
        cfg = StructureConfig(**raw_struct) if isinstance(raw_struct, dict) else raw_struct

        pop_type = (job.parameters or {}).get("qc", {}).get("population_type", "inbred")
        requested_model = (job.parameters or {}).get("gwas", {}).get("model", "auto")

        mark_job(
            db,
            job,
            overall_status=JobStatus.RUNNING_STRUCTURE,
            current_step=PipelineStep.STRUCTURE,
            work_dir=str(workspace),
        )

        append_log(
            log_file,
            f"=== TAHAP 5: STRUKTUR POPULASI, KINSHIP & LD DECAY ===\n"
            f"Waktu mulai        : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Input Genotipe     : {bfile_prefix}\n"
            f"Pruning window/r2  : {cfg.prune_window} SNP / r2={cfg.prune_r2}\n"
            f"Jumlah PC          : {cfg.n_pcs}\n"
            f"Jendela LD Decay   : {cfg.ld_decay_window_kb} kb (bin {cfg.ld_decay_bin_kb} kb, cutoff {cfg.ld_decay_r2_cutoff})\n"
        )

        # 1. Baca data PLINK biner
        samples, variants, G = read_plink_binary(bfile_prefix)
        n_samples, n_variants = G.shape
        append_log(log_file, f"Memuat data genotipe: {n_samples} individu x {n_variants} varian.")

        # 2. PCA Decomposition
        append_log(log_file, "Menjalankan Principal Component Analysis (PCA)...")
        pca_data = run_pca_decomposition(G, samples, n_pcs=cfg.n_pcs)
        pca_file = workspace / "pca.json"
        write_json(pca_file, pca_data)

        # Simpan file eigenvec dan eigenval standar PLINK
        eigenvec_file = workspace / "pca_results.eigenvec"
        with eigenvec_file.open("w", encoding="utf-8") as f_vec:
            for s in pca_data["samples"]:
                pcs_str = "\t".join(str(p) for p in s["pcs"])
                f_vec.write(f"{s['fid']}\t{s['iid']}\t{pcs_str}\n")

        eigenval_file = workspace / "pca_results.eigenval"
        with eigenval_file.open("w", encoding="utf-8") as f_val:
            for ev in pca_data["eigenvalues"]:
                f_val.write(f"{ev}\n")

        # 3. Kinship Matrix (GEMMA Centered)
        append_log(log_file, "Menghitung Matriks Kekerabatan (Centered Kinship Matrix K)...")
        K_matrix = compute_centered_kinship(G)
        kinship_cxx_file = workspace / "kinship.cXX.txt"
        np.savetxt(kinship_cxx_file, K_matrix, fmt="%.8f", delimiter="\t")

        # 4. KING-robust Pairs Table
        append_log(log_file, "Menghitung tabel derajat kekerabatan (KING-robust)...")
        king_data = compute_king_robust_table(G, samples)

        # Downsample matriks kinship untuk heatmap di browser jika sampel > 300
        downsample_factor = max(1, n_samples // 300)
        downsampled_matrix = K_matrix[::downsample_factor, ::downsample_factor].tolist()
        downsampled_order = samples[::downsample_factor]

        kinship_json = {
            "estimator": "gemma_cXX",
            "order": downsampled_order,
            "matrix": [[round(float(v), 5) for v in row] for row in downsampled_matrix],
            "downsampled": downsample_factor > 1,
            "king": king_data,
        }
        kinship_file = workspace / "kinship.json"
        write_json(kinship_file, kinship_json)

        # 5. LD Decay Analysis
        append_log(log_file, "Menghitung kurva peluruhan Linkage Disequilibrium (LD Decay)...")
        ld_decay_data = compute_ld_decay_curve(
            variants=variants,
            G=G,
            window_kb=cfg.ld_decay_window_kb,
            bin_kb=cfg.ld_decay_bin_kb,
            cutoff_r2=cfg.ld_decay_r2_cutoff,
            fallback_kb=cfg.ld_decay_fallback_kb,
        )
        ld_decay_file = workspace / "ld_decay.json"
        write_json(ld_decay_file, ld_decay_data)
        append_log(log_file, f"LD Decay selesai: {ld_decay_data['reason']}")

        # 6. Model Recommendation Engine
        append_log(log_file, "Mengevaluasi rekomendasi model GWAS...")
        rec_data = determine_model_recommendation(
            pca_data=pca_data,
            king_data=king_data,
            population_type=pop_type,
            requested_model=requested_model,
        )
        model_rec_file = workspace / "model_recommendation.json"
        write_json(model_rec_file, rec_data)

        # 7. Siapkan file kovariat PC untuk tahap GWAS
        # File kovariat format GEMMA: Kolom 1 = Intercept (1), Kolom 2..k+1 = PC1..PCk
        n_cov_pcs = min(cfg.n_pcs, rec_data.get("recommended_pc_covariates", 3))
        gemma_cov_file = workspace / "covariates.txt"
        with gemma_cov_file.open("w", encoding="utf-8") as f_cov:
            for s in pca_data["samples"]:
                row_vals = ["1"] + [str(s["pcs"][c]) for c in range(n_cov_pcs)]
                f_cov.write("\t".join(row_vals) + "\n")

        # File kovariat format PLINK2: FID IID PC1 PC2 ...
        plink_cov_file = workspace / "covariates_plink.txt"
        with plink_cov_file.open("w", encoding="utf-8") as f_pcov:
            header = ["#FID", "IID"] + [f"PC{c+1}" for c in range(n_cov_pcs)]
            f_pcov.write("\t".join(header) + "\n")
            for s in pca_data["samples"]:
                row_vals = [s["fid"], s["iid"]] + [str(s["pcs"][c]) for c in range(n_cov_pcs)]
                f_pcov.write("\t".join(row_vals) + "\n")

        # 8. Upsert Results ke database
        upsert_result(db, job_id, ResultType.PCA_DATA, str(pca_file), pca_data)
        upsert_result(db, job_id, ResultType.KINSHIP_DATA, str(kinship_file), kinship_json)
        upsert_result(db, job_id, ResultType.LD_DECAY, str(ld_decay_file), ld_decay_data)
        upsert_result(db, job_id, ResultType.MODEL_RECOMMENDATION, str(model_rec_file), rec_data)

        # Update parameter job & transisi ke tahap GWAS
        job.parameters = {
            **(job.parameters or {}),
            "chosen_ld_decay_kb": ld_decay_data["chosen_kb"],
            "recommended_model": rec_data["recommended_model"],
            "model_used": rec_data["model_used"],
        }
        job.overall_status = JobStatus.RUNNING_GWAS
        job.current_step = PipelineStep.GWAS
        db.commit()

        append_log(
            log_file,
            f"[SUCCESS] Analisis struktur populasi & LD decay selesai.\n"
            f"Rekomendasi model: {rec_data['recommended_model'].upper()} (digunakan: {rec_data['model_used'].upper()})\n"
        )

        return {
            **ctx,
            "status": "completed",
            "eigenvec_path": str(eigenvec_file),
            "eigenval_path": str(eigenval_file),
            "kinship_cxx_path": str(kinship_cxx_file),
            "gemma_covariates_path": str(gemma_cov_file),
            "plink_covariates_path": str(plink_cov_file),
            "ld_decay_kb": ld_decay_data["chosen_kb"],
            "recommended_model": rec_data["recommended_model"],
            "model_rec_data": rec_data,
            "pca_data": pca_data,
            "ld_decay_data": ld_decay_data,
        }

    except JobCancelledError:
        logger.info("Analisis struktur dibatalkan: job=%s", job_id)
        raise
    except Exception as exc:
        db.rollback()
        job = db.get(AnalysisJob, job_id)
        if job is not None:
            mark_job(
                db,
                job,
                overall_status=JobStatus.FAILED,
                failed_step="structure",
                error_message=str(exc),
            )
        logger.exception("Analisis struktur gagal untuk job=%s", job_id)
        raise
    finally:
        db.close()
