"""
Service untuk Gene Ontology (GO) Enrichment berbasis anotasi GO nyata (Fase 5.3).

Sumber anotasi GO, berurutan prioritas per gen:
1. Atribut GFF3 (`Ontology_term=GO:...` / `GO=` / `Dbxref=GO:...`).
2. File pemetaan unggahan: TSV (`gene_id<TAB>GO:xxxxxxx[<TAB>domain[<TAB>term_name]]`)
   atau GAF 2.x (kolom 2 = gene_id, kolom 5 = GO_ID, kolom 9 = aspek P/F/C).

Uji pengayaan hipergeometrik memakai ukuran background dinamis = jumlah total
gen beranotasi GO (`>= 1` istilah GO), diikuti koreksi FDR Benjamini-Hochberg.
"""

from __future__ import annotations

import csv
import gzip
import os
from pathlib import Path
import re
from typing import Any

import numpy as np
from scipy.stats import hypergeom

_GO_ID_RE = re.compile(r"GO:\d{7}", re.IGNORECASE)
_DOMAIN_MAP = {
    "BP": "BP",
    "P": "BP",
    "BIOLOGICAL_PROCESS": "BP",
    "CC": "CC",
    "C": "CC",
    "CELLULAR_COMPONENT": "CC",
    "MF": "MF",
    "F": "MF",
    "MOLECULAR_FUNCTION": "MF",
}


def _allow_synthetic_annotation() -> bool:
    return os.getenv("ALLOW_SYNTHETIC_ANNOTATION", "").strip().lower() in ("1", "true", "yes")


def _normalize_gene_key(gene_id: Any) -> str:
    txt = str(gene_id or "").strip()
    if txt.lower().startswith("gene:"):
        txt = txt.split(":", 1)[1]
    return txt.upper()


def _normalize_domain(raw: Any) -> str:
    key = str(raw or "").strip().upper().replace(" ", "_")
    return _DOMAIN_MAP.get(key, "unknown")


