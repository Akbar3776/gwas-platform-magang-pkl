"""Unit tests untuk fitur tambahan dari GWAS WORKFLOW (SEMUA).md dan HASIL OUTPUT.md:
1. Fase 4C: Multi-Locus Significant SNPs + PVE(%) & FDR (H&B.P.Value)
2. Fase 5.1 & 5.3: Koordinat GFF3 (SOURCE/TYPE/START/END), Tabel GO & GO Enrichment
3. Fase 5.4 / 6–8: Sekuens Flanking 201 bp (100bp+[Alel]+100bp) & Desain Primer3
"""

from __future__ import annotations

from pathlib import Path
import numpy as np

from app.services.go_enrichment_service import run_go_enrichment_analysis
from app.services.primer_service import (
    design_primers_for_flanking,
    extract_or_build_flanking_sequence,
    generate_flanking_and_primers,
)
from app.tasks.multilocus_tasks import (
    compute_bh_fdr,
    compute_snp_pve_pct,
    extract_multilocus_leads_from_tsv,
    write_significant_pve_csv,
)


def test_multilocus_pve_and_fdr_extraction(tmp_path: Path):
    tsv_path = tmp_path / "blink_results.tsv"
    tsv_path.write_text(
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn\n"
        "3\t76985621\tSNP_459187\tA\tG\t0.477578\t1.106297\t0.19\t3.26e-08\t223\n"
        "4\t506141429\tSNP_707230\tT\tC\t0.271300\t-4.683250\t0.72\t2.89e-09\t223\n"
        "5\t1108215563\tSNP_1032422\tG\tA\t0.094170\t-4.368830\t0.56\t1.29e-12\t223\n",
        encoding="utf-8",
    )

    q_vals = compute_bh_fdr(np.array([3.26e-8, 2.89e-9, 1.29e-12]))
    assert len(q_vals) == 3
    assert q_vals[2] <= q_vals[1] <= q_vals[0]

    pve = compute_snp_pve_pct(beta=-4.36883, se=0.56, maf=0.09417, nobs=223, p_val=1.29e-12)
    assert 0.0 < pve < 100.0

    leads = extract_multilocus_leads_from_tsv(tsv_path, method_name="BLINK", trait_name="Clove Number", max_top=5)
    assert len(leads) == 3
    assert leads[0]["snp"] == "SNP_1032422"
    assert leads[0]["trait"] == "Clove Number"
    assert leads[0]["maf"] == 0.09417
    assert leads[0]["nobs"] == 223
    assert leads[0]["pve_pct"] > 0.0
    assert "h_b_p_value" in leads[0]

    csv_out = tmp_path / "BLINK_Significant_SNPs_PVE.csv"
    write_significant_pve_csv(csv_out, leads, trait_name="Clove Number")
    assert csv_out.is_file()
    header = csv_out.read_text(encoding="utf-8").splitlines()[0]
    assert "Phenotype_Variance_Explained(%)" in header
    assert "H&B.P.Value" in header


