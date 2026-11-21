import csv
from pathlib import Path

import numpy as np
import pytest

from app.tasks.multilocus_tasks import (
    _run_python_multilocus_engine,
    compute_snp_pve_pct,
    generate_peak_comparison,
)


def _write_synthetic_engine_fixture(root: Path, kinship: np.ndarray) -> tuple[Path, Path, int, str]:
    rng = np.random.default_rng(20261121)
    n_samples = 60
    n_snps = 200
    causal_idx = n_snps - 1
    causal_rsid = f"rs{causal_idx + 1}"
    genotypes = rng.integers(0, 3, size=(n_samples, n_snps), dtype=np.int8)
    genotypes[:, 100] = genotypes[:, causal_idx]
    phenotype = 3.0 * genotypes[:, causal_idx].astype(float) + rng.normal(0.0, 0.25, size=n_samples)
    samples = [f"S{i + 1:03d}" for i in range(n_samples)]

    job_dir = root / "jobs" / "synthetic"
    normalization_dir = job_dir / "normalization"
    inspection_dir = job_dir / "inspection"
    structure_dir = job_dir / "structure"
    workspace = job_dir / "multilocus"
    for directory in (normalization_dir, inspection_dir, structure_dir, workspace):
        directory.mkdir(parents=True, exist_ok=True)

    vcf_lines = [
        "##fileformat=VCFv4.2",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(samples),
    ]
    for idx in range(n_snps):
        vcf_lines.append(
            "1\t{}\trs{}\tA\tG\t.\tPASS\t.\tGT\t{}".format(
                idx + 1,
                idx + 1,
                "\t".join(f"{value}/{value}" for value in genotypes[:, idx]),
            )
        )
    vcf_path = normalization_dir / "normalized.vcf"
    vcf_path.write_text("\n".join(vcf_lines) + "\n", encoding="utf-8")

    pheno_lines = ["FID\tIID\tTRAIT"]
    pheno_lines.extend(f"{sample}\t{sample}\t{value:.8f}" for sample, value in zip(samples, phenotype))
    (inspection_dir / "pheno_plink.txt").write_text("\n".join(pheno_lines) + "\n", encoding="utf-8")
    np.savetxt(structure_dir / "kinship.cXX.txt", kinship)
    return workspace, vcf_path, causal_idx, causal_rsid


def _read_result_row(path: Path, rsid: str) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return next(row for row in csv.DictReader(handle, delimiter="\t") if row["rs"] == rsid)


def test_python_engine_corrects_qtn_pvalues_and_blink_order(tmp_path):
    kinship = np.eye(60)
    workspace, vcf_path, _, causal_rsid = _write_synthetic_engine_fixture(tmp_path, kinship)
    result = _run_python_multilocus_engine(
        job_id="synthetic",
        dataset_id="dataset",
        methods=["FarmCPU", "BLINK", "MLMM"],
        workspace=workspace,
        log_file=workspace / "multilocus.log",
        ctx={"normalized_vcf_path": str(vcf_path), "trait": "TRAIT"},
    )

    farmcpu = result["methods_results"]["FARMCPU"]
    mlmm = result["methods_results"]["MLMM"]
    blink = result["methods_results"]["BLINK"]
    assert causal_rsid in farmcpu["pseudo_qtns"]
    assert causal_rsid in mlmm["pseudo_qtns"]
    assert blink["pseudo_qtns"][0] == causal_rsid
    assert "rs101" not in blink["pseudo_qtns"]
    assert float(_read_result_row(workspace / "farmcpu_results.tsv", causal_rsid)["p"]) < 1e-4
    assert float(_read_result_row(workspace / "mlmm_results.tsv", causal_rsid)["p"]) < 1e-4
    assert float(_read_result_row(workspace / "farmcpu_results.tsv", causal_rsid)["beta"]) != 0.0
    assert len(farmcpu["pseudo_qtns"]) == 1
    assert len(mlmm["pseudo_qtns"]) == 1
    assert np.allclose(
        float(_read_result_row(workspace / "farmcpu_results.tsv", causal_rsid)["p"]),
        float(_read_result_row(workspace / "mlmm_results.tsv", causal_rsid)["p"]),
        rtol=1e-6,
    )


def test_python_mlmm_changes_under_structured_kinship(tmp_path):
    groups = np.repeat([0.0, 1.0], 30)
    structured = np.where(groups[:, None] == groups[None, :], 0.5, 0.0)
    workspace, vcf_path, _, causal_rsid = _write_synthetic_engine_fixture(tmp_path, structured)
    _run_python_multilocus_engine(
        job_id="synthetic",
        dataset_id="dataset",
        methods=["FarmCPU", "MLMM"],
        workspace=workspace,
        log_file=workspace / "multilocus.log",
        ctx={"normalized_vcf_path": str(vcf_path), "trait": "TRAIT"},
    )

    farm_p = float(_read_result_row(workspace / "farmcpu_results.tsv", causal_rsid)["p"])
    mlmm_p = float(_read_result_row(workspace / "mlmm_results.tsv", causal_rsid)["p"])
    assert not np.allclose(mlmm_p, farm_p, rtol=1e-6, atol=0.0)


def test_pve_uses_partial_r_squared():
    pve = compute_snp_pve_pct(
        beta=-2.998082,
        se=0.607564,
        maf=0.362416,
        nobs=149,
        p_val=8.03e-7,
    )

    assert pve == pytest.approx(14.2, abs=0.1)


def test_peak_comparison_filters_non_significant_multi_and_uses_model_label(tmp_path):
    result = generate_peak_comparison(
        single_lead_snps=[{"snp": "single", "chr": "1", "pos": 1000, "p": 1e-8}],
        multi_lead_snps=[
            {"snp": "multi-false", "chr": "1", "pos": 1000, "p": 1e-8, "is_significant": False},
            {"snp": "multi-true", "chr": "1", "pos": 1000, "p": 1e-8, "is_significant": True},
            {"snp": "multi-other", "chr": "2", "pos": 2000, "p": 1e-8, "is_significant": True},
        ],
        output_csv=tmp_path / "peaks.csv",
        output_json=tmp_path / "peaks.json",
        single_model_label="GLM",
    )

    assert result["summary"]["n_consistent"] == 1
    assert result["summary"]["n_multi_only"] == 1
    assert result["multi_only"][0]["snp"] == "multi-other"
    assert "GLM" in result["consistent"][0]["keterangan"]