def parse_go_annotation_file(mapping_path: str | Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
    """
    Membaca file pemetaan GO berformat TSV (`gene_id<TAB>GO:xxxxxxx[<TAB>domain[<TAB>term_name]]`)
    atau GAF 2.x (`!` header, kolom 2 = gene_id, kolom 5 = GO_ID, kolom 9 = aspek P/F/C).
    """
    path = Path(mapping_path)
    if not path.is_file():
        raise FileNotFoundError(f"File pemetaan GO tidak ditemukan: {path}")

    open_fn = gzip.open if path.name.lower().endswith(".gz") else open
    by_gene: dict[str, dict[str, dict[str, str]]] = {}
    detected_formats: set[str] = set()
    n_mappings = 0

    with open_fn(path, "rt", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith(("!", "#")):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue

            head0 = parts[0].strip().lower()
            head1 = parts[1].strip().lower()
            if head0 in {"gene_id", "gene", "locus", "id"} and head1 in {"go_id", "go", "go_term"}:
                continue

            # GAF 2.x: >= 9 kolom, kolom 5 (idx 4) memuat GO:xxxxxxx, kolom 9 (idx 8) memuat P/F/C
            gaf_go_match = _GO_ID_RE.search(parts[4]) if len(parts) >= 9 else None
            if gaf_go_match is not None:
                gene_raw = parts[1].strip()
                go_id = gaf_go_match.group(0).upper()
                domain = _normalize_domain(parts[8])
                term_name = ""
                detected_formats.add("gaf")
            else:
                tsv_go_match = _GO_ID_RE.search(parts[1])
                if tsv_go_match is None:
                    continue
                gene_raw = parts[0].strip()
                go_id = tsv_go_match.group(0).upper()
                domain = _normalize_domain(parts[2]) if len(parts) >= 3 else "unknown"
                term_name = parts[3].strip() if len(parts) >= 4 else ""
                detected_formats.add("tsv")

            gene_key = _normalize_gene_key(gene_raw)
            if not gene_key:
                continue

            gene_terms = by_gene.setdefault(gene_key, {})
            existing = gene_terms.get(go_id)
            if existing is None:
                gene_terms[go_id] = {
                    "go_id": go_id,
                    "ontology": domain,
                    "description": term_name,
                    "gene_raw": gene_raw,
                }
                n_mappings += 1
            else:
                if existing["ontology"] == "unknown" and domain != "unknown":
                    existing["ontology"] = domain
                if not existing["description"] and term_name:
                    existing["description"] = term_name

    annotations: dict[str, list[dict[str, str]]] = {
        gkey: list(term_map.values())
        for gkey, term_map in by_gene.items()
    }
    fmt = "gaf" if detected_formats == {"gaf"} else ("tsv" if "tsv" in detected_formats else "unknown")
    summary = {
        "format": fmt,
        "n_genes": len(annotations),
        "n_mappings": n_mappings,
    }
    return annotations, summary


def _quickgo_url(go_id: str) -> str:
    return f"https://www.ebi.ac.uk/QuickGO/term/{go_id}"


def _format_domain_cell(entries: list[dict[str, str]], ontology: str) -> str:
    items = [e for e in entries if e["ontology"] == ontology]
    if not items:
        return "-"
    parts = [
        f"{e['description']} ({e['go_id']})" if e["description"] and e["description"] != e["go_id"] else e["go_id"]
        for e in items
    ]
    return ", ".join(parts)


def _merge_gene_go_terms(
    gff_go_ids: list[str],
    uploaded_terms: list[dict[str, str]],
    uploaded_lookup_by_go: dict[str, dict[str, str]],
) -> tuple[list[dict[str, str]], str, str]:
    """
    Resolusi istilah GO untuk satu gen:
    1) Atribut GFF3 `Ontology_term` (diperkaya domain/nama jika GO ID sama ada di file pemetaan)
    2) File pemetaan unggahan (TSV/GAF) bila GFF3 tidak memiliki GO untuk gen ini.
    """
    clean_gff = list(dict.fromkeys(
        str(g).strip().upper()
        for g in (gff_go_ids or [])
        if _GO_ID_RE.fullmatch(str(g).strip().upper())
    ))
    if clean_gff:
        resolved: list[dict[str, str]] = []
        for go_id in clean_gff:
            extra = uploaded_lookup_by_go.get(go_id)
            ontology = extra["ontology"] if extra else "unknown"
            desc = (extra["description"] if extra and extra.get("description") else go_id)
            resolved.append({"go_id": go_id, "ontology": ontology, "description": desc})
        return resolved, "gff3", "GFF3 Ontology_term"

    if uploaded_terms:
        resolved = [
            {
                "go_id": t["go_id"],
                "ontology": t.get("ontology") or "unknown",
                "description": t.get("description") or t["go_id"],
            }
            for t in uploaded_terms
        ]
        return resolved, "mapping", "Uploaded GO Annotation"

    return [], "unavailable", "unavailable"


def _resolve_gene_go_entry(
    gene_id: str,
    gene_name: str = "",
    description: str = "",
    explicit_go_ids: list[str] | None = None,
    uploaded_terms: list[dict[str, str]] | None = None,
    uploaded_lookup_by_go: dict[str, dict[str, str]] | None = None,
    allow_synthetic: bool | None = None,
) -> dict[str, Any]:
    """Memetakan satu gen kandidat ke istilah GO nyata (atau placeholder jika mode sintetis aktif)."""
    if allow_synthetic is None:
        allow_synthetic = _allow_synthetic_annotation()

    terms, src_kind, src_label = _merge_gene_go_terms(
        gff_go_ids=explicit_go_ids or [],
        uploaded_terms=uploaded_terms or [],
        uploaded_lookup_by_go=uploaded_lookup_by_go or {},
    )
    if terms:
        go_ids = [t["go_id"] for t in terms]
        return {
            "gene_id": gene_id,
            "gene_name": gene_name or gene_id,
            "ortholog": "-",
            "terms": terms,
            "go_terms": go_ids,
            "source": src_kind,
            "source_label": src_label,
            "source_url": _quickgo_url(go_ids[0]),
            "synthetic": False,
        }

    if allow_synthetic:
        placeholder = [{"go_id": "GO:0008150", "ontology": "BP", "description": "biological_process"}]
        return {
            "gene_id": gene_id,
            "gene_name": gene_name or gene_id,
            "ortholog": "-",
            "terms": placeholder,
            "go_terms": ["GO:0008150"],
            "source": "synthetic",
            "source_label": "Synthetic Demo Placeholder",
            "source_url": _quickgo_url("GO:0008150"),
            "synthetic": True,
        }

    return {
        "gene_id": gene_id,
        "gene_name": gene_name or gene_id,
        "ortholog": "-",
        "terms": [],
        "go_terms": [],
        "source": "unavailable",
        "source_label": "unavailable",
        "source_url": "",
        "synthetic": False,
    }


def _collect_gff_go_map(
    gff_path: str | Path | None,
    gff_genes_by_chr: dict[str, list[dict[str, Any]]] | None,
    feature_type: str = "gene",
) -> dict[str, list[str]]:
    if gff_genes_by_chr is None and gff_path is not None and Path(gff_path).is_file():
        from app.services.annotation_service import parse_gff3_genes

        gff_genes_by_chr, _ = parse_gff3_genes(gff_path, feature_type=feature_type)

    out: dict[str, list[str]] = {}
    for glist in (gff_genes_by_chr or {}).values():
        for g in glist:
            gkey = _normalize_gene_key(g.get("gene_id"))
            if not gkey:
                continue
            go_ids = list(dict.fromkeys(str(tid).strip().upper() for tid in (g.get("go_ids") or []) if tid))
            if go_ids:
                existing = out.setdefault(gkey, [])
                for tid in go_ids:
                    if tid not in existing:
                        existing.append(tid)
    return out


def run_go_enrichment_analysis(
    mapped_rows: list[dict[str, Any]],
    candidate_genes_list: list[str] | None = None,
    trait_name: str = "Candidate Genes",
    output_dir: str | Path | None = None,
    gff_path: str | Path | None = None,
    gff_genes_by_chr: dict[str, list[dict[str, Any]]] | None = None,
    go_mapping_path: str | Path | None = None,
    feature_type: str = "gene",
) -> dict[str, Any]:
    """
    Menjalankan pemetaan Tabel GO dan analisis pengayaan hipergeometrik (GO Enrichment)
    menggunakan anotasi GO asli (GFF3 `Ontology_term` atau file pemetaan TSV/GAF).
    """
    allow_synthetic = _allow_synthetic_annotation()

    row_meta_lookup: dict[str, dict[str, Any]] = {}
    nearest_meta_map: dict[str, dict[str, Any]] = {}

    for row in mapped_rows:
        ng = row.get("nearest_gene")
        if ng and ng != "-":
            c_info = row.get("closest_gene") or {}
            entry = {
                "gene_id": ng,
                "gene_name": row.get("nearest_gene_name") or c_info.get("gene_name") or ng,
                "description": row.get("description") or c_info.get("description") or "",
                "go_ids": c_info.get("go_ids") or [],
                "snp": row.get("snp"),
                "chr": row.get("chr"),
                "pos": row.get("pos"),
                "category": row.get("category", "Genik"),
            }
            row_meta_lookup.setdefault(ng, entry)
            if row.get("category") in {"Genik", "Near-gen"}:
                nearest_meta_map.setdefault(ng, entry)
        for gw in row.get("genes_in_window") or []:
            gid = gw.get("gene_id") or gw.get("id")
            if gid and gid != "-":
                row_meta_lookup.setdefault(
                    gid,
                    {
                        "gene_id": gid,
                        "gene_name": gw.get("gene_name") or gw.get("name") or gid,
                        "description": gw.get("desc") or "",
                        "go_ids": gw.get("go_ids") or [],
                        "snp": row.get("snp"),
                        "chr": row.get("chr"),
                        "pos": row.get("pos"),
                        "category": "Near-gen" if int(gw.get("distance") or 0) > 0 else "Genik",
                    },
                )

    if candidate_genes_list is not None:
        gene_meta_map: dict[str, dict[str, Any]] = {}
        for gid in candidate_genes_list:
            if not gid or gid == "-":
                continue
            gene_meta_map[gid] = row_meta_lookup.get(
                gid,
                {
                    "gene_id": gid,
                    "gene_name": gid,
                    "description": "",
                    "go_ids": [],
                    "snp": "-",
                    "chr": "-",
                    "pos": 0,
                    "category": "Genik",
                },
            )
    else:
        gene_meta_map = nearest_meta_map

    if not gene_meta_map:
        return {
            "status": "empty",
            "synthetic": False,
            "trait": trait_name,
            "total_genes": 0,
            "background_total": 0,
            "go_table": [],
            "enrichment_terms": [],
        }

    gff_go_map = _collect_gff_go_map(gff_path, gff_genes_by_chr, feature_type=feature_type)
    uploaded_annotations: dict[str, list[dict[str, str]]] = {}
    if go_mapping_path is not None and Path(go_mapping_path).is_file():
        uploaded_annotations, _ = parse_go_annotation_file(go_mapping_path)

    uploaded_lookup_by_go: dict[str, dict[str, str]] = {}
    for term_list in uploaded_annotations.values():
        for t in term_list:
            gid_code = t["go_id"]
            cur = uploaded_lookup_by_go.get(gid_code)
            if cur is None:
                uploaded_lookup_by_go[gid_code] = dict(t)
            else:
                if cur.get("ontology", "unknown") == "unknown" and t.get("ontology", "unknown") != "unknown":
                    cur["ontology"] = t["ontology"]
                if not cur.get("description") and t.get("description"):
                    cur["description"] = t["description"]

    # Bangun semesta latar belakang = semua gen dengan >= 1 GO di GFF3 / file pemetaan GO
    bg_gene_to_go: dict[str, set[str]] = {}
    for gkey, go_ids in gff_go_map.items():
        if go_ids:
            bg_gene_to_go.setdefault(gkey, set()).update(go_ids)
    for gkey, term_list in uploaded_annotations.items():
        if gkey not in bg_gene_to_go and term_list:
            bg_gene_to_go[gkey] = {t["go_id"] for t in term_list}

    go_table: list[dict[str, Any]] = []
    term_to_genes: dict[str, dict[str, Any]] = {}

    for gid in sorted(gene_meta_map.keys()):
        meta = gene_meta_map[gid]
        gkey = _normalize_gene_key(gid)
        explicit_go = list(dict.fromkeys([*(meta.get("go_ids") or []), *gff_go_map.get(gkey, [])]))

        resolved = _resolve_gene_go_entry(
            gene_id=gid,
            gene_name=meta.get("gene_name", gid),
            description=meta.get("description", ""),
            explicit_go_ids=explicit_go,
            uploaded_terms=uploaded_annotations.get(gkey),
            uploaded_lookup_by_go=uploaded_lookup_by_go,
            allow_synthetic=allow_synthetic,
        )
        terms = resolved["terms"]

        if resolved["go_terms"] and not resolved["synthetic"]:
            bg_gene_to_go.setdefault(gkey, set()).update(resolved["go_terms"])

        go_table.append({
            "gene_id": gid,
            "gene_name": resolved["gene_name"],
            "ortholog": resolved["ortholog"],
            "biological_process": _format_domain_cell(terms, "BP"),
            "cellular_component": _format_domain_cell(terms, "CC"),
            "molecular_function": _format_domain_cell(terms, "MF"),
            "unknown_go": _format_domain_cell(terms, "unknown"),
            "bp_ids": [t["go_id"] for t in terms if t["ontology"] == "BP"],
            "cc_ids": [t["go_id"] for t in terms if t["ontology"] == "CC"],
            "mf_ids": [t["go_id"] for t in terms if t["ontology"] == "MF"],
            "unknown_ids": [t["go_id"] for t in terms if t["ontology"] == "unknown"],
            "go_terms": resolved["go_terms"],
            "source": resolved["source"],
            "source_label": resolved["source_label"],
            "source_url": resolved["source_url"],
            "synthetic": resolved["synthetic"],
            "snp": meta.get("snp", "-"),
            "category": meta.get("category", "Genik"),
        })

        for t in terms:
            go_id = t["go_id"]
            entry = term_to_genes.setdefault(
                go_id,
                {
                    "go_id": go_id,
                    "description": t["description"],
                    "ontology": t["ontology"],
                    "genes": [],
                    "synthetic": resolved["synthetic"],
                },
            )
            if resolved["synthetic"]:
                entry["synthetic"] = True
            if gid not in entry["genes"]:
                entry["genes"].append(gid)

    has_synthetic = any(bool(row.get("synthetic")) for row in go_table)
    if bg_gene_to_go:
        n_bg_total = len(bg_gene_to_go)
        bg_term_counts: dict[str, int] = {}
        for tids in bg_gene_to_go.values():
            for tid in tids:
                bg_term_counts[tid] = bg_term_counts.get(tid, 0) + 1
    elif has_synthetic:
        n_bg_total = max(len(go_table) * 10, 20)
        bg_term_counts = {gid_code: max(len(info["genes"]) * 2, 4) for gid_code, info in term_to_genes.items()}
    else:
        n_bg_total = 0
        bg_term_counts = {}

    n_annotated_candidates = sum(1 for row in go_table if row["go_terms"])
    n_candidates = max(n_annotated_candidates, 1)
    raw_terms: list[dict[str, Any]] = []

    for go_id, info in term_to_genes.items():
        k_count = len(info["genes"])
        m_bg = max(bg_term_counts.get(go_id, k_count), k_count)
        effective_bg = max(n_bg_total, m_bg, n_candidates)
        pval = float(hypergeom.sf(max(k_count - 1, 0), effective_bg, m_bg, n_candidates))
        pval = min(max(pval, 1e-60), 1.0)
        gene_ratio = round(k_count / n_candidates, 4)
        raw_terms.append({
            "go_id": go_id,
            "description": info["description"],
            "ontology": info["ontology"],
            "count": k_count,
            "total_candidates": n_candidates,
            "gene_ratio": gene_ratio,
            "gene_ratio_str": f"{k_count}/{n_candidates}",
            "bg_ratio": f"{m_bg}/{effective_bg}",
            "pvalue": pval,
            "genes": info["genes"],
            "gene_id_str": "/".join(info["genes"]),
            "source_url": _quickgo_url(go_id),
            "synthetic": bool(info.get("synthetic")),
        })

    if raw_terms:
        p_arr = np.array([t["pvalue"] for t in raw_terms], dtype=np.float64)
        m_tests = len(p_arr)
        order = np.argsort(p_arr)
        ranked_p = p_arr[order]
        ranks = np.arange(1, m_tests + 1, dtype=np.float64)
        q_raw = ranked_p * (m_tests / ranks)
        q_mono = np.minimum.accumulate(q_raw[::-1])[::-1]
        q_final = np.clip(q_mono, 1e-60, 1.0)
        for rank_idx, orig_idx in enumerate(order):
            raw_terms[int(orig_idx)]["p_adjust"] = float(q_final[rank_idx])

    raw_terms.sort(key=lambda x: (0 if x["ontology"] == "BP" else 1, -x["gene_ratio"], x["p_adjust"]))

    result_payload = {
        "status": "unavailable" if (not raw_terms and not allow_synthetic) else "completed",
        "synthetic": has_synthetic,
        "trait": trait_name,
        "total_genes": len(go_table),
        "background_total": n_bg_total,
        "go_table": go_table,
        "enrichment_terms": raw_terms,
    }

    if output_dir is not None:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        enrichment_csv = out_path / "Hasil_GO_Enrichment.csv"
        with enrichment_csv.open("w", encoding="utf-8", newline="") as ef:
            writer = csv.writer(ef)
            writer.writerow([
                "ID",
                "Description",
                "Ontology",
                "GeneRatio",
                "BgRatio",
                "pvalue",
                "p.adjust",
                "Count",
                "geneID",
            ])
            for t in raw_terms:
                writer.writerow([
                    t["go_id"],
                    t["description"],
                    t["ontology"],
                    t["gene_ratio_str"],
                    t["bg_ratio"],
                    t["pvalue"],
                    t["p_adjust"],
                    t["count"],
                    t["gene_id_str"],
                ])

        table_csv = out_path / "Tabel_GO_Kandidat.csv"
        with table_csv.open("w", encoding="utf-8", newline="") as tf:
            writer = csv.writer(tf)
            writer.writerow([
                "Gene_ID",
                "Ortholog",
                "Biological_Process",
                "Cellular_Component",
                "Molecular_Function",
                "Source",
            ])
            for row in go_table:
                writer.writerow([
                    row["gene_id"],
                    row["ortholog"],
                    row["biological_process"],
                    row["cellular_component"],
                    row["molecular_function"],
                    row["source_url"] or row.get("source", "unavailable"),
                ])

    return result_payload
