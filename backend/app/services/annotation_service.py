"""
Service untuk tahap Anotasi Genom & Pemetaan Gen Kandidat GFF3 (Fase 5).

Fitur:
1. Normalisasi nama kromosom (Chr1, chr01, 1, NC_003070.9).
2. Parser GFF3/GFF streaming dengan dukungan kompresi gzip.
3. Pemetaan Lead SNPs ke fitur gen dalam rentang jendela LD decay.
4. Klasifikasi biologis SNP:
   - Genik: SNP berada persis di dalam badan gen (start <= pos <= end).
   - Near-gen: SNP berada di luar gen tetapi masih dalam jarak <= window_bp (daerah promotor / regulatori).
   - Intergenik: Jarak SNP ke gen terdekat > window_bp.
5. Ekspor daftar gen unik untuk Gene Ontology (GO) Enrichment (Daftar_Gen_Untuk_GO.txt).
"""

from __future__ import annotations

import bisect
import gzip
import re
from pathlib import Path
from typing import Any


def normalize_chr_name(raw: str) -> str:
    """Normalisasi label kromosom agar konsisten antara VCF/bim dan GFF3."""
    clean = str(raw).strip()
    # Hapus prefix CHROMOSOME / chrom / chr (harus urut terpanjang ke terpendek agar chr tidak menelan chromosome)
    clean = re.sub(r"^(chromosome|chrom|chr)[\s_]*", "", clean, flags=re.IGNORECASE)
    # Hapus leading zeros jika angka (01 -> 1)
    if clean.isdigit():
        return str(int(clean))
    return clean.upper()


