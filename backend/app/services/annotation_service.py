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
import csv
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
            feat = parts[2]

            # Hanya proses fitur gen yang diminta (default: gene, mRNA jika gene absen)
            if feat.lower() != feature_type.lower():
                continue

            try:
                start = int(parts[3])
                end = int(parts[4])
                strand = parts[6]
                attributes_raw = parts[8]

                # Parse atribut ID, Name, Description
                attrs: dict[str, str] = {}
                for item in attributes_raw.split(";"):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        attrs[k.strip().upper()] = v.strip().strip('"')

                gene_id = attrs.get("ID") or attrs.get("GENE_ID") or f"GENE_{seqid}_{start}"
                # Hapus prefix tipe (mis. gene:AT1G01010 -> AT1G01010)
                if ":" in gene_id:
                    gene_id = gene_id.split(":", 1)[1]

                gene_name = attrs.get("NAME") or attrs.get("GENE_NAME") or gene_id
                description = attrs.get("NOTE") or attrs.get("DESCRIPTION") or attrs.get("PRODUCT") or ""

                norm_chr = normalize_chr_name(seqid)
                record = {
                    "gene_id": gene_id,
                    "name": gene_name,
                    "chr": norm_chr,
                    "start": min(start, end),
                    "end": max(start, end),
                    "strand": strand,
                    "description": description,
                }
                genes_by_chr.setdefault(norm_chr, []).append(record)

            except (ValueError, IndexError):
                continue

    # Urutkan gen per kromosom berdasarkan posisi start
    for c in genes_by_chr:
        genes_by_chr[c].sort(key=lambda g: g["start"])

    return genes_by_chr, seen_seqids


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

        chr_genes = genes_by_chr.get(norm_chr, [])
        if not chr_genes:
            mapped_rows.append({
                "snp": snp.get("snp"),
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
                    "start": g["start"],
                    "end": g["end"],
                    "strand": g["strand"],
                    "distance": int(dist_g),
                    "desc": g["description"][:100] if g["description"] else "",
                })

        mapped_rows.append({
            "snp": snp.get("snp"),
            "chr": raw_chr,
            "pos": pos,
            "p": snp.get("p"),
            "beta": snp.get("beta", 0.0),
            "nearest_gene": nearest_gene["gene_id"],
            "nearest_gene_name": nearest_gene["name"],
            "closest_gene": {
                "gene_id": nearest_gene["gene_id"],
                "gene_name": nearest_gene["name"],
                "strand": nearest_gene["strand"],
                "description": nearest_gene["description"],
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

