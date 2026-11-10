from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.tasks.imputation_tasks import _beagle_command, _prepare_beagle_safe_vcf, run_imputation
from app.services.qc_service import MAX_32BIT_POS
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
    qc_file = tmp_path / "qc.vcf.gz"
    qc_file.write_text("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n", encoding="utf-8")
    ctx = {
        "status": "completed",
        "job_id": job_id,
        "qc_vcf_path": str(qc_file),
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


def test_beagle_command_supports_window_and_overlap():
    with patch("app.tasks.imputation_tasks.which_tool", return_value="custom_tool"):
        cmd, _ = _beagle_command(
            qc_vcf_path="/path/to/qc.vcf.gz",
            reference_vcf_path=None,
            output_prefix="/path/to/out",
            params={"window": 10.0, "overlap": 1.0},
        )
        assert "window=10.0" in cmd
        assert "overlap=1.0" in cmd


def test_prepare_beagle_safe_vcf_filters_high_coordinate_markers(tmp_path):
    import gzip

    vcf_file = tmp_path / "qc.vcf.gz"
    with gzip.open(vcf_file, "wt", encoding="utf-8") as gz:
        gz.write(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
            f"7\t1000\trs1\tA\tG\t.\tPASS\t.\tGT\t0/1\n"
            f"7\t{MAX_32BIT_POS}\trs_boundary\tC\tT\t.\tPASS\t.\tGT\t0/0\n"
            f"7\t{MAX_32BIT_POS + 1000}\trs_danger\tG\tA\t.\tPASS\t.\tGT\t1/1\n"
        )

    safe_path, removed, singles_count = _prepare_beagle_safe_vcf(
        str(vcf_file), tmp_path, max_pos=MAX_32BIT_POS
    )
    assert removed == 1
    assert singles_count == 0
    assert safe_path != str(vcf_file)
    assert Path(safe_path).is_file()

    with gzip.open(safe_path, "rt", encoding="utf-8") as gz_in:
        content = gz_in.read()
        assert "rs1" in content
        assert "rs_boundary" in content
        assert "rs_danger" not in content


def test_prepare_beagle_safe_vcf_leaves_safe_vcf_intact(tmp_path):
    import gzip

    vcf_file = tmp_path / "already_safe.vcf.gz"
    with gzip.open(vcf_file, "wt", encoding="utf-8") as gz:
        gz.write(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
            "1\t1000\trs1\tA\tG\t.\tPASS\t.\tGT\t0/1\n"
            "1\t2000\trs1b\tA\tG\t.\tPASS\t.\tGT\t0/1\n"
            "2\t5000\trs2\tC\tT\t.\tPASS\t.\tGT\t0/0\n"
            "2\t6000\trs2b\tC\tT\t.\tPASS\t.\tGT\t0/0\n"
        )

    safe_path, removed, singles_count = _prepare_beagle_safe_vcf(
        str(vcf_file), tmp_path, max_pos=MAX_32BIT_POS
    )
    assert removed == 0
    assert singles_count == 0
    assert safe_path == str(vcf_file)
    assert not (tmp_path / "beagle_input_safe.vcf.gz").exists()


def test_prepare_beagle_safe_vcf_isolates_single_marker_scaffolds(tmp_path):
    import gzip

    vcf_file = tmp_path / "scaffolds.vcf.gz"
    with gzip.open(vcf_file, "wt", encoding="utf-8") as gz:
        gz.write(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
            "1\t1000\trs_chr1_a\tA\tG\t.\tPASS\t.\tGT\t0/1\n"
            "1\t2000\trs_chr1_b\tT\tC\t.\tPASS\t.\tGT\t1/0\n"
            "scaffold10019\t27645\tscaffold10019:27645\tC\tT\t.\tPASS\t.\tGT\t0/0\n"
        )

    safe_path, removed, singles_count = _prepare_beagle_safe_vcf(
        str(vcf_file), tmp_path, max_pos=MAX_32BIT_POS
    )
    assert removed == 0
    assert singles_count == 1
    assert safe_path != str(vcf_file)

    with gzip.open(safe_path, "rt", encoding="utf-8") as f_safe:
        safe_content = f_safe.read()
        assert "rs_chr1_a" in safe_content
        assert "rs_chr1_b" in safe_content
        assert "scaffold10019" not in safe_content


def test_imputation_graceful_single_marker_fallback(tmp_path):
    job_id = "test-job-single-marker"
    qc_file = tmp_path / "qc.vcf.gz"
    qc_file.write_text("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n", encoding="utf-8")
    ctx = {
        "status": "completed",
        "job_id": job_id,
        "qc_vcf_path": str(qc_file),
        "qc_bfile_prefix": str(tmp_path / "qc_prefix"),
    }

    mock_db = MagicMock()
    mock_job = MagicMock()
    mock_job.work_dir = str(tmp_path)
    mock_job.parameters = {"imputation": {"mode": "reference_free"}}
    mock_db.get.return_value = mock_job

    single_marker_error = SubprocessFailed(
        cmd=["java", "-jar", "beagle.jar"],
        returncode=1,
        log_file=tmp_path / "beagle.log",
        last_lines=(
            "Window 379 [scaffold10019:27645-27645] Study markers: 1\n",
            "Exception in thread \"main\" java.lang.IllegalArgumentException: Window has only one position: CHROM=scaffold10019 POS=27645\n",
            "    at vcf.MarkerMap.meanSingleBaseGenDist(MarkerMap.java:98)\n",
            "    at phase.FixedPhaseData.markerMap(FixedPhaseData.java:170)\n",
        ),
    )

    with (
        patch("app.tasks.imputation_tasks.SessionLocal", return_value=mock_db),
        patch("app.tasks.imputation_tasks.stage_dir", return_value=tmp_path),
        patch("app.tasks.imputation_tasks.which_tool", return_value="/bin/tool"),
        patch("pathlib.Path.is_file", return_value=True),
        patch("app.tasks.imputation_tasks.run_cli_command", side_effect=single_marker_error),
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
        assert (tmp_path / "skipped.json").exists()


def test_imputation_success_with_single_marker_isolation(tmp_path):
    import gzip
    import uuid

    job_id = str(uuid.uuid4())
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create QC VCF with chr1 (2 markers) and scaffold10019 (1 marker)
    qc_file = workspace / "qc.vcf.gz"
    with gzip.open(qc_file, "wt", encoding="utf-8") as gz:
        gz.write(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
            "1\t1000\trs_chr1_a\tA\tG\t.\tPASS\t.\tGT\t0/1\n"
            "1\t2000\trs_chr1_b\tT\tC\t.\tPASS\t.\tGT\t1/0\n"
            "scaffold10019\t27645\tscaffold10019:27645\tC\tT\t.\tPASS\t.\tGT\t0/0\n"
        )

    ctx = {
        "status": "completed",
        "job_id": job_id,
        "qc_vcf_path": str(qc_file),
        "qc_bfile_prefix": str(workspace / "qc_prefix"),
    }

    mock_db = MagicMock()
    mock_job = MagicMock()
    mock_job.work_dir = str(workspace)
    mock_job.parameters = {"imputation": {"mode": "reference_free"}}
    mock_db.get.return_value = mock_job

    def fake_run_cli(cmd, **kwargs):
        # When Beagle is invoked, simulate creating pure output_prefix.vcf.gz
        if any("beagle" in str(arg).lower() for arg in cmd):
            out_prefix = next(arg.split("out=")[1] for arg in cmd if str(arg).startswith("out="))
            out_vcf = Path(f"{out_prefix}.vcf.gz")
            with gzip.open(out_vcf, "wt", encoding="utf-8") as gz:
                gz.write(
                    "##fileformat=VCFv4.2\n"
                    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                    "1\t1000\trs_chr1_a\tA\tG\t.\tPASS\t.\tGT\t0|1\n"
                    "1\t2000\trs_chr1_b\tT\tC\t.\tPASS\t.\tGT\t1|0\n"
                )

    with (
        patch("app.tasks.imputation_tasks.SessionLocal", return_value=mock_db),
        patch("app.tasks.imputation_tasks.stage_dir", return_value=workspace),
        patch("app.tasks.imputation_tasks.which_tool") as mock_tool,
        patch("app.tasks.imputation_tasks.run_cli_command", side_effect=fake_run_cli),
    ):
        mock_tool.side_effect = lambda t: {
            "java": "/custom/bin/java",
            "beagle_jar": str(workspace / "beagle.jar"),
            "plink2": None,
            "plink": None,
        }.get(t)
        (workspace / "beagle.jar").touch()

        result = run_imputation(
            ctx,
            job_id=job_id,
            reference_vcf_path=None,
            params={"mode": "reference_free"},
        )

        assert result["status"] == "completed"
        assert result["imputation_skipped"] is False
        output_vcf = Path(result["output_vcf"])
        assert output_vcf.is_file()

        with gzip.open(output_vcf, "rt", encoding="utf-8") as gz_in:
            all_content = gz_in.read()
            assert "rs_chr1_a" in all_content
            assert "rs_chr1_b" in all_content
            assert "scaffold10019" not in all_content