def parse_gff3_genes(
    gff_path: str | Path,
    feature_type: str = "gene",
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """
    Membaca file GFF3 secara streaming dan membangun indeks gen per kromosom.
    Interval gen diurutkan berdasarkan posisi awal (start) untuk pencarian cepat.
    """
    path = Path(gff_path)
    if not path.is_file():
        raise FileNotFoundError(f"File anotasi GFF3 tidak ditemukan: {path}")

    genes_by_chr: dict[str, list[dict[str, Any]]] = {}
    seen_seqids: set[str] = set()

    open_fn = gzip.open if path.name.endswith(".gz") else open
    with open_fn(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 9:
                continue

            seqid = parts[0]
            seen_seqids.add(seqid)
            source_col = parts[1] if len(parts) > 1 and parts[1] != "." else "GFF3"
            feat = parts[2]

            # Hanya proses fitur gen yang diminta (default: gene, mRNA jika gene absen)
            if feat.lower() != feature_type.lower():
                continue

            try:
                start = int(parts[3])
                end = int(parts[4])
                strand = parts[6]
                attributes_raw = parts[8]

                # Parse atribut ID, Name, Description, Ontology_term, Dbxref
                attrs: dict[str, str] = {}
                for item in attributes_raw.split(";"):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        attrs[k.strip().upper()] = v.strip().strip('"')

                gene_id = attrs.get("ID") or attrs.get("GENE_ID") or f"GENE_{seqid}_{start}"
                # Hapus prefix tipe (mis. gene:AT1G01010 -> AT1G01010)
                if ":" in gene_id:
                    gene_id = gene_id.split(":", 1)[1]

                gene_name = attrs.get("NAME") or attrs.get("GENE_NAME") or attrs.get("SYMBOL") or gene_id
                description = attrs.get("NOTE") or attrs.get("DESCRIPTION") or attrs.get("PRODUCT") or ""
                go_raw = ",".join(
                    filter(
                        None,
                        [
                            attrs.get("ONTOLOGY_TERM", ""),
                            attrs.get("GO", ""),
                            attrs.get("DBXREF", ""),
                        ],
                    )
                )
                go_ids = sorted(set(re.findall(r"GO:\d{7}", go_raw)))

                norm_chr = normalize_chr_name(seqid)
                record = {
                    "gene_id": gene_id,
                    "name": gene_name,
                    "chr": norm_chr,
                    "gff_chrom": seqid,
                    "source": source_col,
                    "feature_type": feat,
                    "start": min(start, end),
                    "end": max(start, end),
                    "strand": strand,
                    "description": description,
                    "go_ids": go_ids,
                }
                genes_by_chr.setdefault(norm_chr, []).append(record)

            except (ValueError, IndexError):
                continue

    # Urutkan gen per kromosom berdasarkan posisi start
    for c in genes_by_chr:
        genes_by_chr[c].sort(key=lambda g: g["start"])

    return genes_by_chr, seen_seqids


_MULTILOCUS_METHOD_LABELS = {
    "FARMCPU": "FarmCPU",
    "BLINK": "BLINK",
    "MLMM": "MLMM",
}


def _normalize_snp_source_label(raw: Any, default: str = "GEMMA") -> str:
    txt = str(raw or "").strip()
    if not txt:
        return default
    return _MULTILOCUS_METHOD_LABELS.get(txt.upper(), txt)


def merge_annotation_input_snps(
    gemma_snps: list[dict[str, Any]] | None,
    multilocus_snps: list[dict[str, Any]] | None = None,
    p_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """
    Menggabungkan lead SNP GEMMA dengan SNP signifikan multi-locus dan menjaga kolom `source`.
    """
    merged: list[dict[str, Any]] = []
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}

    def _add_candidate(raw_snp: dict[str, Any], source_label: str) -> None:
        if not isinstance(raw_snp, dict):
            return
        snp_id = str(raw_snp.get("snp") or raw_snp.get("rs") or "").strip()
        chr_val = str(raw_snp.get("chr") or "").strip()
        pos_val = int(raw_snp.get("pos") or 0)
        if not snp_id and pos_val <= 0:
            return
        norm_c = normalize_chr_name(chr_val)
        key: tuple[Any, ...] = (norm_c, pos_val) if pos_val > 0 and norm_c else ("snp", snp_id or f"{chr_val}:{pos_val}")

        existing = by_key.get(key)
        if existing is None:
            entry = {
                **raw_snp,
                "snp": snp_id or f"{chr_val}:{pos_val}",
                "chr": chr_val,
                "pos": pos_val,
                "source": source_label,
                "snp_source": source_label,
            }
            by_key[key] = entry
            merged.append(entry)
            return

        existing_sources = [s.strip() for s in str(existing.get("source") or "").split(",") if s.strip()]
        if source_label not in existing_sources:
            existing_sources.append(source_label)
        combined_source = ", ".join(existing_sources)
        existing["source"] = combined_source
        existing["snp_source"] = combined_source
        try:
            new_p = raw_snp.get("p")
            cur_p = existing.get("p")
            if new_p is not None and (cur_p is None or float(new_p) < float(cur_p)):
                existing["p"] = float(new_p)
        except (TypeError, ValueError):
            pass

    for s in gemma_snps or []:
        src = _normalize_snp_source_label(s.get("snp_source") or s.get("source"), default="GEMMA")
        _add_candidate(s, src)

    for s in multilocus_snps or []:
        if not isinstance(s, dict):
            continue
        if "is_significant" in s:
            if not bool(s.get("is_significant")):
                continue
        elif p_threshold is not None:
            try:
                if s.get("p") is None or float(s.get("p")) > p_threshold:
                    continue
            except (TypeError, ValueError):
                continue
        else:
            continue

        ml_src = _normalize_snp_source_label(
            s.get("snp_source") or s.get("source") or s.get("method"),
            default="Multi-Locus",
        )
        _add_candidate(s, ml_src)

    return merged


def map_lead_snps_to_genes(
    lead_snps: list[dict[str, Any]],
    genes_by_chr: dict[str, list[dict[str, Any]]],
    window_bp: int = 100000,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Memetakan setiap lead SNP ke gen terdekat dan gen-gen dalam rentang jendela window_bp.
    Mengklasifikasikan SNP ke:
    - Genik (jarak = 0 bp)
    - Near-gen (jarak <= window_bp)
    - Intergenik (jarak > window_bp)
    """
    mapped_rows: list[dict[str, Any]] = []
    category_counts = {"Genik": 0, "Near-gen": 0, "Intergenik": 0}

    for snp in lead_snps:
        raw_chr = str(snp.get("chr", ""))
        norm_chr = normalize_chr_name(raw_chr)
        pos = int(snp.get("pos", 0))
        snp_source = str(snp.get("snp_source") or snp.get("source") or "GEMMA").strip() or "GEMMA"

        chr_genes = genes_by_chr.get(norm_chr, [])
        if not chr_genes:
            mapped_rows.append({
                "snp": snp.get("snp"),
                "source": snp_source,
                "snp_source": snp_source,
                "gff_source": "GFF3",
                "chr": raw_chr,
                "pos": pos,
                "p": snp.get("p"),
                "beta": snp.get("beta", 0.0),
                "nearest_gene": "-",
                "nearest_gene_name": "-",
                "closest_gene": None,
                "distance_bp": -1,
                "distance_to_closest_gene": None,
                "category": "Intergenik",
                "strand": "-",
                "description": "Tidak ada gen teranotasi pada kromosom ini di GFF3",
                "genes_in_window": [],
                "genes_in_window_count": 0,
            })
            category_counts["Intergenik"] += 1
            continue

        # Cari gen terdekat
        starts = [g["start"] for g in chr_genes]
        idx = bisect.bisect_left(starts, pos)

        # Evaluasi kandidat gen di sekitar index
        candidate_indices = range(max(0, idx - 1), min(len(chr_genes), idx + 2))
        best_dist = float("inf")
        nearest_gene: dict[str, Any] = chr_genes[max(0, idx - 1)]

        for c_idx in candidate_indices:
            g = chr_genes[c_idx]
            if g["start"] <= pos <= g["end"]:
                best_dist = 0
                nearest_gene = g
                break
            elif pos < g["start"]:
                d = g["start"] - pos
            else:
                d = pos - g["end"]

            if d < best_dist:
                best_dist = d
                nearest_gene = g

        # Klasifikasi kategori
        dist_int = int(best_dist)
        if dist_int == 0:
            category = "Genik"
        elif dist_int <= window_bp:
            category = "Near-gen"
        else:
            category = "Intergenik"

        category_counts[category] += 1

        # Kumpulkan semua gen dalam rentang jendela [pos - window_bp, pos + window_bp]
        win_start = pos - window_bp
        win_end = pos + window_bp
        w_left = bisect.bisect_left(starts, win_start)
        # Scan ke kiri sedikit untuk gen yang start < win_start tapi end >= win_start
        while w_left > 0 and chr_genes[w_left - 1]["end"] >= win_start:
            w_left -= 1

        genes_in_window: list[dict[str, Any]] = []
        for i in range(w_left, len(chr_genes)):
            g = chr_genes[i]
            if g["start"] > win_end:
                break
            if g["end"] >= win_start:
                if g["start"] <= pos <= g["end"]:
                    dist_g = 0
                elif pos < g["start"]:
                    dist_g = g["start"] - pos
                else:
                    dist_g = pos - g["end"]
                genes_in_window.append({
                    "id": g["gene_id"],
                    "gene_id": g["gene_id"],
                    "name": g["name"],
                    "gene_name": g["name"],
                    "gff_chrom": g.get("gff_chrom", raw_chr),
                    "source": g.get("source", "GFF3"),
                    "feature_type": g.get("feature_type", "gene"),
                    "start": g["start"],
                    "end": g["end"],
                    "strand": g["strand"],
                    "distance": int(dist_g),
                    "desc": g["description"][:100] if g["description"] else "",
                    "go_ids": g.get("go_ids", []),
                })

        mapped_rows.append({
            "snp": snp.get("snp"),
            "source": snp_source,
            "snp_source": snp_source,
            "chr": raw_chr,
            "pos": pos,
            "p": snp.get("p"),
            "beta": snp.get("beta", 0.0),
            "ref": snp.get("ref"),
            "alt": snp.get("alt"),
            "a1": snp.get("a1"),
            "a0": snp.get("a0"),
            "nearest_gene": nearest_gene["gene_id"],
            "nearest_gene_name": nearest_gene["name"],
            "gff_chrom": nearest_gene.get("gff_chrom", raw_chr),
            "gff_source": nearest_gene.get("source", "GFF3"),
            "feature_type": nearest_gene.get("feature_type", "gene"),
            "gene_start": nearest_gene["start"],
            "gene_end": nearest_gene["end"],
            "closest_gene": {
                "gene_id": nearest_gene["gene_id"],
                "gene_name": nearest_gene["name"],
                "gff_chrom": nearest_gene.get("gff_chrom", raw_chr),
                "source": nearest_gene.get("source", "GFF3"),
                "feature_type": nearest_gene.get("feature_type", "gene"),
                "start": nearest_gene["start"],
                "end": nearest_gene["end"],
                "strand": nearest_gene["strand"],
                "description": nearest_gene["description"],
                "go_ids": nearest_gene.get("go_ids", []),
            },
            "distance_bp": dist_int,
            "distance_to_closest_gene": dist_int,
            "category": category,
            "strand": nearest_gene["strand"],
            "description": nearest_gene["description"],
            "genes_in_window": genes_in_window,
            "genes_in_window_count": len(genes_in_window),
        })

    return mapped_rows, category_counts
