import os

import pytest

from app.tasks import _common as task_common
from app.tasks._common import which_tool


def test_posix_skips_windows_wrappers(tmp_path, monkeypatch):
    monkeypatch.setattr(task_common, "_IS_WINDOWS", False)
    for filename in ("x.cmd", "x.bat", "x.exe"):
        candidate = tmp_path / filename
        candidate.write_text("stub", encoding="utf-8")
        assert task_common._is_usable_local_tool(candidate) is False

    jar_file = tmp_path / "beagle.jar"
    jar_file.write_text("jar", encoding="utf-8")
    assert task_common._is_usable_local_tool(jar_file) is True


def test_windows_accepts_wrappers(tmp_path, monkeypatch):
    monkeypatch.setattr(task_common, "_IS_WINDOWS", True)
    cmd_file = tmp_path / "x.cmd"
    cmd_file.write_text("@echo off\n", encoding="utf-8")
    assert task_common._is_usable_local_tool(cmd_file) is True
    assert task_common._is_usable_local_tool(tmp_path / "missing.cmd") is False


@pytest.mark.skipif(os.name == "nt", reason="bit executable POSIX tidak berlaku di Windows")
def test_posix_suffixless_requires_exec_bit(tmp_path, monkeypatch):
    monkeypatch.setattr(task_common, "_IS_WINDOWS", False)
    tool = tmp_path / "tool"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o644)
    assert task_common._is_usable_local_tool(tool) is False
    tool.chmod(0o755)
    assert task_common._is_usable_local_tool(tool) is True


def test_which_tool_bcftools_falls_back_to_path_on_posix(monkeypatch):
    monkeypatch.setattr(task_common, "_IS_WINDOWS", False)
    for key in ("bcftools", "BCFTOOLS", "BCFTOOLS_BINARY_PATH", "BCFTOOLS_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        task_common.shutil,
        "which",
        lambda name: "/usr/bin/bcftools" if name == "bcftools" else None,
    )
    assert which_tool("bcftools") == "/usr/bin/bcftools"


def test_which_tool_plink2_ignores_plink_binary_path(monkeypatch):
    monkeypatch.setattr(task_common, "_IS_WINDOWS", False)
    for key in ("plink2", "PLINK2", "PLINK2_BINARY_PATH", "plink", "PLINK", "PLINK_BINARY_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PLINK_BINARY_PATH", "plink")
    monkeypatch.setattr(
        task_common.shutil,
        "which",
        lambda name: "/usr/local/bin/plink" if name == "plink" else None,
    )
    assert which_tool("plink2") is None
    assert which_tool("plink") == "/usr/local/bin/plink"


def test_which_tool_plink2_accepts_plink2_env_vars(monkeypatch):
    monkeypatch.setattr(task_common, "_IS_WINDOWS", False)
    for key in ("plink2", "PLINK2", "PLINK2_BINARY_PATH", "PLINK_BINARY_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PLINK2", "plink2")
    monkeypatch.setattr(
        task_common.shutil,
        "which",
        lambda name: "/usr/local/bin/plink2" if name == "plink2" else None,
    )
    assert which_tool("plink2") == "/usr/local/bin/plink2"
