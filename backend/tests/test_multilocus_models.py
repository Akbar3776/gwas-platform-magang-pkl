import csv
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

    y_path, gd_path, gm_path, cv_path, chr_idx_to_label = multilocus_tasks._write_gapit_inputs(workspace, log_file)
    assert cv_path is None

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
    assert chr_idx_to_label == {
        chr_by_snp["rs1"]: "Gm01",
        chr_by_snp["rs2"]: "Gm02",
    }


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


def test_python_multilocus_engine_plink_preserves_non_numeric_chr_labels(tmp_path, monkeypatch):
    import csv

    job_dir = tmp_path / "jobs" / "ml_chr_job"
    multilocus_dir = job_dir / "multilocus"
    qc_dir = job_dir / "qc"
    inspect_dir = job_dir / "inspection"
    multilocus_dir.mkdir(parents=True)
    qc_dir.mkdir(parents=True)
    inspect_dir.mkdir(parents=True)
    (qc_dir / "plink_qc.bed").write_bytes(b"")
    log_file = multilocus_dir / "multilocus.log"

    (inspect_dir / "pheno_plink.txt").write_text(
        "FID\tIID\tTRAIT\nS1\tS1\t10.0\nS2\tS2\t12.0\nS3\tS3\t14.0\nS4\tS4\t16.0\n",
        encoding="utf-8",
    )
    samples = ["S1", "S2", "S3", "S4"]
    variants = [
        {"chrom": "Gm01", "rsid": ".", "pos": 1000, "a1": "A", "a2": "G"},
        {"chrom": "Gm07", "rsid": "rs_g7", "pos": 2000, "a1": "C", "a2": "T"},
    ]
    g_mat = np.array([[0, 1], [1, 0], [2, 1], [0, 2]], dtype=np.int8)
    monkeypatch.setattr(
        "app.services.structure_service.read_plink_binary",
        lambda prefix: (samples, variants, g_mat),
    )

    _run_python_multilocus_engine(
        job_id="ml_chr_job",
        dataset_id="ds_chr",
        methods=["FarmCPU"],
        workspace=multilocus_dir,
        log_file=log_file,
        ctx={},
    )

    tsv_file = multilocus_dir / "farmcpu_results.tsv"
    assert tsv_file.is_file()
    with tsv_file.open("r", encoding="utf-8") as tf:
        rows = list(csv.DictReader(tf, delimiter="\t"))
    assert [r["chr"] for r in rows] == ["Gm01", "Gm07"]
    assert rows[0]["rs"] == "Gm01:1000"


