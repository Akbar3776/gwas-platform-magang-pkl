import csv
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.tasks import multilocus_tasks
from app.tasks.multilocus_tasks import (
    _run_gapit_models,
    _prune_gapit_bfile,
    _run_python_multilocus_engine,
    _write_gapit_inputs,
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


def test_gapit_inputs_prefer_imputed_bfile_and_prune_to_cap(tmp_path, monkeypatch):
    job_dir = tmp_path / "jobs" / "gapit"
    workspace = job_dir / "multilocus"
    inspection = job_dir / "inspection"
    imputation = job_dir / "imputation"
    qc = job_dir / "qc"
    for directory in (workspace, inspection, imputation, qc):
        directory.mkdir(parents=True)
    (inspection / "pheno_plink.txt").write_text(
        "FID\tIID\tTRAIT\nS1\tS1\t1\nS2\tS2\t2\nS3\tS3\t3\n", encoding="utf-8"
    )
    for prefix in (imputation / "imputed", qc / "plink_qc"):
        prefix.with_suffix(".bed").write_bytes(b"")
        prefix.with_suffix(".bim").write_text(
            "\n".join(f"1\trs{i}\t0\t{i + 1}\tA\tG" for i in range(5)) + "\n",
            encoding="utf-8",
        )
        prefix.with_suffix(".fam").write_text("S1 S1 0 0 0 -9\nS2 S2 0 0 0 -9\nS3 S3 0 0 0 -9\n", encoding="utf-8")
    source_samples = ["S1", "S2", "S3"]
    source_variants = [
        {"chrom": "1", "rsid": f"rs{i}", "pos": i + 1, "a1": "A", "a2": "G"}
        for i in range(5)
    ]
    source_matrix = np.arange(15, dtype=np.int8).reshape(3, 5) % 3
    pruned_variants = source_variants[:2]
    pruned_matrix = source_matrix[:, :2]
    read_calls = []

    def fake_read(prefix):
        read_calls.append(Path(prefix))
        if str(prefix).endswith("gapit_input"):
            return source_samples, pruned_variants, pruned_matrix
        return source_samples, source_variants, source_matrix

    def fake_run(cmd, **kwargs):
        if "--indep-pairwise" in cmd:
            out_prefix = Path(cmd[cmd.index("--out") + 1])
            out_prefix.with_suffix(".prune.in").write_text("rs0\nrs1\n", encoding="utf-8")
        elif "--extract" in cmd:
            out_prefix = Path(cmd[cmd.index("--out") + 1])
            out_prefix.with_suffix(".bed").write_bytes(b"")
        return MagicMock(returncode=0)

    monkeypatch.setattr(multilocus_tasks, "GAPIT_MAX_SNPS", 2, raising=False)
    monkeypatch.setattr(multilocus_tasks, "which_tool", lambda name: "plink" if name == "plink" else None)
    monkeypatch.setattr(multilocus_tasks, "run_cli_command", fake_run)
    monkeypatch.setattr("app.services.structure_service.read_plink_binary", fake_read)
    log_file = workspace / "multilocus.log"

    _write_gapit_inputs(workspace, log_file)

    log_text = log_file.read_text(encoding="utf-8")
    assert "Sumber genotipe:" in log_text and "imputation" in log_text
    assert "sebelum=5" in log_text
    assert "sesudah=2" in log_text


def test_gapit_models_passes_pca_and_runner_uses_fread(tmp_path, monkeypatch):
    workspace = tmp_path / "jobs" / "gapit" / "multilocus"
    inspection = workspace.parent / "inspection"
    workspace.mkdir(parents=True)
    inspection.mkdir()
    imputation = workspace.parent / "imputation"
    imputation.mkdir()
    (imputation / "imputed.bed").write_bytes(b"")
    (imputation / "imputed.bim").write_text("1\trs1\t0\t1\tA\tG\n", encoding="utf-8")
    (imputation / "imputed.fam").write_text("S1 S1 0 0 0 -9\nS2 S2 0 0 0 -9\nS3 S3 0 0 0 -9\n", encoding="utf-8")
    (inspection / "pheno_plink.txt").write_text(
        "FID\tIID\tTRAIT\nS1\tS1\t1\nS2\tS2\t2\nS3\tS3\t3\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "app.services.structure_service.read_plink_binary",
        lambda prefix: (
            ["S1", "S2", "S3"],
            [{"chrom": "1", "rsid": "rs1", "pos": 1, "a1": "A", "a2": "G"}],
            np.array([[0], [1], [2]], dtype=np.int8),
        ),
    )
    recorded = {}

    def fake_run(cmd, **kwargs):
        recorded["cmd"] = cmd
        Path(workspace / "GAPIT.Association.GWAS_Results.FarmCPU.TRAIT.csv").write_text(
            "SNP,Chromosome,Position,P.value,maf,nobs,Effect\nrs1,1,1,1e-6,0.3,3,0.4\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0)

    monkeypatch.setattr(multilocus_tasks, "run_cli_command", fake_run)
    runner = Path(__file__).resolve().parents[1] / "app" / "templates" / "gapit_runner.R"
    _run_gapit_models(
        rscript="Rscript",
        runner_script=runner,
        methods=["FarmCPU"],
        workspace=workspace,
        log_file=workspace / "multilocus.log",
        n_pcs=7,
    )

    assert "--pca" in recorded["cmd"]
    assert recorded["cmd"][recorded["cmd"].index("--pca") + 1] == "7"
    runner_text = runner.read_text(encoding="utf-8")
    assert "data.table::fread(opt$gd" in runner_text
    assert "data.table::fread(opt$gm" in runner_text
    assert "PCA.total = as.integer(opt$pca)" in runner_text


def test_gapit_pruning_commands_allow_extra_chr(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.with_suffix(".bed").write_bytes(b"")
    source.with_suffix(".bim").write_text(
        "scaffold1\trs1\t0\t1\tA\tG\n1\trs2\t0\t2\tA\tG\n1\trs3\t0\t3\tA\tG\n",
        encoding="utf-8",
    )
    source.with_suffix(".fam").write_text("S1 S1 0 0 0 -9\n", encoding="utf-8")
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        out_prefix = Path(cmd[cmd.index("--out") + 1])
        if "--indep-pairwise" in cmd:
            out_prefix.with_suffix(".prune.in").write_text("rs1\nrs2\n", encoding="utf-8")
        else:
            for suffix in (".bed", ".bim", ".fam"):
                out_prefix.with_suffix(suffix).write_bytes(b"")
        return MagicMock(returncode=0)

    monkeypatch.setattr(multilocus_tasks, "GAPIT_MAX_SNPS", 2, raising=False)
    monkeypatch.setattr(multilocus_tasks, "which_tool", lambda name: "plink")
    monkeypatch.setattr(multilocus_tasks, "run_cli_command", fake_run)

    _prune_gapit_bfile(source, tmp_path, tmp_path / "probe.log", 3)

    assert len(commands) == 2
    assert all("--allow-extra-chr" in command for command in commands)


@pytest.mark.skipif(shutil.which("plink") is None, reason="plink tidak tersedia")
def test_gapit_pruning_accepts_non_numeric_contigs_with_real_plink(tmp_path, monkeypatch):
    plink = shutil.which("plink")
    source = tmp_path / "source"
    subprocess.run(
        [plink, "--dummy", "20", "10", "--make-bed", "--out", str(source)],
        check=True,
        capture_output=True,
        text=True,
    )
    bim_path = source.with_suffix(".bim")
    bim_lines = bim_path.read_text(encoding="utf-8").splitlines()
    bim_lines[0] = "scaffold1" + bim_lines[0][bim_lines[0].find("\t"):]
    bim_path.write_text("\n".join(bim_lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(multilocus_tasks, "GAPIT_MAX_SNPS", 2, raising=False)
    monkeypatch.setattr(multilocus_tasks, "which_tool", lambda name: plink)

    result, count, _ = _prune_gapit_bfile(source, tmp_path, tmp_path / "probe.log", 10)

    assert count <= 2
    assert result.with_suffix(".bim").is_file()
    assert "scaffold1" in result.with_suffix(".bim").read_text(encoding="utf-8")
