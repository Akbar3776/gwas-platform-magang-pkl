from pathlib import Path

import pytest

from app.schemas.pipeline import PopulationType, QCPipelineConfig
from app.services.qc_service import (
    build_plink19_qc_commands,
    build_plink2_qc_commands,
    build_qc_comparison_from_plink_outputs,
    encode_plink_bed_row,
    resolve_pheno_flags,
    run_python_fallback_qc,
)


def test_build_plink2_qc_commands_inbred():
    cfg = QCPipelineConfig(
        population_type=PopulationType.INBRED,
        maf=0.05,
        geno=0.05,
        mind=0.05,
        snps_only=True,
        max_alleles=2,
        set_missing_var_ids=True,
    )
    cmds = build_plink2_qc_commands(
        cfg=cfg,
        input_vcf="/data/in.vcf.gz",
        output_dir=Path("/data/qc"),
        keep_ids="/data/keep.txt",
        pheno_path="/data/pheno.txt",
        trait="Trait1",
    )
    assert len(cmds) == 5

    # Check step 1
    cmd1 = cmds[1]
    assert cmd1[0] == "plink2"
    assert "--keep" in cmd1
    assert "/data/keep.txt" in cmd1
    assert "--snps-only" in cmd1
    assert "--max-alleles" in cmd1
    assert "--set-all-var-ids" in cmd1
    assert "--pheno" in cmd1
    assert "Trait1" in cmd1

    # CRITICAL: HWE must NOT be present for inbred
    assert "--hwe" not in cmd1


def test_build_plink2_qc_commands_outcross():
    cfg = QCPipelineConfig(
        population_type=PopulationType.OUTCROSS,
        maf=0.01,
        geno=0.05,
        mind=0.1,
        hwe=1e-5,
    )
    cmds = build_plink2_qc_commands(
        cfg=cfg,
        input_vcf="/data/in.vcf.gz",
        output_dir=Path("/data/qc"),
    )
    cmd1 = cmds[1]
    assert "--hwe" in cmd1
    idx = cmd1.index("--hwe")
    assert cmd1[idx + 1] == "1e-05"


def test_build_plink19_qc_commands():
    cfg = QCPipelineConfig(
        population_type=PopulationType.INBRED,
        maf=0.05,
        geno=0.05,
        mind=0.05,
    )
    cmds = build_plink19_qc_commands(
        cfg=cfg,
        input_vcf="/data/in.vcf.gz",
        output_dir=Path("/data/qc"),
    )
    assert len(cmds) == 3
    cmd_main = cmds[1]
    assert cmd_main[0] == "plink"
    assert "--biallelic-only" in cmd_main
    assert "--set-missing-var-ids" in cmd_main
    assert "--hwe" not in cmd_main


def test_resolve_pheno_flags_hyphenated_trait():
    target_path = str(Path("/data/pheno.txt"))
    flags_plink2 = resolve_pheno_flags("/data/pheno.txt", trait="clove-weight", is_plink2=True)
    assert flags_plink2 == ["--pheno", target_path, "--d", "~", "--pheno-name", "clove-weight"]

    flags_plink19 = resolve_pheno_flags("/data/pheno.txt", trait="clove-weight", is_plink2=False)
    assert flags_plink19 == ["--pheno", target_path, "--pheno-name", "clove-weight"]


def test_resolve_pheno_flags_with_real_file(tmp_path):
    pheno_file = tmp_path / "pheno_plink.txt"
    pheno_file.write_text("FID\tIID\tclove-weight\nS1\tS1\t3.45\nS2\tS2\t5.78\n", encoding="utf-8")

    # Mismatch trait parameter (e.g. 'clove') resolves to actual column 'clove-weight'
    flags1 = resolve_pheno_flags(pheno_file, trait="clove", is_plink2=True)
    assert flags1 == ["--pheno", str(pheno_file), "--d", "~", "--pheno-name", "clove-weight"]

    # None trait parameter resolves to actual column 'clove-weight'
    flags2 = resolve_pheno_flags(pheno_file, trait=None, is_plink2=True)
    assert flags2 == ["--pheno", str(pheno_file), "--d", "~", "--pheno-name", "clove-weight"]


def test_build_plink2_qc_commands_hyphenated_trait(tmp_path):
    pheno_file = tmp_path / "pheno_plink.txt"
    pheno_file.write_text("FID\tIID\tclove-weight\nS1\tS1\t3.45\n", encoding="utf-8")

    cfg = QCPipelineConfig(population_type=PopulationType.INBRED)
    cmds = build_plink2_qc_commands(
        cfg=cfg,
        input_vcf="/data/in.vcf.gz",
        output_dir=Path("/data/qc"),
        pheno_path=str(pheno_file),
        trait="clove-weight",
    )
    cmd1 = cmds[1]
    assert "--pheno" in cmd1
    assert "--d" in cmd1
    d_idx = cmd1.index("--d")
    assert cmd1[d_idx + 1] == "~"
    assert cmd1[d_idx + 2] == "--pheno-name"
    assert cmd1[d_idx + 3] == "clove-weight"


def test_encode_plink_bed_row():
    # 0=HomRef (0b11=3), 1=Het (0b10=2), 2=HomAlt (0b00=0), -1=Missing (0b01=1)
    # Sample 0: 0 -> 3 (shift 0) = 3
    # Sample 1: 1 -> 2 (shift 2) = 8
    # Sample 2: 2 -> 0 (shift 4) = 0
    # Sample 3: -1 -> 1 (shift 6) = 64
    # Total byte = 3 + 8 + 0 + 64 = 75
    encoded = encode_plink_bed_row([0, 1, 2, -1])
    assert len(encoded) == 1
    assert encoded[0] == 75

    # 5 samples -> 2 bytes
    encoded_5 = encode_plink_bed_row([0, 0, 0, 0, 2])
    assert len(encoded_5) == 2
    assert encoded_5[0] == 255
    assert encoded_5[1] == 0


