"""Tests for VCF normalization indexing with TBI and CSI fallback support."""

from pathlib import Path
from unittest.mock import MagicMock

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


def test_normalization_splits_overflow_chromosomes_before_bcftools_sort_and_saves_map(
    monkeypatch,
    tmp_path: Path,
):
    """run_vcf_normalization harus memecah kromosom overflow sebelum bcftools sort dan menyimpan split map."""
    import json
    import uuid
    from app.core.config import settings
    from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus
    from app.services.chromosome_service import SIGNED_32BIT_MAX, load_split_map, restore_coord
    from app.tasks import _common as task_common, normalization_tasks
    from tests.test_pipeline_e2e import InMemoryDB

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    db = InMemoryDB()
    monkeypatch.setattr(normalization_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(task_common, "SessionLocal", lambda: db)

    vcf_file = tmp_path / "garlic.vcf"
    vcf_file.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "Chr1\t0\trs_invalid\tA\tG\t.\tPASS\t.\tGT\t0/0\n"
        "Chr1\t1000\trs_ok\tA\tG\t.\tPASS\t.\tGT\t0/1\n"
        f"Chr1\t{SIGNED_32BIT_MAX}\trs_limit\tC\tT\t.\tPASS\t.\tGT\t0/0\n"
        f"Chr1\t{SIGNED_32BIT_MAX + 1}\trs_overflow\tG\tA\t.\tPASS\t.\tGT\t1/1\n",
        encoding="utf-8",
    )

    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        original_filename="garlic.vcf",
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

    captured_split_content = []

    def mock_run_cli(cmd, **kwargs):
        if "sort" in cmd:
            split_arg = Path(cmd[-1])
            assert split_arg.name == "split_input.vcf"
            captured_split_content.append(split_arg.read_text(encoding="utf-8"))
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_bytes(b"sorted_gz")
        return MagicMock(returncode=0)

    monkeypatch.setattr("app.tasks.normalization_tasks.which_tool", lambda name: "bcftools")
    monkeypatch.setattr("app.tasks.normalization_tasks.run_cli_command", mock_run_cli)

    res = normalization_tasks.run_vcf_normalization(job_id=str(job.id), dataset_id=str(ds.id))

    assert len(captured_split_content) == 1
    assert "rs_invalid" not in captured_split_content[0]
    assert "rs_ok" in captured_split_content[0]
    assert "rs_limit" in captured_split_content[0]
    assert "rs_overflow" in captured_split_content[0]
    expected_pseudo_pos = (SIGNED_32BIT_MAX + 1) - (2 * 1_000_000_000)
    assert f"Chr1_p2\t{expected_pseudo_pos}\trs_overflow" in captured_split_content[0]

    assert res["qc_overflow_removed"] == 0
    assert res["chrom_split"]["applied"] is True
    assert res["chrom_split"]["overflow_chromosomes"] == ["Chr1"]
    assert res["chrom_split"]["invalid_pos_removed"] == 1
    assert job.parameters["qc_overflow_removed"] == 0
    assert job.parameters["chrom_split"]["applied"] is True
    assert not (tmp_path / "jobs" / str(job.id) / "normalization" / "split_input.vcf").exists()

    loaded_map = load_split_map(str(job.id))
    assert loaded_map is not None
    assert restore_coord("Chr1_p2", expected_pseudo_pos, loaded_map) == ("Chr1", SIGNED_32BIT_MAX + 1)

    stage_summary_file = tmp_path / "jobs" / str(job.id) / "normalization" / "stage_summary.json"
    assert stage_summary_file.is_file()
    stage_payload = json.loads(stage_summary_file.read_text(encoding="utf-8"))
    assert stage_payload["overflow_chromosomes"] == ["Chr1"]


