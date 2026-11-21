"""Celery task for Population Structure, Kinship, and LD Decay (Fase 3 + 4D)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, ResultType
from app.schemas.pipeline import StructureConfig
from app.services.structure_service import (
    compute_centered_kinship,
    compute_king_robust_table,
    compute_ld_decay_curve,
    determine_model_recommendation,
    read_plink_binary,
    run_pca_decomposition,
    select_ld_decay_blocks,
    write_ld_decay_range_file,
)
from app.tasks._common import (
    JobCancelledError,
    append_log,
    ensure_not_cancelled,
    guard_status_change,
    is_job_cancelled,
    mark_job,
    stage_dir,
    upsert_result,
    which_tool,
    write_json,
    write_stage_summary,
)
from app.tasks.celery_app import celery_app, get_task_logger
from app.utils.process import CLICancelledError, run_cli_command

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.structure_tasks.run_structure_analysis",
    queue="gwas",
    max_retries=0,
)
def run_structure_analysis(
    self,
    imputation_or_qc_result: dict[str, Any],
    job_id: str,
    dataset_id: str,
    structure_params: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Menjalankan analisis Struktur Populasi (LD pruning + PCA), Kekerabatan (Kinship + KING),
    dan evaluasi Linkage Disequilibrium Decay (Fase 3 + 4D).
    """
    stage_started_at = datetime.now(timezone.utc)
    db = SessionLocal()
    ctx: dict[str, Any] = dict(imputation_or_qc_result or {})
    try:
        ensure_not_cancelled(job_id, db, run_id=run_id)
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

        raw_gwas = (job.parameters or {}).get("gwas") or {}
        raw_n_pc_cov = raw_gwas.get("n_pc_covariates") if isinstance(raw_gwas, dict) else None
        if raw_n_pc_cov is None and isinstance(structure_params, dict):
            raw_n_pc_cov = structure_params.get("n_pc_covariates")
        requested_cov_pcs = max(0, int(raw_n_pc_cov)) if raw_n_pc_cov is not None else None
        pca_n_pcs = max(cfg.n_pcs, requested_cov_pcs) if requested_cov_pcs is not None else cfg.n_pcs

        pop_type = (job.parameters or {}).get("qc", {}).get("population_type", "inbred")
        requested_model = raw_gwas.get("model", "auto") if isinstance(raw_gwas, dict) else "auto"

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
            f"Pruning window/r2  : {cfg.prune_window} SNP / step={cfg.prune_step} / r2={cfg.prune_r2}\n"
            f"Jumlah PC          : {cfg.n_pcs}\n"
            f"Jendela LD Decay   : {cfg.ld_decay_window_kb} kb (bin {cfg.ld_decay_bin_kb} kb, cutoff {cfg.ld_decay_r2_cutoff})\n"
        )

        # 1. LD pruning via PLINK sebelum PCA/kinship agar hemat memori & tidak bias LD
        plink_bin = which_tool("plink") or which_tool("plink2")
        if not plink_bin:
            raise RuntimeError(
                "Binary PLINK tidak ditemukan; LD pruning sebelum analisis struktur membutuhkan PLINK."
            )

        prune_prefix = workspace / "ld_prune"
        pruned_bfile = workspace / "pruned"
        prune_cmd = [
            plink_bin,
            "--bfile",
            str(bfile_prefix),
            "--allow-extra-chr",
            "--indep-pairwise",
            str(cfg.prune_window),
            str(cfg.prune_step),
            str(cfg.prune_r2),
            "--out",
            str(prune_prefix),
        ]
        fam_path = Path(f"{bfile_prefix}.fam")
        if "plink2" in Path(plink_bin).stem.lower() and fam_path.is_file():
            n_fam = sum(
                1 for line in fam_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()
            )
            if n_fam < 50:
                append_log(
                    log_file,
                    f"[WARNING] Jumlah sampel ({n_fam}) < 50; menambahkan --bad-ld untuk PLINK 2.",
                )
                prune_cmd.append("--bad-ld")

        extract_cmd = [
            plink_bin,
            "--bfile",
            str(bfile_prefix),
            "--allow-extra-chr",
            "--extract",
            f"{prune_prefix}.prune.in",
            "--make-bed",
            "--out",
            str(pruned_bfile),
        ]

        def _cancel_check() -> bool:
            return is_job_cancelled(job_id, run_id=run_id)

        for cmd in (prune_cmd, extract_cmd):
            ensure_not_cancelled(job_id, db, run_id=run_id)
            append_log(log_file, f"Menjalankan LD pruning PLINK: {' '.join(cmd)}")
            run_cli_command(
                cmd,
                cwd=workspace,
                log_file=log_file,
                timeout=1800,
                cancel_check=_cancel_check,
            )

        if not Path(f"{pruned_bfile}.bed").is_file():
            raise FileNotFoundError(f"Output bfile hasil LD pruning tidak ditemukan: {pruned_bfile}")

        # 2. Baca data PLINK biner hasil pruning
        samples, variants, G = read_plink_binary(str(pruned_bfile))
        n_samples, n_variants = G.shape
        append_log(log_file, f"Memuat data genotipe hasil pruning: {n_samples} individu x {n_variants} varian.")

        def _check_not_cancelled() -> None:
            ensure_not_cancelled(job_id, db, run_id=run_id)

        # 2. PCA Decomposition
        _check_not_cancelled()
        append_log(log_file, "Menjalankan Principal Component Analysis (PCA)...")
        pca_data = run_pca_decomposition(G, samples, n_pcs=pca_n_pcs, cancel_check=_check_not_cancelled)
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
        _check_not_cancelled()
        append_log(log_file, "Menghitung Matriks Kekerabatan (Centered Kinship Matrix K)...")
        K_matrix = compute_centered_kinship(G, cancel_check=_check_not_cancelled)
        kinship_cxx_file = workspace / "kinship.cXX.txt"
        np.savetxt(kinship_cxx_file, K_matrix, fmt="%.8f", delimiter="\t")

        # 4. KING-robust Pairs Table
        _check_not_cancelled()
        append_log(log_file, "Menghitung tabel derajat kekerabatan (KING-robust)...")
        king_data = compute_king_robust_table(G, samples, cancel_check=_check_not_cancelled)

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

        # 5. LD Decay Analysis (dari bfile QC/imputasi tanpa LD pruning, dibatasi blok kontigu)
        _check_not_cancelled()
        append_log(log_file, "Menghitung kurva peluruhan Linkage Disequilibrium (LD Decay)...")
        bim_path = Path(f"{bfile_prefix}.bim")
        if bim_path.is_file():
            block_info = select_ld_decay_blocks(bim_path, max_snps=cfg.ld_decay_max_snps)
        else:
            block_info = {"needs_subset": False, "blocks": []}

        if block_info["needs_subset"] and block_info["blocks"]:
            range_file = workspace / "ld_decay_ranges.txt"
            write_ld_decay_range_file(block_info["blocks"], range_file)
            ld_subset_bfile = workspace / "ld_subset"
            is_plink2 = "plink2" in Path(plink_bin).stem.lower()
            extract_mod = "bed1" if is_plink2 else "range"
            ld_extract_cmd = [
                plink_bin,
                "--bfile",
                str(bfile_prefix),
                "--allow-extra-chr",
                "--extract",
                extract_mod,
                str(range_file),
                "--make-bed",
                "--out",
                str(ld_subset_bfile),
            ]
            ensure_not_cancelled(job_id, db, run_id=run_id)
            append_log(
                log_file,
                f"Mengekstrak blok kontigu LD decay (max {cfg.ld_decay_max_snps} SNP): {' '.join(ld_extract_cmd)}",
            )
            run_cli_command(
                ld_extract_cmd,
                cwd=workspace,
                log_file=log_file,
                timeout=1800,
                cancel_check=_cancel_check,
            )
            _, ld_variants, ld_G = read_plink_binary(str(ld_subset_bfile))
        else:
            _, ld_variants, ld_G = read_plink_binary(str(bfile_prefix))

        append_log(
            log_file,
            f"Menghitung LD decay dari sumber non-prune {bfile_prefix} ({ld_G.shape[1]} varian).",
        )
        ld_decay_data = compute_ld_decay_curve(
            variants=ld_variants,
            G=ld_G,
            window_kb=cfg.ld_decay_window_kb,
            bin_kb=cfg.ld_decay_bin_kb,
            cutoff_r2=cfg.ld_decay_r2_cutoff,
            fallback_kb=cfg.ld_decay_fallback_kb,
            cancel_check=_check_not_cancelled,
        )
        ld_decay_data["method"] = "unphased r^2 (setara plink2 --r2-unphased)"
        ld_decay_data["source_bfile"] = str(bfile_prefix)
        ld_decay_data["n_variants_used"] = int(ld_G.shape[1])
        ld_decay_data["blocks"] = block_info["blocks"]
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
        available_pcs = len(pca_data["samples"][0]["pcs"]) if pca_data["samples"] else pca_n_pcs
        rec_cov_pcs = int(rec_data.get("recommended_pc_covariates", 3))
        target_cov_pcs = requested_cov_pcs if requested_cov_pcs is not None else rec_cov_pcs
        n_cov_pcs = max(0, min(target_cov_pcs, available_pcs, max(0, n_samples - 4)))
        if requested_cov_pcs is not None and n_cov_pcs < requested_cov_pcs:
            append_log(
                log_file,
                f"[WARNING] n_pc_covariates={requested_cov_pcs} dibatasi menjadi {n_cov_pcs} "
                f"(batas derajat kebebasan n={n_samples} atau PC tersedia={available_pcs}).",
            )
        append_log(
            log_file,
            f"Menyiapkan file kovariat ({n_cov_pcs} PC; diminta={requested_cov_pcs}, rekomendasi={rec_cov_pcs}).",
        )
        gemma_cov_file = workspace / "covariates.txt"
        with gemma_cov_file.open("w", encoding="utf-8", newline="\n") as f_cov:
            for s in pca_data["samples"]:
                row_vals = ["1"] + [str(s["pcs"][c]) for c in range(n_cov_pcs)]
                f_cov.write("\t".join(row_vals) + "\n")

        # File kovariat format PLINK2: FID IID PC1 PC2 ...
        plink_cov_file = workspace / "covariates_plink.txt"
        with plink_cov_file.open("w", encoding="utf-8") as f_pcov:
            header = ["#FID", "IID"] + [f"PC{c + 1}" for c in range(n_cov_pcs)]
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
        write_stage_summary(
            job_id,
            "structure",
            {
                "status": "completed",
                "started_at": stage_started_at,
                "recommended_model": rec_data["recommended_model"],
                "model_used": rec_data["model_used"],
                "ld_decay_method": ld_decay_data.get("method"),
                "counts": {
                    "n_samples": int(n_samples),
                    "n_variants_pruned": int(G.shape[1]),
                    "n_variants_ld": int(ld_G.shape[1]),
                    "n_pc": int(n_cov_pcs),
                    "ld_decay_kb": int(ld_decay_data.get("chosen_kb") or 0),
                },
            },
            db=db,
        )
        guard_status_change(db, job.id, JobStatus.RUNNING_GWAS)
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

    except (JobCancelledError, CLICancelledError):
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
