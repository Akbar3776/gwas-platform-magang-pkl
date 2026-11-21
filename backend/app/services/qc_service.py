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
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from app.schemas.pipeline import PopulationType, QCPipelineConfig


def _is_gzip_file(path: Path) -> bool:
    """Periksa apakah file benar-benar terkompresi gzip melalui magic bytes 0x1f 0x8b."""
    try:
        with path.open("rb") as fh:
            return fh.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def resolve_pheno_flags(
    pheno_path: str | Path | None,
    trait: str | None = None,
    is_plink2: bool = True,
) -> list[str]:
    """
    Menyusun flag PLINK (--pheno, --pheno-name, dan --d bila perlu) untuk memuat fenotipe.

    Menangani kasus khusus:
    1. Trait dengan tanda minus/strip (mis. 'clove-weight', 'plant-height').
       Pada PLINK 2, karakter '-' adalah pemisah rentang (range delimiter) default.
       Jika trait mengandung '-', PLINK 2 menganggapnya sebagai rentang (contoh: 'clove' ke 'weight')
       dan memicu error "Error: --pheno-name: 'clove' does not appear in header line".
       Solusinya: tambahkan '--d ~' (atau '^') sebelum '--pheno-name'.
    2. File fenotipe 3 kolom (FID, IID, Trait) seperti 'pheno_plink.txt':
       Jika trait tidak cocok persis (mis. perbedaan huruf besar/kecil atau alias),
       kolom ke-3 otomatis digunakan sebagai trait efektif.
    """
    if not pheno_path:
        return []

    p = Path(pheno_path)
    flags: list[str] = ["--pheno", str(p)]

    effective_trait = trait.strip() if (trait and isinstance(trait, str)) else None

    if p.is_file():
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    cols = s.split("\t") if "\t" in s else (s.split(",") if "," in s else s.split())
                    cols = [c.strip() for c in cols if c.strip()]
                    if len(cols) == 3:
                        if not effective_trait or effective_trait.lower() == cols[2].lower() or effective_trait not in cols:
                            effective_trait = cols[2]
                    elif len(cols) > 3 and effective_trait:
                        for c in cols[2:]:
                            if c.lower() == effective_trait.lower():
                                effective_trait = c
                                break
                    break
        except Exception:
            pass

    if effective_trait:
        if is_plink2 and "-" in effective_trait:
            delim = "^" if "~" in effective_trait else "~"
            flags.extend(["--d", delim, "--pheno-name", effective_trait])
        else:
            flags.extend(["--pheno-name", effective_trait])

    return flags


def build_plink2_qc_commands(
    cfg: QCPipelineConfig,
    input_vcf: str,
    output_dir: Path,
    keep_ids: str | None = None,
    pheno_path: str | None = None,
    trait: str | None = None,
    plink2_bin: str = "plink2",
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
        plink2_bin,
        "--vcf", input_vcf,
        "--allow-extra-chr",
        "--double-id",
        "--freq",
        "--missing",
        "--out", str(output_dir / "before_qc"),
    ])

    # Langkah 1: QC Varian
    cmd1 = [
        plink2_bin,
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
    cmd1.extend(resolve_pheno_flags(pheno_path, trait, is_plink2=True))

    cmd1.extend(["--maf", str(cfg.maf)])
    if cfg.geno is not None:
        cmd1.extend(["--geno", str(cfg.geno)])

    # HARDY-WEINBERG: HANYA untuk outcross, TIDAK PERNAH untuk inbred
    if cfg.population_type == PopulationType.OUTCROSS and cfg.hwe is not None:
        cmd1.extend(["--hwe", str(cfg.hwe)])

    if cfg.memory_mb:
        cmd1.extend(["--memory", str(cfg.memory_mb)])

    cmd1.extend(["--make-bed", "--out", str(output_dir / "qc_step1")])
    commands.append(cmd1)

    # Langkah 2: QC Sampel lanjutan
    cmd2 = [
        plink2_bin,
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
        plink2_bin,
        "--bfile", str(output_dir / "plink_qc"),
        "--allow-extra-chr",
        "--export", "vcf-4.2", "bgz", "id-paste=iid",
        "--out", str(output_dir / "plink_qc"),
    ]
    commands.append(cmd3)

    # Langkah 4: Statistik sesudah QC
    cmd4 = [
        plink2_bin,
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
    plink_bin: str = "plink",
) -> list[list[str]]:
    """Menyusun perintah PLINK 1.9 ekuivalen bila plink2 tidak terpasang."""
    commands: list[list[str]] = []

    # Ringkasan sebelum QC
    commands.append([
        plink_bin,
        "--vcf", input_vcf,
        "--allow-extra-chr",
        "--double-id",
        "--freq",
        "--missing",
        "--out", str(output_dir / "before_qc"),
    ])

    cmd_main = [
        plink_bin,
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
    cmd_main.extend(resolve_pheno_flags(pheno_path, trait, is_plink2=False))

    cmd_main.extend(["--maf", str(cfg.maf), "--mind", str(cfg.mind)])
    if cfg.geno is not None:
        cmd_main.extend(["--geno", str(cfg.geno)])
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
        plink_bin,
        "--bfile", str(output_dir / "plink_qc"),
        "--allow-extra-chr",
        "--freq",
        "--missing",
        "--out", str(output_dir / "after_qc"),
    ])

    return commands


