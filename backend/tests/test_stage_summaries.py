"""Unit tests for structured stage_summary.json outputs across pipeline stages."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import uuid

import numpy as np

from app.core.config import settings
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus, User, UserRole
from app.tasks import (
    _common as task_common,
    annotation_tasks,
    gemma_tasks,
    imputation_tasks,
    inspection_tasks,
    multilocus_tasks,
    normalization_tasks,
    postgwas_tasks,
    qc_tasks,
    structure_tasks,
    visualization_tasks,
)
from tests.test_pipeline_e2e import InMemoryDB


def _patch_sessions(monkeypatch, db: InMemoryDB, *modules) -> None:
    monkeypatch.setattr(task_common, "SessionLocal", lambda: db)
    for mod in modules:
        monkeypatch.setattr(mod, "SessionLocal", lambda: db)


def test_stage_1_to_5_stage_summary_written_with_relevant_counts(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))

    db = InMemoryDB()
    _patch_sessions(
        monkeypatch,
        db,
        inspection_tasks,
        normalization_tasks,
        qc_tasks,
        imputation_tasks,
        structure_tasks,
    )

    user = User(
        id=uuid.uuid4(),
        email="summary@example.com",
        hashed_password="hash",
        role=UserRole.RESEARCHER,
    )
    ds_dir = tmp_path / "datasets" / "ds-summary"
    ds_dir.mkdir(parents=True)
    vcf_path = ds_dir / "cohort.vcf"
    vcf_path.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=1,length=10000>\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\tS3\tS4\tS5\tS6\n"
        "1\t0\trs_inv\tA\tG\t.\tPASS\t.\tGT\t0/0\t0/1\t1/1\t0/1\t0/0\t1/1\n"
        "1\t1000\trs1\tA\tG\t.\tPASS\t.\tGT\t0/0\t0/1\t1/1\t0/1\t0/0\t1/1\n"
        "1\t2000\trs2\tC\tT\t.\tPASS\t.\tGT\t0/1\t1/1\t0/0\t0/1\t1/1\t0/0\n"
        "1\t3000\trs3\tG\tA\t.\tPASS\t.\tGT\t1/1\t0/1\t0/0\t1/1\t0/1\t0/0\n",
        encoding="utf-8",
    )
    pheno_path = ds_dir / "pheno.txt"
    pheno_path.write_text(
        "FID\tIID\tTRAIT1\n"
        "S1\tS1\t10.0\nS2\tS2\t11.0\nS3\tS3\t12.0\nS4\tS4\t13.0\nS5\tS5\t14.0\nS6\tS6\t15.0\n",
        encoding="utf-8",
    )

    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf",
        storage_path=str(vcf_path),
        phenotype_file_path=str(pheno_path),
        file_size_bytes=vcf_path.stat().st_size,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=ds.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.INSPECTION,
        overall_status=JobStatus.PENDING,
        parameters={"qc": {"population_type": "outcross"}, "structure": {"n_pc_covariates": 2}},
    )
    db.add(user)
    db.add(ds)
    db.add(job)

    # Stage 1: Inspection
    insp_out = inspection_tasks.run_inspection(job_id=str(job.id), dataset_id=str(ds.id))

    # Stage 2: Normalization (mock bcftools)
    def mock_norm_cli(cmd, **kwargs):
        if "sort" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"sorted-vcf-gz")
        return MagicMock(returncode=0)

    monkeypatch.setattr(normalization_tasks, "which_tool", lambda name: "bcftools" if name == "bcftools" else None)
    monkeypatch.setattr(normalization_tasks, "run_cli_command", mock_norm_cli)
    norm_out = normalization_tasks.run_vcf_normalization(insp_out, job_id=str(job.id), dataset_id=str(ds.id))

    # Stage 3: QC (mock plink2 + comparison metrics)
    def mock_qc_cli(cmd, **kwargs):
        if "--make-bed" in cmd:
            out_prefix = Path(cmd[cmd.index("--out") + 1])
            for ext in (".bed", ".bim", ".fam", ".vcf.gz"):
                out_prefix.with_suffix(ext).write_bytes(b"stub")
        return MagicMock(returncode=0)

    monkeypatch.setattr(qc_tasks, "which_tool", lambda name: "plink2" if name == "plink2" else None)
    monkeypatch.setattr(qc_tasks, "run_cli_command", mock_qc_cli)
    monkeypatch.setattr(
        qc_tasks,
        "build_qc_comparison_from_plink_outputs",
        lambda **kwargs: {
            "before": {"n_samples": 6, "n_snps": 3},
            "after": {"n_samples": 6, "n_snps": 3},
            "removed": {"maf": 0, "geno": 0, "mind": 0, "hwe": 0, "non_snp": 0, "multiallelic": 0},
            "snp_loss_fraction": 0.0,
            "warning": None,
        },
    )
    qc_out = qc_tasks.run_qc_pipeline(norm_out, job_id=str(job.id), dataset_id=str(ds.id))

    # Stage 4: Imputation (mode=none skip path)
    imp_out = imputation_tasks.run_imputation(qc_out, job_id=str(job.id), params={"mode": "none"})

    # Stage 5: Structure (mock plink + read_plink_binary)
    def mock_struct_cli(cmd, **kwargs):
        out_prefix = Path(cmd[cmd.index("--out") + 1])
        if "--indep-pairwise" in cmd:
            out_prefix.with_suffix(".prune.in").write_text("rs1\nrs2\n", encoding="utf-8")
        elif "--make-bed" in cmd:
            for ext in (".bed", ".bim", ".fam"):
                out_prefix.with_suffix(ext).write_bytes(b"stub")
        return MagicMock(returncode=0)

    monkeypatch.setattr(structure_tasks, "which_tool", lambda name: "plink" if name == "plink" else None)
    monkeypatch.setattr(structure_tasks, "run_cli_command", mock_struct_cli)
    monkeypatch.setattr(
        structure_tasks,
        "read_plink_binary",
        lambda _p: (
            ["S1", "S2", "S3", "S4", "S5", "S6"],
            [
                {"chrom": "1", "rsid": "rs1", "pos": 1000, "a1": "A", "a2": "G"},
                {"chrom": "1", "rsid": "rs2", "pos": 2000, "a1": "C", "a2": "T"},
                {"chrom": "1", "rsid": "rs3", "pos": 3000, "a1": "G", "a2": "A"},
            ],
            np.array(
                [
                    [0, 1, 2],
                    [1, 0, 1],
                    [2, 1, 0],
                    [0, 2, 1],
                    [1, 1, 2],
                    [2, 0, 1],
                ],
                dtype=np.int8,
            ),
        ),
    )
    structure_tasks.run_structure_analysis(
        imp_out,
        job_id=str(job.id),
        dataset_id=str(ds.id),
        structure_params={"n_pc_covariates": 2},
    )

    job_root = tmp_path / "jobs" / str(job.id)
    for stage in ("inspection", "normalization", "qc", "imputation", "structure"):
        sf = job_root / stage / "stage_summary.json"
        assert sf.is_file(), f"Missing stage_summary.json for {stage}"
        disk_payload = json.loads(sf.read_text(encoding="utf-8"))
        assert disk_payload["stage"] == stage
        assert job.parameters["stage_summaries"][stage]["stage"] == stage

    insp_sum = json.loads((job_root / "inspection" / "stage_summary.json").read_text(encoding="utf-8"))
    assert insp_sum["counts"]["samples_matched"] == 6
    assert insp_sum["counts"]["variants_in"] == 4
    assert insp_sum["counts"]["invalid_pos"] == 1

    norm_sum = json.loads((job_root / "normalization" / "stage_summary.json").read_text(encoding="utf-8"))
    assert norm_sum["counts"]["variants_in"] == 4
    assert norm_sum["counts"]["variants_out"] == 3
    assert norm_sum["counts"]["invalid_pos_removed"] == 1

    qc_sum = json.loads((job_root / "qc" / "stage_summary.json").read_text(encoding="utf-8"))
    assert qc_sum["counts"]["samples_in"] == 6
    assert qc_sum["counts"]["samples_out"] == 6
    assert qc_sum["counts"]["variants_out"] == 3

    imp_sum = json.loads((job_root / "imputation" / "stage_summary.json").read_text(encoding="utf-8"))
    assert imp_sum["status"] == "skipped"
    assert imp_sum["counts"]["beagle_guard_removed"] == 0

    struct_sum = json.loads((job_root / "structure" / "stage_summary.json").read_text(encoding="utf-8"))
    assert struct_sum["counts"]["n_samples"] == 6
    assert struct_sum["counts"]["n_pc"] == 2
    assert struct_sum["counts"]["ld_decay_kb"] > 0


def test_stage_6_to_10_stage_summary_written_with_relevant_counts(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))

    db = InMemoryDB()
    _patch_sessions(
        monkeypatch,
        db,
        gemma_tasks,
        multilocus_tasks,
        postgwas_tasks,
        visualization_tasks,
        annotation_tasks,
    )

    user = User(
        id=uuid.uuid4(),
        email="summary610@example.com",
        hashed_password="hash",
        role=UserRole.RESEARCHER,
    )
    ds_dir = tmp_path / "datasets" / "ds-summary-6-10"
    ds_dir.mkdir(parents=True)

    norm_vcf = ds_dir / "cohort.vcf"
    norm_vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=1,length=10000>\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\tS3\tS4\tS5\n"
        "1\t1000\trs1\tA\tG\t.\tPASS\t.\tGT\t0/0\t0/1\t1/1\t1/1\t0/0\n"
        "1\t2000\trs2\tC\tT\t.\tPASS\t.\tGT\t0/1\t1/1\t0/0\t0/1\t1/1\n",
        encoding="utf-8",
    )
    gff_path = ds_dir / "genes.gff3"
    gff_path.write_text(
        "##gff-version 3\n"
        "1\tSimGWAS\tgene\t900\t1200\t.\t+\t.\tID=GENE_1;Name=GENE_1\n",
        encoding="utf-8",
    )
    ref_fa = ds_dir / "reference.fa"
    ref_fa.write_text(f">1\n{'ACGT' * 3000}\n", encoding="utf-8")

    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf",
        storage_path=str(norm_vcf),
        annotation_file_path=str(gff_path),
        extra_metadata={"reference_fasta": {"path": str(ref_fa), "status": "uploaded"}},
        file_size_bytes=norm_vcf.stat().st_size,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=ds.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.GWAS,
        overall_status=JobStatus.RUNNING_GWAS,
        parameters={
            "gwas": {"model": "lm", "enable_multilocus": False},
            "chosen_ld_decay_kb": 10,
            "annotation": {"enabled": True, "window_kb": 10},
        },
    )
    db.add(user)
    db.add(ds)
    db.add(job)

    job_root = tmp_path / "jobs" / str(job.id)
    (job_root / "inspection").mkdir(parents=True)
    pheno_plink = job_root / "inspection" / "pheno_plink.txt"
    pheno_plink.write_text(
        "FID\tIID\tTRAIT1\nS1\tS1\t10.0\nS2\tS2\t12.0\nS3\tS3\t18.0\nS4\tS4\t19.0\nS5\tS5\t10.5\n",
        encoding="utf-8",
    )
    (job_root / "qc").mkdir(parents=True)
    bfile_prefix = job_root / "qc" / "plink_qc"
    bfile_prefix.with_suffix(".bed").write_bytes(b"stub")
    bfile_prefix.with_suffix(".bim").write_text(
        "1\trs1\t0\t1000\tG\tA\n1\trs2\t0\t2000\tT\tC\n",
        encoding="utf-8",
    )
    bfile_prefix.with_suffix(".fam").write_text(
        "\n".join(f"S{i}\tS{i}\t0\t0\t0\t-9" for i in range(1, 6)) + "\n",
        encoding="utf-8",
    )

    dummy_gemma = tmp_path / "gemma.bin"
    dummy_plink = tmp_path / "plink.bin"
    dummy_gemma.write_bytes(b"bin")
    dummy_plink.write_bytes(b"bin")
    monkeypatch.setattr(gemma_tasks, "GEMMA_BINARY", str(dummy_gemma))
    monkeypatch.setattr(gemma_tasks, "PLINK_BINARY", str(dummy_plink))

    def mock_gemma_cli(cmd, **kwargs):
        assoc_out = job_root / "gwas" / "output" / "association.assoc.txt"
        assoc_out.parent.mkdir(parents=True, exist_ok=True)
        assoc_out.write_text(
            "chr\trs\tps\tn_miss\tallele1\tallele0\taf\tbeta\tse\tlogl_H1\tl_remle\tp_wald\n"
            "1\trs1\t1000\t0\tA\tG\t0.3\t0.9\t0.1\t0\t0\t1e-8\n"
            "1\trs2\t2000\t0\tC\tT\t0.4\t0.1\t0.2\t0\t0\t0.5\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0)

    monkeypatch.setattr(gemma_tasks, "run_cli_command", mock_gemma_cli)

    # Stage 6: GEMMA GWAS
    gwas_out = gemma_tasks.run_gemma_gwas(
        {
            "output_vcf": str(norm_vcf),
            "genotype_bfile_prefix": str(bfile_prefix),
            "pheno_path": str(pheno_plink),
        },
        job_id=str(job.id),
        phenotype_path=str(pheno_plink),
        model="lm",
    )

    # Stage 7 (skipped): Multi-locus disabled
    ml_skip_out = multilocus_tasks.run_multilocus_gwas(
        gwas_out,
        job_id=str(job.id),
        dataset_id=str(ds.id),
        params={},
    )
    ml_skip_sum = json.loads((job_root / "multilocus" / "stage_summary.json").read_text(encoding="utf-8"))
    assert ml_skip_sum["status"] == "skipped"
    assert ml_skip_sum["counts"]["n_models"] == 0

    # Stage 7 (completed): Multi-locus enabled with Python engine
    monkeypatch.setattr(multilocus_tasks, "which_tool", lambda _name: None)
    job.parameters["gwas"]["enable_multilocus"] = True
    ml_out = multilocus_tasks.run_multilocus_gwas(
        {**ml_skip_out, "normalized_vcf_path": str(norm_vcf)},
        job_id=str(job.id),
        dataset_id=str(ds.id),
        params={"methods": ["FarmCPU"]},
    )

    # Stage 8: Post-GWAS
    monkeypatch.setattr(postgwas_tasks, "which_tool", lambda _name: None)
    post_out = postgwas_tasks.run_postgwas_analysis(
        ml_out,
        job_id=str(job.id),
        dataset_id=str(ds.id),
    )

    # Stage 9: Visualization
    vis_out = visualization_tasks.run_visualization(
        post_out,
        job_id=str(job.id),
        dataset_id=str(ds.id),
    )

    # Stage 10: Annotation
    annotation_tasks.run_annotation(
        vis_out,
        job_id=str(job.id),
        dataset_id=str(ds.id),
    )

    for stage in ("gwas", "multilocus", "postgwas", "visualization", "annotation"):
        sf = job_root / stage / "stage_summary.json"
        assert sf.is_file(), f"Missing stage_summary.json for {stage}"
        disk_payload = json.loads(sf.read_text(encoding="utf-8"))
        assert disk_payload["stage"] == stage
        assert job.parameters["stage_summaries"][stage]["stage"] == stage

    gwas_sum = json.loads((job_root / "gwas" / "stage_summary.json").read_text(encoding="utf-8"))
    assert gwas_sum["engine"] == "gemma"
    assert gwas_sum["model"] == "lm"
    assert isinstance(gwas_sum["lambda_gc"], float)
    assert gwas_sum["counts"]["total_snps"] == 2
    assert gwas_sum["counts"]["significant_snps"] == 1

    ml_sum = json.loads((job_root / "multilocus" / "stage_summary.json").read_text(encoding="utf-8"))
    assert ml_sum["status"] == "completed"
    assert ml_sum["engines"]["FARMCPU"] == "python-approx"
    assert ml_sum["counts"]["n_models"] == 1

    post_sum = json.loads((job_root / "postgwas" / "stage_summary.json").read_text(encoding="utf-8"))
    assert isinstance(post_sum["lambda_gc"], float)
    assert post_sum["counts"]["lead_snps"] == 1

    vis_sum = json.loads((job_root / "visualization" / "stage_summary.json").read_text(encoding="utf-8"))
    assert vis_sum["counts"]["total_snps"] == 2
    assert vis_sum["counts"]["kept_rows"] == 2

    anno_sum = json.loads((job_root / "annotation" / "stage_summary.json").read_text(encoding="utf-8"))
    assert anno_sum["status"] == "completed"
    assert anno_sum["n_regions"] == 1
    assert anno_sum["regions_total_bp"] == 11000
    assert anno_sum["counts"]["Genik"] == 1
