from pathlib import Path

import numpy as np
import pytest

from app.services.postgwas_service import (
    calculate_unclamped_lambda_gc,
    determine_thresholds,
    run_greedy_clumping,
)


def test_calculate_unclamped_lambda_gc():
    # 1. Null distribution (p-values uniformly distributed U(0,1))
    np.random.seed(42)
    uniform_p = np.random.uniform(0.001, 1.0, size=1000)
    lambda_null = calculate_unclamped_lambda_gc(uniform_p)
    assert 0.85 <= lambda_null <= 1.15

    # 2. Deflated / non-clamped test (e.g. median chi2 is low)
    # Ensure it doesn't clamp to 1.0 if lambda is < 1.0
    high_p = np.random.uniform(0.4, 0.99, size=500)
    lambda_low = calculate_unclamped_lambda_gc(high_p)
    assert lambda_low < 1.0


def test_determine_thresholds():
    p_vals = [1e-8, 2e-7, 1e-5, 0.01, 0.05, 0.1, 0.5]
    n_tests = 1000

    # 1. Bonferroni
    thresh_bonf = determine_thresholds(p_vals, n_tests, method="bonferroni", alpha=0.05)
    assert thresh_bonf["bonferroni"] == pytest.approx(0.05 / 1000)
    assert thresh_bonf["selected_method"] == "bonferroni"
    assert thresh_bonf["selected_p"] == pytest.approx(0.05 / 1000)

    # 2. LOD score
    thresh_lod = determine_thresholds(p_vals, n_tests, method="lod", lod_score=6.0)
    assert thresh_lod["lod_p"] == pytest.approx(1e-6)
    assert thresh_lod["selected_p"] == pytest.approx(1e-6)

    # 3. FDR with significant values
    thresh_fdr = determine_thresholds(p_vals, n_tests, method="fdr", alpha=0.05)
    assert thresh_fdr["fdr"] is not None
    assert thresh_fdr["selected_p"] == thresh_fdr["fdr"]

    # 4. FDR when no values pass (e.g. all p = 0.9)
    thresh_fdr_none = determine_thresholds([0.9, 0.95], 100, method="fdr", alpha=0.05)
    assert thresh_fdr_none["fdr"] is None
    assert thresh_fdr_none["selected_method"] == "bonferroni"
    assert thresh_fdr_none["warning"] is not None


def test_run_greedy_clumping(tmp_path):
    assoc_file = tmp_path / "gwas_results.normalized.tsv"
    # Create test association data on chr 1 with two clusters:
    # Cluster A: pos 1000 (p=1e-8), pos 1050 (p=1e-4), pos 1100 (p=1e-5)
    # Cluster B: pos 500000 (p=5e-9), pos 500200 (p=2e-5)
    # Non-significant: pos 900000 (p=0.5)
    rows = [
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn",
        "1\t1000\trs_lead1\tA\tG\t0.3\t0.5\t0.08\t1e-8\t100",
        "1\t1050\trs_near1\tA\tG\t0.3\t0.4\t0.08\t1e-4\t100",
        "1\t1100\trs_near2\tT\tC\t0.2\t0.3\t0.08\t1e-5\t100",
        "1\t500000\trs_lead2\tC\tG\t0.4\t-0.6\t0.09\t5e-9\t100",
        "1\t500200\trs_near3\tA\tT\t0.4\t-0.4\t0.09\t2e-5\t100",
        "1\t900000\trs_nonsig\tA\tT\t0.5\t0.01\t0.05\t0.5\t100",
    ]
    assoc_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

    lead_snps = run_greedy_clumping(
        assoc_file=assoc_file,
        selected_p=1e-6,
        clump_p2=1e-3,
        clump_kb=250,
    )

    # There should be 2 lead SNPs: rs_lead2 (lowest p 5e-9) and rs_lead1 (1e-8)
    assert len(lead_snps) == 2
    assert lead_snps[0]["snp"] == "rs_lead2"
    assert lead_snps[0]["n_clumped"] == 1
    assert "rs_near3" in lead_snps[0]["clumped_snps"]

    assert lead_snps[1]["snp"] == "rs_lead1"
    assert lead_snps[1]["n_clumped"] == 2