def test_normalization_real_bcftools_on_overflow_dataset(
    monkeypatch,
    tmp_path: Path,
    overflow_dataset: dict,
):
    from app.tasks._common import which_tool

    if which_tool("bcftools") is None:
        pytest.skip("bcftools tidak tersedia di host")

    import gzip
    import uuid
    from app.core.config import settings
    from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus
    from app.services.chromosome_service import load_split_map
    from app.tasks import _common as task_common, normalization_tasks
    from tests.test_pipeline_e2e import InMemoryDB

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    db = InMemoryDB()
    monkeypatch.setattr(normalization_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(task_common, "SessionLocal", lambda: db)

    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        original_filename="overflow.vcf.gz",
        storage_path=str(overflow_dataset["vcf_path"]),
        file_size_bytes=overflow_dataset["vcf_path"].stat().st_size,
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

    res = normalization_tasks.run_vcf_normalization(job_id=str(job.id), dataset_id=str(ds.id))
    norm_gz = Path(res["normalized_vcf_path"])
    assert norm_gz.is_file()
    assert Path(str(norm_gz) + ".tbi").is_file() or Path(str(norm_gz) + ".csi").is_file()
    assert res["chrom_split"]["overflow_chromosomes"] == ["chr7"]
    assert res["chrom_split"]["invalid_pos_removed"] == 1
    assert load_split_map(str(job.id)) is not None

    with gzip.open(norm_gz, "rt", encoding="utf-8") as fh:
        data_lines = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    assert len(data_lines) == 17
    assert any(ln.startswith("chr7_p2\t1500\t") for ln in data_lines)


def test_normalization_filters_non_primary_scaffolds_and_records_consistent_counts(
    monkeypatch,
    tmp_path: Path,
):
    import json
    import uuid
    from app.core.config import settings
    from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus
    from app.tasks import _common as task_common, normalization_tasks
    from tests.test_pipeline_e2e import InMemoryDB

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    db = InMemoryDB()
    monkeypatch.setattr(normalization_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(task_common, "SessionLocal", lambda: db)

    vcf_file = tmp_path / "mixed_garlic.vcf"
    vcf_file.write_text(
        "##fileformat=VCFv4.2\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "##contig=<ID=Chr1,length=3000000000>\n"
        "##contig=<ID=scaffold10019,length=1000>\n"
        "##contig=<ID=scaffold10098,length=2000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "Chr1\t0\trs_inv\tA\tG\t.\tPASS\t.\tGT\t0/0\n"
        "Chr1\t1000\trs_ok\tA\tG\t.\tPASS\t.\tGT\t0/1\n"
        f"Chr1\t{2_147_483_648}\trs_overflow\tG\tA\t.\tPASS\t.\tGT\t1/1\n"
        "scaffold10019\t200\tscaff19_1\tA\tG\t.\tPASS\t.\tGT\t0/0\n"
        "scaffold10098\t300\tscaff98_1\tC\tT\t.\tPASS\t.\tGT\t0/1\n"
        "scaffold10098\t600\tscaff98_2\tG\tA\t.\tPASS\t.\tGT\t1/1\n"
        "scaffold10098\t900\tscaff98_3\tT\tC\t.\tPASS\t.\tGT\t0/0\n",
        encoding="utf-8",
    )

    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        original_filename="mixed_garlic.vcf",
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

    captured_split: list[str] = []

    def mock_run_cli(cmd, **kwargs):
        if "sort" in cmd:
            captured_split.append(Path(cmd[-1]).read_text(encoding="utf-8"))
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_bytes(b"sorted_gz")
        return MagicMock(returncode=0)

    monkeypatch.setattr("app.tasks.normalization_tasks.which_tool", lambda name: "bcftools")
    monkeypatch.setattr("app.tasks.normalization_tasks.run_cli_command", mock_run_cli)

    normalization_tasks.run_vcf_normalization(job_id=str(job.id), dataset_id=str(ds.id))

    assert len(captured_split) == 1
    assert "scaffold" not in captured_split[0]
    assert "rs_ok" in captured_split[0]
    assert "Chr1_p2\t147483648\trs_overflow" in captured_split[0]

    norm_dir = tmp_path / "jobs" / str(job.id) / "normalization"
    stats_payload = json.loads((norm_dir / "qc_overflow_stats.json").read_text(encoding="utf-8"))
    summary_payload = json.loads((norm_dir / "stage_summary.json").read_text(encoding="utf-8"))
    log_text = (norm_dir / "bcftools.log").read_text(encoding="utf-8")

    assert "[Fase 0 - Buang Scaffold] 2 kontig non-utama (4 varian) dibuang; pola=" in log_text
    assert stats_payload["non_primary_removed_contigs"] == 2
    assert stats_payload["non_primary_removed_variants"] == 4
    assert stats_payload["contig_filter_pattern"] == settings.NON_PRIMARY_CONTIG_REGEX
    assert stats_payload["kept_variants"] == 2

    assert summary_payload["counts"]["variants_in"] == 7
    assert summary_payload["counts"]["variants_out"] == 2
    assert summary_payload["counts"]["invalid_pos_removed"] == 1
    assert summary_payload["counts"]["non_primary_removed_contigs"] == 2
    assert summary_payload["counts"]["non_primary_removed_variants"] == 4

    assert job.parameters["contig_filter"] == {
        "pattern": settings.NON_PRIMARY_CONTIG_REGEX,
        "removed_contigs": 2,
        "removed_variants": 4,
    }


def test_normalization_real_bcftools_drops_non_primary_scaffolds_when_present(
    monkeypatch,
    tmp_path: Path,
):
    from app.tasks._common import which_tool

    if which_tool("bcftools") is None:
        pytest.skip("bcftools tidak tersedia di host")

    import gzip
    import uuid
    from app.core.config import settings
    from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus
    from app.tasks import _common as task_common, normalization_tasks
    from tests.test_pipeline_e2e import InMemoryDB

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    db = InMemoryDB()
    monkeypatch.setattr(normalization_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(task_common, "SessionLocal", lambda: db)

    vcf_file = tmp_path / "mixed_real.vcf"
    vcf_file.write_text(
        "##fileformat=VCFv4.2\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "##contig=<ID=chr1,length=5000>\n"
        "##contig=<ID=scaffold10019,length=1000>\n"
        "##contig=<ID=scaffold10098,length=2000>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"
        "chr1\t500\tchr1:500\tA\tG\t100\tPASS\t.\tGT\t0/0\t1/1\n"
        "scaffold10019\t200\tscaff19:200\tA\tG\t100\tPASS\t.\tGT\t0/0\t1/1\n"
        "scaffold10098\t300\tscaff98:300\tC\tT\t100\tPASS\t.\tGT\t0/1\t0/1\n"
        "scaffold10098\t600\tscaff98:600\tG\tA\t100\tPASS\t.\tGT\t1/1\t0/0\n",
        encoding="utf-8",
    )

    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        original_filename="mixed_real.vcf",
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

    res = normalization_tasks.run_vcf_normalization(job_id=str(job.id), dataset_id=str(ds.id))
    norm_gz = Path(res["normalized_vcf_path"])
    assert norm_gz.is_file()
    with gzip.open(norm_gz, "rt", encoding="utf-8") as fh:
        all_lines = fh.read().splitlines()
    contig_headers = [ln for ln in all_lines if ln.startswith("##contig=")]
    data_lines = [ln for ln in all_lines if ln and not ln.startswith("#")]
    assert not any("scaffold" in ln for ln in contig_headers)
    assert len(data_lines) == 1
    assert data_lines[0].startswith("chr1\t500\t")
    assert res["contig_filter"]["removed_contigs"] == 2
    assert res["contig_filter"]["removed_variants"] == 3
