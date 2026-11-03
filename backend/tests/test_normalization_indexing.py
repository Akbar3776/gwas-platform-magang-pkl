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
