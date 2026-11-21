"""Tests for Fase 0 chromosome characterization and split-coordinate helpers."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.chromosome_service import (
    SIGNED_32BIT_MAX,
    load_chromosome_summary,
    load_split_map,
    restore_coord,
    save_split_map,
    split_map_path,
    split_overflow_chromosomes,
    summarize_chromosomes,
    write_chromosome_summary,
)
from app.tasks._common import which_tool


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


def test_split_overflow_chromosomes_roundtrip_header_and_invalid_pos(
    overflow_dataset: dict,
    tmp_path: Path,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    out_vcf = tmp_path / "split.vcf"

    result = split_overflow_chromosomes(
        overflow_dataset["vcf_path"],
        out_vcf,
    )

    assert out_vcf.is_file()
    assert result["split_applied"] is True
    assert result["overflow_chromosomes"] == ["chr7"]
    assert result["invalid_pos_removed"] == 1
    assert result["kept_variants"] == 17
    assert result["overflow_removed"] == 0

    lines = out_vcf.read_text(encoding="utf-8").splitlines()
    contig_lines = [ln for ln in lines if ln.startswith("##contig=")]
    assert "##contig=<ID=chr1,length=4000>" in contig_lines
    assert not any("##contig=<ID=chr7," in ln for ln in contig_lines)
    assert "##contig=<ID=chr7_p0,length=2000>" in contig_lines
    assert "##contig=<ID=chr7_p2,length=2000>" in contig_lines
    assert "##contig=<ID=chr7_p3,length=1000>" in contig_lines

    data_lines = [ln for ln in lines if not ln.startswith("#")]
    assert len(data_lines) == 17
    assert all(int(ln.split("\t")[1]) > 0 for ln in data_lines)
    assert all(int(ln.split("\t")[1]) <= overflow_dataset["split_unit_bp"] for ln in data_lines if ln.startswith("chr7_"))

    # Roundtrip all kept variants via saved chrom_split_map.json
    saved_path = save_split_map("job-w11", result["split_map"])
    assert saved_path == split_map_path("job-w11")
    loaded_map = load_split_map("job-w11")
    assert loaded_map == result["split_map"]

    restored_coords = []
    for ln in data_lines:
        cols = ln.split("\t")
        restored_coords.append(restore_coord(cols[0], int(cols[1]), loaded_map))

    assert ("chr7", 5500) in restored_coords
    assert ("chr7", 5540) in restored_coords
    assert ("chr7", 6500) in restored_coords
    assert ("chr1", 1500) in restored_coords
    # PLINK/chr_label strips "chr" prefix -> "7_p2" must still resolve to "chr7"
    assert restore_coord("7_p2", 1500, loaded_map) == ("chr7", 5500)
    assert restore_coord("7_p2", 1540, loaded_map) == ("chr7", 5540)

    # Missing split part suffix _p<k> logs a warning instead of silent corruption
    with caplog.at_level(logging.WARNING):
        fallback = restore_coord("chr7_p99", 123, loaded_map)
    assert fallback == ("chr7_p99", 123)
    assert any("[WARN] pemulihan koordinat" in rec.message for rec in caplog.records)

    # Reject illegal contig characters in VCF
    bad_vcf = tmp_path / "bad_contig.vcf"
    bad_vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "chr 7;rm\t100\trs1\tA\tG\t100\tPASS\t.\tGT\t0/0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="contig"):
        split_overflow_chromosomes(bad_vcf, tmp_path / "bad_out.vcf", threshold_bp=5000, unit_bp=2000)


@pytest.mark.skipif(which_tool("bcftools") is None, reason="bcftools tidak tersedia di host")
def test_split_vcf_valid_for_bcftools_view_and_csi_index(overflow_dataset: dict, tmp_path: Path):
    bcftools_bin = which_tool("bcftools")
    assert bcftools_bin is not None

    split_vcf = tmp_path / "split.vcf"
    split_overflow_chromosomes(overflow_dataset["vcf_path"], split_vcf)

    view_proc = subprocess.run(
        [bcftools_bin, "view", "-H", str(split_vcf)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert view_proc.returncode == 0, view_proc.stderr
    view_lines = [ln for ln in view_proc.stdout.splitlines() if ln.strip()]
    assert len(view_lines) == 17

    sorted_gz = tmp_path / "split.sorted.vcf.gz"
    sort_proc = subprocess.run(
        [bcftools_bin, "sort", "-Oz", "-o", str(sorted_gz), str(split_vcf)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert sort_proc.returncode == 0, sort_proc.stderr

    idx_proc = subprocess.run(
        [bcftools_bin, "index", "-c", str(sorted_gz)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert idx_proc.returncode == 0, idx_proc.stderr
    assert Path(str(sorted_gz) + ".csi").is_file()
