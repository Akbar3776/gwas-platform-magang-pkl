from pathlib import Path
import pytest
import numpy as np

from app.schemas.pipeline import GWASPipelineConfig
from app.tasks.multilocus_tasks import (
    SUPPORTED_MULTILOCUS_MODELS,
    _run_python_multilocus_engine,
    generate_peak_comparison,
)


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
    # Setup mock workspace
    job_dir = tmp_path / "jobs" / "test_job_123"
    multilocus_dir = job_dir / "multilocus"
    norm_dir = job_dir / "normalization"
    gwas_dir = job_dir / "gwas"
    multilocus_dir.mkdir(parents=True)
    norm_dir.mkdir(parents=True)
    gwas_dir.mkdir(parents=True)

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
    pheno_path = gwas_dir / "phenotype.txt"
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
