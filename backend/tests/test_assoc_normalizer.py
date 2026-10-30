from pathlib import Path

import pytest

from app.services.assoc_normalizer import (
    CANONICAL_COLUMNS,
    detect_association_format,
    normalize_association_results,
)


def test_detect_association_format():
    gemma_header = ["chr", "rs", "ps", "n_miss", "allele1", "allele0", "af", "beta", "se", "p_wald"]
    assert detect_association_format(gemma_header) == "gemma"

    plink2_header = ["#CHROM", "POS", "ID", "REF", "ALT", "A1", "TEST", "OBS_CT", "BETA", "SE", "T_STAT", "P"]
    assert detect_association_format(plink2_header) == "plink2"

    gapit_header = ["SNP", "Chromosome", "Position", "P.value", "maf", "nobs", "H&B.P.Value", "Effect"]
    assert detect_association_format(gapit_header) == "gapit"


def test_normalize_gemma_output(tmp_path):
    gemma_file = tmp_path / "test.assoc.txt"
    gemma_content = (
        "chr\trs\tps\tn_miss\tallele1\tallele0\taf\tbeta\tse\tp_wald\n"
        "1\trs101\t1000\t0\tA\tG\t0.25\t0.15\t0.03\t1.2e-6\n"
        "2\trs102\t2000\t1\tT\tC\t0.10\t-0.20\t0.05\t5.0e-5\n"
    )
    gemma_file.write_text(gemma_content, encoding="utf-8")

    out_tsv = tmp_path / "gwas_results.normalized.tsv"
    count = normalize_association_results(gemma_file, out_tsv)
    assert count == 2

    lines = out_tsv.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    header = lines[0].split("\t")
    assert header == CANONICAL_COLUMNS

    row1 = lines[1].split("\t")
    assert row1[0] == "1"
    assert row1[1] == "1000"
    assert row1[2] == "rs101"
    assert row1[3] == "A"
    assert row1[4] == "G"
    assert float(row1[8]) == 1.2e-6


def test_normalize_plink2_output(tmp_path):
    plink_file = tmp_path / "test.glm.linear"
    plink_content = (
        "#CHROM\tPOS\tID\tREF\tALT\tA1\tTEST\tN_OBS\tBETA\tSE\tP\n"
        "1\t5000\t1:5000\tG\tA\tA\tADD\t150\t0.35\t0.08\t2.5e-8\n"
        "1\t5000\t1:5000\tG\tA\tA\tPC1\t150\t0.01\t0.02\t0.45\n"  # Covariate line should be filtered
        "3\t9000\t3:9000\tC\tT\tT\tADD\t150\t-0.12\t0.04\t1.0e-3\n"
    )
    plink_file.write_text(plink_content, encoding="utf-8")

    out_tsv = tmp_path / "plink_normalized.tsv"
    count = normalize_association_results(plink_file, out_tsv)
    assert count == 2

    lines = out_tsv.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    row1 = lines[1].split("\t")
    assert row1[0] == "1"
    assert row1[1] == "5000"
    assert row1[2] == "1:5000"
    assert row1[3] == "A"
    assert float(row1[8]) == 2.5e-8


def test_normalize_gapit_output(tmp_path):
    gapit_file = tmp_path / "test.csv"
    gapit_content = (
        '"SNP","Chromosome","Position","P.value","maf","nobs","Effect"\n'
        '"rs501",1,12000,3.4e-7,0.30,120,0.45\n'
        '"rs502",2,15000,8.1e-4,0.15,120,-0.22\n'
    )
    gapit_file.write_text(gapit_content, encoding="utf-8")

    out_tsv = tmp_path / "gapit_normalized.tsv"
    count = normalize_association_results(gapit_file, out_tsv)
    assert count == 2

    lines = out_tsv.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    row1 = lines[1].split("\t")
    assert row1[0] == "1"
    assert row1[1] == "12000"
    assert row1[2] == "rs501"
    assert float(row1[8]) == 3.4e-7