def test_run_python_fallback_qc(tmp_path):
    fixture_vcf = Path(__file__).parent / "fixtures" / "data" / "toy_gwas.vcf.gz"
    if not fixture_vcf.is_file():
        from tests.fixtures.make_fixtures import generate_toy_fixtures
        generate_toy_fixtures()

    out_prefix = tmp_path / "plink_qc"
    log_file = tmp_path / "plink_qc.log"
    cfg = QCPipelineConfig(
        population_type=PopulationType.INBRED,
        maf=0.05,
        geno=0.1,
        mind=0.1,
    )

    comparison = run_python_fallback_qc(
        input_vcf=str(fixture_vcf),
        output_prefix=out_prefix,
        cfg=cfg,
        log_file=log_file,
    )

    assert "before" in comparison
    assert "after" in comparison
    assert "removed" in comparison
    assert comparison["before"]["n_snps"] > 0
    assert comparison["after"]["n_snps"] > 0

    bed_file = Path(f"{out_prefix}.bed")
    bim_file = Path(f"{out_prefix}.bim")
    fam_file = Path(f"{out_prefix}.fam")
    vcf_file = Path(f"{out_prefix}.vcf.gz")

    assert bed_file.is_file()
    assert bim_file.is_file()
    assert fam_file.is_file()
    assert vcf_file.is_file()

    # Check PLINK bed magic bytes
    bed_bytes = bed_file.read_bytes()
    assert bed_bytes[:3] == b"\x6c\x1b\x01"

    # Line counts
    bim_lines = bim_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(bim_lines) == comparison["after"]["n_snps"]

    fam_lines = fam_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(fam_lines) == comparison["after"]["n_samples"]


def test_build_qc_comparison_from_plink_outputs(tmp_path):
    # Dummy before_qc.afreq & vmiss & smiss
    (tmp_path / "before_qc.afreq").write_text(
        "#CHROM\tID\tREF\tALT\tALT_FREQS\tOBS_CT\n"
        "1\t1:100\tA\tG\t0.02\t200\n"
        "1\t1:200\tC\tT\t0.25\t200\n"
        "1\t1:300\tG\tA\t0.40\t200\n",
        encoding="utf-8",
    )
    (tmp_path / "before_qc.vmiss").write_text(
        "#CHROM\tID\tMISSING_CT\tOBS_CT\tF_MISS\n"
        "1\t1:100\t10\t100\t0.10\n"
        "1\t1:200\t0\t100\t0.00\n"
        "1\t1:300\t2\t100\t0.02\n",
        encoding="utf-8",
    )
    (tmp_path / "before_qc.smiss").write_text(
        "#FID\tIID\tMISSING_CT\tOBS_CT\tF_MISS\n"
        "S1\tS1\t0\t3\t0.0\n"
        "S2\tS2\t1\t3\t0.33\n"
        "S3\tS3\t0\t3\t0.0\n",
        encoding="utf-8",
    )

    # Dummy after QC (.bim, .fam, .afreq, .vmiss)
    (tmp_path / "plink_qc.bim").write_text(
        "1\t1:200\t0\t200\tT\tC\n"
        "1\t1:300\t0\t300\tA\tG\n",
        encoding="utf-8",
    )
    (tmp_path / "plink_qc.fam").write_text(
        "S1 S1 0 0 0 1.0\n"
        "S3 S3 0 0 0 2.0\n",
        encoding="utf-8",
    )
    (tmp_path / "after_qc.afreq").write_text(
        "#CHROM\tID\tREF\tALT\tPROVISIONAL_REF?\tALT_FREQS\tOBS_CT\n"
        "1\t1:200\tC\tT\tY\t0.25\t198\n"
        "1\t1:300\tG\tA\tY\t0.40\t198\n",
        encoding="utf-8",
    )
    (tmp_path / "after_qc.vmiss").write_text(
        "#CHROM\tID\tMISSING_CT\tOBS_CT\tF_MISS\n"
        "1\t1:200\t0\t99\t0.00\n"
        "1\t1:300\t1\t99\t0.01\n",
        encoding="utf-8",
    )

    # Log text showing removed counts
    (tmp_path / "qc_step1.log").write_text(
        "--geno: 1 variants removed due to missing genotype data.\n"
        "0 variants removed due to allele frequency threshold(s)\n",
        encoding="utf-8",
    )
    (tmp_path / "plink_qc.mindrem.id").write_text(
        "#FID\tIID\n"
        "S2\tS2\n",
        encoding="utf-8",
    )

    res = build_qc_comparison_from_plink_outputs(tmp_path)

    assert res["before"]["n_snps"] == 3
    assert res["before"]["n_samples"] == 3
    assert res["after"]["n_snps"] == 2
    assert res["after"]["n_samples"] == 2
    assert res["removed"]["geno"] == 1
    assert res["removed"]["mind"] == 1
    assert len(res["before"]["maf_hist"]["edges"]) == 11
    assert len(res["after"]["maf_hist"]["edges"]) == 11
    assert res["snp_loss_fraction"] == pytest.approx(0.3333, abs=0.01)
    assert res["warning"] is None

