"""Celery task for Candidate Gene Annotation using GFF3 (Fase 5)."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, ResultType
from app.schemas.pipeline import AnnotationConfig
from app.services.annotation_service import (
    map_lead_snps_to_genes,
    normalize_chr_name,
    parse_gff3_genes,
)
from app.tasks._common import (
    JobCancelledError,
    append_log,
    ensure_not_cancelled,
    mark_job,
    stage_dir,
    upsert_result,
    write_json,
)
from app.tasks.celery_app import celery_app, get_task_logger

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.annotation_tasks.run_annotation",
    queue="gwas",
    max_retries=0,
)
def run_annotation(
    self,
    prev_result: dict[str, Any],
    job_id: str,
    dataset_id: str,
    annotation_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Menjalankan pemetaan Lead SNPs ke gen kandidat terdekat dan gen-gen dalam jendela LD decay
    menggunakan file anotasi genom GFF3 referensi (Fase 5).
    Menghasilkan file GO enrichment dan menandai job sebagai COMPLETED.
    """
    db = SessionLocal()
    ctx: dict[str, Any] = dict(prev_result or {})
    try:
        ensure_not_cancelled(job_id, db)
        job = db.get(AnalysisJob, job_id)
        dataset = db.get(Dataset, dataset_id)
        if job is None:
            raise ValueError(f"AnalysisJob {job_id} tidak ditemukan")
        if dataset is None:
            raise ValueError(f"Dataset {dataset_id} tidak ditemukan")

        workspace = stage_dir(job_id, "annotation")
        log_file = workspace / "annotation.log"

        raw_anno = (job.parameters or {}).get("annotation") or annotation_params or {}
        cfg = AnnotationConfig(**raw_anno) if isinstance(raw_anno, dict) else raw_anno

        # Resolusi file GFF3
        gff_candidate: Path | None = None
        if dataset.annotation_file_path and Path(dataset.annotation_file_path).is_file():
            gff_candidate = Path(dataset.annotation_file_path)
        else:
            ds_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id)
            for cand_name in ("annotation.gff3.gz", "annotation.gff3", "annotation.gff.gz", "annotation.gff"):
                ds_anno = ds_dir / cand_name
                if ds_anno.is_file():
                    gff_candidate = ds_anno
                    break

        # Ambil lead SNPs dari context atau file
        lead_snps = ctx.get("lead_snps")
        if lead_snps is None:
            lead_snps_f = workspace.parent / "postgwas" / "lead_snps.json"
            if lead_snps_f.is_file():
                try:
                    loaded_leads = json.loads(lead_snps_f.read_text(encoding="utf-8"))
                    lead_snps = loaded_leads.get("snps", []) if isinstance(loaded_leads, dict) else loaded_leads
                except Exception:
                    lead_snps = []
            else:
                lead_snps = []

        # Jendela LD decay (default kb * 1000 bp)
        window_kb = cfg.window_kb or ctx.get("ld_decay_kb") or (job.parameters or {}).get("chosen_ld_decay_kb") or 100
        window_bp = int(window_kb * 1000)

        mark_job(
            db,
            job,
            overall_status=JobStatus.RUNNING_ANNOTATION,
            current_step=PipelineStep.ANNOTATION,
            work_dir=str(workspace),
        )

        append_log(
            log_file,
            f"=== TAHAP 10: ANOTASI GEN KANDIDAT GFF3 ===\n"
            f"Waktu mulai        : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"File Anotasi GFF3  : {gff_candidate or '(Tidak terpasang)'}\n"
            f"Jumlah Lead SNPs   : {len(lead_snps)}\n"
            f"Jendela Pencarian  : +/- {window_kb} kb ({window_bp:,} bp)\n"
            f"Tipe Fitur Target  : {cfg.feature_type}\n"
        )

        # Cek kondisi skip
        if not cfg.enabled:
            reason = "Anotasi gen dinonaktifkan oleh pengguna"
            append_log(log_file, f"[SKIPPED] {reason}.\n")
            write_json(workspace / "skipped.json", {"status": "skipped", "reason": reason})
            mark_job(db, job, overall_status=JobStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
            return {**ctx, "status": "skipped", "annotation_skipped": True, "log_file": str(log_file)}

        if gff_candidate is None:
            reason = "File anotasi referensi GFF3 tidak diunggah pada dataset ini"
            append_log(log_file, f"[SKIPPED] {reason}.\n")
            write_json(workspace / "skipped.json", {"status": "skipped", "reason": reason})
            mark_job(db, job, overall_status=JobStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
            return {**ctx, "status": "skipped", "annotation_skipped": True, "log_file": str(log_file)}

        if len(lead_snps) == 0:
            reason = "Tidak ada Lead SNP signifikan yang lolos threshold untuk dianotasi"
            append_log(log_file, f"[SKIPPED] {reason}.\n")
            write_json(workspace / "skipped.json", {"status": "skipped", "reason": reason})
            mark_job(db, job, overall_status=JobStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
            return {**ctx, "status": "skipped", "annotation_skipped": True, "log_file": str(log_file)}

        # 1. Parsing file GFF3
        append_log(log_file, f"Membaca fitur '{cfg.feature_type}' dari file GFF3: {gff_candidate.name}...")
        genes_by_chr, seen_seqids = parse_gff3_genes(gff_candidate, feature_type=cfg.feature_type)
        total_genes = sum(len(glist) for glist in genes_by_chr.values())
        append_log(log_file, f"Berhasil memuat {total_genes} gen pada {len(genes_by_chr)} kromosom/sekuens.")

        # Cek kompatibilitas nama kromosom
        lead_chrs = {normalize_chr_name(str(s.get("chr", ""))) for s in lead_snps}
        common_chrs = lead_chrs.intersection(genes_by_chr.keys())
        if not common_chrs and len(lead_chrs) > 0 and len(genes_by_chr) > 0:
            msg = (
                f"Kromosom Lead SNP ({', '.join(lead_chrs)}) tidak cocok dengan kromosom GFF3 "
                f"({', '.join(list(genes_by_chr.keys())[:8])}). Periksa penamaan kromosom pada referensi GFF3."
            )
            append_log(log_file, f"[WARNING] {msg}")

        # 2. Pemetaan Lead SNPs ke gen
        append_log(log_file, "Memetakan Lead SNPs ke gen kandidat...")
        mapped_rows, category_counts = map_lead_snps_to_genes(
            lead_snps=lead_snps,
            genes_by_chr=genes_by_chr,
            window_bp=window_bp,
        )

        # Kumpulkan daftar ID gen unik dari kategori Genik & Near-gen serta jendela LD
        unique_go_genes: set[str] = set()
        for r in mapped_rows:
            if r["category"] in {"Genik", "Near-gen"} and r["nearest_gene"] != "-":
                unique_go_genes.add(r["nearest_gene"])
            for gw in r.get("genes_in_window", []):
                gid = gw.get("id") or gw.get("gene_id")
                if gid:
                    unique_go_genes.add(gid)

        sorted_go_genes = sorted(unique_go_genes)

        candidate_data = {
            "status": "completed",
            "window_bp": window_bp,
            "window_kb": window_kb,
            "total_lead_snps": len(mapped_rows),
            "counts": category_counts,
            "candidate_genes_list": sorted_go_genes,
            "rows": mapped_rows,
            "mappings": mapped_rows,
        }
        candidate_json_file = workspace / "candidate_genes.json"
        write_json(candidate_json_file, candidate_data)

        # 3. Tulis file ekspor CSV: Ekstraksi_Gen_Mentah.csv & Anotasi_Kategori_SNP.csv
        ekstraksi_csv = workspace / "Ekstraksi_Gen_Mentah.csv"
        with ekstraksi_csv.open("w", encoding="utf-8", newline="") as ef:
            writer = csv.writer(ef)
            writer.writerow(["SNP", "Kromosom", "Posisi", "P_Value", "Gen_Terdekat", "Nama_Gen", "Jarak_bp", "Kategori", "Strand", "Deskripsi", "Gen_Dalam_Jendela"])
            for r in mapped_rows:
                win_ids = ", ".join((g.get("id") or g.get("gene_id") or "") for g in r.get("genes_in_window", []))
                writer.writerow([
                    r["snp"],
                    r["chr"],
                    r["pos"],
                    r["p"],
                    r["nearest_gene"],
                    r["nearest_gene_name"],
                    r["distance_bp"],
                    r["category"],
                    r["strand"],
                    r["description"],
                    win_ids,
                ])

        kategori_csv = workspace / "Anotasi_Kategori_SNP.csv"
        with kategori_csv.open("w", encoding="utf-8", newline="") as kf:
            writer = csv.writer(kf)
            writer.writerow(["Kategori", "Jumlah_SNP", "Persentase"])
            total_mapped = max(len(mapped_rows), 1)
            for cat, count in category_counts.items():
                pct = round((count / total_mapped) * 100, 1)
                writer.writerow([cat, count, f"{pct}%"])

        # 4. Tulis Daftar_Gen_Untuk_GO.txt (Daftar ID gen unik dari kategori Genik & Near-gen)
        go_file = workspace / "Daftar_Gen_Untuk_GO.txt"
        go_file.write_text("\n".join(sorted_go_genes) + ("\n" if sorted_go_genes else ""), encoding="utf-8")

        append_log(
            log_file,
            f"Anotasi gen selesai:\n"
            f"- Genik      : {category_counts.get('Genik', 0)} SNP (tepat di dalam gen)\n"
            f"- Near-gen   : {category_counts.get('Near-gen', 0)} SNP (dalam rentang +/- {window_kb} kb)\n"
            f"- Intergenik : {category_counts.get('Intergenik', 0)} SNP\n"
            f"- Total gen untuk analisis GO: {len(unique_go_genes)} gen unik -> {go_file.name}\n"
            f"[SUCCESS] Seluruh pipeline analisis GWAS berhasil diselesaikan secara lengkap.\n"
        )

        upsert_result(
            db,
            job_id,
            ResultType.CANDIDATE_GENES,
            str(candidate_json_file),
            candidate_data,
        )

        # Tandai job SELESAI PENUH (COMPLETED)
        job.parameters = {
            **(job.parameters or {}),
            "n_candidate_genes": len(unique_go_genes),
            "annotation_counts": category_counts,
        }
        mark_job(
            db,
            job,
            overall_status=JobStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc),
        )

        return {
            **ctx,
            "status": "completed",
            "candidate_genes": candidate_data,
            "go_genes_count": len(unique_go_genes),
            "go_genes_file": str(go_file),
            "log_file": str(log_file),
        }

    except JobCancelledError:
        logger.info("Anotasi dibatalkan: job=%s", job_id)
        raise
    except Exception as exc:
        db.rollback()
        job = db.get(AnalysisJob, job_id)
        if job is not None:
            mark_job(
                db,
                job,
                overall_status=JobStatus.FAILED,
                failed_step="annotation",
                error_message=str(exc),
            )
        logger.exception("Anotasi gen gagal untuk job=%s", job_id)
        raise
    finally:
        db.close()