def test_go_enrichment_disabled_by_default_and_uses_real_go_ids(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALLOW_SYNTHETIC_ANNOTATION", raising=False)
    mapped_without_go = [
        {
            "snp": "chr6:358757461",
            "chr": "6",
            "pos": 358757461,
            "p": 4.31e-6,
            "nearest_gene": "Asa6G01390",
            "nearest_gene_name": "Asa6G01390",
            "category": "Near-gen",
            "genes_in_window": [
                {"gene_id": "UnknownGene1", "gene_name": "UnknownGene1", "distance": 25000},
            ],
        }
    ]
    res_unavail = run_go_enrichment_analysis(
        mapped_rows=mapped_without_go,
        candidate_genes_list=["Asa6G01390", "UnknownGene1"],
        trait_name="Clove Weight",
        output_dir=tmp_path / "unavail",
    )
    assert res_unavail["status"] == "unavailable"
    assert res_unavail["synthetic"] is False
    assert res_unavail["enrichment_terms"] == []
    assert res_unavail["total_genes"] == 2
    for row in res_unavail["go_table"]:
        assert row["go_terms"] == []
        assert row["source"] == "unavailable"
        assert row["synthetic"] is False

    mapped_with_real_go = [
        {
            "snp": "chr1:1000",
            "chr": "1",
            "pos": 1000,
            "p": 1e-8,
            "nearest_gene": "GeneA",
            "nearest_gene_name": "GeneA",
            "category": "Genik",
            "closest_gene": {"gene_id": "GeneA", "go_ids": ["GO:0006357", "GO:0009651"]},
            "genes_in_window": [],
        }
    ]
    res_real = run_go_enrichment_analysis(
        mapped_rows=mapped_with_real_go,
        trait_name="Real Trait",
        output_dir=tmp_path / "real",
    )
    assert res_real["status"] == "completed"
    assert res_real["synthetic"] is False
    assert res_real["go_table"][0]["go_terms"] == ["GO:0006357", "GO:0009651"]
    assert res_real["go_table"][0]["source"] != "unavailable"
    assert len(res_real["enrichment_terms"]) == 2


def test_go_enrichment_synthetic_opt_in_marks_entries(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALLOW_SYNTHETIC_ANNOTATION", "true")
    mapped_rows = [
        {
            "snp": "chr6:358757461",
            "chr": "6",
            "pos": 358757461,
            "p": 4.31e-6,
            "nearest_gene": "Asa6G01390",
            "nearest_gene_name": "Asa6G01390",
            "category": "Near-gen",
            "genes_in_window": [
                {"gene_id": "Asa2G01610", "gene_name": "Asa2G01610", "distance": 25000},
                {"gene_id": "AT1G31650", "gene_name": "ROPGEF14", "distance": 0},
            ],
        }
    ]
    res = run_go_enrichment_analysis(
        mapped_rows=mapped_rows,
        candidate_genes_list=["Asa6G01390", "Asa2G01610", "AT1G31650"],
        trait_name="Clove Weight",
        output_dir=tmp_path,
    )
    assert res["status"] == "completed"
    assert res["synthetic"] is True
    assert res["total_genes"] == 3
    assert len(res["go_table"]) == 3
    assert all(row.get("synthetic") is True for row in res["go_table"])
    assert len(res["enrichment_terms"]) > 0
    assert all(term.get("synthetic") is True for term in res["enrichment_terms"])
    first_term = res["enrichment_terms"][0]
    assert "gene_ratio" in first_term
    assert "p_adjust" in first_term
    assert "count" in first_term
    assert (tmp_path / "Hasil_GO_Enrichment.csv").is_file()
    assert (tmp_path / "Tabel_GO_Kandidat.csv").is_file()


def test_flanking_and_primers_require_fasta_unless_synthetic_enabled(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALLOW_SYNTHETIC_ANNOTATION", raising=False)
    bracketed, raw_seq = extract_or_build_flanking_sequence("6", 358807461, allele="C", fasta_path=None)
    assert bracketed == ""
    assert raw_seq == ""

    lead_snps = [
        {"snp": "chr6:358807461", "chr": "6", "pos": 358807461, "a1": "C", "p": 4.31e-6, "nearest_gene": "Asa6G01390"},
    ]
    no_ref_payload = generate_flanking_and_primers(
        lead_snps=lead_snps,
        trait_name="clove_weight",
        fasta_path=None,
        output_dir=tmp_path / "no_ref",
    )
    assert no_ref_payload["status"] == "no_reference"
    assert no_ref_payload["synthetic"] is False
    assert no_ref_payload["markers_with_primers"] == 0
    assert no_ref_payload["markers"][0]["status"] == "no_reference"
    assert no_ref_payload["markers"][0]["flanking_sequence"] == ""
    assert no_ref_payload["markers"][0]["primer_pairs"] == []

    # With a real FASTA file, flanking extraction succeeds even when synthetic is disabled
    fasta_file = tmp_path / "ref.fa"
    real_seq = (
        "TACCGCTGTTTCTTAAAGAAAAACACACAACGGAACAAACATAAGTACCAAAGTATGATGTAGAAGAAAGATATAAATGTAACAGTTTTAGCATAAGGTC"
        "C"
        "TACTCTAAGATGTAATAAATATATGGATTTTATCTAGCTAATGGAATGATGTAAATGTAACAGTTTTAGCAAGAACAGCCTCCTGATTGTTGTTGATCCT"
    )
    fasta_file.write_text(f">6\n{real_seq}\n", encoding="utf-8")
    pairs = design_primers_for_flanking(real_seq, snp_index_0based=100, max_pairs=5)
    assert len(pairs) >= 1
    assert 100 <= pairs[0]["product_size"] <= 190
    real_payload = generate_flanking_and_primers(
        lead_snps=[{"snp": "chr6:101", "chr": "6", "pos": 101, "a1": "C", "p": 1e-7, "nearest_gene": "G1"}],
        trait_name="clove_weight",
        fasta_path=fasta_file,
        output_dir=tmp_path / "real_ref",
    )
    assert real_payload["status"] == "completed"
    assert real_payload["synthetic"] is False
    assert real_payload["markers_with_primers"] == 1
    assert real_payload["markers"][0]["sequence_length"] == 201
    assert "LEFT PRIMER" in real_payload["markers"][0]["primer3_output"]

    # When synthetic flag is enabled explicitly without FASTA, synthetic markers are labeled synthetic=True
    monkeypatch.setenv("ALLOW_SYNTHETIC_ANNOTATION", "true")
    synth_payload = generate_flanking_and_primers(
        lead_snps=[
            {"snp": "chr6:358807461", "chr": "6", "pos": 358807461, "a1": "C", "p": 4.31e-6, "nearest_gene": "Asa6G01390"},
            {"snp": "chr4:1766400024", "chr": "4", "pos": 1766400024, "a1": "C", "p": 8.12e-6, "nearest_gene": "Asa4G06376"},
        ],
        trait_name="clove_weight",
        output_dir=tmp_path / "synth",
    )
    assert synth_payload["synthetic"] is True
    assert synth_payload["total_markers"] == 2
    assert synth_payload["markers_with_primers"] == 1
    assert all(m.get("synthetic") is True for m in synth_payload["markers"])
    assert "LEFT PRIMER" in synth_payload["markers"][0]["primer3_output"]
    assert synth_payload["markers"][1]["primer3_output"] == "no primers found"