def build_qc_comparison_from_plink_outputs(
    workspace: Path,
    max_loss_warning: float = 0.5,
    overflow_removed: int = 0,
) -> dict[str, Any]:
    """
    Menyusun perbandingan sebelum dan sesudah QC dari artefak file output PLINK 2 / PLINK 1.9
    (before_qc.*, after_qc.*, plink_qc.bim/.fam, plink_qc.mindrem.id, serta log QC).
    """
    # 1. Sampel & Varian Sebelum QC
    before_n_samples = 0
    before_n_snps = 0

    before_smiss = workspace / "before_qc.smiss"
    before_imiss = workspace / "before_qc.imiss"
    if before_smiss.is_file():
        before_n_samples = max(0, sum(1 for _ in before_smiss.open(encoding="utf-8", errors="replace")) - 1)
    elif before_imiss.is_file():
        before_n_samples = max(0, sum(1 for _ in before_imiss.open(encoding="utf-8", errors="replace")) - 1)

    before_vmiss = workspace / "before_qc.vmiss"
    before_lmiss = workspace / "before_qc.lmiss"
    if before_vmiss.is_file():
        before_n_snps = max(0, sum(1 for _ in before_vmiss.open(encoding="utf-8", errors="replace")) - 1)
    elif before_lmiss.is_file():
        before_n_snps = max(0, sum(1 for _ in before_lmiss.open(encoding="utf-8", errors="replace")) - 1)

    # 2. Sampel & Varian Sesudah QC
    after_n_samples = 0
    after_n_snps = 0

    fam_file = workspace / "plink_qc.fam"
    after_smiss = workspace / "after_qc.smiss"
    after_imiss = workspace / "after_qc.imiss"
    if fam_file.is_file():
        after_n_samples = sum(1 for _ in fam_file.open(encoding="utf-8", errors="replace"))
    elif after_smiss.is_file():
        after_n_samples = max(0, sum(1 for _ in after_smiss.open(encoding="utf-8", errors="replace")) - 1)
    elif after_imiss.is_file():
        after_n_samples = max(0, sum(1 for _ in after_imiss.open(encoding="utf-8", errors="replace")) - 1)

    bim_file = workspace / "plink_qc.bim"
    after_vmiss = workspace / "after_qc.vmiss"
    after_lmiss = workspace / "after_qc.lmiss"
    if bim_file.is_file():
        after_n_snps = sum(1 for _ in bim_file.open(encoding="utf-8", errors="replace"))
    elif after_vmiss.is_file():
        after_n_snps = max(0, sum(1 for _ in after_vmiss.open(encoding="utf-8", errors="replace")) - 1)
    elif after_lmiss.is_file():
        after_n_snps = max(0, sum(1 for _ in after_lmiss.open(encoding="utf-8", errors="replace")) - 1)

    # Bila sebelum QC belum terhitung dari vmiss/smiss, parsing log
    if before_n_snps == 0 or before_n_samples == 0:
        for log_candidate in [workspace / "before_qc.log", workspace / "qc_step1.log", workspace / "plink_qc.log"]:
            if log_candidate.is_file():
                txt = log_candidate.read_text(encoding="utf-8", errors="replace")
                m_snps = re.search(r"(\d+)\s+variants loaded from", txt) or re.search(r"--vcf:\s*(\d+)\s+variants", txt)
                m_samples = re.search(r"(\d+)\s+samples\s+\(", txt) or re.search(r"(\d+)\s+people\s+\(", txt)
                if before_n_snps == 0 and m_snps:
                    before_n_snps = int(m_snps.group(1))
                if before_n_samples == 0 and m_samples:
                    before_n_samples = int(m_samples.group(1))

    # Guard batas bawah
    if before_n_snps == 0:
        before_n_snps = after_n_snps
    if before_n_samples == 0:
        before_n_samples = after_n_samples

    # 3. Parsing Histogram MAF
    def _parse_freq_hist(freq_path: Path) -> dict[str, list]:
        if not freq_path.is_file():
            return {"edges": [0.0, 0.5], "counts": [0]}
        mafs: list[float] = []
        with freq_path.open("r", encoding="utf-8", errors="replace") as f:
            header = next(f, None)
            if not header:
                return {"edges": [0.0, 0.5], "counts": [0]}
            cols = header.strip().split()
            idx = -1
            for name in ["ALT_FREQS", "MAF"]:
                if name in cols:
                    idx = cols.index(name)
                    break
            if idx == -1:
                idx = 4 if len(cols) > 4 else -1
            if idx != -1:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) > idx:
                        try:
                            val = float(parts[idx])
                            if 0.0 <= val <= 1.0:
                                mafs.append(min(val, 1.0 - val))
                        except ValueError:
                            pass
        if not mafs:
            return {"edges": [0.0, 0.5], "counts": [0]}
        counts, edges = np.histogram(mafs, bins=10, range=(0.0, 0.5))
        return {
            "edges": [round(float(e), 4) for e in edges],
            "counts": [int(c) for c in counts],
        }

    before_freq = workspace / "before_qc.afreq"
    if not before_freq.is_file():
        before_freq = workspace / "before_qc.frq"

    after_freq = workspace / "after_qc.afreq"
    if not after_freq.is_file():
        after_freq = workspace / "after_qc.frq"

    before_maf_hist = _parse_freq_hist(before_freq)
    after_maf_hist = _parse_freq_hist(after_freq)

    # 4. Parsing Histogram Missingness
    def _parse_miss_hist(miss_path: Path) -> dict[str, list]:
        if not miss_path.is_file():
            return {"edges": [0.0, 1.0], "counts": [0]}
        misses: list[float] = []
        with miss_path.open("r", encoding="utf-8", errors="replace") as f:
            header = next(f, None)
            if not header:
                return {"edges": [0.0, 1.0], "counts": [0]}
            cols = header.strip().split()
            idx = cols.index("F_MISS") if "F_MISS" in cols else (-1)
            if idx == -1:
                idx = 4 if len(cols) > 4 else -1
            if idx != -1:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) > idx:
                        try:
                            val = float(parts[idx])
                            if 0.0 <= val <= 1.0:
                                misses.append(val)
                        except ValueError:
                            pass
        if not misses:
            return {"edges": [0.0, 1.0], "counts": [0]}
        counts, edges = np.histogram(misses, bins=10, range=(0.0, 1.0))
        return {
            "edges": [round(float(e), 4) for e in edges],
            "counts": [int(c) for c in counts],
        }

    before_missing_hist = _parse_miss_hist(before_vmiss if before_vmiss.is_file() else before_lmiss)
    after_missing_hist = _parse_miss_hist(after_vmiss if after_vmiss.is_file() else after_lmiss)

    # 5. Parsing Kriteria Filter yang Terbuang dari Log
    removed_counts = {
        "maf": 0,
        "geno": 0,
        "mind": 0,
        "hwe": 0,
        "non_snp": 0,
        "multiallelic": 0,
        "overflow_32bit": max(0, int(overflow_removed)),
    }

    mindrem_file = workspace / "plink_qc.mindrem.id"
    if mindrem_file.is_file():
        removed_counts["mind"] = max(0, sum(1 for _ in mindrem_file.open(encoding="utf-8", errors="replace")) - 1)

    all_logs_text = ""
    for log_name in ["qc_step1.log", "plink_qc.log", "qc.log", "before_qc.log", "after_qc.log"]:
        lf = workspace / log_name
        if lf.is_file():
            all_logs_text += "\n" + lf.read_text(encoding="utf-8", errors="replace")

    if all_logs_text:
        if removed_counts["overflow_32bit"] == 0:
            m_overflow = re.findall(r"overflow 32-bit=(\d+)", all_logs_text) or re.findall(
                r"32-bit overflow\):\s*(\d+)", all_logs_text
            )
            if m_overflow:
                removed_counts["overflow_32bit"] = max(int(x) for x in m_overflow)
        m_geno = re.findall(r"--geno:\s+(\d+)\s+variants removed", all_logs_text) or re.findall(
            r"(\d+)\s+variants removed due to missing genotype data", all_logs_text
        )
        if m_geno:
            removed_counts["geno"] = sum(int(x) for x in m_geno)

        m_maf = re.findall(r"(\d+)\s+variants removed due to allele frequency threshold", all_logs_text) or re.findall(
            r"(\d+)\s+variants removed due to minor allele threshold", all_logs_text
        )
        if m_maf:
            removed_counts["maf"] = sum(int(x) for x in m_maf)

        if removed_counts["mind"] == 0:
            m_mind = re.findall(r"(\d+)\s+samples removed due to missing genotype data", all_logs_text) or re.findall(
                r"(\d+)\s+people removed due to missing genotype data", all_logs_text
            )
            if m_mind:
                removed_counts["mind"] = sum(int(x) for x in m_mind)

        m_hwe = re.findall(r"--hwe:\s+(\d+)\s+variants removed", all_logs_text) or re.findall(
            r"(\d+)\s+variants removed due to Hardy-Weinberg", all_logs_text
        )
        if m_hwe:
            removed_counts["hwe"] = sum(int(x) for x in m_hwe)

        m_multi = re.findall(r"(\d+)\s+multiallelic variants removed", all_logs_text)
        if m_multi:
            removed_counts["multiallelic"] = sum(int(x) for x in m_multi)
        m_nonsnp = re.findall(r"(\d+)\s+non-SNP[s]? removed", all_logs_text)
        if m_nonsnp:
            removed_counts["non_snp"] = sum(int(x) for x in m_nonsnp)

    loss_fraction = (before_n_snps - after_n_snps) / max(before_n_snps, 1) if before_n_snps > 0 else 0.0

    warning_msg = None
    if loss_fraction > max_loss_warning:
        warning_msg = (
            f"SNP berkurang {round(loss_fraction * 100, 1)}% (>50%) — periksa filter "
            f"(terutama HWE pada populasi inbred/klonal) atau pertimbangkan imputasi."
        )

    return {
        "before": {
            "n_snps": before_n_snps,
            "n_samples": before_n_samples,
            "maf_hist": before_maf_hist,
            "missing_hist": before_missing_hist,
        },
        "after": {
            "n_snps": after_n_snps,
            "n_samples": after_n_samples,
            "maf_hist": after_maf_hist,
            "missing_hist": after_missing_hist,
        },
        "removed": removed_counts,
        "snp_loss_fraction": round(loss_fraction, 4),
        "warning": warning_msg,
    }
