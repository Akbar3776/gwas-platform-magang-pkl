import importlib.metadata
from pathlib import Path

import pytest


def test_kombu_is_pinned_compatible_with_redis_pin():
    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    pins = {}
    for raw_line in requirements.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        package, version = line.split("==", 1)
        pins[package.lower()] = version

    assert "kombu" in pins
    assert len(tuple(int(part) for part in pins["kombu"].split(".")[:3])) == 3
    assert pins["kombu"] == "5.4.2"

    redis_version = tuple(int(part) for part in pins["redis"].split(".")[:3])
    kombu_version = tuple(int(part) for part in pins["kombu"].split(".")[:3])
    if redis_version < (6, 1):
        assert kombu_version < (5, 7)


def test_installed_kombu_accepts_installed_redis():
    pytest.importorskip("kombu")
    pytest.importorskip("redis")

    kombu_version = tuple(
        int(part) for part in importlib.metadata.version("kombu").split(".")[:3]
    )
    redis_version = tuple(
        int(part) for part in importlib.metadata.version("redis").split(".")[:3]
    )
    if kombu_version >= (5, 7):
        assert redis_version >= (6, 1)