def test_run_gapit_models_prefers_nyc_csv_over_kansas(tmp_path, monkeypatch):
    import csv

    workspace, log_file, runner_script = _setup_gapit_fixture(tmp_path / "jobs" / "gapit_nyc", monkeypatch)

    def fake_run_cli(cmd, **kwargs):
        (workspace / "GAPIT.Association.GWAS_Results.FarmCPU.T(Kansas).csv").write_text(
            "SNP,Chr,Pos,P.value,MAF,effect,nobs,H&B.P.Value\nrs1,1,100,0.9,0.3,0.1,3,0.95\n",
            encoding="utf-8",
        )
        (workspace / "GAPIT.Association.GWAS_Results.FarmCPU.T(NYC).csv").write_text(
            "SNP,Chr,Pos,P.value,MAF,effect,nobs,H&B.P.Value\nrs1,1,100,0.01,0.3,0.4,3,0.05\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0)

    monkeypatch.setattr(multilocus_tasks, "run_cli_command", fake_run_cli)

    results = multilocus_tasks._run_gapit_models(
        rscript="/usr/bin/Rscript",
        runner_script=runner_script,
        methods=["FarmCPU"],
        workspace=workspace,
        log_file=log_file,
    )

    assert set(results.keys()) == {"FARMCPU"}
    tsv_file = workspace / "farmcpu_results.tsv"
    assert tsv_file.is_file()
    with tsv_file.open("r", encoding="utf-8") as tf:
        rows = list(csv.DictReader(tf, delimiter="\t"))
    assert len(rows) == 1
    assert float(rows[0]["p"]) == pytest.approx(0.01)


def test_run_gapit_models_restores_chr_labels_from_gm_mapping(tmp_path, monkeypatch):
    import csv

    job_dir = tmp_path / "jobs" / "gapit_chr_restore"
    workspace = job_dir / "multilocus"
    inspect_dir = job_dir / "inspection"
    workspace.mkdir(parents=True)
    inspect_dir.mkdir(parents=True)
    log_file = workspace / "multilocus.log"
    runner_script = job_dir / "gapit_runner.R"
    runner_script.write_text("# runner", encoding="utf-8")

    (inspect_dir / "pheno_plink.txt").write_text(
        "FID\tIID\tTRAIT\nS1\tS1\t1.0\nS2\tS2\t2.0\nS3\tS3\t3.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.structure_service.read_plink_binary",
        lambda prefix: (
            ["S1", "S2", "S3"],
            [
                {"chrom": "Gm01", "rsid": "rs_g1", "pos": 1000, "a1": "A", "a2": "G"},
                {"chrom": "Gm02", "rsid": "rs_g2", "pos": 2000, "a1": "C", "a2": "T"},
            ],
            np.array([[0, 1], [1, 2], [2, 0]], dtype=np.int8),
        ),
    )

    def fake_run_cli(cmd, **kwargs):
        with (workspace / "gapit_GM.txt").open("r", encoding="utf-8") as gm_f:
            gm_rows = list(csv.DictReader(gm_f, delimiter="\t"))
        csv_lines = ["SNP,Chr,Pos,P.value,MAF,effect,nobs,H&B.P.Value"]
        for idx, gm_row in enumerate(gm_rows):
            p_val = "0.01" if idx == 0 else "0.25"
            csv_lines.append(f"{gm_row['SNP']},{gm_row['Chromosome']},{gm_row['Position']},{p_val},0.3,0.4,3,0.05")
        (workspace / "GAPIT.Association.GWAS_Results.FarmCPU.TRAIT(NYC).csv").write_text(
            "\n".join(csv_lines) + "\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0)

    monkeypatch.setattr(multilocus_tasks, "run_cli_command", fake_run_cli)

    results = multilocus_tasks._run_gapit_models(
        rscript="/usr/bin/Rscript",
        runner_script=runner_script,
        methods=["FarmCPU"],
        workspace=workspace,
        log_file=log_file,
    )

    assert set(results.keys()) == {"FARMCPU"}
    with (workspace / "farmcpu_results.tsv").open("r", encoding="utf-8") as tf:
        tsv_rows = list(csv.DictReader(tf, delimiter="\t"))
    assert [r["chr"] for r in tsv_rows] == ["Gm01", "Gm02"]


def test_generate_peak_comparison_preserves_chr_labels_and_matches_gm(tmp_path):
    single_lead = [
        {"snp": "rs101", "chr": "Gm01", "pos": 1000, "p": 1e-8},
        {"snp": "rs_h1", "chr": "H1", "pos": 5000, "p": 2e-7},
    ]
    multi_lead = [
        {"snp": "rs101_ml", "chr": "Gm01", "pos": 1500, "p": 2e-9, "method": "FarmCPU"},
        {"snp": "rs_h1_ml", "chr": "H1", "pos": 5100, "p": 3e-8, "method": "BLINK"},
        {"snp": "rs_h2_ml", "chr": "H2", "pos": 9000, "p": 4e-7, "method": "MLMM"},
    ]

    res = generate_peak_comparison(
        single_lead_snps=single_lead,
        multi_lead_snps=multi_lead,
        output_csv=tmp_path / "Ringkasan_Puncak_GWAS.csv",
        output_json=tmp_path / "peak_comparison.json",
    )

    assert res["summary"]["n_consistent"] == 2
    assert res["summary"]["n_single_only"] == 0
    assert res["summary"]["n_multi_only"] == 1
    assert [c["chr"] for c in res["consistent"]] == ["Gm01", "H1"]
    assert res["multi_only"][0]["chr"] == "H2"


def test_multilocus_extract_and_peak_comparison_preserve_orig_coords(tmp_path):
    tsv_file = tmp_path / "farmcpu_results.tsv"
    tsv_file.write_text(
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn\n"
        "chr7_p2\t1500\tchr7:5500\tA\tG\t0.3\t0.8\t0.1\t1e-6\t100\n",
        encoding="utf-8",
    )
    split_map = {
        "threshold_bp": 5_000,
        "unit_bp": 2_000,
        "parts": {
            "chr7_p2": {"orig_chr": "chr7", "part": 2, "offset_bp": 4_000},
        },
    }
    leads = multilocus_tasks.extract_multilocus_leads_from_tsv(
        tsv_file,
        method_name="FARMCPU",
        trait_name="TRAIT1",
        split_map=split_map,
    )
    assert len(leads) == 1
    assert leads[0]["chr"] == "7_p2"
    assert leads[0]["pos"] == 1500
    assert leads[0]["orig_chr"] == "chr7"
    assert leads[0]["orig_pos"] == 5500

    res = generate_peak_comparison(
        single_lead_snps=[{"snp": "chr7:5500", "chr": "7_p2", "pos": 1500, "p": 1e-8}],
        multi_lead_snps=leads,
        output_csv=tmp_path / "Ringkasan_Puncak_GWAS.csv",
        output_json=tmp_path / "peak_comparison.json",
        split_map=split_map,
    )
    assert res["consistent"][0]["orig_chr"] == "chr7"
    assert res["consistent"][0]["orig_pos"] == 5500

    with (tmp_path / "Ringkasan_Puncak_GWAS.csv").open("r", encoding="utf-8") as cf:
        rows = list(csv.DictReader(cf))
    assert rows[0]["Orig_Chr"] == "chr7"
    assert rows[0]["Orig_Pos"] == "5500"


def test_run_gapit_models_aligns_cv_from_eigenvec_and_sets_pca_zero(tmp_path, monkeypatch):
    job_dir = tmp_path / "jobs" / "gapit_cv_ok"
    workspace = job_dir / "multilocus"
    inspect_dir = job_dir / "inspection"
    struct_dir = job_dir / "structure"
    workspace.mkdir(parents=True)
    inspect_dir.mkdir(parents=True)
    struct_dir.mkdir(parents=True)
    log_file = workspace / "multilocus.log"
    runner_script = job_dir / "gapit_runner.R"
    runner_script.write_text("# runner", encoding="utf-8")

    (inspect_dir / "pheno_plink.txt").write_text(
        "FID\tIID\tTRAIT\nS1\tS1\t1.0\nS2\tS2\t2.0\nS3\tS3\t3.0\n",
        encoding="utf-8",
    )
    # Intentionally shuffled sample order + 4 available PCs; request k = 2
    (struct_dir / "pca_results.eigenvec").write_text(
        "S3\tS3\t0.31\t0.32\t0.33\t0.34\n"
        "S1\tS1\t0.11\t0.12\t0.13\t0.14\n"
        "S2\tS2\t0.21\t0.22\t0.23\t0.24\n",
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

    seen_cmds = []

    def fake_run_cli(cmd, **kwargs):
        seen_cmds.append(list(cmd))
        (workspace / "GAPIT.Association.GWAS_Results.BLINK.TRAIT(NYC).csv").write_text(
            "SNP,Chr,Pos,P.value,MAF,effect,nobs,H&B.P.Value\nrs1,1,100,0.01,0.3,0.4,3,0.05\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0)

    monkeypatch.setattr(multilocus_tasks, "run_cli_command", fake_run_cli)

    results = multilocus_tasks._run_gapit_models(
        rscript="/usr/bin/Rscript",
        runner_script=runner_script,
        methods=["BLINK"],
        workspace=workspace,
        log_file=log_file,
        n_pcs=2,
    )

    assert set(results.keys()) == {"BLINK"}
    assert len(seen_cmds) == 1
    cmd = seen_cmds[0]
    cv_file = workspace / "gapit_CV.txt"
    assert "--cv" in cmd and cmd[cmd.index("--cv") + 1] == str(cv_file)
    assert "--pca" in cmd and cmd[cmd.index("--pca") + 1] == "0"
    assert "SNP.MAF" not in " ".join(cmd)

    cv_lines = [ln.split("\t") for ln in cv_file.read_text(encoding="utf-8").splitlines()]
    assert cv_lines[0] == ["Taxa", "PC1", "PC2"]
    assert [row[0] for row in cv_lines[1:]] == ["S1", "S2", "S3"]
    assert cv_lines[1] == ["S1", "0.11", "0.12"]
    assert cv_lines[3] == ["S3", "0.31", "0.32"]
    assert all(len(row) == 3 for row in cv_lines)

    log_txt = log_file.read_text(encoding="utf-8")
    assert "PCA.total=0" in log_txt
    assert "SNP.MAF" in log_txt


def test_write_gapit_inputs_raises_when_eigenvec_missing_gd_sample(tmp_path, monkeypatch):
    job_dir = tmp_path / "jobs" / "gapit_cv_missing"
    workspace = job_dir / "multilocus"
    inspect_dir = job_dir / "inspection"
    struct_dir = job_dir / "structure"
    workspace.mkdir(parents=True)
    inspect_dir.mkdir(parents=True)
    struct_dir.mkdir(parents=True)
    log_file = workspace / "multilocus.log"

    (inspect_dir / "pheno_plink.txt").write_text(
        "FID\tIID\tTRAIT\nS1\tS1\t1.0\nS2\tS2\t2.0\nS3\tS3\t3.0\n",
        encoding="utf-8",
    )
    (struct_dir / "pca_results.eigenvec").write_text(
        "S1\tS1\t0.1\t0.2\nS2\tS2\t0.3\t0.4\n",
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

    with pytest.raises(ValueError, match="S3"):
        multilocus_tasks._write_gapit_inputs(workspace, log_file, n_pcs=2)
