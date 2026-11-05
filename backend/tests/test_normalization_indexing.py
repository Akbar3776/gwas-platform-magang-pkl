"""Tests for VCF normalization indexing with TBI and CSI fallback support."""

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from app.tasks.normalization_tasks import _index_normalized_vcf
from app.utils.process import SubprocessFailed


def test_index_normalized_vcf_prefers_tbi(monkeypatch, tmp_path: Path):
    """When TBI indexing succeeds, it should not execute CSI fallback."""
    vcf_path = tmp_path / "normalized.vcf.gz"
    vcf_path.touch()
    log_path = tmp_path / "bcftools.log"

    executed_commands = []

    def mock_run_cli(cmd, **kwargs):
        executed_commands.append(cmd)
        return MagicMock(returncode=0)

    monkeypatch.setattr("app.tasks.normalization_tasks.run_cli_command", mock_run_cli)

    _index_normalized_vcf("bcftools", vcf_path, tmp_path, log_path)

    assert len(executed_commands) == 1
    assert executed_commands[0] == ["bcftools", "index", "-t", str(vcf_path)]


def test_index_normalized_vcf_falls_back_to_csi_on_large_coordinate_error(monkeypatch, tmp_path: Path):
    """When TBI indexing fails (e.g. coordinates >512Mb), it automatically runs CSI indexing."""
    vcf_path = tmp_path / "normalized.vcf.gz"
    vcf_path.touch()
    log_path = tmp_path / "bcftools.log"

    executed_commands = []

    def mock_run_cli(cmd, **kwargs):
        executed_commands.append(cmd)
        if "-t" in cmd:
            raise SubprocessFailed(
                cmd,
                255,
                log_path,
                (
                    "[E::hts_idx_check_range] Region 536917513..536917514 cannot be stored in a tbi index. Try using a csi index\n",
                    "index: failed to create index\n",
                ),
            )
        return MagicMock(returncode=0)

    monkeypatch.setattr("app.tasks.normalization_tasks.run_cli_command", mock_run_cli)

    _index_normalized_vcf("bcftools", vcf_path, tmp_path, log_path)

    assert len(executed_commands) == 2
    assert executed_commands[0] == ["bcftools", "index", "-t", str(vcf_path)]
    assert executed_commands[1] == ["bcftools", "index", "-c", str(vcf_path)]
    assert log_path.is_file()
    log_text = log_path.read_text(encoding="utf-8")
    assert "Index TBI gagal" in log_text
    assert "beralih membuat index CSI" in log_text


def test_index_normalized_vcf_tolerates_complete_indexing_failure(monkeypatch, tmp_path: Path):
    """If both TBI and CSI fail, it logs a warning without crashing the pipeline."""
    vcf_path = tmp_path / "normalized.vcf.gz"
    vcf_path.touch()
    log_path = tmp_path / "bcftools.log"

    executed_commands = []

    def mock_run_cli(cmd, **kwargs):
        executed_commands.append(cmd)
        raise RuntimeError("CLI failed")

    monkeypatch.setattr("app.tasks.normalization_tasks.run_cli_command", mock_run_cli)

    # Should not raise
    _index_normalized_vcf("bcftools", vcf_path, tmp_path, log_path)

    assert len(executed_commands) == 2
    assert log_path.is_file()
    assert "[WARNING] Pembuatan index VCF gagal" in log_path.read_text(encoding="utf-8")


def test_normalization_halts_immediately_on_bcftools_failure_without_python_fallback(monkeypatch, tmp_path: Path):
    """When bcftools sort fails, run_vcf_normalization must stop immediately and mark job FAILED."""
    import uuid
    from app.core.config import settings
    from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus
    from app.tasks import normalization_tasks
    from tests.test_pipeline_e2e import InMemoryDB

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    db = InMemoryDB()
    monkeypatch.setattr(normalization_tasks, "SessionLocal", lambda: db)

    vcf_file = tmp_path / "input.vcf"
    vcf_file.write_text("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n", encoding="utf-8")

    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        original_filename="input.vcf",
        storage_path=str(vcf_file),
        file_size_bytes=vcf_file.stat().st_size,
        upload_status=UploadStatus.UPLOADED,
    )
    db.add(ds)

    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=ds.id,
        triggered_by=ds.owner_id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.NORMALIZATION,
        overall_status=JobStatus.RUNNING_NORMALIZATION,
        parameters={},
    )
    db.add(job)

    log_path = tmp_path / "jobs" / str(job.id) / "normalization" / "bcftools.log"

    def mock_run_cli(cmd, **kwargs):
        if "sort" in cmd:
            raise SubprocessFailed(cmd, 134, log_path, ("vcfsort.c:177: buf_push failed\n",))
        return MagicMock(returncode=0)

    monkeypatch.setattr("app.tasks.normalization_tasks.which_tool", lambda name: "bcftools")
    monkeypatch.setattr("app.tasks.normalization_tasks.run_cli_command", mock_run_cli)

    with pytest.raises(RuntimeError, match="Eksekusi bcftools gagal"):
        normalization_tasks.run_vcf_normalization(job_id=str(job.id), dataset_id=str(ds.id))

    assert job.overall_status == JobStatus.FAILED
    assert job.failed_step == "normalization"
    assert not (tmp_path / "jobs" / str(job.id) / "normalization" / "normalized.vcf.gz").exists()
    assert "[ERROR] bcftools gagal dieksekusi" in log_path.read_text(encoding="utf-8")


def test_qc_halts_immediately_on_plink_failure_without_python_fallback(monkeypatch, tmp_path: Path):
    """When PLINK2 fails during QC, run_qc_pipeline must stop immediately without Python fallback."""
    import uuid
    from app.core.config import settings
    from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus
    from app.tasks import qc_tasks
    from tests.test_pipeline_e2e import InMemoryDB

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    db = InMemoryDB()
    monkeypatch.setattr(qc_tasks, "SessionLocal", lambda: db)

    vcf_file = tmp_path / "normalized.vcf.gz"
    vcf_file.write_bytes(b"dummy")

    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        original_filename="input.vcf",
        storage_path=str(vcf_file),
        file_size_bytes=5,
        upload_status=UploadStatus.UPLOADED,
    )
    db.add(ds)

    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=ds.id,
        triggered_by=ds.owner_id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.QC,
        overall_status=JobStatus.RUNNING_QC,
        parameters={},
    )
    db.add(job)

    log_path = tmp_path / "jobs" / str(job.id) / "qc" / "plink_qc.log"

    def mock_run_cli(cmd, **kwargs):
        raise SubprocessFailed(cmd, 6, log_path, ("Error: Invalid POS on line 1 of --vcf file.\n",))

    monkeypatch.setattr("app.tasks.qc_tasks.which_tool", lambda name: "plink2" if name == "plink2" else None)
    monkeypatch.setattr("app.tasks.qc_tasks.run_cli_command", mock_run_cli)

    with pytest.raises(SubprocessFailed):
        qc_tasks.run_qc_pipeline(
            {"normalized_vcf_path": str(vcf_file)},
            job_id=str(job.id),
            dataset_id=str(ds.id),
        )

    assert job.overall_status == JobStatus.FAILED
    assert job.failed_step == "qc"
    assert "[ERROR] PLINK2 gagal dieksekusi" in log_path.read_text(encoding="utf-8")
