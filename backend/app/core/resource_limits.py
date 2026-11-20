"""Resolve Beagle JVM memory flags from the worker container limit.

RLIMIT_AS is intentionally not used for the Beagle JVM. A/B/C testing showed
that limiting virtual address space made the JVM fail native allocation and
even prevented startup, while the same heap ran normally without RLIMIT_AS.
"""

import re
from pathlib import Path


HEAP_FRACTION_OF_CONTAINER_LIMIT = 0.75
_MEMORY_FLAG_PATTERN = re.compile(r"-Xmx(\d+)([mMgG])")


def read_container_memory_limit_mb(cgroup_root: Path = Path("/sys/fs/cgroup")) -> int | None:
    """Return the finite cgroup memory limit in MB, rounded down."""
    for limit_path in (
        cgroup_root / "memory.max",
        cgroup_root / "memory" / "memory.limit_in_bytes",
    ):
        try:
            value = limit_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value == "max":
            continue
        try:
            limit_bytes = int(value)
        except ValueError:
            continue
        if limit_bytes >= 2**60:
            continue
        return limit_bytes // (1024 * 1024)
    return None


def resolve_beagle_memory_flags(
    explicit: str | None,
    container_limit_mb: int | None,
) -> tuple[list[str], str | None]:
    """Return JVM memory flags and an optional warning for an explicit override."""
    if not explicit or not explicit.strip():
        return [
            "-XX:MaxRAMPercentage=75.0",
            "-XX:InitialRAMPercentage=10.0",
            "-XX:+ExitOnOutOfMemoryError",
        ], None

    value = explicit.strip()
    match = _MEMORY_FLAG_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("BEAGLE_MAX_MEMORY harus berbentuk -Xmx512m atau -Xmx8g")

    heap_mb = int(match.group(1)) * (1024 if match.group(2).lower() == "g" else 1)
    warning = None
    if container_limit_mb is not None:
        max_heap_mb = int(container_limit_mb * HEAP_FRACTION_OF_CONTAINER_LIMIT)
        if heap_mb > max_heap_mb:
            heap_mb = max_heap_mb
            warning = (
                f"[WARN] BEAGLE_MAX_MEMORY {value} melebihi 75% limit container "
                f"{container_limit_mb} MB; dipakai -Xmx{heap_mb}m"
            )

    return [
        f"-Xmx{heap_mb}m",
        f"-Xms{min(heap_mb, 1024)}m",
        "-XX:+ExitOnOutOfMemoryError",
    ], warning
