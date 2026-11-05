"""
Service normalisasi hasil asosiasi GWAS (Fase 4A/4B/4C).

Menghasilkan file TSV kanonik 'gwas_results.normalized.tsv' dengan header seragam:
chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn

Mendukung input dari:
1. GEMMA (.assoc.txt): kolom chr, rs, ps, n_miss, allele1, allele0, af, beta, se, p_wald.
2. PLINK 2 (.glm.linear / .glm.logistic): filter TEST == ADD.
3. GAPIT (FarmCPU / BLINK CSV): kolom SNP, Chromosome, Position, P.value, maf, nobs, Effect.
4. Python Fallback TSV.
"""

from __future__ import annotations

import csv
import gzip
from pathlib import Path
from typing import Any, Generator


CANONICAL_COLUMNS = ["chr", "pos", "rs", "a1", "a0", "af", "beta", "se", "p", "n"]


def detect_association_format(header: list[str]) -> str:
    """Mendeteksi jenis format output tool GWAS berdasarkan baris header."""
    header_lower = [h.lower() for h in header]
    header_raw = set(header)

    if "p_wald" in header_lower or "allele1" in header_lower or "ps" in header_lower:
        return "gemma"
    if "#chrom" in header_lower or "test" in header_lower or "a1_freq" in header_lower:
        return "plink2"
    if "p.value" in header_raw or "effect" in header_raw or "nobs" in header_raw:
        return "gapit"
    if "pos" in header_lower and "p" in header_lower and "beta" in header_lower:
        return "canonical"

    return "unknown"


