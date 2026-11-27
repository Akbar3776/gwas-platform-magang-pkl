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
from dataclasses import dataclass
import logging
import math
from pathlib import Path
import re
from typing import Any, Literal

import numpy as np
from scipy.stats import hypergeom

from app.core.config import settings
from app.services.gene_id_utils import GENE_ID_RE, strip_isoform_suffix
from app.services.upload_validation import UploadValidationError, open_lines_bounded

logger = logging.getLogger(__name__)

_GO_ID_RE = re.compile(r"GO:\d{7}", re.IGNORECASE)
_GO_TOKEN_SPLIT_RE = re.compile(r"[,;|\s]+")
_GO_HEADER_COL1 = {"gene_id", "gene", "locus", "id"}
_GO_HEADER_COL2 = {"go_id", "go", "go_term"}
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
    import os

    return os.getenv("ALLOW_SYNTHETIC_ANNOTATION", "").strip().lower() in ("1", "true", "yes")


def _clean_cell_token(raw: str) -> str:
    return raw.strip().strip('"').strip("'")


def _normalize_gene_token(raw: str) -> str:
    txt = _clean_cell_token(raw)
    if txt.lower().startswith("gene:"):
        txt = txt[5:].strip()
    return txt


def _extract_go_tokens(raw: str) -> list[str]:
    out: list[str] = []
    for part in _GO_TOKEN_SPLIT_RE.split(raw.strip()):
        tok = _clean_cell_token(part).upper()
        if tok and _GO_ID_RE.fullmatch(tok):
            out.append(tok)
    return out


_GO_CANDIDATE_FILENAMES = (
    "go_annotation.tsv.gz",
    "go_annotation.tsv",
    "go_annotation.gaf",
    "go_annotation.txt",
    "go_annotation.gaf.gz",
    "go_annotation.txt.gz",
)


