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
4. Downsampling dilakukan PER CHUNK (bukan setelah full load ke DataFrame
   raksasa), sehingga puncak penggunaan memori tetap terbatas pada ukuran
   satu chunk, bukan ukuran keseluruhan file.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Nama kolom p-value pada output GEMMA berbeda tergantung model yang dipakai:
#   -lmm 1/2/3/4 -> p_wald / p_lrt / p_score (bisa lebih dari satu sekaligus)
#   -lm 1        -> p_wald
# Kita cek beberapa kandidat secara berurutan dan pakai yang pertama ditemukan.
CANDIDATE_PVALUE_COLUMNS = ["p_wald", "p_lrt", "p_score"]


def _detect_pvalue_column(header_cols: list[str]) -> str:
    for col in CANDIDATE_PVALUE_COLUMNS:
        if col in header_cols:
            return col
    raise ValueError(
        f"Tidak menemukan kolom p-value yang dikenali pada output GEMMA. "
        f"Kolom yang tersedia: {header_cols}"
    )


def generate_manhattan_cache(
    assoc_file_path: str,
    output_cache_path: str,
    sig_threshold: float = 0.1,
    downsample_keep_ratio: float = 0.02,
    chunksize: int = 200_000,
    random_seed: int = 42,
) -> dict:
    """
    Membaca file `.assoc.txt` hasil GEMMA secara chunked, memfilter dan
    men-downsample SNP dengan p-value besar, lalu menyimpan hasilnya sebagai
    file Parquet ringan berkolom: CHR, BP, SNP, LOG10_P.

    Parameters
    ----------
    sig_threshold : float
        Semua SNP dengan p-value <= threshold ini SELALU disimpan penuh,
        karena titik-titik inilah yang paling relevan secara biologis
        (kandidat sinyal asosiasi).
    downsample_keep_ratio : float
        Proporsi SNP dengan p-value > threshold yang tetap dipertahankan,
        semata-mata untuk menjaga bentuk visual sebaran titik pada
        Manhattan Plot -- bukan untuk keperluan inferensi statistik.
    chunksize : int
        Jumlah baris yang dibaca per iterasi. 200_000 baris umumnya aman
        untuk worker container dengan RAM 1-2 GB; turunkan jika perlu.
    random_seed : int
        Agar hasil downsampling reproducible antar-run untuk file yang sama.

    Returns
    -------
    dict: {"total_snps_read", "total_snps_plotted", "output_path"}
    """
    rng = np.random.default_rng(random_seed)

    # Baca header lebih dulu (nrows=0) untuk mendeteksi nama kolom p-value
    # tanpa perlu membaca isi file sama sekali.
    header_cols = pd.read_csv(assoc_file_path, sep="\t", nrows=0).columns.tolist()
    pvalue_col = _detect_pvalue_column(header_cols)

    usecols = ["chr", "rs", "ps", pvalue_col]
    dtype_map = {
        "chr": "uint8",  # konvensi PLINK/GEMMA: 1-22 autosom, 23=X, 24=Y, dst.
        "rs": "string",
        "ps": "uint32",  # posisi basepair, cukup unsigned 32-bit
        pvalue_col: "float32",  # presisi float32 sudah cukup untuk -log10(p)
    }

    kept_chunks: list[pd.DataFrame] = []
    total_read = 0

    reader = pd.read_csv(
        assoc_file_path,
        sep="\t",
        usecols=usecols,
        dtype=dtype_map,
        chunksize=chunksize,
        engine="c",
    )

    for chunk in reader:
        total_read += len(chunk)

        # Hindari log10(0) untuk p-value yang underflow ke 0 pada float32
        p = chunk[pvalue_col].to_numpy()
        p = np.clip(p, a_min=np.finfo(np.float32).tiny, a_max=1.0)
        log10p = -np.log10(p)

        is_significant = p <= sig_threshold
        keep_mask = is_significant.copy()

        # Downsampling acak untuk SNP non-signifikan agar bentuk sebaran
        # titik pada plot tetap representatif tanpa mengirim jutaan titik
        # yang secara visual hanya akan menumpuk di bagian bawah plot.
        if (~is_significant).any():
            random_draw = rng.random(len(chunk))
            keep_mask |= (~is_significant) & (random_draw < downsample_keep_ratio)

        if not keep_mask.any():
            continue

        kept_chunks.append(
            pd.DataFrame(
                {
                    "CHR": chunk["chr"].to_numpy()[keep_mask],
                    "BP": chunk["ps"].to_numpy()[keep_mask],
                    "SNP": chunk["rs"].to_numpy()[keep_mask],
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
    del kept_chunks  # bebaskan memori chunk sementara secepatnya

    # Urutkan berdasarkan kromosom & posisi agar Manhattan Plot tergambar
    # rapi dari kiri (CHR 1) ke kanan (CHR terakhir) tanpa perlu di-sort ulang
    # di sisi frontend.
    result.sort_values(["CHR", "BP"], inplace=True, kind="mergesort")
    result.reset_index(drop=True, inplace=True)

    # Parquet dipilih ketimbang JSON/CSV sebagai format cache karena: ukuran
    # file jauh lebih kecil (kolom + kompresi), dan load kembali oleh
    # endpoint FastAPI jauh lebih cepat dibanding parse ulang file teks.
    result.to_parquet(output_cache_path, index=False, compression="snappy")

    summary = {
        "total_snps_read": int(total_read),
        "total_snps_plotted": int(len(result)),
        "output_path": output_cache_path,
    }
    logger.info(
        "Manhattan cache dibuat: %s | %s SNP dibaca -> %s SNP ditampilkan (%.2f%%)",
        output_cache_path,
        total_read,
        len(result),
        (len(result) / total_read * 100) if total_read else 0,
    )
    return summary
