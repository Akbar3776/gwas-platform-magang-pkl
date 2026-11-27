"""Unit tests untuk G11: background universe mengikuti pola kontig job dan feature_type."""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.services.go_enrichment_service import (
    resolve_job_contig_pattern,
    run_go_enrichment_analysis,
)


def test_resolve_job_contig_pattern_cases():
    assert resolve_job_contig_pattern({"contig_filter": {"pattern": "X"}}) == ("X", "job")
    assert resolve_job_contig_pattern({"contig_filter": {"pattern": ""}}) == ("", "job")
    assert resolve_job_contig_pattern({}) == ("", "legacy_none")
    assert resolve_job_contig_pattern(None) == ("", "legacy_none")


def test_background_universe_filters_non_primary_contigs_and_keeps_legacy_candidates(tmp_path: Path, monkeypatch, caplog):
    monkeypatch.delenv("ALLOW_SYNTHETIC_ANNOTATION", raising=False)

    gff = tmp_path / "contigs.gff3"
    gff.write_text(
        "##gff-version 3\n"
        "chr1\tEVM\tgene\t100\t200\t.\t+\t.\tID=G_CHR1;Ontology_term=GO:0006357\n"
        "scaffold12\tEVM\tgene\t100\t200\t.\t+\t.\tID=G_SCAF;Ontology_term=GO:0006357\n"
        "chrUn_KI270302v1\tEVM\tgene\t100\t200\t.\t+\t.\tID=G_UN;Ontology_term=GO:0009651\n"
        "Gm01\tEVM\tgene\t100\t200\t.\t+\t.\tID=G_GM01;Ontology_term=GO:0009651\n"
        "LG3\tEVM\tgene\t100\t200\t.\t+\t.\tID=G_LG3;Ontology_term=GO:0009651\n",
        encoding="utf-8",
    )

    # 1. Pattern settings (contig_filter_pattern=None) -> 3 primary (chr1, Gm01, LG3), 2 excluded
    res_settings = run_go_enrichment_analysis(
        mapped_rows=[{"snp": "s1", "chr": "1", "pos": 150, "p": 1e-8, "nearest_gene": "G_CHR1", "category": "Genik"}],
        candidate_genes_list=["G_CHR1"],
        gff_path=gff,
    )
    bg_s = res_settings["go_stats"]["background"]
    assert bg_s["source"] == "gff_primary"
    assert bg_s["pattern_source"] == "settings"
    assert bg_s["n_universe"] == 3
    assert bg_s["n_excluded_non_primary"] == 2
    assert res_settings["background_total"] == 3

    # 2. Pattern kosong "" -> semua 5 fitur GFF (source="gff_all")
    res_empty = run_go_enrichment_analysis(
        mapped_rows=[{"snp": "s1", "chr": "1", "pos": 150, "p": 1e-8, "nearest_gene": "G_CHR1", "category": "Genik"}],
        candidate_genes_list=["G_CHR1"],
        gff_path=gff,
        contig_filter_pattern="",
    )
    assert res_empty["go_stats"]["background"]["source"] == "gff_all"
    assert res_empty["go_stats"]["background"]["n_universe"] == 5
    assert res_empty["go_stats"]["background"]["n_excluded_non_primary"] == 0

    # 3. Kandidat di scaffold12 tetap tampil di tabel dan masuk universe -> n_candidates_outside_universe=1
    res_legacy_cand = run_go_enrichment_analysis(
        mapped_rows=[{"snp": "s2", "chr": "scaffold12", "pos": 150, "p": 1e-8, "nearest_gene": "G_SCAF", "category": "Genik"}],
        candidate_genes_list=["G_SCAF"],
        gff_path=gff,
        contig_filter_pattern=settings.NON_PRIMARY_CONTIG_REGEX,
    )
    assert res_legacy_cand["go_table"][0]["gene_id"] == "G_SCAF"
    assert res_legacy_cand["go_stats"]["background"]["n_candidates_outside_universe"] == 1
    assert res_legacy_cand["background_total"] == 4

    # 4. GFF hanya scaffold* -> universe kosong fallback ke mapping_file + WARN
    gff_only_scaf = tmp_path / "only_scaf.gff3"
    gff_only_scaf.write_text(
        "##gff-version 3\n"
        "scaffold1\tEVM\tgene\t100\t200\t.\t+\t.\tID=G_S1\n",
        encoding="utf-8",
    )
    tsv_fallback = tmp_path / "map.tsv"
    tsv_fallback.write_text("G_S1\tGO:0006357\nG_S2\tGO:0009651\n", encoding="utf-8")
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        res_fb = run_go_enrichment_analysis(
            mapped_rows=[{"snp": "s1", "chr": "scaffold1", "pos": 150, "p": 1e-8, "nearest_gene": "G_S1", "category": "Genik"}],
            candidate_genes_list=["G_S1"],
            gff_path=gff_only_scaf,
            go_mapping_path=tsv_fallback,
            contig_filter_pattern=settings.NON_PRIMARY_CONTIG_REGEX,
        )
    assert res_fb["go_stats"]["background"]["source"] == "mapping_file"
    assert any("Background GFF kosong" in r.message for r in caplog.records)
