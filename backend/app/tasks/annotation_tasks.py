"""Celery task for Candidate Gene Annotation using GFF3 (Fase 5)."""

from __future__ import annotations

import csv
import gzip
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
    merge_annotation_input_snps,
    normalize_chr_name,
    parse_gff3_genes,
)
from app.services.go_enrichment_service import run_go_enrichment_analysis
from app.services.primer_service import generate_flanking_and_primers
from app.tasks._common import (
    JobCancelledError,
    append_log,
    chr_label,
    ensure_not_cancelled,
    mark_job,
    stage_dir,
    upsert_result,
    write_json,
)
from app.tasks.celery_app import celery_app, get_task_logger

logger = get_task_logger(__name__)


def _enrich_vcf_ref_alleles(snps: list[dict[str, Any]], vcf_candidates: list[Path | None]) -> None:
    targets: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for s in snps:
        if s.get("ref"):
            continue
        pos = int(s.get("pos") or 0)
        if pos <= 0:
            continue
        targets.setdefault((chr_label(s.get("chr") or ""), pos), []).append(s)
    if not targets:
        return

    vcf_path = next((p for p in vcf_candidates if p is not None and p.is_file()), None)
    if vcf_path is None:
        return

    open_fn = gzip.open if str(vcf_path).lower().endswith(".gz") else open
    remaining = set(targets.keys())
    try:
        with open_fn(vcf_path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line or line[0] == "#":
                    continue
                parts = line.split("\t", 6)
                if len(parts) < 5:
                    continue
                try:
                    key = (chr_label(parts[0]), int(parts[1]))
                except ValueError:
                    continue
                if key in remaining:
                    ref_val = parts[3].strip().upper()
                    alt_val = parts[4].split(",")[0].strip().upper()
                    for item in targets[key]:
                        item.setdefault("ref", ref_val)
                        if not item.get("alt") and alt_val != ".":
                            item["alt"] = alt_val
                    remaining.remove(key)
                    if not remaining:
                        break
    except Exception:
        pass


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
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Menjalankan pemetaan Lead SNPs ke gen kandidat terdekat dan gen-gen dalam jendela LD decay
    menggunakan file anotasi genom GFF3 referensi (Fase 5).
    Menghasilkan file GO enrichment dan menandai job sebagai COMPLETED.
    """
    db = SessionLocal()
    ctx: dict[str, Any] = dict(prev_result or {})
    try:
        ensure_not_cancelled(job_id, db, run_id=run_id)
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

        # Ambil lead SNPs GEMMA dari context atau file
        gemma_leads = ctx.get("lead_snps")
        if gemma_leads is None:
            lead_snps_f = workspace.parent / "postgwas" / "lead_snps.json"
            if lead_snps_f.is_file():
                try:
                    loaded_leads = json.loads(lead_snps_f.read_text(encoding="utf-8"))
                    gemma_leads = loaded_leads.get("snps", []) if isinstance(loaded_leads, dict) else loaded_leads
                except Exception:
                    gemma_leads = []
            else:
                gemma_leads = []

        # Gabungkan dengan SNP signifikan multi-locus (jika ada)
        ml_candidates: list[dict[str, Any]] = list(ctx.get("multilocus_lead_snps") or [])
        ml_summary_f = workspace.parent / "multilocus" / "multilocus_summary.json"
        if ml_summary_f.is_file():
            try:
                ml_data = json.loads(ml_summary_f.read_text(encoding="utf-8"))
                if not ml_candidates:
                    ml_candidates.extend(ml_data.get("lead_snps") or [])
                for m_entry in (ml_data.get("results") or {}).values():
                    if isinstance(m_entry, dict):
                        ml_candidates.extend(m_entry.get("significant_snps") or [])
            except Exception:
                pass

        p_thresh: float | None = None
        thresh_f = workspace.parent / "postgwas" / "thresholds.json"
        if thresh_f.is_file():
            try:
                t_data = json.loads(thresh_f.read_text(encoding="utf-8"))
                if isinstance(t_data, dict) and t_data.get("selected_p") is not None:
                    p_thresh = float(t_data["selected_p"])
            except Exception:
                p_thresh = None

        lead_snps = merge_annotation_input_snps(
            gemma_snps=gemma_leads,
            multilocus_snps=ml_candidates,
            p_threshold=p_thresh,
        )

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

        trait_name = str(
            ctx.get("trait")
            or (((job.parameters or {}).get("phenotype") or {}).get("trait"))
            or "trait"
        )

        # Selalu jalankan Fase 5.4 / Fase 6–8 (Flanking 201 bp & Primer Design) jika ada Lead SNPs
        fasta_candidate: Path | None = None
        ref_meta = (dataset.extra_metadata or {}).get("reference_fasta") if dataset.extra_metadata else None
        if isinstance(ref_meta, dict) and ref_meta.get("path") and Path(ref_meta["path"]).is_file():
            fasta_candidate = Path(ref_meta["path"])
        else:
            ds_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id)
            for cand_name in ("reference.fa", "reference.fasta", "reference.fna"):
                ref_f = ds_dir / cand_name
                if ref_f.is_file():
                    fasta_candidate = ref_f
                    break

        if fasta_candidate is not None and lead_snps:
            _enrich_vcf_ref_alleles(
                lead_snps[:20],
                [
                    workspace.parent / "qc" / "plink_qc.vcf.gz",
                    workspace.parent / "normalization" / "normalized.vcf.gz",
                    Path(dataset.storage_path) if dataset.storage_path else None,
                ],
            )

        primers_data: dict[str, Any] | None = None
        if len(lead_snps) > 0:
            try:
                primers_data = generate_flanking_and_primers(
                    lead_snps=lead_snps,
                    trait_name=trait_name,
                    max_snps=20,
                    fasta_path=fasta_candidate,
                    output_dir=workspace,
                )
                write_json(workspace / "primers.json", primers_data)
                append_log(
                    log_file,
                    f"Desain Primer3 & Flanking 201 bp selesai untuk {primers_data.get('total_markers', 0)} Lead SNP "
                    f"({primers_data.get('markers_with_primers', 0)} penanda menghasilkan pasangan primer).",
                )
            except Exception as p_exc:
                logger.warning("Gagal membangkitkan desain primer di annotation_tasks: %s", p_exc)

        # Cek kondisi skip untuk pemetaan GFF3
        if not cfg.enabled:
            reason = "Anotasi gen dinonaktifkan oleh pengguna"
            append_log(log_file, f"[SKIPPED] {reason}.\n")
            write_json(workspace / "skipped.json", {"status": "skipped", "reason": reason})
            mark_job(db, job, overall_status=JobStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
            return {**ctx, "status": "skipped", "annotation_skipped": True, "primers": primers_data, "log_file": str(log_file)}

        if gff_candidate is None:
            reason = "File anotasi referensi GFF3 tidak diunggah pada dataset ini"
            append_log(log_file, f"[SKIPPED] {reason}.\n")
            write_json(workspace / "skipped.json", {"status": "skipped", "reason": reason})
            mark_job(db, job, overall_status=JobStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
            return {**ctx, "status": "skipped", "annotation_skipped": True, "primers": primers_data, "log_file": str(log_file)}

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
            snp_examples = sorted({str(s.get("chr", "")).strip() for s in lead_snps if str(s.get("chr", "")).strip()})[:5]
            gff_examples = sorted(seen_seqids)[:5] if seen_seqids else sorted(genes_by_chr.keys())[:5]
            msg = (
                f"Tidak ada kecocokan nama kromosom antara Lead SNP (contoh: {', '.join(snp_examples)}) "
                f"dan referensi GFF3 (contoh: {', '.join(gff_examples)}). "
                "Periksa kesesuaian penamaan kromosom pada VCF dan GFF3."
            )
            append_log(log_file, f"[ERROR] {msg}")
            raise ValueError(msg)

        # 2. Pemetaan Lead SNPs ke gen
        append_log(log_file, "Memetakan Lead SNPs ke gen kandidat...")
        mapped_rows, category_counts = map_lead_snps_to_genes(
            lead_snps=lead_snps,
            genes_by_chr=genes_by_chr,
            window_bp=window_bp,
        )

        # Kumpulkan daftar ID gen unik hanya dari gen terdekat yang valid (Genik / Near-gen)
        unique_go_genes: set[str] = set()
        for r in mapped_rows:
            if r["category"] in {"Genik", "Near-gen"} and r["nearest_gene"] != "-":
                unique_go_genes.add(r["nearest_gene"])

        sorted_go_genes = sorted(unique_go_genes)

        go_mapping_candidate: Path | None = None
        go_meta = (dataset.extra_metadata or {}).get("go_annotation") if dataset.extra_metadata else None
        if isinstance(go_meta, dict) and go_meta.get("path") and Path(go_meta["path"]).is_file():
            go_mapping_candidate = Path(go_meta["path"])
        else:
            ds_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id)
            for cand_name in ("go_annotation.tsv", "go_annotation.gaf", "go_annotation.txt", "go_annotation.tsv.gz", "go_annotation.gaf.gz"):
                go_f = ds_dir / cand_name
                if go_f.is_file():
                    go_mapping_candidate = go_f
                    break

        # Jalankan Fase 5.3 Gene Ontology (GO) Enrichment berbasis anotasi GO nyata
        go_enrichment_data = run_go_enrichment_analysis(
            mapped_rows=mapped_rows,
            candidate_genes_list=sorted_go_genes,
            trait_name=trait_name,
            output_dir=workspace,
            gff_path=gff_candidate,
            gff_genes_by_chr=genes_by_chr,
            go_mapping_path=go_mapping_candidate,
            feature_type=cfg.feature_type,
        )
        write_json(workspace / "go_enrichment.json", go_enrichment_data)

        # Perbarui juga desain primer dengan atribut nearest_gene dari mapped_rows
        if mapped_rows:
            primers_data = generate_flanking_and_primers(
                lead_snps=mapped_rows,
                trait_name=trait_name,
                max_snps=20,
                fasta_path=fasta_candidate,
                output_dir=workspace,
            )
            write_json(workspace / "primers.json", primers_data)

        candidate_data = {
            "status": "completed",
            "window_bp": window_bp,
            "window_kb": window_kb,
            "total_lead_snps": len(mapped_rows),
            "counts": category_counts,
            "candidate_genes_list": sorted_go_genes,
            "rows": mapped_rows,
            "mappings": mapped_rows,
            "go_enrichment": go_enrichment_data,
        }
        candidate_json_file = workspace / "candidate_genes.json"
        write_json(candidate_json_file, candidate_data)

        # 3. Tulis file ekspor CSV: Ekstraksi_Gen_Mentah.csv & Anotasi_Kategori_SNP.csv
        ekstraksi_csv = workspace / "Ekstraksi_Gen_Mentah.csv"
        with ekstraksi_csv.open("w", encoding="utf-8", newline="") as ef:
            writer = csv.writer(ef)
            writer.writerow(["SNP", "Sumber", "Kromosom", "Posisi", "P_Value", "Gen_Terdekat", "Nama_Gen", "Jarak_bp", "Kategori", "Strand", "Deskripsi", "Gen_Dalam_Jendela"])
            for r in mapped_rows:
                win_ids = ", ".join((g.get("id") or g.get("gene_id") or "") for g in r.get("genes_in_window", []))
                writer.writerow([
                    r["snp"],
                    r.get("source") or r.get("snp_source") or "GEMMA",
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
