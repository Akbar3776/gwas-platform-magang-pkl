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


def test_go_enrichment_and_ortholog_mapping(tmp_path: Path):
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
    assert res["total_genes"] == 3
    assert len(res["go_table"]) == 3
    assert len(res["enrichment_terms"]) > 0
    first_term = res["enrichment_terms"][0]
    assert "gene_ratio" in first_term
    assert "p_adjust" in first_term
    assert "count" in first_term
    assert (tmp_path / "Hasil_GO_Enrichment.csv").is_file()
    assert (tmp_path / "Tabel_GO_Kandidat.csv").is_file()


def test_flanking_201bp_and_primer3_design(tmp_path: Path):
    # Test curated SNP #1 from HASIL OUTPUT.md (clove_weight_6_358807461)
    bracketed, raw_seq = extract_or_build_flanking_sequence("6", 358807461, allele="C")
    assert len(raw_seq) == 201
    assert "[C]" in bracketed

    pairs = design_primers_for_flanking(raw_seq, snp_index_0based=100, max_pairs=5)
    assert len(pairs) >= 1
    assert 100 <= pairs[0]["product_size"] <= 190

    # Test curated SNP #10 from HASIL OUTPUT.md (AT-rich sequence -> no primers found)
    _, raw_at_rich = extract_or_build_flanking_sequence("4", 1766400024, allele="C")
    pairs_at = design_primers_for_flanking(raw_at_rich, snp_index_0based=100, max_pairs=5)
    assert pairs_at == []

    # Test full batch generation & file outputs
    lead_snps = [
        {"snp": "chr6:358807461", "chr": "6", "pos": 358807461, "a1": "C", "p": 4.31e-6, "nearest_gene": "Asa6G01390"},
        {"snp": "chr4:1766400024", "chr": "4", "pos": 1766400024, "a1": "C", "p": 8.12e-6, "nearest_gene": "Asa4G06376"},
    ]
    payload = generate_flanking_and_primers(
        lead_snps=lead_snps,
        trait_name="clove_weight",
        output_dir=tmp_path,
    )
    assert payload["total_markers"] == 2
    assert payload["markers_with_primers"] == 1
    assert payload["markers"][0]["marker_id"] == "clove_weight_6_358807461"
    assert "LEFT PRIMER" in payload["markers"][0]["primer3_output"]
    assert payload["markers"][1]["primer3_output"] == "no primers found"
    assert (tmp_path / "Flanking_dan_Primer3.txt").is_file()
    assert (tmp_path / "Desain_Primer_Kandidat.csv").is_file()
