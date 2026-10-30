"""
Service untuk tahap Quality Control (Fase 2) GWAS Pipeline.

Mendukung:
1. PLINK 2 (`plink2`):
   - Inbred (tanaman inbred, selfing, klonal): HWE dinonaktifkan sepenuhnya.
   - Outcross (hewan, manusia, tanaman kawin silang): HWE diaktifkan.
   - Filtering: --snps-only just-acgt, --max-alleles 2, --set-all-var-ids @:#,
     --keep keep_ids.txt, --pheno pheno_plink.txt.
   - Sebelum dan sesudah QC: kalkulasi frekuensi alel & missingness untuk qc_comparison.json.
2. PLINK 1.9 (`plink`):
   - Equivalent flag builder bila PLINK 2 tidak tersedia.
3. Fallback Python Murni:
   - Menghasilkan file biner PLINK yang valid (.bed SNP-major dengan magic bytes 0x6c, 0x1b, 0x01),
     .bim, .fam, .vcf.gz, dan qc_comparison.json.
"""

from __future__ import annotations

import gzip
import re
import subprocess
from collections import deque
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app.schemas.pipeline import PopulationType, QCPipelineConfig

_LOG_TAIL_SIZE = 50


class PlinkExecutionError(RuntimeError):
    """Dilempar jika proses PLINK keluar dengan return code bukan 0."""

    def __init__(self, message: str, log_tail: str):
        super().__init__(message)
        self.log_tail = log_tail


def build_plink_qc_command(
    input_vcf_path: str,
    output_prefix: str,
    maf: float = 0.01,
    geno: float = 0.05,
    mind: float = 0.1,
    hwe: float = 1e-6,
) -> list[str]:
    """Legacy helper untuk kompatibilitas PLINK 1.9 satu tahap."""
    return [
        "plink",
        "--vcf", input_vcf_path,
        "--double-id",
        "--biallelic-only",
        "--allow-extra-chr",
        "--maf", str(maf),
        "--geno", str(geno),
        "--mind", str(mind),
        "--hwe", str(hwe),
        "--make-bed",
        "--recode", "vcf", "bgz",
        "--out", output_prefix,
    ]


def build_plink2_qc_commands(
    cfg: QCPipelineConfig,
    input_vcf: str,
    output_dir: Path,
    keep_ids: str | None = None,
    pheno_path: str | None = None,
    trait: str | None = None,
) -> list[list[str]]:
    """
    Menyusun urutan perintah PLINK 2 untuk Fase 2 Quality Control:
    0. Summary sebelum QC (--freq --missing).
    1. QC Varian (--snps-only, --max-alleles 2, --set-all-var-ids, --maf, --geno, conditional --hwe).
    2. QC Sampel (--mind).
    3. Ekspor VCF (bgzip).
    4. Summary sesudah QC (--freq --missing).
    """
    commands: list[list[str]] = []

    # Langkah 0: Statistik sebelum QC
    commands.append([
        "plink2",
        "--vcf", input_vcf,
        "--allow-extra-chr",
        "--double-id",
        "--freq",
        "--missing",
        "--out", str(output_dir / "before_qc"),
    ])

    # Langkah 1: QC Varian
    cmd1 = [
        "plink2",
        "--vcf", input_vcf,
        "--double-id",
        "--allow-extra-chr",
    ]
    if keep_ids:
        cmd1.extend(["--keep", str(keep_ids)])
    if cfg.snps_only:
        cmd1.extend(["--snps-only", "just-acgt"])
    if cfg.max_alleles == 2:
        cmd1.extend(["--max-alleles", "2"])
    if cfg.set_missing_var_ids:
        cmd1.extend(["--set-all-var-ids", "@:#"])
    if pheno_path and trait:
        cmd1.extend(["--pheno", str(pheno_path), "--pheno-name", str(trait)])

    cmd1.extend(["--maf", str(cfg.maf), "--geno", str(cfg.geno)])

    # HARDY-WEINBERG: HANYA untuk outcross, TIDAK PERNAH untuk inbred
    if cfg.population_type == PopulationType.OUTCROSS and cfg.hwe is not None:
        cmd1.extend(["--hwe", str(cfg.hwe)])

    if cfg.memory_mb:
        cmd1.extend(["--memory", str(cfg.memory_mb)])

    cmd1.extend(["--make-bed", "--out", str(output_dir / "qc_step1")])
    commands.append(cmd1)

    # Langkah 2: QC Sampel lanjutan
    cmd2 = [
        "plink2",
        "--bfile", str(output_dir / "qc_step1"),
        "--allow-extra-chr",
        "--mind", str(cfg.mind),
        "--maf", str(cfg.maf),
        "--make-bed",
        "--out", str(output_dir / "plink_qc"),
    ]
    commands.append(cmd2)

    # Langkah 3: Ekspor VCF terstandarisasi untuk imputasi/parser
    cmd3 = [
        "plink2",
        "--bfile", str(output_dir / "plink_qc"),
        "--allow-extra-chr",
        "--export", "vcf-4.2", "bgz", "id-paste=iid",
        "--out", str(output_dir / "plink_qc"),
    ]
    commands.append(cmd3)

    # Langkah 4: Statistik sesudah QC
    cmd4 = [
        "plink2",
        "--bfile", str(output_dir / "plink_qc"),
        "--allow-extra-chr",
        "--freq",
        "--missing",
        "--out", str(output_dir / "after_qc"),
    ]
    commands.append(cmd4)

    return commands


