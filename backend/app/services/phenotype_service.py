"""Phenotype data parsing, validation, inspection, and reconciliation with VCF genotypes."""

from __future__ import annotations

import csv
import gzip
from pathlib import Path
import re
import shutil
from typing import Any
import numpy as np
import pandas as pd


def read_vcf_sample_ids(vcf_path: Path | str) -> list[str]:
    """Extract sample IDs from VCF header using bcftools query -l or fallback Python parsing."""
    path = Path(vcf_path)
    if not path.is_file():
        raise FileNotFoundError(f"File VCF tidak ditemukan: {vcf_path}")

    # Check if bcftools is available
    bcftools = shutil.which("bcftools")
    if bcftools:
        try:
            import subprocess
            res = subprocess.run(
                [bcftools, "query", "-l", str(path)],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            samples = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            if samples:
                return samples
        except Exception:
            pass

    # Fallback: stream VCF header until #CHROM line
    open_fn = gzip.open if (path.name.endswith(".gz") or path.suffix == ".gz") else open
    with open_fn(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#CHROM"):
                parts = line.strip().split("\t")
                if len(parts) > 9:
                    return parts[9:]
                return []
            if not line.startswith("#"):
                break
    return []


def load_phenotype_table(path: Path | str) -> pd.DataFrame:
    """Load phenotype table, auto-detecting delimiters (tab, comma, whitespace) and stripping whitespace."""
    target_path = Path(path)
    if not target_path.is_file():
        raise FileNotFoundError(f"File fenotipe tidak ditemukan: {target_path}")

    with target_path.open("r", encoding="utf-8", errors="replace") as f:
        first_line = f.readline()
        second_line = f.readline()

    # Determine delimiter
    sample_text = first_line + "\n" + second_line
    sep = r"\s+"
    if "\t" in first_line:
        sep = "\t"
    elif "," in first_line:
        sep = ","

    df = pd.read_csv(
        target_path,
        sep=sep,
        dtype=str,
        engine="python" if sep == r"\s+" else "c",
        skipinitialspace=True,
    )
    # Strip whitespace from column names and string cells
    df.columns = [str(c).strip() for c in df.columns]
    for col in df.columns:
        df[col] = df[col].astype(str).str.strip()
    return df


def inspect_phenotype(
    vcf_ids: list[str],
    df: pd.DataFrame,
    trait: str,
    id_column: str = "IID",
    fid_column: str | None = "FID",
    missing_tokens: list[str] | None = None,
) -> dict[str, Any]:
    """Inspect and reconcile VCF sample IDs and phenotype data according to Phase 1 specs."""
    if missing_tokens is None:
        missing_tokens = ["", ".", "NA", "-9", "nan", "null"]
    missing_set = set(t.upper() for t in missing_tokens)

    warnings: list[str] = []
    blocking_errors: list[str] = []

    # Match ID column case-insensitively if needed
    matched_id_col = None
    for c in df.columns:
        if c.upper() == id_column.upper():
            matched_id_col = c
            break

    if not matched_id_col:
        # Check if first or second column could be IID
        if len(df.columns) >= 2 and df.columns[1].upper() in {"IID", "ID", "SAMPLE", "INDIVIDUAL"}:
            matched_id_col = df.columns[1]
        elif len(df.columns) >= 1:
            matched_id_col = df.columns[0]
            warnings.append(f"Kolom identitas '{id_column}' tidak ditemukan; menggunakan kolom pertama '{matched_id_col}'.")
        else:
            blocking_errors.append(f"File fenotipe tidak memiliki kolom identitas '{id_column}'.")

    # Match trait column
    matched_trait_col = None
    if trait:
        for c in df.columns:
            if c.upper() == trait.upper():
                matched_trait_col = c
                break
        if not matched_trait_col:
            clean_trait = re.sub(r"[\s_-]+", "", trait).upper()
            for c in df.columns:
                if re.sub(r"[\s_-]+", "", c).upper() == clean_trait:
                    matched_trait_col = c
                    break

    if not matched_trait_col:
        # Check candidate numeric columns (exclude ID and FID)
        candidate_cols = [c for c in df.columns if c not in {matched_id_col, fid_column}]
        numeric_candidates = []
        for c in candidate_cols:
            vals = pd.to_numeric(df[c], errors="coerce")
            if vals.notna().sum() >= 3:
                numeric_candidates.append(c)

        is_placeholder_trait = not trait or trait.strip().upper() in {"PHENO1", "TRAIT", "PHENOTYPE", "DEFAULT"}

        if numeric_candidates and is_placeholder_trait:
            matched_trait_col = numeric_candidates[0]
            warnings.append(
                f"Kolom trait target '{trait or 'default'}' otomatis dialihkan ke kolom numerik pertama '{matched_trait_col}'."
            )
        elif len(candidate_cols) >= 1 and is_placeholder_trait:
            matched_trait_col = candidate_cols[0]
            warnings.append(f"Trait tidak dispesifikasikan; menggunakan kolom '{matched_trait_col}'.")
        else:
            if trait and not is_placeholder_trait:
                blocking_errors.append(f"Kolom trait '{trait}' tidak ditemukan dalam file fenotipe.")
            else:
                blocking_errors.append("Kolom trait belum dipilih dan tidak ditemukan kolom numerik valid dalam file fenotipe.")

    # VCF ID checks
    vcf_ids_clean = [str(x).strip() for x in vcf_ids]
    vcf_empty_count = sum(1 for x in vcf_ids_clean if not x)
    vcf_seen = set()
    vcf_duplicates = []
    for x in vcf_ids_clean:
        if x in vcf_seen and x not in vcf_duplicates:
            vcf_duplicates.append(x)
        vcf_seen.add(x)

    if vcf_duplicates:
        warnings.append(f"Terdapat {len(vcf_duplicates)} ID duplikat pada file VCF.")

    # Phenotype ID checks
    pheno_ids = []
    pheno_empty_count = 0
    pheno_duplicates = []
    pheno_seen = set()

    if matched_id_col and matched_id_col in df.columns:
        for val in df[matched_id_col]:
            s_val = str(val).strip()
            if not s_val or s_val.upper() in missing_set:
                pheno_empty_count += 1
            else:
                if s_val in pheno_seen and s_val not in pheno_duplicates:
                    pheno_duplicates.append(s_val)
                pheno_seen.add(s_val)
            pheno_ids.append(s_val)

    if pheno_duplicates:
        warnings.append(f"Terdapat {len(pheno_duplicates)} ID duplikat pada file fenotipe.")

    # Value validation on trait column
    valid_numeric_map: dict[str, float] = {}
    non_numeric_examples: list[dict[str, str]] = []
    missing_count = 0

    if matched_trait_col and matched_trait_col in df.columns and matched_id_col:
        for _, row in df.iterrows():
            iid = str(row[matched_id_col]).strip()
            raw_val = str(row[matched_trait_col]).strip()
            if not raw_val or raw_val.upper() in missing_set:
                missing_count += 1
                continue
            try:
                num_val = float(raw_val)
                # Keep first occurrence if duplicate
                if iid and iid not in valid_numeric_map:
                    valid_numeric_map[iid] = num_val
            except ValueError:
                if len(non_numeric_examples) < 10:
                    non_numeric_examples.append({"iid": iid, "value": raw_val})

    # Sample intersection (Matched VCF sample IDs that also have a valid numeric phenotype)
    vcf_set = set(vcf_ids_clean)
    valid_pheno_ids_set = set(valid_numeric_map.keys())

    matched_ids = [s for s in vcf_ids_clean if s in valid_pheno_ids_set]
    only_in_vcf = [s for s in vcf_ids_clean if s not in pheno_seen]
    only_in_pheno = [s for s in pheno_seen if s not in vcf_set]

    n_matched = len(matched_ids)
    if n_matched < 3:
        blocking_errors.append(f"Jumlah sampel yang cocok dan memiliki fenotipe valid ({n_matched}) terlalu sedikit (< 3).")
    elif n_matched < 30:
        warnings.append(f"Jumlah sampel yang cocok ({n_matched}) relatif sedikit (< 30) untuk analisis GWAS yang andal.")

    if len(vcf_ids_clean) > 0 and len(only_in_vcf) / len(vcf_ids_clean) > 0.20:
        pct = round(len(only_in_vcf) / len(vcf_ids_clean) * 100, 1)
        warnings.append(f"{pct}% sampel VCF ({len(only_in_vcf)} sampel) tidak ditemukan di file fenotipe.")

    # Descriptive statistics on matched values
    matched_vals = [valid_numeric_map[iid] for iid in matched_ids]
    stats_dict: dict[str, Any] = {}
    hist_dict: dict[str, Any] = {"edges": [], "counts": []}
    boxplot_dict: dict[str, Any] = {
        "q1": 0.0,
        "median": 0.0,
        "q3": 0.0,
        "whisker_low": 0.0,
        "whisker_high": 0.0,
        "outliers": [],
    }

    if matched_vals:
        arr = np.array(matched_vals, dtype=np.float64)
        mean_val = float(np.mean(arr))
        median_val = float(np.median(arr))
        sd_val = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
        min_val = float(np.min(arr))
        max_val = float(np.max(arr))
        q1_val = float(np.percentile(arr, 25))
        q3_val = float(np.percentile(arr, 75))
        iqr = q3_val - q1_val

        # Skewness
        if sd_val > 0 and len(arr) > 2:
            skew_val = float(np.mean(((arr - mean_val) / sd_val) ** 3))
        else:
            skew_val = 0.0

        stats_dict = {
            "N": int(len(arr)),
            "mean": round(mean_val, 4),
            "median": round(median_val, 4),
            "sd": round(sd_val, 4),
            "min": round(min_val, 4),
            "q1": round(q1_val, 4),
            "q3": round(q3_val, 4),
            "max": round(max_val, 4),
            "skewness": round(skew_val, 4),
        }

        # Histogram: Freedman-Diaconis rule
        if iqr > 0 and len(arr) > 1:
            h = 2 * iqr / (len(arr) ** (1 / 3))
            n_bins = int(np.clip(np.ceil((max_val - min_val) / max(h, 1e-6)), 10, 50))
        else:
            n_bins = 10
        counts, edges = np.histogram(arr, bins=n_bins)
        hist_dict = {
            "edges": [round(float(e), 4) for e in edges],
            "counts": [int(c) for c in counts],
        }

        # Boxplot with outliers
        low_limit = q1_val - 1.5 * iqr
        high_limit = q3_val + 1.5 * iqr
        whisker_low = float(np.min(arr[arr >= low_limit])) if np.any(arr >= low_limit) else min_val
        whisker_high = float(np.max(arr[arr <= high_limit])) if np.any(arr <= high_limit) else max_val

        outliers = []
        for iid in matched_ids:
            v = valid_numeric_map[iid]
            if v < low_limit or v > high_limit:
                outliers.append({"iid": iid, "value": round(float(v), 4)})

        boxplot_dict = {
            "q1": round(q1_val, 4),
            "median": round(median_val, 4),
            "q3": round(q3_val, 4),
            "whisker_low": round(whisker_low, 4),
            "whisker_high": round(whisker_high, 4),
            "outliers": outliers[:50],
        }

    return {
        "trait": matched_trait_col or trait,
        "id_column": matched_id_col or id_column,
        "n_vcf_samples": len(vcf_ids_clean),
        "n_pheno_rows": len(df),
        "n_valid_pheno": len(valid_numeric_map),
        "n_matched": n_matched,
        "vcf_empty_ids": vcf_empty_count,
        "vcf_duplicate_ids": vcf_duplicates[:50],
        "pheno_empty_ids": pheno_empty_count,
        "pheno_duplicate_ids": pheno_duplicates[:50],
        "only_in_vcf_count": len(only_in_vcf),
        "only_in_vcf_sample": only_in_vcf[:50],
        "only_in_pheno_count": len(only_in_pheno),
        "only_in_pheno_sample": only_in_pheno[:50],
        "non_numeric_count": len(non_numeric_examples),
        "non_numeric_examples": non_numeric_examples,
        "missing_count": missing_count,
        "stats": stats_dict,
        "histogram": hist_dict,
        "boxplot": boxplot_dict,
        "warnings": warnings,
        "blocking_errors": blocking_errors,
        "matched_ids": matched_ids,
    }

