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
    normalize_chr_name,
    parse_gff3_genes,
)
from app.services.chromosome_service import _is_gzip_file, load_split_map
from app.services.flanking_service import (
    attach_ref_alt_from_bim,
    build_kasp_input,
    collect_significant_snps,
    extract_regions_fasta,
    write_candidate_regions,
)
from app.services.go_enrichment_service import run_go_enrichment_analysis
from app.services.kasp_service import analyze_kasp_oligos, design_kasp
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
    write_stage_summary,
)
from app.tasks.celery_app import celery_app, get_task_logger

logger = get_task_logger(__name__)


def _enrich_vcf_ref_alleles(
    snps: list[dict[str, Any]],
    vcf_candidates: list[Path | tuple[Path | None, bool] | None],
) -> None:
    selected: tuple[Path, bool] | None = None
    for cand in vcf_candidates:
        if isinstance(cand, tuple):
            cand_path, use_pseudo = cand
        else:
            cand_path, use_pseudo = cand, False
        if cand_path is not None and cand_path.is_file():
            selected = (cand_path, bool(use_pseudo))
            break
    if selected is None:
        return

    vcf_path, use_pseudo = selected
    targets: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for s in snps:
        if s.get("ref"):
            continue
        if use_pseudo:
            raw_chr = s.get("pseudo_chr") or s.get("chr") or ""
            pos = int(s.get("pseudo_pos") or s.get("pos") or 0)
        else:
            raw_chr = s.get("orig_chr") or s.get("chr") or ""
            pos = int(s.get("orig_pos") or s.get("pos") or 0)
        if pos <= 0:
            continue
        targets.setdefault((chr_label(str(raw_chr)), pos), []).append(s)
    if not targets:
        return

    open_fn = gzip.open if _is_gzip_file(vcf_path) else open
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
                        if not item.get("ref"):
                            item["ref"] = ref_val
                        if not item.get("alt") and alt_val != ".":
                            item["alt"] = alt_val
                    remaining.remove(key)
                    if not remaining:
                        break
    except Exception:
        pass


def _resolve_bim_candidate(workspace: Path, ctx: dict[str, Any]) -> Path | None:
    """Resolve authoritative .bim genotype file (post-imputation or QC)."""
    imp_skipped = (workspace.parent / "imputation" / "skipped.json").is_file()
    imp_bim = workspace.parent / "imputation" / "imputed.bim"
    if imp_bim.is_file() and not imp_skipped:
        return imp_bim
    qc_bim = workspace.parent / "qc" / "plink_qc.bim"
    if qc_bim.is_file():
        return qc_bim
    ctx_prefix = ctx.get("genotype_bfile_prefix")
    if ctx_prefix:
        cand = Path(f"{ctx_prefix}.bim")
        if cand.is_file():
            return cand
    return None


