import shutil
import subprocess
from unittest.mock import patch

import pytest

from app.core.resource_limits import read_container_memory_limit_mb, resolve_beagle_memory_flags
from app.tasks.imputation_tasks import _beagle_command


def test_default_memory_flags_use_container_percentage():
    flags, warning = resolve_beagle_memory_flags(None, 4096)

    assert flags == [
        "-XX:MaxRAMPercentage=75.0",
        "-XX:InitialRAMPercentage=10.0",
        "-XX:+ExitOnOutOfMemoryError",
    ]
    assert warning is None
    assert not any(flag.startswith("-Xmx") for flag in flags)


def test_explicit_memory_flags_are_not_clamped_below_limit():
    flags, warning = resolve_beagle_memory_flags("-Xmx2g", 4096)

    assert flags == ["-Xmx2048m", "-Xms1024m", "-XX:+ExitOnOutOfMemoryError"]
    assert warning is None


def test_explicit_memory_flags_are_clamped_to_container_limit():
    flags, warning = resolve_beagle_memory_flags("-Xmx8g", 4096)

    assert flags == ["-Xmx3072m", "-Xms1024m", "-XX:+ExitOnOutOfMemoryError"]
    assert warning is not None
    assert "-Xmx8g" in warning
    assert "4096" in warning


def test_explicit_memory_flags_without_detected_limit_are_unclamped():
    flags, warning = resolve_beagle_memory_flags("-Xmx8g", None)

    assert flags == ["-Xmx8192m", "-Xms1024m", "-XX:+ExitOnOutOfMemoryError"]
    assert warning is None


def test_invalid_memory_flags_raise_value_error():
    with pytest.raises(ValueError):
        resolve_beagle_memory_flags("8192m", None)


def test_reads_cgroup_v2_memory_limit(tmp_path):
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    (cgroup_root / "memory.max").write_text("4294967296", encoding="utf-8")

    assert read_container_memory_limit_mb(cgroup_root) == 4096


def test_reads_cgroup_v1_memory_limit(tmp_path):
    cgroup_root = tmp_path / "cgroup"
    memory_dir = cgroup_root / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "memory.limit_in_bytes").write_text("4294967296", encoding="utf-8")

    assert read_container_memory_limit_mb(cgroup_root) == 4096


@pytest.mark.parametrize("value", ["max", str(2**60)])
def test_ignores_unbounded_cgroup_limit(tmp_path, value):
    (tmp_path / "memory.max").write_text(value, encoding="utf-8")

    assert read_container_memory_limit_mb(tmp_path) is None


def test_missing_cgroup_limit_returns_none(tmp_path):
    assert read_container_memory_limit_mb(tmp_path) is None


def test_beagle_command_returns_list_without_rlimit():
    with (
        patch("app.tasks.imputation_tasks.which_tool", side_effect=lambda name: {
            "java": "/custom/bin/java",
            "beagle_jar": "/custom/beagle.jar",
        }.get(name)),
        patch("app.tasks.imputation_tasks.read_container_memory_limit_mb", return_value=4096),
        patch("app.tasks.imputation_tasks.BEAGLE_MAX_MEMORY", ""),
    ):
        command = _beagle_command("/input.vcf.gz", None, "/output", {})

    assert isinstance(command, list)
    assert command[:4] == [
        "/custom/bin/java",
        "-XX:MaxRAMPercentage=75.0",
        "-XX:InitialRAMPercentage=10.0",
        "-XX:+ExitOnOutOfMemoryError",
    ]
    assert not any("RLIMIT" in item or "preexec" in item for item in command)


def test_jvm_accepts_default_and_clamped_flags():
    if shutil.which("java") is None:
        pytest.skip("java tidak tersedia di host")

    default_flags, _ = resolve_beagle_memory_flags(None, 4096)
    clamped_flags, _ = resolve_beagle_memory_flags("-Xmx8g", 4096)
    for flags in (default_flags, clamped_flags):
        result = subprocess.run(["java", *flags, "-version"], capture_output=True, timeout=60)
        assert result.returncode == 0
