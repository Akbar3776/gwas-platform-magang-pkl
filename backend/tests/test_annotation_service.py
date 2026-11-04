from pathlib import Path

import pytest

from app.services.annotation_service import (
    map_lead_snps_to_genes,
    normalize_chr_name,
    parse_gff3_genes,
)


def test_normalize_chr_name():
    assert normalize_chr_name("chr1") == "1"
    assert normalize_chr_name("Chr01") == "1"
    assert normalize_chr_name("Chr22") == "22"
    assert normalize_chr_name("CHROMOSOME_X") == "X"
    assert normalize_chr_name("1") == "1"
    assert normalize_chr_name("NC_003070.9") == "NC_003070.9"


def test_parse_gff3_genes():
    gff_fixture = Path(__file__).parent / "fixtures" / "data" / "toy_annotation.gff3"
    if not gff_fixture.is_file():
        from tests.fixtures.make_fixtures import generate_toy_fixtures
        generate_toy_fixtures()

    genes_by_chr, seqids = parse_gff3_genes(gff_fixture, feature_type="gene")
    assert len(genes_by_chr) > 0
    assert "1" in genes_by_chr or 1 in genes_by_chr

    # Check ordering
    for c, glist in genes_by_chr.items():
        for i in range(len(glist) - 1):
            assert glist[i]["start"] <= glist[i + 1]["start"]


def test_map_lead_snps_to_genes(tmp_path):
    # Buat mini GFF3 di tmp_path
    mini_gff = tmp_path / "mini.gff3"
    content = (
        "##gff-version 3\n"
        "1\tSOURCE\tgene\t1000\t2000\t.\t+\t.\tID=gene:GENE_A;Name=GeneA;Note=Test kinase\n"
        "1\tSOURCE\tgene\t100000\t105000\t.\t-\t.\tID=GENE_B;Name=GeneB;Note=Test receptor\n"
    )
    mini_gff.write_text(content, encoding="utf-8")

    genes_by_chr, _ = parse_gff3_genes(mini_gff)

    lead_snps = [
        {"snp": "SNP_inside", "chr": "chr1", "pos": 1500, "p": 1e-8},      # Inside GENE_A -> Genik
        {"snp": "SNP_near", "chr": "1", "pos": 110000, "p": 2e-7},        # 5000 bp from GENE_B -> Near-gen (window 50kb)
        {"snp": "SNP_inter", "chr": "1", "pos": 500000, "p": 5e-6},       # >100kb -> Intergenik
    ]

    mapped, counts = map_lead_snps_to_genes(lead_snps, genes_by_chr, window_bp=50000)
    assert len(mapped) == 3

    # 1. SNP_inside
    assert mapped[0]["category"] == "Genik"
    assert mapped[0]["nearest_gene"] == "GENE_A"
    assert mapped[0]["closest_gene"]["gene_id"] == "GENE_A"
    assert mapped[0]["closest_gene"]["gene_name"] == "GeneA"
    assert mapped[0]["distance_bp"] == 0
    assert mapped[0]["distance_to_closest_gene"] == 0
    assert mapped[0]["genes_in_window"][0]["gene_id"] == "GENE_A"
    assert mapped[0]["genes_in_window"][0]["distance"] == 0

    # 2. SNP_near
    assert mapped[1]["category"] == "Near-gen"
    assert mapped[1]["nearest_gene"] == "GENE_B"
    assert mapped[1]["closest_gene"]["gene_id"] == "GENE_B"
    assert mapped[1]["distance_bp"] == 5000
    assert mapped[1]["distance_to_closest_gene"] == 5000
    assert mapped[1]["genes_in_window"][0]["gene_id"] == "GENE_B"
    assert mapped[1]["genes_in_window"][0]["distance"] == 5000

    # 3. SNP_inter
    assert mapped[2]["category"] == "Intergenik"
    assert mapped[2]["distance_bp"] > 50000
    assert mapped[2]["distance_to_closest_gene"] > 50000

    # Check counts
    assert counts["Genik"] == 1
    assert counts["Near-gen"] == 1
    assert counts["Intergenik"] == 1


def test_normalize_candidate_genes_payload():
    from app.api.routes.results import _normalize_candidate_genes_payload

    legacy_payload = {
        "window_bp": 100000,
        "window_kb": 100,
        "counts": {"Genik": 1, "Near-gen": 1, "Intergenik": 0},
        "rows": [
            {
                "snp": "Chr2_43381920",
                "chr": "2",
                "pos": 43381920,
                "p": 0.00018,
                "nearest_gene": "Asa2G01356",
                "nearest_gene_name": "Asa2G01356",
                "distance_bp": 0,
                "category": "Genik",
                "strand": "-",
                "description": "",
                "genes_in_window": [
                    {
                        "id": "Asa2G01356",
                        "name": "Asa2G01356",
                        "start": 43381845,
                        "end": 43382210,
                        "strand": "-",
                        "desc": "",
                    }
                ],
            }
        ],
    }

    normalized = _normalize_candidate_genes_payload(legacy_payload)
    assert normalized["status"] == "completed"
    assert normalized["total_lead_snps"] == 1
    assert len(normalized["mappings"]) == 1
    assert normalized["candidate_genes_list"] == ["Asa2G01356"]
    row = normalized["mappings"][0]
    assert row["closest_gene"]["gene_id"] == "Asa2G01356"
    assert row["distance_to_closest_gene"] == 0
    assert row["genes_in_window"][0]["gene_id"] == "Asa2G01356"
    assert row["genes_in_window"][0]["distance"] == 0
