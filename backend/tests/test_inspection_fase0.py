"""Tests for W48 compact Fase 0 JSON chromosome summary and full audit TXT retention."""

from __future__ import annotations

import json
from pathlib import Path
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes.results import get_inspection_data
from app.core.config import settings
from app.db.base import Base
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus, User, UserRole
from app.tasks import _common as task_common, inspection_tasks


def _seed_inspection_job(tmp_path: Path, monkeypatch, vcf_path: Path, pheno_path: Path, db_name: str):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    engine = create_engine(f"sqlite:///{tmp_path / db_name}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(inspection_tasks, "SessionLocal", Session)
    monkeypatch.setattr(task_common, "SessionLocal", Session)

    db = Session()
    try:
        user = User(
            id=uuid.uuid4(),
            email=f"{db_name}@test.local",
            hashed_password="x",
            role=UserRole.RESEARCHER,
        )
        ds = Dataset(
            id=uuid.uuid4(),
            owner_id=user.id,
            original_filename=vcf_path.name,
            storage_path=str(vcf_path),
            phenotype_file_path=str(pheno_path),
            upload_status=UploadStatus.UPLOADED,
        )
        job = AnalysisJob(
            id=uuid.uuid4(),
            dataset_id=ds.id,
            triggered_by=user.id,
            pipeline_type="full_pipeline",
            current_step=PipelineStep.INSPECTION,
            overall_status=JobStatus.PENDING,
            parameters={},
        )
        db.add_all([user, ds, job])
        db.commit()
        return Session, str(job.id), str(ds.id)
    finally:
        db.close()


def test_inspection_summarizes_scaffolds_in_json_while_keeping_full_txt(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "CHROM_SPLIT_THRESHOLD_BP", 5_000)
    vcf_path = tmp_path / "mixed.vcf"
    vcf_path.write_text(
        "##fileformat=VCFv4.2\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\tS3\tS4\n"
        "chr1\t500\tr1\tA\tG\t100\tPASS\t.\tGT\t0/0\t1/1\t0/1\t0/0\n"
        "chr7\t6000\tr2\tC\tT\t100\tPASS\t.\tGT\t0/1\t1/1\t0/0\t0/1\n"
        "scaffold10019\t200\tr3\tA\tG\t100\tPASS\t.\tGT\t0/0\t1/1\t0/1\t0/0\n"
        "scaffold10098\t300\tr4\tC\tT\t100\tPASS\t.\tGT\t0/1\t0/1\t1/1\t0/0\n"
        "scaffold10098\t600\tr5\tG\tA\t100\tPASS\t.\tGT\t1/1\t0/0\t0/1\t1/1\n"
        "scaffold10098\t900\tr6\tT\tC\t100\tPASS\t.\tGT\t0/0\t0/1\t1/1\t0/1\n",
        encoding="utf-8",
    )
    pheno_path = tmp_path / "pheno.txt"
    pheno_path.write_text(
        "FID\tIID\tTRAIT1\nS1\tS1\t10.5\nS2\tS2\t12.0\nS3\tS3\t13.5\nS4\tS4\t15.2\n",
        encoding="utf-8",
    )

    Session, job_id, ds_id = _seed_inspection_job(tmp_path, monkeypatch, vcf_path, pheno_path, "w48_mixed.db")

    res = inspection_tasks.run_inspection(
        job_id=job_id,
        dataset_id=ds_id,
        phenotype_cfg={"trait": "TRAIT1", "id_column": "IID", "fid_column": "FID"},
    )

    insp_dir = tmp_path / "jobs" / job_id / "inspection"
    report_json = json.loads((insp_dir / "inspection_report.json").read_text(encoding="utf-8"))
    chr_sum = report_json["chromosome_summary"]
    assert [row["chrom"] for row in chr_sum["chromosomes"]] == ["chr1", "chr7"]
    assert chr_sum["excluded_contigs"] == {
        "pattern": settings.NON_PRIMARY_CONTIG_REGEX,
        "contigs": 2,
        "variants": 4,
        "single_marker_contigs": 1,
        "examples": ["scaffold10019", "scaffold10098"],
    }
    assert len(chr_sum["excluded_contigs"]["examples"]) <= 20
    assert any("2 scaffold (4 SNP" in msg for msg in report_json.get("info_messages", []))
    assert "akan dibuang pada tahap normalisasi" in chr_sum.get("info_message", "")

    txt_content = Path(res["chromosome_summary_path"]).read_text(encoding="utf-8")
    assert "scaffold10019\t1\t200\t200\t0\t0" in txt_content
    assert "scaffold10098\t3\t300\t900\t0\t0" in txt_content

    log_content = (insp_dir / "inspection.log").read_text(encoding="utf-8")
    assert "2 scaffold (4 SNP" in log_content

    stage_summary = json.loads((insp_dir / "stage_summary.json").read_text(encoding="utf-8"))
    assert stage_summary["counts"]["non_primary_contigs"] == 2
    assert stage_summary["counts"]["non_primary_variants"] == 4

    verify_db = Session()
    try:
        api_payload = get_inspection_data(uuid.UUID(job_id), db=verify_db)
        assert [row["chrom"] for row in api_payload["chromosome_summary"]["chromosomes"]] == ["chr1", "chr7"]
        assert api_payload["chromosome_summary"]["excluded_contigs"]["contigs"] == 2
    finally:
        verify_db.close()


def test_inspection_report_json_stays_compact_with_10000_synthetic_scaffolds(
    tmp_path: Path,
    monkeypatch,
):
    vcf_path = tmp_path / "many_scaffolds.vcf"
    lines = [
        "##fileformat=VCFv4.2",
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\tS3\tS4",
        "chr1\t1000\tc1_1\tA\tG\t100\tPASS\t.\tGT\t0/0\t1/1\t0/1\t0/0",
        "chr2\t2000\tc2_1\tC\tT\t100\tPASS\t.\tGT\t0/1\t1/1\t0/0\t0/1",
    ]
    lines.extend(
        f"scaffold{i:05d}\t100\ts_{i}\tA\tG\t100\tPASS\t.\tGT\t0/0\t1/1\t0/1\t0/0"
        for i in range(1, 10_001)
    )
    vcf_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    pheno_path = tmp_path / "pheno.txt"
    pheno_path.write_text(
        "FID\tIID\tTRAIT1\nS1\tS1\t10.0\nS2\tS2\t12.5\nS3\tS3\t15.0\nS4\tS4\t20.0\n",
        encoding="utf-8",
    )

    _, job_id, ds_id = _seed_inspection_job(tmp_path, monkeypatch, vcf_path, pheno_path, "w48_10k.db")

    res = inspection_tasks.run_inspection(
        job_id=job_id,
        dataset_id=ds_id,
        phenotype_cfg={"trait": "TRAIT1", "id_column": "IID", "fid_column": "FID"},
    )

    report_path = tmp_path / "jobs" / job_id / "inspection" / "inspection_report.json"
    assert report_path.stat().st_size < 50_000

    report_json = json.loads(report_path.read_text(encoding="utf-8"))
    chr_sum = report_json["chromosome_summary"]
    assert [row["chrom"] for row in chr_sum["chromosomes"]] == ["chr1", "chr2"]
    assert chr_sum["excluded_contigs"]["contigs"] == 10_000
    assert chr_sum["excluded_contigs"]["variants"] == 10_000
    assert chr_sum["excluded_contigs"]["single_marker_contigs"] == 10_000
    assert len(chr_sum["excluded_contigs"]["examples"]) == 20

    txt_lines = Path(res["chromosome_summary_path"]).read_text(encoding="utf-8").splitlines()
    assert len(txt_lines) == 10_003
