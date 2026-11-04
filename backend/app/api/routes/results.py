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
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, Result, ResultType
from app.schemas.results import ManhattanDataResponse
from app.services.manhattan_parser import generate_manhattan_cache, load_manhattan_cache

router = APIRouter(prefix="/results", tags=["results"])


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
    raw_snps = data if isinstance(data, list) else (data.get("snps", []) if isinstance(data, dict) else [])
    if not raw_snps:
        job_root = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id)
        candidates = [
            job_root / "gwas" / "gwas_results.normalized.tsv",
            job_root / "postgwas" / "gwas_results.normalized.tsv",
            job_root / "gwas" / "gwas_lmm.assoc.txt",
            job_root / "gwas" / "gwas_glm.assoc.txt",
        ]
        norm_assoc = next((p for p in candidates if p.is_file()), None)
        if norm_assoc is not None:
            from app.services.postgwas_service import run_greedy_clumping
            thresh_data = _load_job_json_result(job_id, db, ResultType.THRESHOLDS, "postgwas", "thresholds.json")
            sel_p = float(thresh_data.get("selected_p") or 5e-8) if isinstance(thresh_data, dict) else 5e-8
            regenerated = run_greedy_clumping(assoc_file=norm_assoc, selected_p=sel_p)
            if regenerated:
                postgwas_dir = job_root / "postgwas"
                if postgwas_dir.is_dir():
                    try:
                        lead_f = postgwas_dir / "lead_snps.json"
                        lead_f.write_text(
                            json.dumps({"count": len(regenerated), "snps": regenerated}, indent=2),
                            encoding="utf-8",
                        )
                    except Exception:
                        pass
                return {"count": len(regenerated), "snps": regenerated}

    if isinstance(data, list):
        wrapped = {"count": len(data), "snps": data}
        try:
            lead_f = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "postgwas" / "lead_snps.json"
            if lead_f.is_file():
                lead_f.write_text(json.dumps(wrapped, indent=2), encoding="utf-8")
        except Exception:
            pass
        return wrapped
    if isinstance(data, dict) and "snps" not in data:
        return {**data, "count": len(raw_snps), "snps": raw_snps}
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


def _normalize_candidate_genes_payload(data: dict[str, Any]) -> dict[str, Any]:
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
            if gid:
                unique_genes.add(gid)
            win_list.append(g)

        r["genes_in_window"] = win_list
        r["genes_in_window_count"] = len(win_list)
        normalized_rows.append(r)

    win_bp = int(data.get("window_bp") or (int(data.get("window_kb") or 100) * 1000))
    win_kb = int(data.get("window_kb") or max(1, round(win_bp / 1000)))
    sorted_genes = sorted(unique_genes)

    go_enrichment = data.get("go_enrichment")
    if not isinstance(go_enrichment, dict) or not go_enrichment.get("go_table"):
        try:
            from app.services.go_enrichment_service import run_go_enrichment_analysis

            go_enrichment = run_go_enrichment_analysis(
                mapped_rows=normalized_rows,
                candidate_genes_list=sorted_genes,
            )
        except Exception:
            go_enrichment = None

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
        "go_enrichment": go_enrichment,
    }


@router.get("/{job_id}/candidate-genes", summary="Pemetaan anotasi gen kandidat GFF3 & GO (Fase 5)")
def get_candidate_genes_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    data = _load_job_json_result(
        job_id, db, ResultType.CANDIDATE_GENES, "annotation", "candidate_genes.json"
    )
    if isinstance(data, dict) and (data.get("rows") or data.get("mappings")):
        return _normalize_candidate_genes_payload(data)

    # Pemulihan on-the-fly jika dataset memiliki file GFF3 dan job memiliki Lead SNPs
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    if job is not None and job.dataset_id is not None:
        dataset = db.query(Dataset).filter(Dataset.id == job.dataset_id).first()
        gff_candidate: Path | None = None
        if dataset is not None:
            if dataset.annotation_file_path and Path(dataset.annotation_file_path).is_file():
                gff_candidate = Path(dataset.annotation_file_path)
            else:
                ds_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id)
                for cand_name in ("annotation.gff3.gz", "annotation.gff3", "annotation.gff.gz", "annotation.gff"):
                    ds_anno = ds_dir / cand_name
                    if ds_anno.is_file():
                        gff_candidate = ds_anno
                        break

        if gff_candidate is not None:
            lead_res = get_lead_snps_data(job_id, db)
            lead_snps = lead_res if isinstance(lead_res, list) else lead_res.get("snps", [])
            if lead_snps:
                try:
                    import csv
                    from app.services.annotation_service import map_lead_snps_to_genes, parse_gff3_genes

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
                    })

                    anno_dir = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "annotation"
                    anno_dir.mkdir(parents=True, exist_ok=True)
                    cand_file = anno_dir / "candidate_genes.json"
                    cand_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

                    go_genes = payload.get("candidate_genes_list") or []
                    (anno_dir / "Daftar_Gen_Untuk_GO.txt").write_text(
                        "\n".join(go_genes) + ("\n" if go_genes else ""), encoding="utf-8"
                    )

                    ekstraksi_csv = anno_dir / "Ekstraksi_Gen_Mentah.csv"
                    with ekstraksi_csv.open("w", encoding="utf-8", newline="") as ef:
                        writer = csv.writer(ef)
                        writer.writerow(["SNP", "Kromosom", "Posisi", "P_Value", "Gen_Terdekat", "Nama_Gen", "Jarak_bp", "Kategori", "Strand", "Deskripsi", "Gen_Dalam_Jendela"])
                        for r in payload["rows"]:
                            win_ids = ", ".join((g.get("id") or g.get("gene_id") or "") for g in r.get("genes_in_window", []))
                            writer.writerow([
                                r.get("snp"),
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
    if go_file.is_file():
        try:
            loaded = json.loads(go_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("go_table"):
                return loaded
        except Exception:
            pass

    cand = get_candidate_genes_data(job_id, db)
    if isinstance(cand, dict) and isinstance(cand.get("go_enrichment"), dict):
        go_payload = cand["go_enrichment"]
        try:
            from app.services.go_enrichment_service import run_go_enrichment_analysis

            job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
            trait_label = str(
                (((job.parameters if job else None) or {}).get("phenotype") or {}).get("trait")
                or "Candidate Genes"
            )
            go_payload = run_go_enrichment_analysis(
                mapped_rows=cand.get("rows") or [],
                candidate_genes_list=cand.get("candidate_genes_list") or [],
                trait_name=trait_label,
                output_dir=anno_dir,
            )
            go_file.write_text(json.dumps(go_payload, indent=2), encoding="utf-8")
        except Exception:
            pass
        return go_payload

    return {"status": "not_available", "job_id": str(job_id), "go_table": [], "enrichment_terms": []}


@router.get("/{job_id}/primers", summary="Sekuens Flanking 201 bp & Desain Primer PCR Primer3 (Fase 5.4 / 6–8)")
def get_primers_data(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    anno_dir = Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "annotation"
    primers_file = anno_dir / "primers.json"
    if primers_file.is_file():
        try:
            loaded = json.loads(primers_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("markers"):
                return loaded
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
            payload = generate_flanking_and_primers(
                lead_snps=source_snps,
                trait_name=trait_label,
                max_snps=20,
                output_dir=anno_dir,
            )
            anno_dir.mkdir(parents=True, exist_ok=True)
            primers_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return payload
        except Exception:
            pass

    return {"status": "not_available", "job_id": str(job_id), "total_markers": 0, "markers": []}
