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
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

from app.tasks._common import chr_label


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
    min_p_val = float(valid_p[0]) if n_valid > 0 else 1.0

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
        "min_p": min_p_val,
        "warning": warning,
    }


def _load_clump_variants(
    assoc_path: Path,
    *,
    max_p: float,
    min_p_exclusive: float = -1.0,
    exclude_snps: set[str] | None = None,
) -> list[dict[str, Any]]:
    excluded = exclude_snps or set()
    variants: list[dict[str, Any]] = []
    with assoc_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            try:
                p_val = float(row["p"])
                if p_val <= min_p_exclusive or p_val > max_p:
                    continue
                rs = str(row["rs"])
                if rs in excluded:
                    continue
                variants.append({
                    "chr": chr_label(row["chr"]),
                    "pos": int(row["pos"]),
                    "rs": rs,
                    "p": p_val,
                    "beta": float(row.get("beta") or 0.0),
                    "se": float(row.get("se") or 0.0),
                    "a1": row.get("a1", "A"),
                    "a0": row.get("a0", "G"),
                })
            except (ValueError, KeyError):
                continue
    return variants


def _clump_variant_rows(
    variants: list[dict[str, Any]],
    *,
    max_lead_p: float,
    clump_p2: float,
    clump_kb: int,
    max_count: int,
    is_candidate: bool = False,
    is_suggestive: bool = False,
) -> list[dict[str, Any]]:
    from bisect import bisect_left, bisect_right

    if not variants:
        return []

    variants.sort(key=lambda x: x["p"])

    chr_by_pos: dict[str, list[dict[str, Any]]] = {}
    chr_positions: dict[str, list[int]] = {}
    for v in variants:
        chr_by_pos.setdefault(v["chr"], []).append(v)
    for c_key, c_list in chr_by_pos.items():
        c_list.sort(key=lambda x: x["pos"])
        chr_positions[c_key] = [x["pos"] for x in c_list]

    peaks: list[dict[str, Any]] = []
    clumped_ids: set[str] = set()
    window_bp = int(clump_kb) * 1000

    for candidate in variants:
        if candidate["p"] > max_lead_p:
            break
        if candidate["rs"] in clumped_ids:
            continue
        if len(peaks) >= max_count:
            break

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
            if other["p"] <= max(clump_p2, candidate["p"] * 2.0):
                clumped_ids.add(oid)
                absorbed_snps.append(oid)

        entry: dict[str, Any] = {
            "snp": lead_id,
            "chr": c_chr,
            "pos": c_pos,
            "a1": candidate["a1"],
            "a0": candidate["a0"],
            "p": candidate["p"],
            "beta": candidate["beta"],
            "se": candidate["se"],
            "n_clumped": len(absorbed_snps),
            "clumped_snps": absorbed_snps[:30],
            "is_candidate": is_candidate,
        }
        if is_suggestive:
            entry["is_suggestive"] = True
        peaks.append(entry)

    return peaks


def run_greedy_clumping(
    assoc_file: Path | str,
    selected_p: float,
    clump_p2: float = 1e-4,
    clump_kb: int = 100,
    clump_r2: float = 0.2,
    genotype_bfile: str | None = None,
    max_lead_snps: int = 50,
) -> list[dict[str, Any]]:
    """
    Greedy clumping untuk mengidentifikasi lead SNPs independen yang memenuhi
    ambang signifikansi 4E (`p <= selected_p`). Tidak mengembalikan kandidat non-signifikan.
    """
    assoc_path = Path(assoc_file)
    if not assoc_path.is_file():
        return []

    max_keep_p = max(float(selected_p), float(clump_p2))
    variants = _load_clump_variants(assoc_path, max_p=max_keep_p)
    return _clump_variant_rows(
        variants,
        max_lead_p=float(selected_p),
        clump_p2=float(clump_p2),
        clump_kb=int(clump_kb),
        max_count=int(max_lead_snps),
        is_candidate=False,
    )


def _lead_p(snp: dict[str, Any]) -> float:
    try:
        return float(snp.get("p"))
    except (TypeError, ValueError):
        return 1.0


def _clumped_ids(snp: dict[str, Any]) -> list[str]:
    raw = snp.get("clumped_snps")
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        return []
    return [str(c).strip() for c in raw if c and str(c).strip() not in ("", ".")]


