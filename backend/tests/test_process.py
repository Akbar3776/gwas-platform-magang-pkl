import sys
import time
from pathlib import Path

import pytest

from app.utils import process


class FakeStream:
    def __init__(self, lines):
        self._lines = iter(lines)

    def readline(self):
        try:
            return next(self._lines)
        except StopIteration:
            return ""

    def close(self):
        pass


class FakeProcess:
    def __init__(self, returncode):
        self.stdout = FakeStream(["tool started\n", "last diagnostic\n"])
        self.returncode = returncode
        self.pid = 1234

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def test_run_cli_command_streams_output_and_raises_on_nonzero(monkeypatch, tmp_path: Path):
    fake_process = FakeProcess(returncode=2)
    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: fake_process)

    log_path = tmp_path / "plink.log"
    with pytest.raises(process.SubprocessFailed) as raised:
        process.run_cli_command(
            ["plink", "--vcf", "input.vcf.gz"],
            cwd=tmp_path,
            log_file=log_path,
            timeout=5,
        )

    assert raised.value.returncode == 2
    assert "last diagnostic" in str(raised.value)
    assert "tool started" in log_path.read_text(encoding="utf-8")


def test_run_cli_command_calls_line_callback(monkeypatch, tmp_path: Path):
    fake_process = FakeProcess(returncode=0)
    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: fake_process)
    received = []

    result = process.run_cli_command(
        ["bcftools", "view", "-h", "input.vcf.gz"],
        cwd=tmp_path,
        log_file=tmp_path / "bcftools.log",
        on_line=received.append,
    )

    assert result.returncode == 0
    assert received == ["tool started", "last diagnostic"]


def test_run_cli_command_terminates_when_cancelled(tmp_path: Path):
    log_path = tmp_path / "x.log"
    started = time.monotonic()
    with pytest.raises(process.CLICancelledError):
        process.run_cli_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            log_file=log_path,
            cancel_check=lambda: True,
            cancel_poll_seconds=0.2,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 10.0
    assert "[CANCELLED]" in log_path.read_text(encoding="utf-8")
