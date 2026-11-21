"""Tests for pure-Python EasyKASP KASP primer design (W40) and oligo QC (W41)."""

from __future__ import annotations

import pytest

from app.services.kasp_service import FAM_TAIL, HEX_TAIL, analyze_kasp_oligos, design_kasp

SNP_74098_FLANK = (
    "TGTTCATTGTTGTAGCCTTCAATGTCAAATACCTTCTAAGGGTTATGTGatcataatatataaagt"
    "CTTGTATCATAATGTCAAATACACAATGTCTTGTACATCTGGTGAATAAATTCTGTATCATCCGTTCTGG"
    "acttgaataaaata[T/C]ttcctATTGAGACAATCATCCCAATTCTCAACAATGGTGAATTTCAATAT"
    "CTACCTACGCGAAAGAGATGAAGTTCTGATTTTATACTGACTGAAACTACATAACTtactacaagaaaaagag"
    "TCATTACTCCCTAATGCATATGGTTCC"
)


def test_design_kasp_golden_snp_74098_matches_easykasp_output():
    result = design_kasp("SNP_74098", "T", "C", SNP_74098_FLANK)

    assert result["status"] == "OK"
    assert result["direction"] == "reverse"
    assert result["allele_x"] == "T"
    assert result["allele_y"] == "C"
    assert result["primer_x"] == f"{FAM_TAIL}AATTGGGATGATTGTCTCAATAGGAAA"
    assert result["primer_y"] == f"{HEX_TAIL}AATTGGGATGATTGTCTCAATAGGAAG"
    assert result["primer_c"] == "AAATTCTGTATCATCCGTTCTGGACTTG"
    assert result["primer_x_no_tail"] == "AATTGGGATGATTGTCTCAATAGGAAA"
    assert result["primer_y_no_tail"] == "AATTGGGATGATTGTCTCAATAGGAAG"
    assert result["notes"] == []


def test_design_kasp_rejects_malformed_flanking_sequences():
    dup_bracket = SNP_74098_FLANK.replace("ttcct", "t[A/G]t")
    res_dup = design_kasp("SNP_DUP", "T", "C", dup_bracket)
    assert res_dup["status"] == "Failed"
    assert any("Variation mistake" in note for note in res_dup["notes"])

    for bad_char in (",", "\\", "*"):
        res_bad = design_kasp("SNP_BAD", "T", "C", f"{SNP_74098_FLANK[:50]}{bad_char}{SNP_74098_FLANK[50:]}")
        assert res_bad["status"] == "Failed"

    res_no_slash = design_kasp("SNP_NOSLASH", "T", "C", SNP_74098_FLANK.replace("[T/C]", "[TC]"))
    assert res_no_slash["status"] == "Failed"


def test_design_kasp_flags_allele_mismatch_and_uses_bracket_alleles():
    result = design_kasp("SNP_74098", "A", "G", SNP_74098_FLANK)

    assert result["status"] == "OK"
    assert result["allele_x"] == "T"
    assert result["allele_y"] == "C"
    assert any("Allele mistake" in note for note in result["notes"])
    assert result["primer_x"] == f"{FAM_TAIL}AATTGGGATGATTGTCTCAATAGGAAA"


def test_design_kasp_avoids_n_masked_bases_in_primers():
    flank_with_n = SNP_74098_FLANK.replace("[T/C]ttcctATTGAGACAATCATC", "[T/C]ttNctATTGAGACAATCATC")
    result = design_kasp("SNP_MASKED_N", "T", "C", flank_with_n)

    if result["status"] == "OK":
        assert "N" not in result["primer_x"]
        assert "N" not in result["primer_y"]
        assert "N" not in result["primer_c"]
    else:
        assert result["primer_x"] == ""


def test_analyze_kasp_oligos_golden_snp_74098_includes_tails_and_preserves_status():
    design = design_kasp("SNP_74098", "T", "C", SNP_74098_FLANK)
    assert design["status"] == "OK"

    oligo = analyze_kasp_oligos(design)
    assert design["status"] == "OK"
    assert oligo["analyzed"] is True
    assert oligo["input_sequences"]["primer_x"].startswith(FAM_TAIL)
    assert oligo["input_sequences"]["primer_y"].startswith(HEX_TAIL)
    assert oligo["input_sequences"]["primer_x"] == design["primer_x"]
    assert oligo["input_sequences"]["primer_y"] == design["primer_y"]
    assert oligo["input_sequences"]["primer_c"] == design["primer_c"]

    px_hp = oligo["hairpins"]["primer_x"]
    assert px_hp["structure_found"] is True
    assert px_hp["tm"] == pytest.approx(46.0, abs=0.5)
    assert px_hp["dg"] == pytest.approx(-0.91, abs=0.1)

    assert set(oligo["homodimers"].keys()) == {"primer_x", "primer_y", "primer_c"}
    assert set(oligo["heterodimers"].keys()) == {"x_c", "y_c", "x_y"}
    assert set(oligo["end_stability"].keys()) == {"x_c", "y_c", "x_y", "primer_x", "primer_y", "primer_c"}


def test_analyze_kasp_oligos_handles_failed_design():
    failed = design_kasp("SNP_FAIL", "T", "C", "ACGT")
    oligo = analyze_kasp_oligos(failed)
    assert oligo["analyzed"] is False
    assert oligo["warn"] is False
    assert oligo["hairpins"] == {}
