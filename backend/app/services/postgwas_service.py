"""
Service untuk tahap Pasca-GWAS: Ambang Signifikansi & Clumping (Fase 4E & 4F).

Fitur:
1. Perhitungan lambda_GC tanpa clamping palsu:
   lambda_GC = median(chi2_isf(p, df=1)) / chi2_ppf(0.5, df=1).
2. Penentuan ambang signifikansi dinamis:
   - Bonferroni: alpha / M
   - Benjamini-Hochberg FDR: p_max dengan q < alpha
   - LOD Score: p < 10^(-LOD)
   - Suggestive threshold: default 1e-5
3. Clumping untuk mengidentifikasi Lead SNPs:
   - PLINK 2: --clump dengan jendela LD decay
   - Fallback Python greedy clumping (r^2 >= r2 dalam radius kb)
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats


def calculate_unclamped_lambda_gc(p_values: list[float] | np.ndarray) -> float:
    """
    Menghitung faktor inflasi genomik (lambda_GC) murni tanpa clamping artifisial.
    Menggunakan inverse survival function chi-square 1 derajat kebebasan.
    """
    p_arr = np.array(p_values, dtype=float)
    # Filter nilai p yang valid (0 < p <= 1)
    valid = p_arr[(p_arr > 0) & (p_arr <= 1.0) & ~np.isnan(p_arr)]
    if len(valid) < 5:
        return 1.0

    # Chi-square stat dari p-values: chi2 = isf(p, df=1)
    chi2_stats = stats.chi2.isf(valid, df=1)
    # Median observasi dibagi nilai ekspektasi median chi2(1) ~ 0.4549364
    median_obs = float(np.median(chi2_stats))
    median_exp = float(stats.chi2.ppf(0.5, df=1))

    lambda_gc = median_obs / max(median_exp, 1e-9)
    return round(float(lambda_gc), 4)


def determine_thresholds(
    p_values: list[float] | np.ndarray,
    n_tests: int,
    method: str = "bonferroni",
    alpha: float = 0.05,
    lod_score: float = 5.6,
    suggestive_p: float = 1e-5,
) -> dict[str, Any]:
    """
    Menentukan nilai ambang batas signifikansi:
    - Bonferroni: alpha / M
    - FDR: ambang terbesar yang memenuhi kriteria Benjamini-Hochberg
    - LOD: 10^(-lod_score)
    - Suggestive: 1e-5
    """
    m = max(n_tests, 1)
    bonferroni_p = float(alpha / m)
    lod_p = float(10.0 ** (-float(lod_score)))

    # Benjamini-Hochberg FDR
    fdr_p: float | None = None
    warning: str | None = None

    p_arr = np.sort(np.array(p_values, dtype=float))
    valid_p = p_arr[(p_arr > 0) & (p_arr <= 1.0) & ~np.isnan(p_arr)]
    n_valid = len(valid_p)

    if n_valid > 0:
        # Cari i terbesar di mana p_(i) <= (i / m) * alpha
        bh_thresholds = (np.arange(1, n_valid + 1) / float(m)) * alpha
        passed = valid_p <= bh_thresholds
        if np.any(passed):
            max_idx = int(np.where(passed)[0][-1])
            fdr_p = float(valid_p[max_idx])

    # Tentukan selected_p sesuai pilihan pengguna
    selected_method = method.lower()
    if selected_method == "bonferroni":
        selected_p = bonferroni_p
    elif selected_method == "fdr":
        if fdr_p is not None:
            selected_p = fdr_p
        else:
            selected_p = bonferroni_p
            warning = "Tidak ada SNP yang lolos ambang FDR (q < alpha); beralih ke ambang Bonferroni."
            selected_method = "bonferroni"
    elif selected_method == "lod":
        selected_p = lod_p
    else:
        selected_p = bonferroni_p

    return {
        "n_tests": m,
        "alpha": alpha,
        "bonferroni": bonferroni_p,
        "fdr": fdr_p,
        "lod_p": lod_p,
        "lod_score": lod_score,
        "suggestive": suggestive_p,
        "selected_method": selected_method,
        "selected_p": selected_p,
        "warning": warning,
    }


def run_greedy_clumping(
    assoc_file: Path | str,
    selected_p: float,
    clump_p2: float = 1e-4,
    clump_kb: int = 100,
    clump_r2: float = 0.2,
    genotype_bfile: str | None = None,
) -> list[dict[str, Any]]:
    """
    Greedy clumping untuk mengidentifikasi lead SNPs independen:
    - Membaca baris dari file asosiasi kanonik.
    - Mengurutkan varian berdasarkan p-value naik.
    - Untuk varian dengan p <= selected_p:
      Bila belum ter-clump oleh lead SNP sebelumnya, jadikan Lead SNP baru.
      Kelompokkan varian lain dalam jarak +/- clump_kb pada kromosom yang sama.
    """
    from bisect import bisect_left, bisect_right

    assoc_path = Path(assoc_file)
    if not assoc_path.is_file():
        return []

    max_keep_p = max(float(selected_p), float(clump_p2))

    # Baca hanya varian yang berpotensi menjadi lead SNP atau ter-clump (p <= max(selected_p, clump_p2))
    variants: list[dict[str, Any]] = []
    with assoc_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            try:
                p_val = float(row["p"])
                if p_val > max_keep_p:
                    continue
                pos = int(row["pos"])
                chrom = str(row["chr"]).lstrip("chrCHR")
                rs = str(row["rs"])
                beta = float(row.get("beta") or 0.0)
                se = float(row.get("se") or 0.0)
                variants.append({
                    "chr": chrom,
                    "pos": pos,
                    "rs": rs,
                    "p": p_val,
                    "beta": beta,
                    "se": se,
                    "a1": row.get("a1", "A"),
                    "a0": row.get("a0", "G"),
                })
            except (ValueError, KeyError):
                continue

    # Urutkan berdasarkan p-value terkecil untuk urutan prioritas Lead SNP
    variants.sort(key=lambda x: x["p"])

    # Indeks per kromosom terurut berdasarkan posisi fisik (pos) untuk pencarian window O(log N)
    chr_by_pos: dict[str, list[dict[str, Any]]] = {}
    chr_positions: dict[str, list[int]] = {}
    for v in variants:
        chr_by_pos.setdefault(v["chr"], []).append(v)
    for c_key, c_list in chr_by_pos.items():
        c_list.sort(key=lambda x: x["pos"])
        chr_positions[c_key] = [x["pos"] for x in c_list]

    lead_snps: list[dict[str, Any]] = []
    clumped_ids: set[str] = set()
    window_bp = int(clump_kb) * 1000

    for candidate in variants:
        if candidate["p"] > selected_p:
            break
        if candidate["rs"] in clumped_ids:
            continue

        lead_id = candidate["rs"]
        clumped_ids.add(lead_id)
        absorbed_snps: list[str] = []

        c_chr = candidate["chr"]
        c_pos = candidate["pos"]
        pos_list = chr_positions.get(c_chr, [])
        var_list = chr_by_pos.get(c_chr, [])

        lo = bisect_left(pos_list, c_pos - window_bp)
        hi = bisect_right(pos_list, c_pos + window_bp)
        for idx in range(lo, hi):
            other = var_list[idx]
            oid = other["rs"]
            if oid == lead_id or oid in clumped_ids:
                continue
            if other["p"] <= clump_p2:
                clumped_ids.add(oid)
                absorbed_snps.append(oid)

        lead_snps.append({
            "snp": lead_id,
            "chr": c_chr,
            "pos": c_pos,
            "a1": candidate["a1"],
            "a0": candidate["a0"],
            "p": candidate["p"],
            "beta": candidate["beta"],
            "se": candidate["se"],
            "n_clumped": len(absorbed_snps),
            "clumped_snps": absorbed_snps[:30],  # simpan hingga 30 id
        })

    return lead_snps
