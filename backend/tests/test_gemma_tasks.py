from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.tasks.gemma_tasks import _bfile_prefix


def test_bfile_prefix_uses_max_alleles_with_plink2(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    log_file = work_dir / "test.log"
    vcf_path = tmp_path / "test.vcf.gz"
    vcf_path.touch()

    recorded_cmd = []

    def mock_run_cli(cmd, **kwargs):
        recorded_cmd.extend(cmd)
        # Create output files
        (work_dir / "genotypes.bed").touch()
        (work_dir / "genotypes.bim").touch()
        (work_dir / "genotypes.fam").touch()

    with (
        patch("app.tasks.gemma_tasks.PLINK_BINARY", "plink2"),
        patch("app.tasks.gemma_tasks.run_cli_command", side_effect=mock_run_cli),
    ):
        prefix = _bfile_prefix(str(vcf_path), work_dir, log_file)
        assert prefix == str(work_dir / "genotypes")
        assert "--max-alleles" in recorded_cmd
        assert "2" in recorded_cmd
        assert "--biallelic-only" not in recorded_cmd


def test_bfile_prefix_uses_biallelic_only_with_plink1(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    log_file = work_dir / "test.log"
    vcf_path = tmp_path / "test.vcf.gz"
    vcf_path.touch()

    recorded_cmd = []

    def mock_run_cli(cmd, **kwargs):
        recorded_cmd.extend(cmd)
        (work_dir / "genotypes.bed").touch()
        (work_dir / "genotypes.bim").touch()
        (work_dir / "genotypes.fam").touch()

    with (
        patch("app.tasks.gemma_tasks.PLINK_BINARY", "plink"),
        patch("app.tasks.gemma_tasks.run_cli_command", side_effect=mock_run_cli),
    ):
        prefix = _bfile_prefix(str(vcf_path), work_dir, log_file)
        assert prefix == str(work_dir / "genotypes")
        assert "--biallelic-only" in recorded_cmd
        assert "--max-alleles" not in recorded_cmd


def test_run_gemma_reuses_candidate_bfile_from_imputation(tmp_path):
    from app.tasks.gemma_tasks import run_gemma_gwas

    job_id = "test-job-gemma-bfile"
    work_dir = tmp_path / "gwas_work"
    work_dir.mkdir()
    imputation_dir = tmp_path / "imputation"
    imputation_dir.mkdir()

    # Create dummy imputed.bed, .bim, .fam
    candidate_prefix = imputation_dir / "imputed"
    (imputation_dir / "imputed.bed").touch()
    (imputation_dir / "imputed.bim").touch()
    (imputation_dir / "imputed.fam").write_text("FID1 IID1 0 0 0 1.5\nFID2 IID2 0 0 0 2.5\n", encoding="utf-8")

    pheno_file = tmp_path / "pheno.txt"
    pheno_file.write_text("IID\ttrait\nIID1\t10.0\nIID2\t20.0\n", encoding="utf-8")

    imputation_result = {
        "status": "completed",
        "imputation_skipped": False,  # Imputation succeeded!
        "genotype_bfile_prefix": str(candidate_prefix),
        "output_vcf": str(imputation_dir / "imputed.vcf.gz"),
    }

    mock_db = MagicMock()
    mock_job = MagicMock()
    mock_job.work_dir = str(work_dir)
    mock_db.get.return_value = mock_job

    dummy_gemma = tmp_path / "dummy_gemma"
    dummy_gemma.touch()
    dummy_plink = tmp_path / "dummy_plink2"
    dummy_plink.touch()

    # Create dummy gemma output
    output_dir = work_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "association.assoc.txt").write_text(
        "chr\trs\tps\tn_miss\tallele1\tallele0\taf\tbeta\tse\tl_remle\tp_wald\n"
        "1\trs1\t1000\t0\tA\tG\t0.5\t0.1\t0.02\t0\t0.0001\n",
        encoding="utf-8",
    )
    (output_dir / "kinship.cXX.txt").write_text("1.0 0.0\n0.0 1.0\n", encoding="utf-8")

    with (
        patch("app.tasks.gemma_tasks.SessionLocal", return_value=mock_db),
        patch("app.tasks.gemma_tasks._mark_job"),
        patch("app.tasks.gemma_tasks._upsert_result"),
        patch("app.tasks.gemma_tasks.GEMMA_BINARY", str(dummy_gemma)),
        patch("app.tasks.gemma_tasks.PLINK_BINARY", str(dummy_plink)),
        patch("app.tasks.gemma_tasks.run_cli_command") as mock_cli,
    ):
        result = run_gemma_gwas(
            imputation_result,
            job_id=job_id,
            phenotype_path=str(pheno_file),
            model="glm",
            params={"output_dir": str(work_dir)},
        )

        assert result["status"] == "completed"
        # Verify copied candidate bfile was used instead of converting VCF
        assert (work_dir / "genotypes.bed").is_file()
        assert (work_dir / "genotypes.fam").is_file()
        # Verify run_cli_command was called for GEMMA, NOT for plink --vcf conversion
        for call_args in mock_cli.call_args_list:
            cmd = call_args[0][0]
            assert "--vcf" not in cmd