def resolve_dataset_go_mapping_path(dataset: Any) -> Path | None:
    """Mengembalikan path file pemetaan GO dataset yang valid dan berada di bawah STORAGE_ROOT."""
    if dataset is None:
        return None
    from app.tasks._common import is_under_storage

    go_meta = (getattr(dataset, "extra_metadata", None) or {}).get("go_annotation")
    if isinstance(go_meta, dict) and go_meta.get("path"):
        meta_path = Path(go_meta["path"])
        if meta_path.is_file() and is_under_storage(meta_path):
            return meta_path

    ds_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id)
    for cand_name in _GO_CANDIDATE_FILENAMES:
        go_f = ds_dir / cand_name
        if go_f.is_file():
            return go_f
    return None


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
    atau GAF 2.x (`!` header, kolom 2 = gene_id, kolom 5 = GO_ID, kolom 9 = aspek P/F/C)
    dengan pembacaan biner terbatas.
    """
    path = Path(mapping_path)
    if not path.is_file():
        raise FileNotFoundError(f"File pemetaan GO tidak ditemukan: {path}")

    by_gene: dict[str, dict[str, dict[str, str]]] = {}
    detected_formats: set[str] = set()
    n_mappings = 0
    n_data_lines = 0
    n_invalid_lines = 0
    first_invalid_line: int | None = None
    read_stats: dict[str, int] = {"decompressed_bytes": 0, "lines": 0}

    with open_lines_bounded(
        path,
        max_decompressed_bytes=settings.GO_MAPPING_MAX_DECOMPRESSED_BYTES,
        max_lines=settings.GO_MAPPING_MAX_LINES,
        max_line_bytes=settings.UPLOAD_TEXT_MAX_LINE_BYTES,
        stats=read_stats,
    ) as lines:
        for line_no, line in lines:
            stripped = line.strip()
            if not stripped or line.lstrip().startswith(("!", "#")):
                continue

            parts = line.split("\t")
            if len(parts) >= 2:
                head0 = _clean_cell_token(parts[0]).lower()
                head1 = _clean_cell_token(parts[1]).lower()
                if head0 in _GO_HEADER_COL1 and head1 in _GO_HEADER_COL2:
                    continue

            n_data_lines += 1
            if len(parts) < 2:
                n_invalid_lines += 1
                if first_invalid_line is None:
                    first_invalid_line = line_no
                continue

            gaf_go_tok = _clean_cell_token(parts[4]).upper() if len(parts) >= 9 else ""
            if len(parts) >= 9 and _GO_ID_RE.fullmatch(gaf_go_tok):
                gene_raw = _normalize_gene_token(parts[1])
                if not GENE_ID_RE.fullmatch(gene_raw):
                    n_invalid_lines += 1
                    if first_invalid_line is None:
                        first_invalid_line = line_no
                    continue
                go_tokens = [gaf_go_tok]
                domain = _normalize_domain(parts[8])
                term_name = ""
                detected_formats.add("gaf")
            else:
                gene_raw = _normalize_gene_token(parts[0])
                go_tokens = _extract_go_tokens(parts[1])
                if not GENE_ID_RE.fullmatch(gene_raw) or not go_tokens:
                    n_invalid_lines += 1
                    if first_invalid_line is None:
                        first_invalid_line = line_no
                    continue
                domain = _normalize_domain(parts[2]) if len(parts) >= 3 else "unknown"
                term_name = _clean_cell_token(parts[3]) if len(parts) >= 4 else ""
                detected_formats.add("tsv")

            gene_key = _normalize_gene_key(gene_raw)
            gene_terms = by_gene.setdefault(gene_key, {})
            for go_id in go_tokens:
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
    if detected_formats == {"gaf"}:
        fmt = "gaf"
    elif detected_formats == {"tsv"}:
        fmt = "tsv"
    elif detected_formats:
        fmt = "mixed"
    else:
        fmt = "unknown"

    summary = {
        "format": fmt,
        "n_genes": len(annotations),
        "n_mappings": n_mappings,
        "n_genes_raw": len(annotations),
        "n_mappings_raw": n_mappings,
        "n_data_lines": n_data_lines,
        "n_invalid_lines": n_invalid_lines,
        "first_invalid_line": first_invalid_line,
        "decompressed_bytes": int(read_stats.get("decompressed_bytes", 0)),
    }
    return annotations, summary


@dataclass
class GoAnnotationIndex:
    """Indeks pemetaan GO dengan pencocokan exact lalu fallback satu level isoform."""

    exact: dict[str, dict[str, dict[str, str]]]
    base: dict[str, dict[str, dict[str, str]]]

    def lookup(
        self,
        gene_id: Any,
    ) -> tuple[list[dict[str, str]], Literal["exact", "isoform_fallback", "none"]]:
        k = _normalize_gene_key(gene_id)
        if not k:
            return [], "none"
        if k in self.exact:
            return list(self.exact[k].values()), "exact"
        if k in self.base:
            return list(self.base[k].values()), "isoform_fallback"
        base_k = strip_isoform_suffix(k)
        if base_k != k:
            if base_k in self.exact:
                return list(self.exact[base_k].values()), "isoform_fallback"
            if base_k in self.base:
                return list(self.base[base_k].values()), "isoform_fallback"
        return [], "none"


def build_go_annotation_index(
    annotations: dict[str, list[dict[str, str]]],
) -> GoAnnotationIndex:
    """Membangun GoAnnotationIndex (exact dan base level gen) dari hasil parse pemetaan GO."""
    exact: dict[str, dict[str, dict[str, str]]] = {}
    base: dict[str, dict[str, dict[str, str]]] = {}

    for gkey, term_list in annotations.items():
        norm_key = _normalize_gene_key(gkey)
        if not norm_key:
            continue
        exact_map = exact.setdefault(norm_key, {})
        base_key = strip_isoform_suffix(norm_key)
        base_map = base.setdefault(base_key, {})

        for term in term_list:
            go_id = str(term.get("go_id") or "").strip().upper()
            if not go_id:
                continue
            domain = term.get("ontology") or "unknown"
            desc = term.get("description") or ""
            for target_map in (exact_map, base_map):
                existing = target_map.get(go_id)
                if existing is None:
                    target_map[go_id] = {
                        "go_id": go_id,
                        "ontology": domain,
                        "description": desc,
                        "gene_raw": term.get("gene_raw", norm_key),
                    }
                else:
                    if existing.get("ontology", "unknown") == "unknown" and domain != "unknown":
                        existing["ontology"] = domain
                    if not existing.get("description") and desc:
                        existing["description"] = desc

    return GoAnnotationIndex(exact=exact, base=base)


_FORMULA_PREFIX_RE = re.compile(r"^[=+\-@\t\r]")
_NUMERIC_STRING_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def csv_safe_cell(value: Any) -> str:
    """Menetralkan sel CSV terhadap formula injection, identik dengan frontend/src/utils/csv.js."""
    if value is None:
        return ""
    if isinstance(value, bool):
        txt = str(value)
    elif isinstance(value, (int, float)):
        return str(value) if math.isfinite(value) else ""
    else:
        txt = str(value)

    if _FORMULA_PREFIX_RE.match(txt):
        if not txt.startswith(("\t", "\r")) and _NUMERIC_STRING_RE.fullmatch(txt.strip()):
            return txt
        return f"'{txt}"
    return txt


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
        seen_ids: set[str] = set()
        for raw_go_id in clean_gff:
            go_id = raw_go_id
            if go_id in seen_ids:
                continue
            seen_ids.add(go_id)
            extra = uploaded_lookup_by_go.get(go_id)
            ontology_val = extra["ontology"] if extra else "unknown"
            desc = extra["description"] if extra and extra.get("description") else go_id
            resolved.append({"go_id": go_id, "ontology": ontology_val, "description": desc})
        if resolved:
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


def _collect_gff_features(
    gff_path: str | Path | None,
    gff_genes_by_chr: dict[str, list[dict[str, Any]]] | None,
    feature_type: str = "gene",
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    if gff_genes_by_chr is None and gff_path is not None and Path(gff_path).is_file():
        from app.services.annotation_service import parse_gff3_genes

        gff_genes_by_chr, _ = parse_gff3_genes(gff_path, feature_type=feature_type)

    features: list[dict[str, Any]] = []
    out: dict[str, list[str]] = {}
    for glist in (gff_genes_by_chr or {}).values():
        for g in glist:
            features.append(g)
            gkey = _normalize_gene_key(g.get("gene_id"))
            if not gkey:
                continue
            go_ids = list(dict.fromkeys(str(tid).strip().upper() for tid in (g.get("go_ids") or []) if tid))
            if go_ids:
                existing = out.setdefault(gkey, [])
                for tid in go_ids:
                    if tid not in existing:
                        existing.append(tid)
    return features, out


def _fmt_id_int(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _fmt_id_pct(value: float) -> str:
    rounded = round(float(value), 2)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def format_go_log_lines(
    payload: dict[str, Any],
    *,
    sample_gff_ids: list[str] | None = None,
    sample_mapping_ids: list[str] | None = None,
) -> list[str]:
    """Menyusun ringkasan baris log [GO] dan [WARN] berbahasa Indonesia untuk tahap anotasi."""
    stats = payload.get("go_stats") or {}
    if not stats:
        return []

    lines: list[str] = []
    mapping = stats.get("mapping") or {}
    if mapping.get("available"):
        lines.append(
            f"[GO] Pemetaan: {_fmt_id_int(int(mapping.get('n_genes_raw', 0)))} gen / "
            f"{_fmt_id_int(int(mapping.get('n_pairs_raw', 0)))} pasangan mentah -> "
            f"{_fmt_id_int(int(mapping.get('n_genes', 0)))} gen / "
            f"{_fmt_id_int(int(mapping.get('n_pairs', 0)))} pasangan (gen, GO) unik setelah normalisasi."
        )

    match = stats.get("match") or {}
    n_cand = int(match.get("n_candidates", 0))
    n_exact = int(match.get("n_exact", 0))
    n_iso = int(match.get("n_isoform_fallback", 0))
    n_cand_matched = n_exact + n_iso
    n_gff = int(match.get("n_gff_features", 0))
    n_gff_matched = int(match.get("n_gff_features_matched", 0))
    pct_cand = _fmt_id_pct(float(match.get("pct_candidates_matched", 0.0)))
    pct_gff = _fmt_id_pct(float(match.get("pct_gff_features_matched", 0.0)))
    lines.append(
        f"[GO] Kecocokan ID (feature_type={match.get('feature_type', 'gene')}): "
        f"kandidat {n_cand_matched}/{n_cand} ({pct_cand}%) "
        f"[exact {n_exact}, fallback isoform {n_iso}]; "
        f"seluruh GFF {_fmt_id_int(n_gff_matched)}/{_fmt_id_int(n_gff)} ({pct_gff}%)."
    )

    bg = stats.get("background") or {}
    bg_desc = "dari file pemetaan" if bg.get("source", "mapping_file") == "mapping_file" else "semua fitur GFF"
    lines.append(f"[GO] Background: {_fmt_id_int(int(bg.get('n_universe', 0)))} gen ({bg_desc}).")

    if n_gff > 0 and mapping.get("available") and float(match.get("pct_gff_features_matched", 0.0)) < 10.0:
        gff_ex = ", ".join(sample_gff_ids or []) or "-"
        map_ex = ", ".join(sample_mapping_ids or []) or "-"
        lines.append(
            f"[WARN] Hanya {pct_gff}% fitur GFF yang memiliki GO. "
            f"Contoh ID GFF: {gff_ex}; contoh ID file: {map_ex}. Periksa konvensi ID."
        )
    return lines


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

    gff_features, gff_go_map = _collect_gff_features(gff_path, gff_genes_by_chr, feature_type=feature_type)
    raw_annotations: dict[str, list[dict[str, str]]] = {}
    parse_summary: dict[str, Any] = {}
    mapping_available = bool(go_mapping_path is not None and Path(go_mapping_path).is_file())
    if mapping_available and go_mapping_path is not None:
        try:
            raw_annotations, parse_summary = parse_go_annotation_file(go_mapping_path)
        except UploadValidationError as err:
            logger.warning("Pemetaan GO dilewati: %s", err.code)
            raw_annotations = {}
            parse_summary = {}

    n_genes_raw = int(parse_summary.get("n_genes_raw", len(raw_annotations)))
    n_pairs_raw = int(parse_summary.get("n_pairs_raw", sum(len(v) for v in raw_annotations.values())))

    uploaded_annotations = raw_annotations
    n_genes_norm = n_genes_raw
    n_pairs_norm = n_pairs_raw

    index = build_go_annotation_index(uploaded_annotations)

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

    # Bangun semesta latar belakang di ruang kunci GFF (jika tersedia) atau level gen file pemetaan
    bg_gene_to_go: dict[str, set[str]] = {}
    n_gff_features = len(gff_features)
    n_gff_features_matched = 0
    gff_keys: set[str] = set()
    gff_base_keys: set[str] = set()

    if n_gff_features > 0:
        for feat in gff_features:
            fkey = _normalize_gene_key(feat.get("gene_id"))
            if not fkey:
                continue
            gff_keys.add(fkey)
            gff_base_keys.add(strip_isoform_suffix(fkey))
            feat_explicit = list(
                dict.fromkeys(
                    str(tid).strip().upper()
                    for tid in (feat.get("go_ids") or [])
                    if _GO_ID_RE.fullmatch(str(tid).strip().upper())
                )
            )
            feat_go_ids = set(feat_explicit)

            if not feat_go_ids:
                matched_terms, _ = index.lookup(feat.get("gene_id"))
                feat_go_ids = {t["go_id"] for t in matched_terms}

            if feat_go_ids:
                n_gff_features_matched += 1
                bg_gene_to_go.setdefault(fkey, set()).update(feat_go_ids)

        if bg_gene_to_go:
            bg_source = "gff_all"
        else:
            bg_source = "mapping_file"
            for bkey, term_map in index.base.items():
                if term_map:
                    bg_gene_to_go[bkey] = set(term_map.keys())
            if index.base or n_gff_features_matched > 0:
                logger.warning("Background GFF kosong (feature_type/pola kontig); memakai file pemetaan.")
    else:
        bg_source = "mapping_file"
        for bkey, term_map in index.base.items():
            if term_map:
                bg_gene_to_go[bkey] = set(term_map.keys())
        if index.base:
            logger.warning("Background dari file pemetaan (GFF tidak tersedia/kosong).")

    n_mapping_genes_not_in_gff = (
        sum(1 for b in index.base if b not in gff_keys and b not in gff_base_keys)
        if n_gff_features > 0
        else 0
    )

    go_table: list[dict[str, Any]] = []
    term_to_genes: dict[str, dict[str, Any]] = {}
    n_cand_exact = 0
    n_cand_isoform = 0
    n_cand_unmatched = 0
    n_candidates_outside_universe = 0

    for gid in sorted(gene_meta_map.keys()):
        meta = gene_meta_map[gid]
        gkey = _normalize_gene_key(gid)
        explicit_go = list(dict.fromkeys([*(meta.get("go_ids") or []), *gff_go_map.get(gkey, [])]))
        mapped_terms, match_kind = index.lookup(gid)

        resolved = _resolve_gene_go_entry(
            gene_id=gid,
            gene_name=meta.get("gene_name", gid),
            description=meta.get("description", ""),
            explicit_go_ids=explicit_go,
            uploaded_terms=mapped_terms,
            uploaded_lookup_by_go=uploaded_lookup_by_go,
            allow_synthetic=allow_synthetic,
        )
        terms = resolved["terms"]

        if resolved["source"] == "gff3":
            n_cand_exact += 1
        elif resolved["source"] == "mapping":
            if match_kind == "isoform_fallback":
                n_cand_isoform += 1
            else:
                n_cand_exact += 1
        else:
            n_cand_unmatched += 1

        if resolved["go_terms"] and not resolved["synthetic"]:
            if gkey not in bg_gene_to_go:
                n_candidates_outside_universe += 1
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

    pct_cand_matched = round(((n_cand_exact + n_cand_isoform) / len(go_table)) * 100, 2) if go_table else 0.0
    pct_gff_matched = round((n_gff_features_matched / n_gff_features) * 100, 2) if n_gff_features > 0 else 0.0

    go_stats = {
        "mapping": {
            "available": mapping_available,
            "n_genes_raw": n_genes_raw,
            "n_pairs_raw": n_pairs_raw,
            "n_genes": n_genes_norm,
            "n_pairs": n_pairs_norm,
        },
        "match": {
            "feature_type": feature_type,
            "n_candidates": len(go_table),
            "n_exact": n_cand_exact,
            "n_isoform_fallback": n_cand_isoform,
            "n_unmatched": n_cand_unmatched,
            "pct_candidates_matched": pct_cand_matched,
            "n_gff_features": n_gff_features,
            "n_gff_features_matched": n_gff_features_matched,
            "pct_gff_features_matched": pct_gff_matched,
        },
        "background": {
            "source": bg_source,
            "n_universe": n_bg_total,
            "n_mapping_genes_not_in_gff": n_mapping_genes_not_in_gff,
            "n_candidates_outside_universe": n_candidates_outside_universe,
        },
        "ortholog": {"available": False},
    }

    result_payload = {
        "status": "unavailable" if (not raw_terms and not allow_synthetic) else "completed",
        "synthetic": has_synthetic,
        "trait": trait_name,
        "total_genes": len(go_table),
        "background_total": n_bg_total,
        "go_table": go_table,
        "enrichment_terms": raw_terms,
        "go_stats": go_stats,
    }

    sample_gff = [str(f.get("gene_id")) for f in gff_features[:3] if f.get("gene_id")]
    sample_map = [
        str(terms[0].get("gene_raw", gkey))
        for gkey, terms in list(uploaded_annotations.items())[:3]
        if terms
    ]
    for log_line in format_go_log_lines(
        result_payload,
        sample_gff_ids=sample_gff,
        sample_mapping_ids=sample_map,
    ):
        if log_line.startswith("[WARN]"):
            logger.warning(log_line)
        else:
            logger.info(log_line)

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
                    csv_safe_cell(v)
                    for v in (
                        t["go_id"],
                        t["description"],
                        t["ontology"],
                        t["gene_ratio_str"],
                        t["bg_ratio"],
                        t["pvalue"],
                        t["p_adjust"],
                        t["count"],
                        t["gene_id_str"],
                    )
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
                    csv_safe_cell(v)
                    for v in (
                        row["gene_id"],
                        row["ortholog"],
                        row["biological_process"],
                        row["cellular_component"],
                        row["molecular_function"],
                        row["source_url"] or row.get("source", "unavailable"),
                    )
                ])

    return result_payload
