from pathlib import Path
from unittest.mock import MagicMock
import pytest
import numpy as np

from app.schemas.pipeline import GWASPipelineConfig
from app.tasks import multilocus_tasks
from app.tasks.multilocus_tasks import (
    _run_python_multilocus_engine,
    generate_peak_comparison,
)
from app.utils.process import CLICancelledError


def test_gwas_pipeline_config_multilocus_models():
    # Validasi model farmcpu, blink, mlmm
    cfg_farmcpu = GWASPipelineConfig(model="farmcpu")
    assert cfg_farmcpu.model == "farmcpu"

    cfg_blink = GWASPipelineConfig(model="blink")
    assert cfg_blink.model == "blink"

    cfg_mlmm = GWASPipelineConfig(model="mlmm")
    assert cfg_mlmm.model == "mlmm"

    # Validasi multilocus_methods
    cfg_methods = GWASPipelineConfig(
        enable_multilocus=True,
        multilocus_methods=["FarmCPU", "BLINK", "MLMM"],
    )
    assert "MLMM" in cfg_methods.multilocus_methods
    assert "FarmCPU" in cfg_methods.multilocus_methods
    assert "BLINK" in cfg_methods.multilocus_methods


def test_generate_peak_comparison_with_mlmm(tmp_path):
    single_lead = [
        {"snp": "rs101", "chr": "1", "pos": 10000, "p": 1.2e-8},
        {"snp": "rs102", "chr": "2", "pos": 20000, "p": 4.5e-5},
    ]
    multi_lead = [
        {"snp": "rs101", "chr": "1", "pos": 10000, "p": 2.1e-9, "method": "BLINK"},
        {"snp": "rs103", "chr": "3", "pos": 35000, "p": 3.4e-6, "method": "MLMM"},
    ]

    out_csv = tmp_path / "Ringkasan_Puncak_GWAS.csv"
    out_json = tmp_path / "peak_comparison.json"

    res = generate_peak_comparison(
        single_lead_snps=single_lead,
        multi_lead_snps=multi_lead,
        output_csv=out_csv,
        output_json=out_json,
    )

    assert res["summary"]["n_consistent"] == 1
    assert res["summary"]["n_single_only"] == 1
    assert res["summary"]["n_multi_only"] == 1
    assert res["consistent"][0]["snp"] == "rs101"
    assert res["consistent"][0]["method"] == "BLINK"
    assert res["multi_only"][0]["snp"] == "rs103"
    assert res["multi_only"][0]["method"] == "MLMM"

    assert out_csv.is_file()
    assert out_json.is_file()


def test_python_multilocus_engine_execution(tmp_path):
    # Mock workspace (Python fallback phenotype from inspection/pheno_plink.txt)
    job_dir = tmp_path / "jobs" / "test_job_123"
    multilocus_dir = job_dir / "multilocus"
    norm_dir = job_dir / "normalization"
    inspect_dir = job_dir / "inspection"
    multilocus_dir.mkdir(parents=True)
    norm_dir.mkdir(parents=True)
    inspect_dir.mkdir(parents=True)

    log_file = multilocus_dir / "multilocus.log"

    # Mock VCF file
    vcf_path = norm_dir / "normalized.vcf"
    vcf_content = (
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\tS3\tS4\tS5\n"
        "1\t1000\trs101\tA\tG\t.\tPASS\t.\tGT\t0/0\t0/1\t1/1\t0/1\t0/0\n"
        "1\t2000\trs102\tC\tT\t.\tPASS\t.\tGT\t0/1\t1/1\t0/0\t0/0\t0/1\n"
        "2\t5000\trs201\tG\tC\t.\tPASS\t.\tGT\t1/1\t0/0\t0/1\t0/1\t1/1\n"
        "2\t8000\trs202\tT\tA\t.\tPASS\t.\tGT\t0/0\t0/0\t0/1\t1/1\t0/1\n"
    )
    vcf_path.write_text(vcf_content, encoding="utf-8")

    # Mock Phenotype
    pheno_path = inspect_dir / "pheno_plink.txt"
    pheno_content = (
        "FID\tIID\tPHENO1\n"
        "S1\tS1\t12.5\n"
        "S2\tS2\t15.2\n"
        "S3\tS3\t18.1\n"
        "S4\tS4\t14.0\n"
        "S5\tS5\t11.8\n"
    )
    pheno_path.write_text(pheno_content, encoding="utf-8")

    ctx = {"normalized_vcf_path": str(vcf_path)}
    methods = ["FarmCPU", "BLINK", "MLMM"]

    res = _run_python_multilocus_engine(
        job_id="test_job_123",
        dataset_id="test_ds",
        methods=methods,
        workspace=multilocus_dir,
        log_file=log_file,
        ctx=ctx,
    )

    assert "FARMCPU" in res["methods_results"]
    assert "BLINK" in res["methods_results"]
    assert "MLMM" in res["methods_results"]

    assert (multilocus_dir / "farmcpu_results.tsv").is_file()
    assert (multilocus_dir / "blink_results.tsv").is_file()
    assert (multilocus_dir / "mlmm_results.tsv").is_file()


