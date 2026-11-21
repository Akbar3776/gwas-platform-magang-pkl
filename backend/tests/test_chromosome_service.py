"""Tests for Fase 0 chromosome characterization and split-coordinate helpers."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

import pytest

from app.core.config import DEFAULT_NON_PRIMARY_CONTIG_REGEX, settings
from app.services.chromosome_service import (
    SIGNED_32BIT_MAX,
    is_non_primary_contig,
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
    assert summary["total_variants"] == 22
    assert summary["total_overflow"] == 5
    assert summary["total_invalid_pos"] == 1
    assert summary["needs_split"] is True
    assert summary["overflow_chromosomes"] == ["chr7"]
    assert summary["excluded_contigs"] == {
        "pattern": settings.NON_PRIMARY_CONTIG_REGEX,
        "contigs": 2,
        "variants": 4,
        "single_marker_contigs": 1,
        "examples": ["scaffold10019", "scaffold10098"],
    }

    by_chr = {row["chrom"]: row for row in summary["chromosomes"]}
    assert list(by_chr.keys()) == ["chr1", "chr7", "scaffold10019", "scaffold10098"]
    assert by_chr["chr1"] == {
        "chrom": "chr1",
        "total_snp": 7,
        "min_pos": 500,
        "max_pos": 3500,
        "overflow_count": 0,
        "invalid_pos_count": 0,
        "needs_split": False,
        "excluded": False,
    }
    assert by_chr["chr7"] == {
        "chrom": "chr7",
        "total_snp": 11,
        "min_pos": 0,
        "max_pos": 6500,
        "overflow_count": 5,
        "invalid_pos_count": 1,
        "needs_split": True,
        "excluded": False,
    }
    assert by_chr["scaffold10019"]["excluded"] is True
    assert by_chr["scaffold10098"]["excluded"] is True


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
        "scaffold10019\t1\t200\t200\t0\t0\n"
        "scaffold10098\t3\t300\t900\t0\t0\n"
    )
    assert out_file.read_text(encoding="utf-8") == expected_tsv

    reloaded = load_chromosome_summary(out_file)
    assert reloaded is not None
    assert reloaded["total_variants"] == 22
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
    assert result["non_primary_removed_variants"] == 4
    assert result["non_primary_removed_contigs"] == 2
    assert result["kept_variants"] == 17
    assert result["overflow_removed"] == 0

    lines = out_vcf.read_text(encoding="utf-8").splitlines()
    contig_lines = [ln for ln in lines if ln.startswith("##contig=")]
    assert "##contig=<ID=chr1,length=4000>" in contig_lines
    assert not any("##contig=<ID=chr7," in ln for ln in contig_lines)
    assert not any("scaffold" in ln for ln in contig_lines)
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


@pytest.mark.parametrize(
    "contig",
    [
        "scaffold1",
        "scaffold10019",
        "Scaffold_5",
        "contig_12",
        "ctg7",
        "chrUn_KI270302v1",
        "chr1_random",
        "UNPLACED_99",
    ],
)
def test_default_non_primary_contig_regex_matches_scaffolds(contig: str):
    pattern = re.compile(DEFAULT_NON_PRIMARY_CONTIG_REGEX)
    assert pattern.search(contig) is not None


@pytest.mark.parametrize(
    "contig",
    [
        "chr1",
        "chr8",
        "1",
        "Chr01",
        "Gm01",
        "LG3",
        "A01",
        "chr7_p0",
        "chr7_p2",
        "chrX",
        "chrM",
        "chrC",
        "Pt",
        "MT",
    ],
)
def test_default_non_primary_contig_regex_preserves_primary_and_organelle_contigs(contig: str):
    pattern = re.compile(DEFAULT_NON_PRIMARY_CONTIG_REGEX)
    assert pattern.search(contig) is None


def test_is_non_primary_contig_helper_respects_pattern_and_disable():
    assert is_non_primary_contig("scaffold10019") is True
    assert is_non_primary_contig("chr7_p2") is False
    assert is_non_primary_contig("scaffold10019", pattern="") is False
    assert is_non_primary_contig("Gm01", pattern=r"^Gm") is True


def _write_mixed_scaffold_vcf(path: Path) -> Path:
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "##contig=<ID=chr1,length=4000>\n"
        "##contig=<ID=chr7,length=7000>\n"
        "##contig=<ID=scaffold10019,length=1000>\n"
        "##contig=<ID=scaffold10098,length=2000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"
        "chr1\t500\tchr1:500\tA\tG\t100\tPASS\t.\tGT\t0/0\t1/1\n"
        "chr1\t1500\tchr1:1500\tC\tT\t100\tPASS\t.\tGT\t0/1\t1/1\n"
        "chr7\t0\tchr7:0\tA\tG\t100\tPASS\t.\tGT\t0/0\t0/1\n"
        "chr7\t1200\tchr7:1200\tA\tG\t100\tPASS\t.\tGT\t0/0\t1/1\n"
        "chr7\t5500\tchr7:5500\tC\tT\t100\tPASS\t.\tGT\t0/1\t1/1\n"
        "scaffold10019\t200\tscaff19:200\tA\tG\t100\tPASS\t.\tGT\t0/0\t1/1\n"
        "scaffold10098\t300\tscaff98:300\tC\tT\t100\tPASS\t.\tGT\t0/1\t0/1\n"
        "scaffold10098\t600\tscaff98:600\tG\tA\t100\tPASS\t.\tGT\t1/1\t0/0\n"
        "scaffold10098\t900\tscaff98:900\tT\tC\t100\tPASS\t.\tGT\t0/0\t0/1\n",
        encoding="utf-8",
    )
    return path


def test_summarize_and_split_mixed_vcf_drops_non_primary_contigs(tmp_path: Path):
    mixed_vcf = _write_mixed_scaffold_vcf(tmp_path / "mixed.vcf")
    summary = summarize_chromosomes(mixed_vcf, threshold_bp=5_000)

    assert summary["total_variants"] == 9
    assert summary["total_invalid_pos"] == 1
    assert summary["overflow_chromosomes"] == ["chr7"]
    by_chr = {r["chrom"]: r for r in summary["chromosomes"]}
    assert by_chr["chr1"]["excluded"] is False
    assert by_chr["chr7"]["excluded"] is False
    assert by_chr["scaffold10019"]["excluded"] is True
    assert by_chr["scaffold10098"]["excluded"] is True
    assert summary["excluded_contigs"] == {
        "pattern": settings.NON_PRIMARY_CONTIG_REGEX,
        "contigs": 2,
        "variants": 4,
        "single_marker_contigs": 1,
        "examples": ["scaffold10019", "scaffold10098"],
    }

    txt_path = tmp_path / "chromosome_characteristics_summary.txt"
    write_chromosome_summary(summary, txt_path)
    txt_content = txt_path.read_text(encoding="utf-8")
    assert "scaffold10019\t1\t200\t200\t0\t0" in txt_content
    assert "scaffold10098\t3\t300\t900\t0\t0" in txt_content

    out_vcf = tmp_path / "split_clean.vcf"
    split_res = split_overflow_chromosomes(
        mixed_vcf,
        out_vcf,
        threshold_bp=5_000,
        unit_bp=2_000,
    )
    assert split_res["total_variants"] == 9
    assert split_res["invalid_pos_removed"] == 1
    assert split_res["non_primary_removed_variants"] == 4
    assert split_res["non_primary_removed_contigs"] == 2
    assert split_res["kept_variants"] == 4
    assert split_res["kept_variants"] == (
        split_res["total_variants"]
        - split_res["invalid_pos_removed"]
        - split_res["non_primary_removed_variants"]
    )
    assert split_res["overflow_chromosomes"] == ["chr7"]

    out_lines = out_vcf.read_text(encoding="utf-8").splitlines()
    contig_headers = [ln for ln in out_lines if ln.startswith("##contig=")]
    assert not any("scaffold" in ln for ln in contig_headers)
    assert "##contig=<ID=chr1,length=4000>" in contig_headers
    assert "##contig=<ID=chr7_p2,length=2000>" in contig_headers

    data_lines = [ln for ln in out_lines if not ln.startswith("#")]
    assert len(data_lines) == 4
    assert not any("scaffold" in ln.split("\t", 1)[0] for ln in data_lines)
    restored = [restore_coord(ln.split("\t")[0], int(ln.split("\t")[1]), split_res["split_map"]) for ln in data_lines]
    assert ("chr7", 5500) in restored


def test_split_drops_non_primary_before_validate_contig_and_guards_all_dropped(tmp_path: Path):
    mixed_bad_scaffold = tmp_path / "mixed_bad_scaffold.vcf"
    mixed_bad_scaffold.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "chr1\t100\trs1\tA\tG\t100\tPASS\t.\tGT\t0/0\n"
        "scaffold bad;name\t200\trs2\tC\tT\t100\tPASS\t.\tGT\t0/1\n",
        encoding="utf-8",
    )
    out_ok = tmp_path / "out_ok.vcf"
    res = split_overflow_chromosomes(mixed_bad_scaffold, out_ok, threshold_bp=5_000, unit_bp=2_000)
    assert res["non_primary_removed_contigs"] == 1
    assert res["non_primary_removed_variants"] == 1
    assert res["kept_variants"] == 1

    all_scaffold_vcf = tmp_path / "only_scaffolds.vcf"
    all_scaffold_vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "scaffold10019\t200\trs1\tA\tG\t100\tPASS\t.\tGT\t0/0\n"
        "scaffold10098\t300\trs2\tC\tT\t100\tPASS\t.\tGT\t0/1\n",
        encoding="utf-8",
    )
    guard_out = tmp_path / "should_not_exist.vcf"
    with pytest.raises(ValueError, match="Semua varian berada di kontig non-utama"):
        split_overflow_chromosomes(all_scaffold_vcf, guard_out, threshold_bp=5_000, unit_bp=2_000)
    assert not guard_out.exists()


@pytest.mark.skipif(which_tool("bcftools") is None, reason="bcftools tidak tersedia di host")
def test_split_mixed_scaffold_vcf_valid_for_bcftools(tmp_path: Path):
    bcftools_bin = which_tool("bcftools")
    assert bcftools_bin is not None

    mixed_vcf = _write_mixed_scaffold_vcf(tmp_path / "mixed.vcf")
    split_vcf = tmp_path / "split.vcf"
    split_overflow_chromosomes(mixed_vcf, split_vcf, threshold_bp=5_000, unit_bp=2_000)

    hdr_proc = subprocess.run(
        [bcftools_bin, "view", "-h", str(split_vcf)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert hdr_proc.returncode == 0, hdr_proc.stderr
    hdr_contig_lines = [ln for ln in hdr_proc.stdout.splitlines() if ln.startswith("##contig=")]
    assert not any("scaffold" in ln.lower() for ln in hdr_contig_lines)
    assert "scaffold10019" not in hdr_proc.stdout
    assert "scaffold10098" not in hdr_proc.stdout

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
