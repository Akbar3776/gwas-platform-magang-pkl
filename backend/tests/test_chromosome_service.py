"""Tests for Fase 0 chromosome characterization and split-coordinate helpers."""

from __future__ import annotations

from pathlib import Path

from app.services.chromosome_service import (
    SIGNED_32BIT_MAX,
    load_chromosome_summary,
    summarize_chromosomes,
    write_chromosome_summary,
)


def test_summarize_chromosomes_on_overflow_dataset(overflow_dataset: dict):
    summary = summarize_chromosomes(
        overflow_dataset["vcf_path"],
        threshold_bp=overflow_dataset["split_threshold_bp"],
    )

    assert SIGNED_32BIT_MAX == 2_147_483_647
    assert summary["threshold_bp"] == 5_000
    assert summary["total_variants"] == 18
    assert summary["total_overflow"] == 5
    assert summary["total_invalid_pos"] == 1
    assert summary["needs_split"] is True
    assert summary["overflow_chromosomes"] == ["chr7"]

    by_chr = {row["chrom"]: row for row in summary["chromosomes"]}
    assert list(by_chr.keys()) == ["chr1", "chr7"]
    assert by_chr["chr1"] == {
        "chrom": "chr1",
        "total_snp": 7,
        "min_pos": 500,
        "max_pos": 3500,
        "overflow_count": 0,
        "invalid_pos_count": 0,
        "needs_split": False,
    }
    assert by_chr["chr7"] == {
        "chrom": "chr7",
        "total_snp": 11,
        "min_pos": 0,
        "max_pos": 6500,
        "overflow_count": 5,
        "invalid_pos_count": 1,
        "needs_split": True,
    }


def test_write_chromosome_summary_matches_awk_reference_tsv(
    overflow_dataset: dict,
    tmp_path: Path,
):
    summary = summarize_chromosomes(overflow_dataset["vcf_path"])
    out_file = tmp_path / "chromosome_characteristics_summary.txt"
    written = write_chromosome_summary(summary, out_file)

    assert written == out_file
    assert out_file.is_file()

    expected_tsv = (
        "CHROM\tTOTAL_SNP\tMIN_POS\tMAX_POS\tOVERFLOW\tINVALID_POS\n"
        "chr1\t7\t500\t3500\t0\t0\n"
        "chr7\t11\t0\t6500\t5\t1\n"
    )
    assert out_file.read_text(encoding="utf-8") == expected_tsv

    reloaded = load_chromosome_summary(out_file)
    assert reloaded is not None
    assert reloaded["total_variants"] == 18
    assert reloaded["total_overflow"] == 5
    assert reloaded["total_invalid_pos"] == 1
    assert reloaded["overflow_chromosomes"] == ["chr7"]
    assert reloaded["needs_split"] is True
    assert reloaded["chromosomes"] == summary["chromosomes"]
