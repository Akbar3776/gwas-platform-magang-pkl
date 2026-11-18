"""
Parser & downsampler untuk output GEMMA (`.assoc.txt`) menjadi dataset ringan
yang siap dikonsumsi frontend React untuk Manhattan Plot.

Strategi efisiensi memori (penting untuk file assoc yang bisa berisi
jutaan baris SNP pada studi genom skala penuh):

1. Baca file dengan `pandas.read_csv(..., chunksize=...)` sehingga TIDAK
   PERNAH memuat seluruh file ke RAM sekaligus.
2. Gunakan `usecols` agar hanya kolom yang diperlukan yang dibaca dari disk.
3. Tentukan `dtype` eksplisit (uint8 / uint32 / float32) agar footprint
   memori per baris jauh lebih kecil dibanding default int64/float64 pandas.
4. Downsampling dilakukan PER CHUNK + pembatasan akhir (`max_points=12_000`)
   dengan menjaga 100% SNP paling signifikan agar payload JSON ringan dan
   visualisasi di browser tidak pernah freeze/crash.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

CANDIDATE_PVALUE_COLUMNS = ["p_wald", "p_lrt", "p_score", "P", "p", "PVAL", "pval"]
CANDIDATE_CHR_COLUMNS = ["chr", "CHR", "CHROM", "chrom", "#CHROM"]
CANDIDATE_RS_COLUMNS = ["rs", "SNP", "snp", "ID", "id"]
CANDIDATE_POS_COLUMNS = ["ps", "BP", "POS", "pos", "bp"]
DEFAULT_MAX_PLOT_POINTS = 12_000


def _detect_column(header_cols: list[str], candidates: list[str], label: str) -> str:
    for col in candidates:
        if col in header_cols:
            return col
    raise ValueError(
        f"Tidak menemukan kolom {label} yang dikenali pada output GWAS. "
        f"Kolom yang tersedia: {header_cols}"
    )


def _detect_pvalue_column(header_cols: list[str]) -> str:
    return _detect_column(header_cols, CANDIDATE_PVALUE_COLUMNS, "p-value")


def cap_manhattan_dataframe(
    df: pd.DataFrame,
    max_points: int = DEFAULT_MAX_PLOT_POINTS,
    top_preserve: int = 2_500,
) -> pd.DataFrame:
    """
    Pastikan jumlah titik yang dikirim ke frontend tidak melebihi `max_points`.
    Selalu mempertahankan 100% `top_preserve` SNP paling signifikan (LOG10_P tertinggi),
    dan mengambil sampel merata (stride) untuk sisanya agar struktur kromosom tetap utuh.
    """
    if len(df) <= max_points:
        return df

    top_n = min(top_preserve, max_points // 2)
    # Ambil indeks dengan LOG10_P tertinggi
    top_indices = df["LOG10_P"].nlargest(top_n).index
    remaining_df = df.drop(index=top_indices)

    budget = max(max_points - len(top_indices), 1)
    if len(remaining_df) > budget:
        stride_positions = np.linspace(0, len(remaining_df) - 1, num=budget, dtype=int)
        sampled_remaining = remaining_df.iloc[stride_positions]
    else:
        sampled_remaining = remaining_df

    combined = pd.concat([df.loc[top_indices], sampled_remaining], ignore_index=True)
    combined.sort_values(["CHR", "BP"], inplace=True, kind="mergesort")
    combined.reset_index(drop=True, inplace=True)
    return combined


def _save_cache_df(df: pd.DataFrame, output_cache_path: str) -> str:
    path = Path(output_cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path, index=False, compression="snappy")
        return str(path)
    except ImportError:
        df.to_pickle(path)
        return str(path)


def load_manhattan_cache(
    cache_path: str,
    max_points: int = DEFAULT_MAX_PLOT_POINTS,
) -> pd.DataFrame:
    """Load cached Manhattan Plot dataframe from Parquet or pickle fallback, capped to max_points."""
    path = Path(cache_path)
    if not path.is_file():
        raise FileNotFoundError(f"Cache Manhattan Plot tidak ditemukan: {cache_path}")
    try:
        df = pd.read_parquet(path, columns=["CHR", "BP", "SNP", "LOG10_P"])
    except Exception:
        df = pd.read_pickle(path)[["CHR", "BP", "SNP", "LOG10_P"]]
    return cap_manhattan_dataframe(df, max_points=max_points)


_SEX_CHR_MAP = {"X": 23, "Y": 24, "MT": 25, "M": 25}
_CHR_PREFIX_RE = re.compile(r"^chr", re.IGNORECASE)
_ALNUM_PREFIX_CHR_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def _natural_chr_sort_key(label: str) -> list[tuple[int, int | str]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", label)
        if part != ""
    ]


def _build_chr_mapping(raw_tokens: set[str]) -> tuple[dict[str, int], dict[int, str]]:
    """Map raw chromosome tokens to distinct integer indices plus optional display labels."""
    token_to_idx: dict[str, int] = {}
    non_numeric: list[str] = []

    for tok in raw_tokens:
        if not tok:
            continue
        stripped = _CHR_PREFIX_RE.sub("", tok)
        if stripped.isdigit():
            token_to_idx[tok] = int(stripped)
        elif stripped.upper() in _SEX_CHR_MAP:
            token_to_idx[tok] = _SEX_CHR_MAP[stripped.upper()]
        else:
            non_numeric.append(tok)

    chr_labels: dict[int, str] = {}
    if not non_numeric:
        return token_to_idx, chr_labels

    prefix_matches = [_ALNUM_PREFIX_CHR_RE.fullmatch(tok) for tok in non_numeric]
    if all(m is not None for m in prefix_matches):
        prefixes = {m.group(1).lower() for m in prefix_matches if m is not None}
        nums = [int(m.group(2)) for m in prefix_matches if m is not None]
        if (
            len(prefixes) == 1
            and len(set(nums)) == len(non_numeric)
            and all(n >= 1 for n in nums)
            and not (set(nums) & set(token_to_idx.values()))
        ):
            for tok, num in zip(non_numeric, nums):
                token_to_idx[tok] = num
                chr_labels[num] = tok
            return token_to_idx, chr_labels

    next_idx = max(token_to_idx.values(), default=0) + 1
    for tok in sorted(non_numeric, key=_natural_chr_sort_key):
        token_to_idx[tok] = next_idx
        chr_labels[next_idx] = tok
        next_idx += 1

    return token_to_idx, chr_labels


def load_manhattan_chr_labels(cache_path: str) -> dict[int, str] | None:
    """Load chromosome display label map adjacent to the Manhattan cache if present."""
    labels_path = Path(cache_path).with_name("manhattan_chr_labels.json")
    if not labels_path.is_file():
        return None
    try:
        raw = json.loads(labels_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        parsed = {int(k): str(v) for k, v in raw.items()}
        return parsed or None
    except Exception as exc:
        logger.warning("Gagal membaca chr labels Manhattan (%s): %s", labels_path, exc)
        return None


def compute_assoc_summary_stats(
    assoc_file_path: str,
    genome_wide_threshold: float = 5e-8,
    chunksize: int = 200_000,
) -> dict[str, int | float]:
    """Compute total SNPs, genome-wide significant SNPs, and genomic inflation factor (lambda GC)."""
    header_cols = pd.read_csv(assoc_file_path, sep=r"\s+", nrows=0).columns.tolist()
    pvalue_col = _detect_pvalue_column(header_cols)

    total_snps = 0
    significant_snps = 0
    sampled_pvalues: list[np.ndarray] = []
    rng = np.random.default_rng(42)

    reader = pd.read_csv(
        assoc_file_path,
        sep=r"\s+",
        usecols=[pvalue_col],
        chunksize=chunksize,
    )
    for chunk in reader:
        p = pd.to_numeric(chunk[pvalue_col], errors="coerce").dropna().to_numpy(dtype=np.float64)
        if len(p) == 0:
            continue
        p = np.clip(p, 1e-300, 1.0)
        total_snps += int(len(p))
        significant_snps += int(np.sum(p <= genome_wide_threshold))
        if len(p) <= 10_000:
            sampled_pvalues.append(p)
        else:
            idx = rng.choice(len(p), size=10_000, replace=False)
            sampled_pvalues.append(p[idx])

    lambda_gc = 1.0
    if sampled_pvalues:
        all_p = np.concatenate(sampled_pvalues)
        median_p = float(np.median(all_p))
        median_p = float(np.clip(median_p, 1e-300, 1.0 - 1e-15))
        try:
            from scipy.stats import chi2  # type: ignore[import-untyped]

            chi2_median = float(chi2.isf(median_p, df=1))
            lambda_gc = round(float(chi2_median / 0.4549364231195728), 3)
        except Exception:
            # Fallback approximation using Ramanujan/Wilson-Hilferty if scipy is unavailable
            z = float(np.sqrt(max(-2.0 * np.log(median_p), 0.0)))
            chi2_approx = z * z
            lambda_gc = round(float(chi2_approx / 0.4549364231195728), 3)

    return {
        "total_snps": total_snps,
        "significant_snps": significant_snps,
        "lambda_gc": lambda_gc,
    }


def generate_manhattan_cache(
    assoc_file_path: str,
    output_cache_path: str,
    sig_threshold: float = 1e-3,
    downsample_keep_ratio: float = 0.005,
    max_points: int = DEFAULT_MAX_PLOT_POINTS,
    chunksize: int = 200_000,
    random_seed: int = 42,
) -> dict:
    """
    Membaca file `.assoc.txt` hasil GEMMA secara chunked, memfilter dan
    men-downsample SNP dengan p-value besar, lalu menyimpan hasilnya sebagai
    file Parquet ringan berkolom: CHR, BP, SNP, LOG10_P (maksimal `max_points` titik).
    """
    rng = np.random.default_rng(random_seed)

    header_cols = pd.read_csv(assoc_file_path, sep=r"\s+", nrows=0).columns.tolist()
    pvalue_col = _detect_pvalue_column(header_cols)
    chr_col = _detect_column(header_cols, CANDIDATE_CHR_COLUMNS, "kromosom (CHR)")
    rs_col = _detect_column(header_cols, CANDIDATE_RS_COLUMNS, "ID varian (SNP)")
    ps_col = _detect_column(header_cols, CANDIDATE_POS_COLUMNS, "posisi pasangan basa (POS/BP)")

    usecols = [chr_col, rs_col, ps_col, pvalue_col]

    kept_chunks: list[pd.DataFrame] = []
    observed_chr_tokens: set[str] = set()
    total_read = 0

    reader = pd.read_csv(
        assoc_file_path,
        sep=r"\s+",
        usecols=usecols,
        dtype={chr_col: str},
        chunksize=chunksize,
    )

    for chunk in reader:
        total_read += len(chunk)

        p = pd.to_numeric(chunk[pvalue_col], errors="coerce").fillna(1.0).to_numpy(dtype=np.float32)
        p = np.clip(p, a_min=np.finfo(np.float32).tiny, a_max=1.0)
        log10p = -np.log10(p)

        if len(chunk) <= max_points and total_read <= max_points:
            keep_mask = np.ones(len(chunk), dtype=bool)
        else:
            is_significant = p <= sig_threshold
            keep_mask = is_significant.copy()
            if (~is_significant).any():
                random_draw = rng.random(len(chunk))
                keep_mask |= (~is_significant) & (random_draw < downsample_keep_ratio)

        if not keep_mask.any():
            continue

        kept_raw_chr = chunk[chr_col].iloc[keep_mask].fillna("").astype(str).str.strip().to_numpy()
        observed_chr_tokens.update(tok for tok in kept_raw_chr if tok)

        bp_vals = pd.to_numeric(chunk[ps_col], errors="coerce").fillna(0).astype("uint32").to_numpy()
        snp_vals = chunk[rs_col].astype(str).to_numpy()

        kept_chunks.append(
            pd.DataFrame(
                {
                    "CHR_RAW": kept_raw_chr,
                    "BP": bp_vals[keep_mask],
                    "SNP": snp_vals[keep_mask],
                    "LOG10_P": log10p[keep_mask].astype("float32"),
                }
            )
        )

    if not kept_chunks:
        raise ValueError(
            "Tidak ada SNP tersisa setelah parsing -- periksa kembali file "
            "output GEMMA, kemungkinan format/kolom tidak sesuai."
        )

    result = pd.concat(kept_chunks, ignore_index=True)
    del kept_chunks

    token_to_idx, chr_labels = _build_chr_mapping(observed_chr_tokens)
    result["CHR"] = pd.Series(
        [token_to_idx.get(tok, 0) for tok in result["CHR_RAW"]],
        dtype="uint32",
    )
    result.drop(columns=["CHR_RAW"], inplace=True)

    result = cap_manhattan_dataframe(result, max_points=max_points)
    saved_path = _save_cache_df(result, output_cache_path)

    present_chrs = set(result["CHR"].tolist())
    active_chr_labels = {int(k): v for k, v in chr_labels.items() if k in present_chrs}
    labels_path = Path(saved_path).with_name("manhattan_chr_labels.json")
    labels_path.write_text(
        json.dumps({str(k): v for k, v in sorted(active_chr_labels.items())}, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = {
        "total_snps_read": int(total_read),
        "total_snps_plotted": int(len(result)),
        "kept_rows": int(len(result)),
        "output_path": saved_path,
    }
    logger.info(
        "Manhattan cache dibuat: %s | %s SNP dibaca -> %s SNP ditampilkan (%.2f%%)",
        saved_path,
        total_read,
        len(result),
        (len(result) / total_read * 100) if total_read else 0,
    )
    return summary
