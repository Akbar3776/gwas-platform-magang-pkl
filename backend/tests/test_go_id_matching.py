"""Unit tests untuk G06: indeks GO, fallback isoform, prioritas 3 sumber, dedup, dan go_stats."""

from __future__ import annotations

import logging
from pathlib import Path

from app.services.gene_id_utils import strip_isoform_suffix
from app.services.go_enrichment_service import run_go_enrichment_analysis


def test_strip_isoform_suffix_strips_single_numeric_suffix():
    assert strip_isoform_suffix("ASA7G00001.1") == "ASA7G00001"
    assert strip_isoform_suffix("GLYMA.01G000100") == "GLYMA.01G000100"
    assert strip_isoform_suffix("SOLYC01G005000.3.1") == "SOLYC01G005000.3"


def test_go_isoform_fallback_both_directions_and_exact_wins(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALLOW_SYNTHETIC_ANNOTATION", raising=False)

    # 1. Fitur GFF level gene (G1, G2) vs mapping level transkrip (G1.1, G2.1)
    gff_gene = tmp_path / "genes.gff3"
    gff_gene.write_text(
        "##gff-version 3\n"
        "chr1\tEVM\tgene\t100\t500\t.\t+\t.\tID=G1\n"
        "chr1\tEVM\tgene\t600\t900\t.\t+\t.\tID=G2\n",
        encoding="utf-8",
    )
    map_tx = tmp_path / "tx.tsv"
    map_tx.write_text("G1.1\tGO:0006357\nG2.1\tGO:0009651\n", encoding="utf-8")

    res_gene = run_go_enrichment_analysis(
        mapped_rows=[
            {"snp": "s1", "chr": "1", "pos": 200, "p": 1e-8, "nearest_gene": "G1", "category": "Genik"},
            {"snp": "s2", "chr": "1", "pos": 700, "p": 1e-7, "nearest_gene": "G2", "category": "Genik"},
        ],
        candidate_genes_list=["G1", "G2"],
        gff_path=gff_gene,
        go_mapping_path=map_tx,
        feature_type="gene",
    )
    assert res_gene["status"] == "completed"
    assert res_gene["background_total"] == 2
    assert res_gene["go_stats"]["match"]["n_isoform_fallback"] == 2
    assert res_gene["go_stats"]["match"]["n_exact"] == 0
    assert res_gene["go_stats"]["match"]["n_gff_features_matched"] == 2

    # 2. Arah sebaliknya: fitur mRNA (G1.1) + mapping level gen (G1)
    gff_mrna = tmp_path / "mrna.gff3"
    gff_mrna.write_text(
        "##gff-version 3\n"
        "chr1\tEVM\tmRNA\t100\t500\t.\t+\t.\tID=G1.1\n",
        encoding="utf-8",
    )
    map_gene = tmp_path / "gene.tsv"
    map_gene.write_text("G1\tGO:0006357\n", encoding="utf-8")

    res_mrna = run_go_enrichment_analysis(
        mapped_rows=[{"snp": "s1", "chr": "1", "pos": 200, "p": 1e-8, "nearest_gene": "G1.1", "category": "Genik"}],
        candidate_genes_list=["G1.1"],
        gff_path=gff_mrna,
        go_mapping_path=map_gene,
        feature_type="mRNA",
    )
    assert res_mrna["status"] == "completed"
    assert res_mrna["go_stats"]["match"]["n_isoform_fallback"] == 1

    # 3. Mapping punya G1 DAN G1.1 dengan term berbeda -> kandidat G1 hanya memakai exact
    map_both = tmp_path / "both.tsv"
    map_both.write_text("G1\tGO:0006357\nG1.1\tGO:0009651\n", encoding="utf-8")
    res_both = run_go_enrichment_analysis(
        mapped_rows=[{"snp": "s1", "chr": "1", "pos": 200, "p": 1e-8, "nearest_gene": "G1", "category": "Genik"}],
        candidate_genes_list=["G1"],
        gff_path=gff_gene,
        go_mapping_path=map_both,
    )
    assert res_both["go_table"][0]["go_terms"] == ["GO:0006357"]
    assert res_both["go_stats"]["match"]["n_exact"] == 1
    assert res_both["go_stats"]["match"]["n_isoform_fallback"] == 0


def test_go_three_source_priority_dedup_no_double_count_and_logging(tmp_path: Path, monkeypatch, caplog):
    monkeypatch.delenv("ALLOW_SYNTHETIC_ANNOTATION", raising=False)

    gff = tmp_path / "anno.gff3"
    gff.write_text(
        "##gff-version 3\n"
        "chr1\tEVM\tgene\t100\t200\t.\t+\t.\tID=G1\n"
        "chr1\tEVM\tgene\t300\t400\t.\t+\t.\tID=G2\n"
        "chr1\tEVM\tgene\t500\t600\t.\t+\t.\tID=G3\n",
        encoding="utf-8",
    )
    tsv = tmp_path / "dup.tsv"
    # Dua isoform dengan GO sama + baris duplikat -> dedup
    tsv.write_text(
        "G1.1\tGO:0009651\n"
        "G2.1\tGO:0009651\n"
        "G2.2\tGO:0009651\n"
        "G2.2\tGO:0009651\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.INFO):
        res = run_go_enrichment_analysis(
            mapped_rows=[
                {
                    "snp": "s1",
                    "chr": "1",
                    "pos": 150,
                    "p": 1e-8,
                    "nearest_gene": "G1",
                    "category": "Genik",
                    "closest_gene": {"gene_id": "G1", "go_ids": ["GO:0006357"]},
                },
                {"snp": "s2", "chr": "1", "pos": 350, "p": 1e-7, "nearest_gene": "G2", "category": "Genik"},
            ],
            candidate_genes_list=["G1", "G2"],
            gff_path=gff,
            go_mapping_path=tsv,
        )

    by_gene = {r["gene_id"]: r for r in res["go_table"]}
    assert by_gene["G1"]["source"] == "gff3"
    assert by_gene["G1"]["go_terms"] == ["GO:0006357"]
    assert by_gene["G2"]["source"] == "mapping"
    assert by_gene["G2"]["go_terms"] == ["GO:0009651"]
    # Tidak ada hitung ganda G2.1 / G2.2 + G2 di background (hanya G1, G2 dari GFF)
    assert res["background_total"] == 2
    assert res["go_stats"]["mapping"]["n_pairs"] == 3
    assert any("[GO] Kecocokan ID" in rec.message for rec in caplog.records)

    # Kasus 0% cocok harus memancarkan [WARN]
    caplog.clear()
    tsv_nomatch = tmp_path / "nomatch.tsv"
    tsv_nomatch.write_text("OTHER1\tGO:0006357\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        res0 = run_go_enrichment_analysis(
            mapped_rows=[{"snp": "s1", "chr": "1", "pos": 150, "p": 1e-8, "nearest_gene": "G1", "category": "Genik"}],
            candidate_genes_list=["G1"],
            gff_path=gff,
            go_mapping_path=tsv_nomatch,
        )
    assert res0["go_stats"]["match"]["n_gff_features_matched"] == 0
    assert any("[WARN]" in rec.message for rec in caplog.records)
