"""Unit tests untuk G09: integrasi ontologi OBO ke GO enrichment dan metadata upload."""

from __future__ import annotations

import io
import uuid
from pathlib import Path

from starlette.datastructures import UploadFile

from app.api.routes import upload as upload_routes
from app.core.config import settings
from app.db.engine.models import Dataset, UploadStatus, User, UserRole
from app.services.go_enrichment_service import run_go_enrichment_analysis
from app.services.go_ontology_service import clear_go_ontology_cache

FIXTURE_OBO = Path(__file__).parent / "fixtures" / "go_mini.obo"


def test_go_enrichment_normalizes_mapping_alt_chain_drop_and_dedups(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALLOW_SYNTHETIC_ANNOTATION", raising=False)
    monkeypatch.setattr(settings, "GO_OBO_PATH", str(FIXTURE_OBO))
    clear_go_ontology_cache()

    tsv = tmp_path / "map.tsv"
    tsv.write_text(
        "G1.1\tGO:0016021\tunknown\told name\n"
        "G1.1\tGO:0000275\n"
        "G1.1\tGO:9000001\n"
        "G1.1\tGO:0006357\n"
        "G1.1\tGO:0055114\n"
        "G2.1\tGO:0055114\n"
        "G3.1\tGO:7777777\n",
        encoding="utf-8",
    )
    gff = tmp_path / "g.gff3"
    gff.write_text(
        "##gff-version 3\n"
        "chr1\tEVM\tgene\t100\t200\t.\t+\t.\tID=G1\n"
        "chr1\tEVM\tgene\t300\t400\t.\t+\t.\tID=G2\n"
        "chr1\tEVM\tgene\t500\t600\t.\t+\t.\tID=G3\n",
        encoding="utf-8",
    )

    res = run_go_enrichment_analysis(
        mapped_rows=[
            {"snp": "s1", "chr": "1", "pos": 150, "p": 1e-8, "nearest_gene": "G1", "category": "Genik"},
            {"snp": "s2", "chr": "1", "pos": 350, "p": 1e-7, "nearest_gene": "G2", "category": "Genik"},
            {"snp": "s3", "chr": "1", "pos": 550, "p": 1e-6, "nearest_gene": "G3", "category": "Genik"},
        ],
        candidate_genes_list=["G1", "G2", "G3"],
        gff_path=gff,
        go_mapping_path=tsv,
    )

    stats = res["go_stats"]
    assert stats["obo"] == {
        "available": True,
        "version": "releases/2099-01-01",
        "n_rows_via_alt_id": 1,
        "n_rows_obsolete_remapped": 2,
        "n_rows_obsolete_dropped": 2,
        "n_terms_unknown": 1,
    }
    assert stats["mapping"]["n_genes_raw"] == 3
    assert stats["mapping"]["n_pairs_raw"] == 7
    assert stats["mapping"]["n_genes"] == 2
    assert stats["mapping"]["n_pairs"] == 4
    assert stats["match"]["n_gff_features_matched"] == 2

    by_gene = {r["gene_id"]: r for r in res["go_table"]}
    assert sorted(by_gene["G1"]["go_terms"]) == ["GO:0006357", "GO:0016020", "GO:0045259"]
    assert sorted(by_gene["G1"]["cc_ids"]) == ["GO:0016020", "GO:0045259"]
    assert by_gene["G1"]["bp_ids"] == ["GO:0006357"]
    assert "membrane (GO:0016020)" in by_gene["G1"]["cellular_component"]
    assert by_gene["G2"]["go_terms"] == []
    assert by_gene["G3"]["unknown_ids"] == ["GO:7777777"]
    assert all(not str(t.get("description", "")).startswith("obsolete ") for t in res["enrichment_terms"])


def test_go_enrichment_normalizes_gff_ontology_term_and_row_go_ids(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALLOW_SYNTHETIC_ANNOTATION", raising=False)
    monkeypatch.setattr(settings, "GO_OBO_PATH", str(FIXTURE_OBO))
    clear_go_ontology_cache()

    gff = tmp_path / "anno.gff3"
    gff.write_text(
        "##gff-version 3\n"
        "chr1\tEVM\tgene\t100\t200\t.\t+\t.\tID=G_GFF;Ontology_term=GO:0016021,GO:0055114\n",
        encoding="utf-8",
    )
    res_gff = run_go_enrichment_analysis(
        mapped_rows=[{"snp": "s1", "chr": "1", "pos": 150, "p": 1e-8, "nearest_gene": "G_GFF", "category": "Genik"}],
        candidate_genes_list=["G_GFF"],
        gff_path=gff,
    )
    assert res_gff["go_table"][0]["go_terms"] == ["GO:0016020"]
    assert res_gff["go_table"][0]["cc_ids"] == ["GO:0016020"]

    res_row = run_go_enrichment_analysis(
        mapped_rows=[
            {
                "snp": "s2",
                "chr": "1",
                "pos": 250,
                "p": 1e-8,
                "nearest_gene": "G_ROW",
                "category": "Genik",
                "closest_gene": {"gene_id": "G_ROW", "go_ids": ["GO:0000275", "GO:0055114"]},
            }
        ],
        candidate_genes_list=["G_ROW"],
    )
    assert res_row["go_table"][0]["go_terms"] == ["GO:0045259"]
    assert res_row["go_table"][0]["cc_ids"] == ["GO:0045259"]


def test_go_enrichment_without_obo_matches_g06_behavior(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALLOW_SYNTHETIC_ANNOTATION", raising=False)
    tsv = tmp_path / "map.tsv"
    tsv.write_text("G1.1\tGO:0016021\nG1.1\tGO:0055114\n", encoding="utf-8")
    res = run_go_enrichment_analysis(
        mapped_rows=[{"snp": "s1", "chr": "1", "pos": 100, "p": 1e-8, "nearest_gene": "G1", "category": "Genik"}],
        candidate_genes_list=["G1"],
        go_mapping_path=tsv,
    )
    assert res["go_stats"]["obo"] == {"available": False}
    assert sorted(res["go_table"][0]["go_terms"]) == ["GO:0016021", "GO:0055114"]
    assert sorted(res["go_table"][0]["unknown_ids"]) == ["GO:0016021", "GO:0055114"]


def test_upload_go_metadata_includes_obo_stats_when_present_or_none_when_absent(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    owner = User(id=uuid.uuid4(), email="u@example.com", hashed_password="x", role=UserRole.RESEARCHER)
    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="c.vcf",
        storage_path=str(tmp_path / "c.vcf"),
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
    )

    class _DB:
        def get(self, model, oid):
            return ds if model is Dataset and str(oid) == str(ds.id) else None

        def query(self, *args):
            class _Q:
                def filter(self, *a):
                    return self

                def all(self):
                    return []

            return _Q()

        def commit(self):
            pass

    payload = b"G1.1\tGO:0016021\nG2.1\tGO:0055114\n"

    # Tanpa OBO (fixture conftest autouse)
    out_no_obo = upload_routes.upload_go_annotation_file(
        file_id=ds.id,
        file=UploadFile(file=io.BytesIO(payload), filename="a.tsv"),
        db=_DB(),
        user=owner,
    )
    assert out_no_obo["obo"] is None
    assert out_no_obo["n_genes_raw"] == 2
    assert out_no_obo["n_mappings_raw"] == 2
    assert out_no_obo["n_genes"] == 2
    assert out_no_obo["n_mappings"] == 2

    # Dengan OBO
    monkeypatch.setattr(settings, "GO_OBO_PATH", str(FIXTURE_OBO))
    clear_go_ontology_cache()
    out_with_obo = upload_routes.upload_go_annotation_file(
        file_id=ds.id,
        file=UploadFile(file=io.BytesIO(payload), filename="b.tsv"),
        db=_DB(),
        user=owner,
    )
    assert set(out_with_obo["obo"].keys()) == {
        "available",
        "version",
        "n_rows_via_alt_id",
        "n_rows_obsolete_remapped",
        "n_rows_obsolete_dropped",
        "n_terms_unknown",
    }
    assert out_with_obo["obo"]["available"] is True
    assert out_with_obo["obo"]["n_rows_via_alt_id"] == 1
    assert out_with_obo["obo"]["n_rows_obsolete_dropped"] == 1
    assert out_with_obo["n_genes_raw"] == 2
    assert out_with_obo["n_mappings_raw"] == 2
    assert out_with_obo["n_genes"] == 1
    assert out_with_obo["n_mappings"] == 1