def _attach_primers_extras(
    primers_data: dict[str, Any] | None,
    regions_fa_info: dict[str, Any] | None,
    candidate_regions_meta: dict[str, Any] | None,
    kasp_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Attach candidate region and KASP summary blocks onto primers.json payload."""
    if primers_data is None:
        return None
    if regions_fa_info is not None:
        primers_data["regions_status"] = regions_fa_info["status"]
    if candidate_regions_meta is not None:
        primers_data["candidate_regions"] = candidate_regions_meta
    if kasp_data is not None:
        primers_data["kasp"] = kasp_data
    return primers_data


def _max_thermo_tm(section: dict[str, Any]) -> str:
    """Return max melting temperature (formatted string) across detected secondary structures."""
    tms = [
        float(val["tm"])
        for val in section.values()
        if isinstance(val, dict) and val.get("structure_found") and val.get("tm") is not None
    ]
    return f"{max(tms):.2f}" if tms else "0.00"


def _run_kasp_pipeline(
    lead_snps: list[dict[str, Any]],
    *,
    fasta_path: Path | None,
    bim_path: Path | None,
    split_map: dict[str, Any] | None,
    workspace: Path,
    log_file: Path,
    max_snps: int = 20,
) -> dict[str, Any]:
    """Generate KASP inputs, design EasyKASP primers, score oligo structure, and write 3 TSV/FASTA exports."""
    selected = list(lead_snps)[:max_snps]
    if not selected:
        return {
            "status": "skipped",
            "reason": "Tidak ada Lead SNP untuk desain KASP",
            "total": 0,
            "designed": 0,
            "failed": 0,
            "success_rate": 0.0,
            "markers": [],
            "files": {},
        }
    if fasta_path is None or not Path(fasta_path).is_file():
        reason = "FASTA referensi tidak tersedia"
        append_log(log_file, f"[5.4-KASP] skipped ({reason}).")
        return {
            "status": "skipped",
            "reason": reason,
            "total": 0,
            "designed": 0,
            "failed": 0,
            "success_rate": 0.0,
            "markers": [],
            "files": {},
        }

    input_rows = build_kasp_input(
        selected,
        fasta_path,
        bim_path=bim_path,
        split_map=split_map,
    )
    input_tsv = workspace / "kasp_input.tsv"
    input_tmp = workspace / "kasp_input.tsv.tmp"
    with input_tmp.open("w", encoding="utf-8", newline="") as kf:
        writer = csv.writer(kf, delimiter="\t")
        writer.writerow(["SNP ID", "Allele 1", "Allele 2", "Flanking Sequence"])
        for row in input_rows:
            if row["status"] == "ok":
                writer.writerow([row["snp_id"], row["allele_1"], row["allele_2"], row["flanking"]])
    input_tmp.replace(input_tsv)

    primers_tsv = workspace / "kasp_primers.tsv"
    primers_tmp = workspace / "kasp_primers.tsv.tmp"
    no_tail_fa = workspace / "kasp_primers_no_tail.fa"
    no_tail_tmp = workspace / "kasp_primers_no_tail.fa.tmp"

    markers: list[dict[str, Any]] = []
    fa_lines: list[str] = []
    designed_count = 0

    with primers_tmp.open("w", encoding="utf-8", newline="") as pf:
        pwriter = csv.writer(pf, delimiter="\t")
        pwriter.writerow([
            "SNP_ID",
            "allele_X",
            "allele_Y",
            "primer_X",
            "primer_Y",
            "primer_C",
            "direction",
            "tm_x",
            "tm_y",
            "tm_c",
            "status",
            "notes",
            "hairpin_max_tm",
            "homodimer_max_tm",
            "heterodimer_max_tm",
            "oligo_warn",
        ])
        for in_row in input_rows:
            if in_row["status"] != "ok":
                notes = [in_row["reason"]] if in_row.get("reason") else []
                entry: dict[str, Any] = {
                    "snp_id": in_row["snp_id"],
                    "allele_x": in_row["allele_1"],
                    "allele_y": in_row["allele_2"],
                    "primer_x": "",
                    "primer_y": "",
                    "primer_c": "",
                    "primer_x_no_tail": "",
                    "primer_y_no_tail": "",
                    "direction": None,
                    "tm_x": None,
                    "tm_y": None,
                    "tm_c": None,
                    "status": "Failed",
                    "input_status": in_row["status"],
                    "notes": notes,
                    "oligo_qc": analyze_kasp_oligos({"status": "Failed"}),
                }
            else:
                design = design_kasp(
                    in_row["snp_id"],
                    in_row["allele_1"],
                    in_row["allele_2"],
                    in_row["flanking"],
                )
                oligo_qc = analyze_kasp_oligos(design)
                entry = {
                    **design,
                    "input_status": "ok",
                    "masked_neighbors": in_row["masked_neighbors"],
                    "flanking_input": in_row["flanking"],
                    "oligo_qc": oligo_qc,
                }
                if design["status"] == "OK":
                    designed_count += 1
                    fa_lines.extend([
                        f">{design['snp_id']}_X",
                        design["primer_x_no_tail"],
                        f">{design['snp_id']}_Y",
                        design["primer_y_no_tail"],
                        f">{design['snp_id']}_C",
                        design["primer_c"],
                    ])

            qc_block = entry["oligo_qc"]
            pwriter.writerow([
                entry["snp_id"],
                entry["allele_x"],
                entry["allele_y"],
                entry["primer_x"],
                entry["primer_y"],
                entry["primer_c"],
                entry["direction"] or "-",
                entry["tm_x"] if entry["tm_x"] is not None else "",
                entry["tm_y"] if entry["tm_y"] is not None else "",
                entry["tm_c"] if entry["tm_c"] is not None else "",
                entry["status"],
                "; ".join(entry.get("notes") or []),
                _max_thermo_tm(qc_block.get("hairpins") or {}),
                _max_thermo_tm(qc_block.get("homodimers") or {}),
                _max_thermo_tm(qc_block.get("heterodimers") or {}),
                "WARN" if qc_block.get("warn") else "OK",
            ])
            markers.append(entry)

    primers_tmp.replace(primers_tsv)
    no_tail_tmp.write_text(("\n".join(fa_lines) + "\n") if fa_lines else "", encoding="utf-8")
    no_tail_tmp.replace(no_tail_fa)

    total_count = len(markers)
    failed_count = total_count - designed_count
    success_rate = round((designed_count / total_count) * 100.0, 2) if total_count else 0.0
    append_log(log_file, f"[5.4-KASP] {designed_count}/{total_count} berhasil.")

    return {
        "status": "completed",
        "total": total_count,
        "designed": designed_count,
        "failed": failed_count,
        "success_rate": success_rate,
        "markers": markers,
        "files": {
            "input_tsv": input_tsv.name,
            "primers_tsv": primers_tsv.name,
            "primers_no_tail_fa": no_tail_fa.name,
        },
    }


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
    stage_started_at = datetime.now(timezone.utc)
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
        ml_engines: dict[str, str] = {}
        ml_summary_f = workspace.parent / "multilocus" / "multilocus_summary.json"
        if ml_summary_f.is_file():
            try:
                ml_data = json.loads(ml_summary_f.read_text(encoding="utf-8"))
                raw_eng = ml_data.get("engines") or {}
                if isinstance(raw_eng, dict):
                    ml_engines.update({str(k).upper(): str(v) for k, v in raw_eng.items() if v})
                for m_name, m_entry in (ml_data.get("results") or {}).items():
                    if isinstance(m_entry, dict) and m_entry.get("engine"):
                        ml_engines.setdefault(str(m_name).upper(), str(m_entry["engine"]))
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

        split_map = load_split_map(job_id)
        lead_snps = collect_significant_snps(
            lead_snps=gemma_leads,
            multilocus_snps=ml_candidates,
            engines_by_method=ml_engines,
            p_threshold=p_thresh,
            split_map=split_map,
        )

        # Jendela LD decay (default kb * 1000 bp)
        window_kb = cfg.window_kb or ctx.get("ld_decay_kb") or (job.parameters or {}).get("chosen_ld_decay_kb") or 100
        window_bp = int(window_kb * 1000)

        bim_candidate = _resolve_bim_candidate(workspace, ctx)
        if lead_snps:
            attach_ref_alt_from_bim(lead_snps, bim_candidate, split_map=split_map)

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

        if lead_snps and any(not s.get("ref") or not s.get("alt") for s in lead_snps):
            _enrich_vcf_ref_alleles(
                lead_snps,
                [
                    (workspace.parent / "qc" / "plink_qc.vcf.gz", True),
                    (workspace.parent / "normalization" / "normalized.vcf.gz", True),
                    (Path(dataset.storage_path) if dataset.storage_path else None, False),
                ],
            )

        regions_fa_info: dict[str, Any] | None = None
        candidate_regions_meta: dict[str, Any] | None = None
        if lead_snps:
            region_info = write_candidate_regions(
                lead_snps,
                window_bp,
                workspace / "candidate_regions.csv",
                workspace / "candidate_regions.bed",
            )
            regions_fa_info = extract_regions_fasta(
                region_info["bed_path"],
                fasta_candidate,
                workspace / "candidate_regions.fa",
            )
            region_log = (
                f"[5.4] region kandidat: {regions_fa_info['written_regions']}/{region_info['n_regions']} SNP, "
                f"total {regions_fa_info['total_bp']} bp (batas {regions_fa_info['max_total_bp']} bp)"
            )
            if regions_fa_info.get("truncated"):
                region_log += f" — dipotong ({regions_fa_info['truncated_count']} region melewati batas)"
            elif regions_fa_info.get("status") == "no_reference":
                region_log += " — FASTA tidak tersedia"
            append_log(log_file, region_log)
            candidate_regions_meta = {
                "csv": "candidate_regions.csv",
                "bed": "candidate_regions.bed",
                "fasta": "candidate_regions.fa" if regions_fa_info.get("fasta_path") else None,
                "n_regions": region_info["n_regions"],
                "written_regions": regions_fa_info["written_regions"],
                "total_bp": regions_fa_info["total_bp"],
                "max_total_bp": regions_fa_info["max_total_bp"],
                "truncated": bool(regions_fa_info.get("truncated")),
                "truncated_count": int(regions_fa_info.get("truncated_count") or 0),
                "window_bp": window_bp,
                "reason": regions_fa_info.get("reason"),
            }

        primers_data: dict[str, Any] | None = None
        kasp_data: dict[str, Any] | None = None
        if len(lead_snps) > 0:
            try:
                kasp_data = _run_kasp_pipeline(
                    lead_snps,
                    fasta_path=fasta_candidate,
                    bim_path=bim_candidate,
                    split_map=split_map,
                    workspace=workspace,
                    log_file=log_file,
                    max_snps=20,
                )
            except Exception as k_exc:
                logger.warning("Gagal membangkitkan desain KASP di annotation_tasks: %s", k_exc)
            try:
                primers_data = generate_flanking_and_primers(
                    lead_snps=lead_snps,
                    trait_name=trait_name,
                    max_snps=20,
                    fasta_path=fasta_candidate,
                    output_dir=workspace,
                    bim_path=bim_candidate,
                    split_map=split_map,
                )
                _attach_primers_extras(primers_data, regions_fa_info, candidate_regions_meta, kasp_data)
                write_json(workspace / "primers.json", primers_data)
                append_log(
                    log_file,
                    f"Desain Primer3 & Flanking 201 bp selesai untuk {primers_data.get('total_markers', 0)} Lead SNP "
                    f"({primers_data.get('markers_with_primers', 0)} penanda menghasilkan pasangan primer).",
                )
            except Exception as p_exc:
                logger.warning("Gagal membangkitkan desain primer di annotation_tasks: %s", p_exc)

        # Cek kondisi skip untuk pemetaan GFF3
        skip_reason: str | None = None
        if not cfg.enabled:
            skip_reason = "Anotasi gen dinonaktifkan oleh pengguna"
        elif gff_candidate is None:
            skip_reason = "File anotasi referensi GFF3 tidak diunggah pada dataset ini"
        elif len(lead_snps) == 0:
            skip_reason = "Tidak ada Lead SNP signifikan yang lolos threshold untuk dianotasi"

        if skip_reason is not None:
            append_log(log_file, f"[SKIPPED] {skip_reason}.\n")
            write_json(workspace / "skipped.json", {"status": "skipped", "reason": skip_reason})
            write_stage_summary(
                job_id,
                "annotation",
                {
                    "status": "skipped",
                    "started_at": stage_started_at,
                    "reason": skip_reason,
                    "regions_status": (regions_fa_info or {}).get("status"),
                    "n_regions": int((candidate_regions_meta or {}).get("n_regions") or 0),
                    "regions_total_bp": int((candidate_regions_meta or {}).get("total_bp") or 0),
                    "kasp_status": (kasp_data or {}).get("status"),
                    "kasp_designed": int((kasp_data or {}).get("designed") or 0),
                    "kasp_total": int((kasp_data or {}).get("total") or 0),
                },
                db=db,
            )
            mark_job(db, job, overall_status=JobStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
            skip_ret: dict[str, Any] = {
                **ctx,
                "status": "skipped",
                "annotation_skipped": True,
                "log_file": str(log_file),
            }
            if not cfg.enabled or gff_candidate is None:
                skip_ret["primers"] = primers_data
            return skip_ret

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

        unmatched_chrs = sorted(ch for ch in (lead_chrs - set(genes_by_chr.keys())) if ch)
        if unmatched_chrs:
            append_log(
                log_file,
                f"[WARN] Sebagian kromosom Lead SNP tidak ditemukan di GFF3 ({', '.join(unmatched_chrs)}); "
                "SNP pada kromosom tersebut akan ditandai Intergenik.",
            )

        # 2. Pemetaan Lead SNPs ke gen
        append_log(log_file, "Memetakan Lead SNPs ke gen kandidat...")
        mapped_rows, category_counts = map_lead_snps_to_genes(
            lead_snps=lead_snps,
            genes_by_chr=genes_by_chr,
            window_bp=window_bp,
        )
        for src_snp, mapped_row in zip(lead_snps, mapped_rows):
            for coord_key in ("orig_chr", "orig_pos", "pseudo_chr", "pseudo_pos", "engine"):
                if coord_key in src_snp:
                    mapped_row[coord_key] = src_snp[coord_key]
            for allele_key in ("ref", "alt"):
                if src_snp.get(allele_key):
                    mapped_row[allele_key] = src_snp[allele_key]

        # Kumpulkan daftar ID gen unik hanya dari gen terdekat yang valid (Genik / Near-gen)
        unique_go_genes: set[str] = set()
        for r in mapped_rows:
            if r["category"] in {"Genik", "Near-gen"} and r["nearest_gene"] != "-":
                unique_go_genes.add(r["nearest_gene"])

        sorted_go_genes = sorted(unique_go_genes)

        from app.services.go_enrichment_service import (
            format_go_log_lines,
            resolve_dataset_go_mapping_path,
            resolve_job_contig_pattern,
        )
        from app.services.ortholog_service import resolve_dataset_ortholog_index_path

        go_mapping_candidate = resolve_dataset_go_mapping_path(dataset)
        ortholog_candidate = resolve_dataset_ortholog_index_path(dataset)
        contig_pattern, contig_pattern_source = resolve_job_contig_pattern(job.parameters)

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
            contig_filter_pattern=contig_pattern,
            contig_pattern_source=contig_pattern_source,
            ortholog_index_path=ortholog_candidate,
        )
        write_json(workspace / "go_enrichment.json", go_enrichment_data)
        sample_gff_ids = [
            str(g.get("gene_id"))
            for glist in genes_by_chr.values()
            for g in glist[:3]
            if g.get("gene_id")
        ][:3]
        for go_log_line in format_go_log_lines(go_enrichment_data, sample_gff_ids=sample_gff_ids):
            append_log(log_file, go_log_line)

        # Perbarui juga desain primer dengan atribut nearest_gene dari mapped_rows
        if mapped_rows:
            primers_data = generate_flanking_and_primers(
                lead_snps=mapped_rows,
                trait_name=trait_name,
                max_snps=20,
                fasta_path=fasta_candidate,
                output_dir=workspace,
                bim_path=bim_candidate,
                split_map=split_map,
            )
            _attach_primers_extras(primers_data, regions_fa_info, candidate_regions_meta, kasp_data)
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
        write_stage_summary(
            job_id,
            "annotation",
            {
                "status": "completed",
                "started_at": stage_started_at,
                "total_lead_snps": len(mapped_rows),
                "n_candidate_genes": len(unique_go_genes),
                "counts": category_counts,
                "regions_status": (regions_fa_info or {}).get("status"),
                "n_regions": int((candidate_regions_meta or {}).get("n_regions") or 0),
                "regions_total_bp": int((candidate_regions_meta or {}).get("total_bp") or 0),
                "kasp_status": (kasp_data or {}).get("status"),
                "kasp_designed": int((kasp_data or {}).get("designed") or 0),
                "kasp_total": int((kasp_data or {}).get("total") or 0),
                "go_status": go_enrichment_data.get("status"),
                "go_pct_gff_features_matched": float(
                    ((go_enrichment_data.get("go_stats") or {}).get("match") or {}).get(
                        "pct_gff_features_matched", 0.0
                    )
                ),
                "go_obo_version": (
                    ((go_enrichment_data.get("go_stats") or {}).get("obo") or {}).get("version")
                ),
                "go_n_rows_obsolete_dropped": int(
                    ((go_enrichment_data.get("go_stats") or {}).get("obo") or {}).get(
                        "n_rows_obsolete_dropped", 0
                    )
                ),
                "go_n_universe": int(
                    ((go_enrichment_data.get("go_stats") or {}).get("background") or {}).get(
                        "n_universe", 0
                    )
                ),
            },
            db=db,
        )
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
