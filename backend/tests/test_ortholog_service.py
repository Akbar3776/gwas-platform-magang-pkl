"""Unit tests untuk parser indeks ortolog BLAST outfmt6 (G13)."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.ortholog_service import (
    OrthologHit,
    build_besthit_index,
    clear_ortholog_cache,
    load_besthit_index,
)
from app.services.upload_validation import UploadValidationError

_FIXTURE_BLAST = Path(__file__).resolve().parent / "fixtures" / "blast_mini.tsv"


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_ortholog_cache()
    yield
    clear_ortholog_cache()


def _blast_row(
    qseqid: str,
    sseqid: str,
    pident: str = "40.0",
    evalue: str = "1e-20",
    bitscore: str = "100.0",
    extra_cols: int = 12,
) -> str:
    cols = [
        qseqid,
        sseqid,
        pident,
        "100",
        "10",
        "1",
        "1",
        "100",
        "1",
        "100",
        evalue,
        bitscore,
    ]
    return "\t".join(cols[:extra_cols])


def test_build_besthit_index_positional_header_and_sorted_roundtrip(tmp_path):
    dest = tmp_path / "ortholog_besthit.tsv.gz"
    stats = build_besthit_index(_FIXTURE_BLAST, dest)

    assert stats["n_rows"] == 6
    assert stats["n_queries"] == 3
    assert stats["n_ties"] == 1
    assert stats["n_invalid_lines"] == 0
    assert dest.exists()

    with gzip.open(dest, "rt", encoding="utf-8") as fh:
        lines = [line.rstrip("\n") for line in fh]

    assert lines[0] == (
        "query_id\tsubject_locus\tsubject_id\tpident\tevalue\tbitscore\tn_hits\tn_loci_at_best"
    )
    query_order = [line.split("\t")[0] for line in lines[1:]]
    assert query_order == sorted(query_order)

    index = load_besthit_index(dest)
    assert set(index) == {"Asa1G00001", "Asa2G00002", "Asa7G00205"}

    asa7 = index["Asa7G00205"]
    assert isinstance(asa7, OrthologHit)
    assert asa7.subject_locus == "AT3G05510"
    assert asa7.subject_id == "AT3G05510.2"
    assert asa7.pident == pytest.approx(29.07)
    assert asa7.evalue == pytest.approx(2e-21)
    assert asa7.bitscore == pytest.approx(99.4)
    assert asa7.n_hits == 2
    assert asa7.n_loci_at_best == 1

    # Dua isoform lokus sama di bitscore maks -> n_loci_at_best=1 dan tie-break sseqid terkecil
    asa1 = index["Asa1G00001"]
    assert asa1.subject_locus == "AT1G00010"
    assert asa1.subject_id == "AT1G00010.1"
    assert asa1.n_hits == 2
    assert asa1.n_loci_at_best == 1

    # Dua lokus berbeda di bitscore maks -> n_loci_at_best=2, lokus terkecil menang
    asa2 = index["Asa2G00002"]
    assert asa2.subject_locus == "AT2G00010"
    assert asa2.n_hits == 2
    assert asa2.n_loci_at_best == 2


@pytest.mark.parametrize(
    ("row_a", "row_b", "expected_subject_id", "expected_loci_at_best", "expected_ties"),
    [
        # 1. Bitscore maksimum menang
        (
            _blast_row("Q1", "AT1G00001.1", pident="90.0", evalue="1e-40", bitscore="80.0"),
            _blast_row("Q1", "AT2G00002.1", pident="30.0", evalue="1e-10", bitscore="150.0"),
            "AT2G00002.1",
            1,
            0,
        ),
        # 2. Bitscore sama -> evalue minimum menang
        (
            _blast_row("Q1", "AT1G00001.1", pident="80.0", evalue="1e-15", bitscore="100.0"),
            _blast_row("Q1", "AT2G00002.1", pident="35.0", evalue="1e-25", bitscore="100.0"),
            "AT2G00002.1",
            2,
            1,
        ),
        # 3. Bitscore & evalue sama -> pident maksimum menang
        (
            _blast_row("Q1", "AT1G00001.1", pident="42.5", evalue="1e-20", bitscore="100.0"),
            _blast_row("Q1", "AT2G00002.1", pident="55.0", evalue="1e-20", bitscore="100.0"),
            "AT2G00002.1",
            2,
            1,
        ),
        # 4. Bitscore, evalue, pident sama -> subject_locus leksikografis terkecil menang
        (
            _blast_row("Q1", "AT5G00005.1", pident="50.0", evalue="1e-20", bitscore="100.0"),
            _blast_row("Q1", "AT3G00003.9", pident="50.0", evalue="1e-20", bitscore="100.0"),
            "AT3G00003.9",
            2,
            1,
        ),
        # 5. Semua sama termasuk subject_locus -> sseqid leksikografis terkecil menang
        (
            _blast_row("Q1", "AT3G00003.2", pident="50.0", evalue="1e-20", bitscore="100.0"),
            _blast_row("Q1", "AT3G00003.1", pident="50.0", evalue="1e-20", bitscore="100.0"),
            "AT3G00003.1",
            1,
            0,
        ),
    ],
)
def test_tie_break_criteria_per_rule(
    tmp_path, row_a, row_b, expected_subject_id, expected_loci_at_best, expected_ties
):
    src = tmp_path / "tie.tsv"
    src.write_text(f"{row_a}\n{row_b}\n", encoding="utf-8")
    dest = tmp_path / "tie.tsv.gz"

    stats = build_besthit_index(src, dest)
    index = load_besthit_index(dest)
    hit = index["Q1"]

    assert hit.subject_id == expected_subject_id
    assert hit.n_hits == 2
    assert hit.n_loci_at_best == expected_loci_at_best
    assert stats["n_ties"] == expected_ties


def test_validation_counts_invalid_lines_including_second_header(tmp_path):
    header = (
        "Query_id\tSubject_id\talignment_length\tmismatches\tgap_openings\t"
        "query_start\tquery_end\tsubject_start\tsubject_end\t%identity\te-value\tbit_score"
    )
    lines = [
        header,
        _blast_row("Q1", "AT1G00001.1"),
        _blast_row("Q2", "AT1G00002.1"),
        _blast_row("Q3", "AT1G00003.1"),
        _blast_row("Q4", "AT1G00004.1"),
        _blast_row("Q5", "AT1G00005.1"),
        _blast_row("Q6", "AT1G00006.1"),
        _blast_row("BAD1", "AT1G00007.1", extra_cols=11),
        _blast_row("BAD2", "AT1G00008.1", pident="150"),
        _blast_row("BAD3", "AT1G00009.1", evalue="nan"),
        _blast_row("BAD4", "AT1G00010.1", bitscore="inf"),
        header,
    ]
    src = tmp_path / "mixed.tsv"
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dest = tmp_path / "mixed.tsv.gz"

    stats = build_besthit_index(src, dest)
    assert stats["n_rows"] == 6
    assert stats["n_queries"] == 6
    assert stats["n_invalid_lines"] == 5


def test_validation_rejects_no_valid_rows_and_low_ratio_and_query_cap(tmp_path, monkeypatch):
    all_bad = tmp_path / "all_bad.tsv"
    all_bad.write_text(
        "\n".join(
            [
                _blast_row("BAD1", "AT1G00001.1", extra_cols=11),
                _blast_row("BAD2", "AT1G00002.1", pident="101"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(UploadValidationError) as exc_no_valid:
        build_besthit_index(all_bad, tmp_path / "out1.tsv.gz")
    assert exc_no_valid.value.code == "NO_VALID_ROWS"
    assert exc_no_valid.value.http_status == 400

    low_ratio = tmp_path / "low_ratio.tsv"
    low_ratio.write_text(
        "\n".join(
            [
                _blast_row("Q1", "AT1G00001.1"),
                _blast_row("BAD1", "AT1G00002.1", pident="150"),
                _blast_row("BAD2", "AT1G00003.1", evalue="-1"),
                _blast_row("BAD3", "AT1G00004.1", bitscore="nan"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(UploadValidationError) as exc_ratio:
        build_besthit_index(low_ratio, tmp_path / "out2.tsv.gz")
    assert exc_ratio.value.code == "TOO_MANY_INVALID_LINES"
    assert exc_ratio.value.http_status == 400

    monkeypatch.setattr(settings, "ORTHOLOG_MAX_QUERIES", 2)
    too_many_queries = tmp_path / "too_many.tsv"
    too_many_queries.write_text(
        "\n".join(
            [
                _blast_row("Q1", "AT1G00001.1"),
                _blast_row("Q2", "AT1G00002.1"),
                _blast_row("Q3", "AT1G00003.1"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(UploadValidationError) as exc_q:
        build_besthit_index(too_many_queries, tmp_path / "out3.tsv.gz")
    assert exc_q.value.code == "TOO_MANY_QUERIES"
    assert exc_q.value.http_status == 413