def build_plink19_qc_commands(
    cfg: QCPipelineConfig,
    input_vcf: str,
    output_dir: Path,
    keep_ids: str | None = None,
    pheno_path: str | None = None,
    trait: str | None = None,
) -> list[list[str]]:
    """Menyusun perintah PLINK 1.9 ekuivalen bila plink2 tidak terpasang."""
    commands: list[list[str]] = []

    # Ringkasan sebelum QC
    commands.append([
        "plink",
        "--vcf", input_vcf,
        "--allow-extra-chr",
        "--double-id",
        "--freq",
        "--missing",
        "--out", str(output_dir / "before_qc"),
    ])

    cmd_main = [
        "plink",
        "--vcf", input_vcf,
        "--double-id",
        "--allow-extra-chr",
    ]
    if keep_ids:
        cmd_main.extend(["--keep", str(keep_ids)])
    if cfg.snps_only:
        cmd_main.extend(["--snps-only", "just-acgt"])
    if cfg.max_alleles == 2:
        cmd_main.extend(["--biallelic-only", "strict"])
    if cfg.set_missing_var_ids:
        cmd_main.extend(["--set-missing-var-ids", "@:#"])
    if pheno_path and trait:
        cmd_main.extend(["--pheno", str(pheno_path), "--pheno-name", str(trait)])

    cmd_main.extend(["--maf", str(cfg.maf), "--geno", str(cfg.geno), "--mind", str(cfg.mind)])
    if cfg.population_type == PopulationType.OUTCROSS and cfg.hwe is not None:
        cmd_main.extend(["--hwe", str(cfg.hwe)])

    cmd_main.extend([
        "--make-bed",
        "--recode", "vcf-iid", "bgz",
        "--out", str(output_dir / "plink_qc"),
    ])
    commands.append(cmd_main)

    # Ringkasan sesudah QC
    commands.append([
        "plink",
        "--bfile", str(output_dir / "plink_qc"),
        "--allow-extra-chr",
        "--freq",
        "--missing",
        "--out", str(output_dir / "after_qc"),
    ])

    return commands


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
) -> dict[str, Any]:
    """
    Implementasi fallback Python murni untuk QC PLINK bila plink2/plink tidak ada.
    Menghasilkan file .bed (SNP-major valid), .bim, .fam, .vcf.gz, dan qc_comparison.json.
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
                allowed_iids.add(parts[-1])  # ambil kolom IID (kolom terakhir)

    # 2. Baca fenotipe mapping bila ada
    pheno_map: dict[str, str] = {}
    if pheno_path and Path(pheno_path).is_file() and trait:
        try:
            lines = Path(pheno_path).read_text(encoding="utf-8").splitlines()
            if lines:
                header = lines[0].strip().split()
                if trait in header:
                    t_idx = header.index(trait)
                    iid_idx = header.index("IID") if "IID" in header else 1
                    for l in lines[1:]:
                        cols = l.strip().split()
                        if len(cols) > max(iid_idx, t_idx):
                            pheno_map[cols[iid_idx]] = cols[t_idx]
        except Exception:
            pass

    # 3. Parse input VCF
    src = Path(input_vcf)
    open_fn = gzip.open if src.name.endswith(".gz") else open

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
        "non_snp": 0,
        "multiallelic": 0,
        "geno": 0,
        "maf": 0,
        "hwe": 0,
        "mind": 0,
    }

    # Step A: Filter varian non-SNP dan multi-alelik
    step1_variants: list[dict[str, Any]] = []
    acgt_set = {"A", "C", "G", "T", "a", "c", "g", "t"}
    for var in raw_variants:
        ref = var["ref"].upper()
        alt = var["alt"].upper()
        if cfg.max_alleles == 2 and "," in alt:
            removed_counts["multiallelic"] += 1
            continue
        if cfg.snps_only and (ref not in acgt_set or alt not in acgt_set or len(ref) != 1 or len(alt) != 1):
            removed_counts["non_snp"] += 1
            continue

        # Parse genotypes untuk sampel aktif
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

        # Missingness SNP (--geno)
        valid = [g for g in gts_arr if g >= 0]
        miss_rate = (len(gts_arr) - len(valid)) / max(len(gts_arr), 1)
        if miss_rate > cfg.geno:
            removed_counts["geno"] += 1
            continue

        # MAF (--maf)
        if not valid:
            removed_counts["maf"] += 1
            continue
        af = sum(valid) / (2.0 * len(valid))
        maf = min(af, 1.0 - af)
        if maf < cfg.maf:
            removed_counts["maf"] += 1
            continue

        # HWE check HANYA bila outcross
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
            # chi2 1 df critical p-value approximation
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
    # Magic bytes PLINK .bed: 0x6c, 0x1b, 0x01 (SNP-major mode)
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

    # Tulis ringkasan log
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


def run_subprocess_with_logging(
    cmd: list[str],
    log_file_path: Path,
    timeout_seconds: int,
    on_line: Callable[[str], None] | None = None,
) -> str:
    """Menjalankan `cmd` sebagai subprocess, streaming stdout+stderr langsung ke file log."""
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    log_tail: deque[str] = deque(maxlen=_LOG_TAIL_SIZE)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        with open(log_file_path, "a", encoding="utf-8") as log_file:
            for line in process.stdout:
                log_file.write(line)
                log_file.flush()
                log_tail.append(line.rstrip("\n"))
                if on_line is not None:
                    on_line(line.rstrip("\n"))

            try:
                return_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise

    finally:
        if process.stdout:
            process.stdout.close()

    tail_text = "\n".join(log_tail)
    if return_code != 0:
        raise PlinkExecutionError(
            f"PLINK keluar dengan return code {return_code}", log_tail=tail_text
        )

    return tail_text
