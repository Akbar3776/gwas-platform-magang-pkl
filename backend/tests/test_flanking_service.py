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


def test_extract_regions_fasta_matches_slice_enforces_cap_and_handles_missing_fasta(
    overflow_dataset: dict,
    tmp_path: Path,
):
    from app.services.flanking_service import extract_regions_fasta

    fasta_lines = overflow_dataset["fasta_path"].read_text(encoding="utf-8").splitlines()
    chr7_seq = "".join(ln.strip() for ln in fasta_lines if not ln.startswith(">") and ln.strip())
    # Grab true chr7 sequence directly from FASTA by contig section
    seqs: dict[str, list[str]] = {}
    cur = None
    for ln in fasta_lines:
        if ln.startswith(">"):
            cur = ln[1:].strip().split()[0]
            seqs[cur] = []
        elif cur is not None:
            seqs[cur].append(ln.strip())
    chr7_seq = "".join(seqs["chr7"]).upper()

    bed_path = tmp_path / "regions.bed"
    # Region 1: 101 bp (5450..5550 -> 0-based 5449..5550), Region 2: 201 bp -> total 302 bp
    bed_path.write_text(
        "chr7\t5449\t5550\tchr7:5500\n"
        "chr7\t6100\t6301\tchr7:6200\n",
        encoding="utf-8",
    )

    out_fa = tmp_path / "candidate_regions.fa"
    res_full = extract_regions_fasta(bed_path, overflow_dataset["fasta_path"], out_fa, max_total_bp=500)
    assert res_full["status"] == "completed"
    assert res_full["written_regions"] == 2
    assert res_full["truncated"] is False
    assert res_full["total_bp"] == 302

    fa_lines = out_fa.read_text(encoding="utf-8").splitlines()
    assert fa_lines[0] == ">chr7:5500::chr7:5449-5550"
    assert fa_lines[1] + fa_lines[2] == chr7_seq[5449:5550]

    # Enforce tight cap: allows first region (101 bp) and stops before second (201 bp)
    capped_fa = tmp_path / "capped.fa"
    res_cap = extract_regions_fasta(bed_path, overflow_dataset["fasta_path"], capped_fa, max_total_bp=150)
    assert res_cap["status"] == "completed"
    assert res_cap["written_regions"] == 1
    assert res_cap["truncated"] is True
    assert res_cap["truncated_count"] == 1
    assert res_cap["total_bp"] == 101
    assert capped_fa.read_text(encoding="utf-8").count(">") == 1

    # Missing FASTA must NOT create output file
    missing_fa = tmp_path / "should_not_exist.fa"
    res_missing = extract_regions_fasta(bed_path, None, missing_fa)
    assert res_missing["status"] == "no_reference"
    assert not missing_fa.exists()


def test_run_annotation_sets_regions_status_no_reference_without_fasta(
    tmp_path: Path,
    monkeypatch,
):
    import json
    from app.tasks import annotation_tasks
    from tests.test_annotation_service import _setup_annotation_job

    gff_text = "##gff-version 3\n1\tPhytozome\tgene\t1000\t2000\t.\t+\t.\tID=GENE_1;Name=Gene1\n"
    job, dataset, job_root = _setup_annotation_job(tmp_path, monkeypatch, gff_text)
    qc_dir = job_root / "qc"
    qc_dir.mkdir(parents=True, exist_ok=True)
    (qc_dir / "plink_qc.bim").write_text("1\trs1\t0\t1500\tG\tA\n", encoding="utf-8")
    (job_root / "postgwas").mkdir(parents=True, exist_ok=True)
    (job_root / "postgwas" / "lead_snps.json").write_text(
        json.dumps({"count": 1, "snps": [{"snp": "rs1", "chr": "1", "pos": 1500, "p": 1e-8}]}),
        encoding="utf-8",
    )

    annotation_tasks.run_annotation(prev_result={}, job_id=str(job.id), dataset_id=str(dataset.id))
    anno_dir = job_root / "annotation"
    assert (anno_dir / "candidate_regions.bed").is_file()
    assert not (anno_dir / "candidate_regions.fa").exists()
    primers = json.loads((anno_dir / "primers.json").read_text(encoding="utf-8"))
    assert primers["regions_status"] == "no_reference"
    assert primers["candidate_regions"]["fasta"] is None


def test_build_kasp_input_masks_neighbors_and_reports_validation_statuses(
    overflow_dataset: dict,
    tmp_path: Path,
):
    from app.services.flanking_service import build_kasp_input

    fasta_lines = overflow_dataset["fasta_path"].read_text(encoding="utf-8").splitlines()
    seqs: dict[str, list[str]] = {}
    cur = None
    for ln in fasta_lines:
        if ln.startswith(">"):
            cur = ln[1:].strip().split()[0]
            seqs[cur] = []
        elif cur is not None:
            seqs[cur].append(ln.strip())
    chr7_seq = "".join(seqs["chr7"]).upper()
    ref_5500 = chr7_seq[5500 - 1]
    alt_5500 = {"A": "G", "G": "A", "C": "T", "T": "C"}[ref_5500]
    ref_30 = chr7_seq[30 - 1]
    alt_30 = {"A": "G", "G": "A", "C": "T", "T": "C"}[ref_30]

    split_map = {
        "threshold_bp": overflow_dataset["split_threshold_bp"],
        "unit_bp": overflow_dataset["split_unit_bp"],
        "parts": {
            "chr7_p0": {"orig_chr": "chr7", "part": 0, "offset_bp": 0},
            "chr7_p2": {"orig_chr": "chr7", "part": 2, "offset_bp": 4_000},
        },
    }
    bim_path = tmp_path / "split.bim"
    bim_path.write_text(
        f"chr7_p2\tchr7:5500\t0\t1500\t{alt_5500}\t{ref_5500}\n"
        "chr7_p2\tchr7:5540\t0\t1540\tT\tC\n",
        encoding="utf-8",
    )

    snps = [
        {"snp": "chr7:5500", "chr": "7_p2", "pos": 1500, "ref": ref_5500, "alt": alt_5500},
        {"snp": "chr7:5500_mismatch", "chr": "7_p2", "pos": 1500, "ref": alt_5500, "alt": ref_5500},
        {"snp": "chr7:30_short", "chr": "chr7", "pos": 30, "ref": ref_30, "alt": alt_30},
        {"snp": "chr7:5500_indel", "chr": "chr7", "pos": 5500, "ref": "AT", "alt": "A"},
    ]

    rows = build_kasp_input(
        snps,
        overflow_dataset["fasta_path"],
        bim_path,
        split_map=split_map,
    )
    by_id = {row["snp_id"]: row for row in rows}

    ok_row = by_id["chr7:5500"]
    assert ok_row["status"] == "ok"
    assert ok_row["orig_chr"] == "chr7"
    assert ok_row["orig_pos"] == 5500
    assert ok_row["left_len"] == 150
    assert ok_row["right_len"] == 150
    assert ok_row["masked_neighbors"] == 1
    assert ok_row["flanking"].count("[") == 1
    left_flank, right_flank = ok_row["flanking"].split(f"[{ref_5500}/{alt_5500}]")
    assert len(left_flank) == 150
    assert len(right_flank) == 150
    assert right_flank[39] == "N"

    assert by_id["chr7:5500_mismatch"]["status"] == "ref_mismatch"
    assert by_id["chr7:30_short"]["status"] == "flank_too_short"
    assert by_id["chr7:5500_indel"]["status"] == "unsupported_variant"