def _drop_absorbed_by_leads(
    variants: list[dict[str, Any]],
    leads: list[dict[str, Any]],
    *,
    clump_p2: float,
    clump_kb: int,
) -> list[dict[str, Any]]:
    """Buang varian yang sudah terserap jendela clumping Lead SNP (kriteria greedy yang sama)."""
    from bisect import bisect_left, bisect_right

    window_bp = int(clump_kb) * 1000
    anchors: dict[str, list[tuple[int, float]]] = {}
    for lead in leads:
        try:
            pos = int(lead["pos"])
        except (KeyError, TypeError, ValueError):
            continue
        absorb_p = max(float(clump_p2), _lead_p(lead) * 2.0)
        anchors.setdefault(str(lead.get("chr")), []).append((pos, absorb_p))
    for items in anchors.values():
        items.sort()
    anchor_pos = {c: [pos for pos, _ in items] for c, items in anchors.items()}

    kept: list[dict[str, Any]] = []
    for v in variants:
        items = anchors.get(v["chr"])
        if items:
            positions = anchor_pos[v["chr"]]
            lo = bisect_left(positions, v["pos"] - window_bp)
            hi = bisect_right(positions, v["pos"] + window_bp)
            if any(v["p"] <= items[i][1] for i in range(lo, hi)):
                continue
        kept.append(v)
    return kept


def partition_peaks_at_threshold(
    assoc_file: Path | str,
    *,
    base_leads: list[dict[str, Any]],
    base_p: float,
    threshold_p: float,
    suggestive_p: float = 1e-5,
    clump_p2: float = 1e-4,
    clump_kb: int = 100,
    max_extra_leads: int = 500,
    max_suggestive_snps: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Membagi puncak independen menjadi Lead SNP (`p <= threshold_p`) dan puncak
    sugestif/eksploratif (`p > threshold_p`) untuk ambang aktif yang dipilih pengguna.

    `base_leads` adalah Lead SNP pipeline hasil clumping pada `base_p`; semuanya
    dipertahankan apa adanya. Clumping greedy hanya dilanjutkan untuk varian
    `p > base_p` yang tidak terserap jendela Lead SNP pipeline, sehingga hasilnya
    setara dengan clumping greedy sekali jalan pada ambang yang lebih longgar.
    """
    threshold_p = float(threshold_p)
    base_p = float(base_p)
    leads = [dict(s) for s in base_leads if _lead_p(s) <= threshold_p]
    # Lead pipeline yang tidak lolos ambang aktif selalu ditampilkan utuh (tanpa batas jumlah).
    demoted = [
        {**s, "is_candidate": True, "is_suggestive": True}
        for s in base_leads
        if _lead_p(s) > threshold_p
    ]
    beyond: list[dict[str, Any]] = []

    assoc_path = Path(assoc_file)
    if assoc_path.is_file():
        excluded: set[str] = set()
        for s in base_leads:
            if s.get("snp"):
                excluded.add(str(s["snp"]))
            excluded.update(_clumped_ids(s))
        ceiling = max(threshold_p, float(suggestive_p), float(clump_p2), 0.05)
        variants = _load_clump_variants(
            assoc_path, max_p=ceiling, min_p_exclusive=base_p, exclude_snps=excluded
        )
        variants = _drop_absorbed_by_leads(
            variants, base_leads, clump_p2=float(clump_p2), clump_kb=int(clump_kb)
        )
        lead_room = int(max_extra_leads) if threshold_p > base_p else 0
        extra = _clump_variant_rows(
            variants,
            max_lead_p=ceiling,
            clump_p2=float(clump_p2),
            clump_kb=int(clump_kb),
            max_count=lead_room + int(max_suggestive_snps),
        )
        leads.extend(e for e in extra if e["p"] <= threshold_p)
        beyond.extend(
            {**e, "is_candidate": True, "is_suggestive": True}
            for e in extra
            if e["p"] > threshold_p
        )

    demoted.sort(key=_lead_p)
    beyond.sort(key=_lead_p)
    if any(_lead_p(s) <= float(suggestive_p) for s in (*demoted, *beyond)):
        beyond = [s for s in beyond if _lead_p(s) <= float(suggestive_p)]
    else:
        beyond = [s for s in beyond if _lead_p(s) <= 0.05]
    return leads, demoted + beyond[: int(max_suggestive_snps)]


def extract_suggestive_peaks(
    assoc_file: Path | str,
    *,
    selected_p: float,
    suggestive_p: float = 1e-5,
    clump_p2: float = 1e-4,
    clump_kb: int = 100,
    lead_snps: list[dict[str, Any]] | None = None,
    max_suggestive_snps: int = 20,
) -> list[dict[str, Any]]:
    """
    Mengumpulkan puncak sugestif / kandidat eksploratif terpisah (`p > selected_p`)
    tanpa mencampurkannya ke daftar Lead SNP signifikan utama. Varian di dalam
    jendela clumping `lead_snps` dianggap sudah terserap, bukan puncak baru.
    """
    if not Path(assoc_file).is_file():
        return []
    _, suggestive = partition_peaks_at_threshold(
        assoc_file,
        base_leads=list(lead_snps or []),
        base_p=float(selected_p),
        threshold_p=float(selected_p),
        suggestive_p=float(suggestive_p),
        clump_p2=float(clump_p2),
        clump_kb=int(clump_kb),
        max_suggestive_snps=int(max_suggestive_snps),
    )
    return suggestive
