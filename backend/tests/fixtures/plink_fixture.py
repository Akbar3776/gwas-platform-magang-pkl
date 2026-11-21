"""Pure-Python PLINK binary fixture builder for test suites."""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

import numpy as np

from app.schemas.pipeline import PopulationType, QCPipelineConfig
from app.services.qc_service import _is_gzip_file
from app.tasks.imputation_tasks import MAX_32BIT_POS


def encode_plink_bed_row(genotypes: list[int]) -> bytes:
    """
    Encode baris genotipe (0=HomRef, 1=Het, 2=HomAlt, -1=Missing)
    menjadi format biner PLINK (.bed) SNP-major 2-bit per individu.
    PLINK codes:
    00: Hom 1/1 (2 copies of A1)
    01: Missing genotype
    10: Het 0/1 (1 copy of A1)
    11: Hom 0/0 (0 copies of A1)
    """
    mapping = {0: 3, 1: 2, 2: 0, -1: 1}
    out = bytearray((len(genotypes) + 3) // 4)
    for idx, g in enumerate(genotypes):
        code = mapping.get(g, 1)
        byte_idx = idx // 4
        bit_shift = (idx % 4) * 2
        out[byte_idx] |= (code << bit_shift)
    return bytes(out)


def _make_hist(
    values: list[float], bins: int = 10, range_min: float = 0.0, range_max: float = 1.0
) -> dict[str, list]:
    if not values:
        return {"edges": [range_min, range_max], "counts": [0]}
    counts, edges = np.histogram(values, bins=bins, range=(range_min, range_max))
    return {
        "edges": [round(float(e), 4) for e in edges],
        "counts": [int(c) for c in counts],
    }


def run_python_fallback_qc(
    input_vcf: str,
    output_prefix: Path,
    cfg: QCPipelineConfig,
    keep_ids: str | None = None,
    pheno_path: str | None = None,
    trait: str | None = None,
    log_file: Path | None = None,
    initial_overflow_removed: int = 0,
) -> dict[str, Any]:
    """
    Implementasi Python murni untuk membangun berkas .bed/.bim/.fam/.vcf.gz sintetis
    pada pengujian saat CLI PLINK tidak dijalankan.
    """
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)

    # 1. Baca keep_ids bila disediakan
    allowed_iids: set[str] | None = None
    if keep_ids and Path(keep_ids).is_file():
        allowed_iids = set()
        for line in Path(keep_ids).read_text(encoding="utf-8").splitlines():
            parts = line.strip().split()
            if parts:
                allowed_iids.add(parts[-1])

    # 2. Baca fenotipe mapping bila ada
    pheno_map: dict[str, str] = {}
    if pheno_path and Path(pheno_path).is_file():
        try:
            lines = Path(pheno_path).read_text(encoding="utf-8").splitlines()
            if lines:
                header = lines[0].strip().split("\t") if "\t" in lines[0] else lines[0].strip().split()
                header = [c.strip() for c in header if c.strip()]
                target_trait = trait.strip() if (trait and isinstance(trait, str)) else None
                if len(header) == 3 and (not target_trait or target_trait not in header):
                    target_trait = header[2]
                elif target_trait:
                    for h in header[2:]:
                        if h.lower() == target_trait.lower():
                            target_trait = h
                            break
                if target_trait and target_trait in header:
                    t_idx = header.index(target_trait)
                    iid_idx = header.index("IID") if "IID" in header else 1
                    for line in lines[1:]:
                        cols = line.strip().split("\t") if "\t" in line else line.strip().split()
                        cols = [c.strip() for c in cols if c.strip()]
                        if len(cols) > max(iid_idx, t_idx):
                            pheno_map[cols[iid_idx]] = cols[t_idx]
        except Exception:
            pass

    # 3. Parse input VCF
    src = Path(input_vcf)
    open_fn = gzip.open if _is_gzip_file(src) else open

    raw_samples: list[str] = []
    raw_variants: list[dict[str, Any]] = []

    with open_fn(src, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#CHROM"):
                cols = line.strip().split("\t")
                if len(cols) > 9:
                    raw_samples = cols[9:]
                continue
            if line.startswith("#") or not line.strip():
                continue

            parts = line.strip().split("\t")
            if len(parts) < 10:
                continue

            chrom, pos, rsid, ref, alt = parts[0], parts[1], parts[2], parts[3], parts[4]
            gts = parts[9:]
            raw_variants.append({
                "chrom": chrom,
                "pos": pos,
                "rsid": rsid,
                "ref": ref,
                "alt": alt,
                "gts": gts,
            })

    # Tentukan sampel yang diikutsertakan
    if allowed_iids is not None:
        sample_indices = [i for i, s in enumerate(raw_samples) if s in allowed_iids]
    else:
        sample_indices = list(range(len(raw_samples)))

    active_samples = [raw_samples[i] for i in sample_indices]
    n_samples_before = len(active_samples)
    n_snps_before = len(raw_variants)

    # Statistik sebelum QC
    before_mafs: list[float] = []
    before_miss: list[float] = []
    for var in raw_variants:
        parsed_gts = []
        for s_idx in sample_indices:
            token = var["gts"][s_idx].split(":")[0] if s_idx < len(var["gts"]) else "./."
            if token in {"0/0", "0|0"}:
                parsed_gts.append(0)
            elif token in {"0/1", "1/0", "0|1", "1|0"}:
                parsed_gts.append(1)
            elif token in {"1/1", "1|1"}:
                parsed_gts.append(2)
            else:
                parsed_gts.append(-1)
        valid = [g for g in parsed_gts if g >= 0]
        v_miss = (len(parsed_gts) - len(valid)) / max(len(parsed_gts), 1)
        before_miss.append(v_miss)
        if valid:
            af = sum(valid) / (2.0 * len(valid))
            before_mafs.append(min(af, 1.0 - af))
        else:
            before_mafs.append(0.0)

    # Filter varian & sampel
    removed_counts = {
        "overflow_32bit": max(0, int(initial_overflow_removed)),
        "non_snp": 0,
        "multiallelic": 0,
        "geno": 0,
        "maf": 0,
        "hwe": 0,
        "mind": 0,
    }

    # Step A: Filter varian overflow 32-bit, non-SNP, dan multi-alelik
    step1_variants: list[dict[str, Any]] = []
    acgt_set = {"A", "C", "G", "T", "a", "c", "g", "t"}
    for var in raw_variants:
        try:
            pos_int = int(var["pos"])
        except ValueError:
            removed_counts["overflow_32bit"] += 1
            continue
        if pos_int <= 0 or pos_int > MAX_32BIT_POS:
            removed_counts["overflow_32bit"] += 1
            continue

        ref = var["ref"].upper()
        alt = var["alt"].upper()
        if cfg.max_alleles == 2 and "," in alt:
            removed_counts["multiallelic"] += 1
            continue
        if cfg.snps_only and (ref not in acgt_set or alt not in acgt_set or len(ref) != 1 or len(alt) != 1):
            removed_counts["non_snp"] += 1
            continue

        gts_arr: list[int] = []
        for s_idx in sample_indices:
            token = var["gts"][s_idx].split(":")[0] if s_idx < len(var["gts"]) else "./."
            if token in {"0/0", "0|0"}:
                gts_arr.append(0)
            elif token in {"0/1", "1/0", "0|1", "1|0"}:
                gts_arr.append(1)
            elif token in {"1/1", "1|1"}:
                gts_arr.append(2)
            else:
                gts_arr.append(-1)

        valid = [g for g in gts_arr if g >= 0]
        miss_rate = (len(gts_arr) - len(valid)) / max(len(gts_arr), 1)
        if cfg.geno is not None and miss_rate > cfg.geno:
            removed_counts["geno"] += 1
            continue

        if not valid:
            removed_counts["maf"] += 1
            continue
        af = sum(valid) / (2.0 * len(valid))
        maf = min(af, 1.0 - af)
        if maf < cfg.maf:
            removed_counts["maf"] += 1
            continue

        if cfg.population_type == PopulationType.OUTCROSS and cfg.hwe is not None and len(valid) >= 5:
            n_aa = sum(1 for g in valid if g == 0)
            n_ab = sum(1 for g in valid if g == 1)
            n_bb = sum(1 for g in valid if g == 2)
            n_tot = len(valid)
            p_a = (2 * n_aa + n_ab) / (2.0 * n_tot)
            p_b = 1.0 - p_a
            exp_aa = (p_a ** 2) * n_tot
            exp_ab = (2 * p_a * p_b) * n_tot
            exp_bb = (p_b ** 2) * n_tot
            chi2_stat = 0.0
            if exp_aa > 0:
                chi2_stat += ((n_aa - exp_aa) ** 2) / exp_aa
            if exp_ab > 0:
                chi2_stat += ((n_ab - exp_ab) ** 2) / exp_ab
            if exp_bb > 0:
                chi2_stat += ((n_bb - exp_bb) ** 2) / exp_bb
            from scipy.stats import chi2
            p_val = float(chi2.sf(chi2_stat, df=1))
            if p_val < cfg.hwe:
                removed_counts["hwe"] += 1
                continue

        var_id = f"{var['chrom']}:{var['pos']}" if cfg.set_missing_var_ids else var["rsid"]
        step1_variants.append({
            "chrom": var["chrom"],
            "pos": var["pos"],
            "rsid": var_id,
            "ref": ref,
            "alt": alt,
            "genotypes": gts_arr,
            "maf": maf,
            "miss_rate": miss_rate,
        })

    # Step B: Filter missingness sampel (--mind)
    kept_sample_indices: list[int] = []
    if step1_variants:
        for s_pos in range(len(active_samples)):
            sample_gts = [v["genotypes"][s_pos] for v in step1_variants]
            n_miss = sum(1 for g in sample_gts if g < 0)
            if n_miss / len(step1_variants) <= cfg.mind:
                kept_sample_indices.append(s_pos)
            else:
                removed_counts["mind"] += 1
    else:
        kept_sample_indices = list(range(len(active_samples)))

    final_samples = [active_samples[i] for i in kept_sample_indices]
    final_variants: list[dict[str, Any]] = []
    after_mafs: list[float] = []
    after_miss: list[float] = []

    for var in step1_variants:
        gts_filtered = [var["genotypes"][i] for i in kept_sample_indices]
        valid = [g for g in gts_filtered if g >= 0]
        if valid:
            af = sum(valid) / (2.0 * len(valid))
            maf = min(af, 1.0 - af)
        else:
            maf = 0.0
        miss = (len(gts_filtered) - len(valid)) / max(len(gts_filtered), 1)
        final_variants.append({
            "chrom": var["chrom"],
            "pos": var["pos"],
            "rsid": var["rsid"],
            "ref": var["ref"],
            "alt": var["alt"],
            "genotypes": gts_filtered,
        })
        after_mafs.append(maf)
        after_miss.append(miss)

    n_samples_after = len(final_samples)
    n_snps_after = len(final_variants)
    loss_fraction = (n_snps_before - n_snps_after) / max(n_snps_before, 1)

    warning_msg: str | None = None
    if loss_fraction > cfg.max_snp_loss_warning:
        warning_msg = (
            f"SNP berkurang {round(loss_fraction * 100, 1)}% (>50%) — periksa filter "
            f"(terutama HWE pada populasi inbred/klonal) atau pertimbangkan imputasi."
        )

    # 4. Tulis file output PLINK (.bed, .bim, .fam)
    bed_path = Path(f"{output_prefix}.bed")
    with bed_path.open("wb") as bed_out:
        bed_out.write(b"\x6c\x1b\x01")
        for v in final_variants:
            bed_out.write(encode_plink_bed_row(v["genotypes"]))

    bim_path = Path(f"{output_prefix}.bim")
    with bim_path.open("w", encoding="utf-8") as bim_out:
        for v in final_variants:
            bim_out.write(f"{v['chrom']}\t{v['rsid']}\t0\t{v['pos']}\t{v['alt']}\t{v['ref']}\n")

    fam_path = Path(f"{output_prefix}.fam")
    with fam_path.open("w", encoding="utf-8") as fam_out:
        for s in final_samples:
            p_val = pheno_map.get(s, "-9")
            fam_out.write(f"{s} {s} 0 0 0 {p_val}\n")

    # 5. Tulis file output VCF terfilter (.vcf.gz)
    vcf_gz_path = Path(f"{output_prefix}.vcf.gz")
    with gzip.open(vcf_gz_path, "wt", encoding="utf-8") as vcf_out:
        vcf_out.write("##fileformat=VCFv4.2\n")
        vcf_out.write("##source=PLINK_QC_Fallback\n")
        vcf_out.write("##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n")
        header_line = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(final_samples) + "\n"
        vcf_out.write(header_line)
        for v in final_variants:
            gt_tokens = []
            for g in v["genotypes"]:
                if g == 0:
                    gt_tokens.append("0/0")
                elif g == 1:
                    gt_tokens.append("0/1")
                elif g == 2:
                    gt_tokens.append("1/1")
                else:
                    gt_tokens.append("./.")
            vcf_out.write(
                f"{v['chrom']}\t{v['pos']}\t{v['rsid']}\t{v['ref']}\t{v['alt']}\t.\tPASS\t.\tGT\t"
                + "\t".join(gt_tokens)
                + "\n"
            )

    # 6. Buat qc_comparison.json
    comparison = {
        "before": {
            "n_snps": n_snps_before,
            "n_samples": n_samples_before,
            "maf_hist": _make_hist(before_mafs, bins=10, range_min=0.0, range_max=0.5),
            "missing_hist": _make_hist(before_miss, bins=10, range_min=0.0, range_max=1.0),
        },
        "after": {
            "n_snps": n_snps_after,
            "n_samples": n_samples_after,
            "maf_hist": _make_hist(after_mafs, bins=10, range_min=0.0, range_max=0.5),
            "missing_hist": _make_hist(after_miss, bins=10, range_min=0.0, range_max=1.0),
        },
        "removed": removed_counts,
        "snp_loss_fraction": round(loss_fraction, 4),
        "warning": warning_msg,
    }

    if log_file:
        with log_file.open("a", encoding="utf-8") as lf:
            lf.write(
                f"PLINK QC Python Fallback:\n"
                f"- Input  : {n_snps_before} varian, {n_samples_before} sampel\n"
                f"- Terfilter : {removed_counts}\n"
                f"- Lolos  : {n_snps_after} varian, {n_samples_after} sampel (Loss: {round(loss_fraction * 100, 1)}%)\n"
            )
            if warning_msg:
                lf.write(f"WARNING: {warning_msg}\n")

    return comparison