def iter_normalized_rows(source_file: Path | str) -> Generator[dict[str, Any], None, None]:
    """
    Generator streaming yang membaca file asosiasi mentah dan mengembalikan
    kamus baris kanonik yang telah divalidasi.
    """
    path = Path(source_file)
    if not path.is_file():
        raise FileNotFoundError(f"File hasil asosiasi tidak ditemukan: {path}")

    open_fn = gzip.open if path.name.endswith(".gz") else open
    with open_fn(path, "rt", encoding="utf-8", errors="replace", newline="\n") as f:
        # Deteksi delimiter (tab, spasi, koma)
        first_line = f.readline().replace("\r", "")
        if not first_line:
            return

        delimiter = "\t"
        if "," in first_line and "\t" not in first_line:
            delimiter = ","
        elif "\t" not in first_line and len(first_line.split()) > 4:
            delimiter = None  # whitespace split

        header = first_line.strip().split(delimiter) if delimiter else first_line.strip().split()
        header = [h.strip().strip('"') for h in header]
        fmt = detect_association_format(header)

        # Buat mapping indeks kolom
        idx_map: dict[str, int] = {col: i for i, col in enumerate(header)}
        lower_map: dict[str, int] = {col.lower(): i for i, col in enumerate(header)}

        for raw_line in f:
            line = raw_line.replace("\r", "")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.strip().split(delimiter) if delimiter else line.strip().split()
            if len(parts) < 3:
                continue

            parts = [p.strip().strip('"') for p in parts]

            try:
                row_out: dict[str, Any] = {}

                if fmt == "gemma":
                    # GEMMA: chr rs ps n_miss allele1 allele0 af beta se p_wald
                    p_chr = parts[lower_map.get("chr", 0)]
                    p_pos = parts[lower_map.get("ps", 2)]
                    p_rs = parts[lower_map.get("rs", 1)]
                    p_a1 = parts[lower_map.get("allele1", 4)]
                    p_a0 = parts[lower_map.get("allele0", 5)]
                    p_af = parts[lower_map.get("af", 6)]
                    p_beta = parts[lower_map.get("beta", 7)]
                    p_se = parts[lower_map.get("se", 8)]

                    # Prioritas kolom p: p_wald -> p_lrt -> p_score
                    p_col = (
                        lower_map.get("p_wald")
                        or lower_map.get("p_lrt")
                        or lower_map.get("p_score")
                        or (len(parts) - 1)
                    )
                    p_val_raw = parts[p_col]

                    p_n = parts[lower_map.get("n_miss", 3)] if "n_miss" in lower_map else "NA"

                    p_val = float(p_val_raw)
                    if p_val <= 0 or p_val > 1.0 or not (p_val == p_val):
                        continue

                    row_out = {
                        "chr": str(p_chr).lstrip("chrCHR"),
                        "pos": int(float(p_pos)),
                        "rs": str(p_rs),
                        "a1": str(p_a1),
                        "a0": str(p_a0),
                        "af": round(float(p_af), 5) if p_af not in {"NA", "."} else "NA",
                        "beta": round(float(p_beta), 6) if p_beta not in {"NA", "."} else 0.0,
                        "se": round(float(p_se), 6) if p_se not in {"NA", "."} else 0.0,
                        "p": p_val,
                        "n": int(float(p_n)) if p_n not in {"NA", "."} else "NA",
                    }

                elif fmt == "plink2":
                    # PLINK 2 GLM: #CHROM POS ID REF ALT A1 AX A1_FREQ N_OBS BETA SE ... P
                    test_col = lower_map.get("test")
                    if test_col is not None and parts[test_col] != "ADD":
                        continue

                    p_chr = parts[lower_map.get("#chrom", lower_map.get("chrom", 0))]
                    p_pos = parts[lower_map.get("pos", 1)]
                    p_rs = parts[lower_map.get("id", 2)]
                    p_ref = parts[lower_map.get("ref", 3)]
                    p_alt = parts[lower_map.get("alt", 4)]
                    p_a1 = parts[lower_map.get("a1", 5)]
                    p_a0 = p_ref if p_a1 == p_alt else p_alt
                    p_af = parts[lower_map.get("a1_freq", -1)] if "a1_freq" in lower_map else "NA"
                    p_n = parts[lower_map.get("n_obs", -1)] if "n_obs" in lower_map else "NA"
                    p_beta = parts[lower_map.get("beta", -1)] if "beta" in lower_map else "0.0"
                    p_se = parts[lower_map.get("se", -1)] if "se" in lower_map else "0.0"
                    p_val_raw = parts[lower_map.get("p", -1)]

                    p_val = float(p_val_raw)
                    if p_val <= 0 or p_val > 1.0 or not (p_val == p_val):
                        continue

                    row_out = {
                        "chr": str(p_chr).lstrip("chrCHR"),
                        "pos": int(float(p_pos)),
                        "rs": str(p_rs),
                        "a1": str(p_a1),
                        "a0": str(p_a0),
                        "af": round(float(p_af), 5) if p_af not in {"NA", "."} else "NA",
                        "beta": round(float(p_beta), 6) if p_beta not in {"NA", "."} else 0.0,
                        "se": round(float(p_se), 6) if p_se not in {"NA", "."} else 0.0,
                        "p": p_val,
                        "n": int(float(p_n)) if p_n not in {"NA", "."} else "NA",
                    }

                elif fmt == "gapit":
                    # GAPIT: SNP Chromosome Position P.value maf nobs Effect
                    p_rs = parts[idx_map.get("SNP", 0)]
                    p_chr = parts[idx_map.get("Chromosome", 1)]
                    p_pos = parts[idx_map.get("Position", 2)]
                    p_val_raw = parts[idx_map.get("P.value", 3)]
                    p_af = parts[idx_map.get("maf", -1)] if "maf" in idx_map else "NA"
                    p_n = parts[idx_map.get("nobs", -1)] if "nobs" in idx_map else "NA"
                    p_beta = parts[idx_map.get("Effect", -1)] if "Effect" in idx_map else "0.0"

                    p_val = float(p_val_raw)
                    if p_val <= 0 or p_val > 1.0 or not (p_val == p_val):
                        continue

                    row_out = {
                        "chr": str(p_chr).lstrip("chrCHR"),
                        "pos": int(float(p_pos)),
                        "rs": str(p_rs),
                        "a1": "A1",
                        "a0": "A0",
                        "af": round(float(p_af), 5) if p_af not in {"NA", "."} else "NA",
                        "beta": round(float(p_beta), 6) if p_beta not in {"NA", "."} else 0.0,
                        "se": 0.0,
                        "p": p_val,
                        "n": int(float(p_n)) if p_n not in {"NA", "."} else "NA",
                    }

                else:
                    # Format kanonik default
                    p_chr = parts[lower_map.get("chr", 0)]
                    p_pos = parts[lower_map.get("pos", 1)]
                    p_rs = parts[lower_map.get("rs", 2)]
                    p_a1 = parts[lower_map.get("a1", 3)] if "a1" in lower_map else "A1"
                    p_a0 = parts[lower_map.get("a0", 4)] if "a0" in lower_map else "A0"
                    p_af = parts[lower_map.get("af", 5)] if "af" in lower_map else "NA"
                    p_beta = parts[lower_map.get("beta", 6)] if "beta" in lower_map else "0.0"
                    p_se = parts[lower_map.get("se", 7)] if "se" in lower_map else "0.0"
                    p_val_raw = parts[lower_map.get("p", 8)]
                    p_n = parts[lower_map.get("n", 9)] if "n" in lower_map else "NA"

                    p_val = float(p_val_raw)
                    if p_val <= 0 or p_val > 1.0 or not (p_val == p_val):
                        continue

                    row_out = {
                        "chr": str(p_chr).lstrip("chrCHR"),
                        "pos": int(float(p_pos)),
                        "rs": str(p_rs),
                        "a1": str(p_a1),
                        "a0": str(p_a0),
                        "af": round(float(p_af), 5) if p_af not in {"NA", "."} else "NA",
                        "beta": round(float(p_beta), 6) if p_beta not in {"NA", "."} else 0.0,
                        "se": round(float(p_se), 6) if p_se not in {"NA", "."} else 0.0,
                        "p": p_val,
                        "n": int(float(p_n)) if p_n not in {"NA", "."} else "NA",
                    }

                yield row_out

            except (ValueError, IndexError):
                continue


def normalize_association_results(source_file: Path | str, target_tsv: Path | str) -> int:
    """
    Menulis hasil asosiasi ternormalisasi kanonik ke target_tsv.
    Mengembalikan jumlah baris varian yang berhasil ditulis.
    """
    target = Path(target_tsv)
    target.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with target.open("w", encoding="utf-8", newline="") as out_f:
        writer = csv.writer(out_f, delimiter="\t")
        writer.writerow(CANONICAL_COLUMNS)

        for row in iter_normalized_rows(source_file):
            writer.writerow([
                row["chr"],
                row["pos"],
                row["rs"],
                row["a1"],
                row["a0"],
                row["af"],
                row["beta"],
                row["se"],
                row["p"],
                row["n"],
            ])
            count += 1

    return count
