from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.tasks.imputation_tasks import _beagle_command, run_imputation
from app.utils.process import SubprocessFailed


def test_beagle_command_resolution():
    with patch("app.tasks.imputation_tasks.which_tool") as mock_tool:
        mock_tool.side_effect = lambda t: {
            "java": "/custom/bin/java",
            "beagle_jar": "/custom/beagle.jar",
        }.get(t)

        cmd, rlimit = _beagle_command(
            qc_vcf_path="/path/to/qc.vcf.gz",
            reference_vcf_path=None,
            output_prefix="/path/to/out",
            params={"nthreads": 4, "beagle_max_memory": "-Xmx4g"},
        )

        assert cmd[0] == "/custom/bin/java"
        assert cmd[1] == "-Xmx4g"
        assert "/custom/beagle.jar" in cmd
        assert "gt=/path/to/qc.vcf.gz" in cmd
        assert "out=/path/to/out" in cmd
        assert "nthreads=4" in cmd


def test_imputation_graceful_integer_overflow_fallback(tmp_path):
    job_id = "test-job-overflow"
    ctx = {
        "status": "completed",
        "job_id": job_id,
        "qc_vcf_path": str(tmp_path / "qc.vcf.gz"),
        "qc_bfile_prefix": str(tmp_path / "qc_prefix"),
    }

    mock_db = MagicMock()
    mock_job = MagicMock()
    mock_job.work_dir = str(tmp_path)
    mock_job.parameters = {"imputation": {"mode": "reference_free"}}
    mock_db.get.return_value = mock_job

    overflow_error = SubprocessFailed(
        cmd=["java", "-jar", "beagle.jar"],
        returncode=1,
        log_file=tmp_path / "beagle.log",
        last_lines=(
            "Window 331 [7:2052271596-2091008499]\n",
            "java.lang.IllegalArgumentException: An estimated base position exceeds the maximum integer value\n",
            "Is the window parameter in cM units?\n",
            "    at vcf.PositionMap.basePos(PositionMap.java:71)\n",
        ),
    )

    with (
        patch("app.tasks.imputation_tasks.SessionLocal", return_value=mock_db),
        patch("app.tasks.imputation_tasks.stage_dir", return_value=tmp_path),
        patch("app.tasks.imputation_tasks.which_tool", return_value="/bin/tool"),
        patch("pathlib.Path.is_file", return_value=True),
        patch("app.tasks.imputation_tasks.run_cli_command", side_effect=overflow_error),
    ):
        result = run_imputation(
            ctx,
            job_id=job_id,
            reference_vcf_path=None,
            params={"mode": "reference_free"},
        )

        assert result["status"] == "skipped"
        assert result["imputation_skipped"] is True
        assert result["genotype_vcf_path"] == str(tmp_path / "qc.vcf.gz")
        assert result["genotype_bfile_prefix"] == str(tmp_path / "qc_prefix")
        assert (tmp_path / "skipped.json").exists()
