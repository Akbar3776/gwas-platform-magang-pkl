from pathlib import Path

import numpy as np
import pytest

from app.schemas.pipeline import QCPipelineConfig
from app.services.qc_service import run_python_fallback_qc
from app.services.structure_service import (
    compute_centered_kinship,
    compute_king_robust_table,
    compute_ld_decay_curve,
    determine_model_recommendation,
    read_plink_binary,
    run_pca_decomposition,
)


@pytest.fixture
def toy_bfile(tmp_path):
    fixture_vcf = Path(__file__).parent / "fixtures" / "data" / "toy_gwas.vcf.gz"
    if not fixture_vcf.is_file():
        from tests.fixtures.make_fixtures import generate_toy_fixtures
        generate_toy_fixtures()

    out_prefix = tmp_path / "plink_qc"
    cfg = QCPipelineConfig(maf=0.01, geno=0.2, mind=0.2)
    run_python_fallback_qc(input_vcf=str(fixture_vcf), output_prefix=out_prefix, cfg=cfg)
    return out_prefix


def test_read_plink_binary_and_pca(toy_bfile):
    samples, variants, G = read_plink_binary(toy_bfile)
    assert len(samples) > 0
    assert len(variants) > 0
    assert G.shape == (len(samples), len(variants))
    assert set(np.unique(G)).issubset({-1, 0, 1, 2})

    pca_data = run_pca_decomposition(G, samples, n_pcs=5)
    assert "samples" in pca_data
    assert "eigenvalues" in pca_data
    assert "pve" in pca_data
    assert "cumulative_pve" in pca_data
    assert len(pca_data["samples"]) == len(samples)
    assert len(pca_data["samples"][0]["pcs"]) == 5

    # Check PVE is non-negative and cumulative is monotonic
    for p in pca_data["pve"]:
        assert p >= 0.0
    cum = pca_data["cumulative_pve"]
    for i in range(len(cum) - 1):
        assert cum[i] <= cum[i + 1] + 1e-6


def test_centered_kinship(toy_bfile):
    samples, _, G = read_plink_binary(toy_bfile)
    K = compute_centered_kinship(G)
    assert K.shape == (len(samples), len(samples))

    # K must be symmetric
    assert np.allclose(K, K.T, atol=1e-5)

    # Diagonal elements should be close to 1 on average
    assert abs(np.mean(np.diag(K)) - 1.0) < 0.1


def test_king_robust_table(toy_bfile):
    samples, _, G = read_plink_binary(toy_bfile)
    king = compute_king_robust_table(G, samples)
    assert king["estimator"] == "KING-robust"
    assert "pairs_summary" in king
    assert "duplicate" in king["pairs_summary"]
    assert "first_degree" in king["pairs_summary"]


def test_ld_decay_curve(toy_bfile):
    _, variants, G = read_plink_binary(toy_bfile)
    ld = compute_ld_decay_curve(variants, G, window_kb=500, bin_kb=20, cutoff_r2=0.2)
    assert "bins" in ld
    assert "chosen_kb" in ld
    assert "reason" in ld
    assert len(ld["bins"]) > 0
    for b in ld["bins"]:
        assert 0.0 <= b["mean_r2"] <= 1.0


def test_determine_model_recommendation():
    # 1. Structure + Kinship -> LMM
    rec1 = determine_model_recommendation(
        pca_data={"pve": [0.15, 0.08], "k_clusters": 2},
        king_data={"pairs_summary": {"duplicate": 1, "first_degree": 2, "unrelated": 10}},
        population_type="outcross",
    )
    assert rec1["recommended_model"] == "lmm"
    assert rec1["structure_detected"] is True
    assert rec1["kinship_detected"] is True

    # 2. Structure only -> GLM
    rec2 = determine_model_recommendation(
        pca_data={"pve": [0.12, 0.05], "k_clusters": 2},
        king_data={"pairs_summary": {"duplicate": 0, "first_degree": 0, "unrelated": 100}},
        population_type="outcross",
    )
    assert rec2["recommended_model"] == "glm"
    assert rec2["structure_detected"] is True
    assert rec2["kinship_detected"] is False

    # 3. Kinship only -> LMM
    rec3 = determine_model_recommendation(
        pca_data={"pve": [0.03, 0.02], "k_clusters": 1},
        king_data={"pairs_summary": {"duplicate": 2, "first_degree": 5, "unrelated": 20}},
        population_type="outcross",
    )
    assert rec3["recommended_model"] == "lmm"
    assert rec3["structure_detected"] is False
    assert rec3["kinship_detected"] is True

    # 4. Neither + inbred -> LMM
    rec4 = determine_model_recommendation(
        pca_data={"pve": [0.03, 0.02], "k_clusters": 1},
        king_data={"pairs_summary": {"duplicate": 0, "first_degree": 0, "unrelated": 50}},
        population_type="inbred",
    )
    assert rec4["recommended_model"] == "lmm"
