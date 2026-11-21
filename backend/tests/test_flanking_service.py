"""Tests for Fase 5.4 BIM REF/ALT enrichment and candidate region CSV/BED exports."""

from __future__ import annotations

import csv
from pathlib import Path

from app.services.flanking_service import (
    attach_ref_alt_from_bim,
    collect_significant_snps,
    write_candidate_regions,
)


def test_attach_ref_alt_from_bim_enriches_all_snps_beyond_twenty(tmp_path: Path):
    bim_path = tmp_path / "plink_qc.bim"
    bim_lines: list[str] = []
    snps: list[dict] = []
    bases = [("A", "G"), ("C", "T"), ("G", "A"), ("T", "C")]
    for i in range(25):
        pos = 1000 + i * 100
        alt_base, ref_base = bases[i % len(bases)]
        snp_id = f"rs_bim_{i}"
        # PLINK .bim col 5 = ALT, col 6 = REF
        bim_lines.append(f"chr1\t{snp_id}\t0\t{pos}\t{alt_base}\t{ref_base}")
        snps.append({"snp": snp_id, "chr": "1", "pos": pos, "p": 1e-8 * (i + 1)})
    bim_path.write_text("\n".join(bim_lines) + "\n", encoding="utf-8")

    enriched = attach_ref_alt_from_bim(snps, bim_path)
    assert len(enriched) == 25
    assert all(s.get("ref") and s.get("alt") for s in enriched)
    assert enriched[24]["alt"] == bases[24 % 4][0]
    assert enriched[24]["ref"] == bases[24 % 4][1]


def test_collect_and_write_candidate_regions_csv_bed_restores_coords_and_engine(tmp_path: Path):
    split_map = {
        "threshold_bp": 5_000,
        "unit_bp": 2_000,
        "parts": {
            "chr7_p2": {"orig_chr": "chr7", "part": 2, "offset_bp": 4_000},
        },
    }
    bim_path = tmp_path / "plink_qc.bim"
    bim_path.write_text(
        "chr7_p2\tchr7:5500\t0\t1500\tG\tA\n"
        "chr1\tchr1:50\t0\t50\tT\tC\n",
        encoding="utf-8",
    )

    merged = collect_significant_snps(
        lead_snps=[{"snp": "chr7:5500", "chr": "7_p2", "pos": 1500, "p": 1e-10}],
        multilocus_snps=[
            {
                "snp": "chr1:50",
                "chr": "chr1",
                "pos": 50,
                "p": 1e-7,
                "method": "BLINK",
                "is_significant": True,
            },
            {
                "snp": "chr1:900",
                "chr": "chr1",
                "pos": 900,
                "p": 0.25,
                "method": "FARMCPU",
                "is_significant": False,
            },
        ],
        engines_by_method={"BLINK": "python-approx", "FARMCPU": "gapit"},
        split_map=split_map,
    )
    attach_ref_alt_from_bim(merged, bim_path, split_map=split_map)

    assert len(merged) == 2
    csv_out = tmp_path / "candidate_regions.csv"
    bed_out = tmp_path / "candidate_regions.bed"
    summary = write_candidate_regions(merged, window_bp=100, csv_path=csv_out, bed_path=bed_out)
    assert summary["n_regions"] == 2

    with csv_out.open("r", encoding="utf-8") as cf:
        csv_rows = list(csv.DictReader(cf))
    assert list(csv_rows[0].keys()) == [
        "SNP",
        "Chromosome_GFF",
        "True_Pos",
        "REF",
        "ALT",
        "Flanking_Start",
        "Flanking_End",
        "P.value",
        "Source",
        "Engine",
    ]
    assert csv_rows[0]["SNP"] == "chr7:5500"
    assert csv_rows[0]["Chromosome_GFF"] == "chr7"
    assert csv_rows[0]["True_Pos"] == "5500"
    assert csv_rows[0]["REF"] == "A"
    assert csv_rows[0]["ALT"] == "G"
    assert csv_rows[0]["Flanking_Start"] == "5400"
    assert csv_rows[0]["Flanking_End"] == "5600"
    assert csv_rows[0]["Engine"] == "gemma"

    assert csv_rows[1]["SNP"] == "chr1:50"
    assert csv_rows[1]["REF"] == "C"
    assert csv_rows[1]["ALT"] == "T"
    assert csv_rows[1]["Flanking_Start"] == "1"
    assert csv_rows[1]["Flanking_End"] == "150"
    assert csv_rows[1]["Engine"] == "python-approx"

    bed_lines = bed_out.read_text(encoding="utf-8").splitlines()
    assert bed_lines == [
        "chr7\t5399\t5600\tchr7:5500",
        "chr1\t0\t150\tchr1:50",
    ]
    # Unclamped region length == 2 * window + 1
    c0, s0, e0, _ = bed_lines[0].split("\t")
    assert c0 == "chr7"
    assert int(e0) - int(s0) == 2 * 100 + 1