def test_write_gapit_inputs_formats_y_gd_gm_and_imputes_missing(tmp_path, monkeypatch):
    job_dir = tmp_path / "jobs" / "gapit_job"
    workspace = job_dir / "multilocus"
    inspect_dir = job_dir / "inspection"
    workspace.mkdir(parents=True)
    inspect_dir.mkdir(parents=True)
    log_file = workspace / "multilocus.log"

    (inspect_dir / "pheno_plink.txt").write_text(
        "FID\tIID\tTRAIT\n"
        "S1\tS1\t10.5\n"
        "S2\tS2\tNA\n"
        "S3\tS3\t14.0\n"
        "S4\tS4\t12.0\n",
        encoding="utf-8",
    )

    samples = ["S1", "S2", "S3", "S4"]
    variants = [
        {"chrom": "Gm01", "rsid": "rs1", "pos": 100, "a1": "A", "a2": "G"},
        {"chrom": "Gm01", "rsid": "rs_all_miss", "pos": 200, "a1": "C", "a2": "T"},
        {"chrom": "Gm02", "rsid": "rs2", "pos": 300, "a1": "G", "a2": "T"},
    ]
    g_mat = np.array(
        [
            [0, -1, 1],
            [-1, -1, 2],
            [2, -1, 0],
            [2, -1, 1],
        ],
        dtype=np.int8,
    )
    monkeypatch.setattr(
        "app.services.structure_service.read_plink_binary",
        lambda prefix: (samples, variants, g_mat),
    )

    y_path, gd_path, gm_path = multilocus_tasks._write_gapit_inputs(workspace, log_file)

    y_lines = y_path.read_text(encoding="utf-8").splitlines()
    assert y_lines[0] == "Taxa\tTRAIT"
    assert "S2\tNA" in y_lines

    gd_lines = gd_path.read_text(encoding="utf-8").splitlines()
    assert gd_lines[0] == "taxa\trs1\trs2"
    for row in gd_lines[1:]:
        vals = row.split("\t")[1:]
        assert "-1" not in vals
        assert len(vals) == 2

    gm_lines = gm_path.read_text(encoding="utf-8").splitlines()
    assert gm_lines[0] == "SNP\tChromosome\tPosition"
    gm_rows = [line.split("\t") for line in gm_lines[1:]]
    assert [r[0] for r in gm_rows] == ["rs1", "rs2"]
    chr_by_snp = {r[0]: int(r[1]) for r in gm_rows}
    assert chr_by_snp["rs1"] != chr_by_snp["rs2"]


def _setup_gapit_fixture(workspace_root: Path, monkeypatch):
    workspace = workspace_root / "multilocus"
    inspect_dir = workspace_root / "inspection"
    workspace.mkdir(parents=True)
    inspect_dir.mkdir(parents=True)
    log_file = workspace / "multilocus.log"
    runner_script = workspace_root / "gapit_runner.R"
    runner_script.write_text("# runner", encoding="utf-8")

    (inspect_dir / "pheno_plink.txt").write_text(
        "FID\tIID\tTRAIT\nS1\tS1\t1.0\nS2\tS2\t2.0\nS3\tS3\t3.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.structure_service.read_plink_binary",
        lambda prefix: (
            ["S1", "S2", "S3"],
            [{"chrom": "1", "rsid": "rs1", "pos": 100, "a1": "A", "a2": "G"}],
            np.array([[0], [1], [2]], dtype=np.int8),
        ),
    )
    return workspace, log_file, runner_script


def test_run_gapit_models_passes_gd_gm_timeout_cancel_and_returns_only_produced_csvs(tmp_path, monkeypatch):
    workspace, log_file, runner_script = _setup_gapit_fixture(tmp_path / "jobs" / "gapit_run", monkeypatch)

    recorded = {}

    def fake_run_cli(cmd, **kwargs):
        recorded["cmd"] = cmd
        recorded["kwargs"] = kwargs
        (workspace / "GAPIT.Association.GWAS_Results.FarmCPU.TRAIT.csv").write_text(
            "SNP,Chromosome,Position,P.value,maf,nobs,Effect\nrs1,1,100,1e-6,0.3,3,0.4\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0)

    def _never_cancel():
        return False

    monkeypatch.setattr(multilocus_tasks, "run_cli_command", fake_run_cli)

    results = multilocus_tasks._run_gapit_models(
        rscript="/usr/bin/Rscript",
        runner_script=runner_script,
        methods=["FarmCPU", "BLINK"],
        workspace=workspace,
        log_file=log_file,
        cancel_check=_never_cancel,
    )

    assert "--gd" in recorded["cmd"]
    assert "--gm" in recorded["cmd"]
    assert "timeout" in recorded["kwargs"]
    assert recorded["kwargs"]["cancel_check"] is _never_cancel
    assert set(results.keys()) == {"FARMCPU"}
    assert results["FARMCPU"]["engine"] == "gapit"
    assert (workspace / "farmcpu_results.tsv").is_file()


def test_run_gapit_models_propagates_cli_cancelled_error(tmp_path, monkeypatch):
    workspace, log_file, runner_script = _setup_gapit_fixture(tmp_path / "jobs" / "gapit_cancel", monkeypatch)

    def raise_cancelled(cmd, **kwargs):
        raise CLICancelledError(cmd, log_file)

    monkeypatch.setattr(multilocus_tasks, "run_cli_command", raise_cancelled)

    with pytest.raises(CLICancelledError):
        multilocus_tasks._run_gapit_models(
            rscript="/usr/bin/Rscript",
            runner_script=runner_script,
            methods=["FarmCPU"],
            workspace=workspace,
            log_file=log_file,
            cancel_check=lambda: True,
        )
