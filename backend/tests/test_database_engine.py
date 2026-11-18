import asyncio
import importlib.util
import json
from types import SimpleNamespace

from app import main
from app.core.config import settings
from app.db.engine import database


def test_build_engine_no_silent_sqlite_fallback_when_disabled(monkeypatch):
    monkeypatch.setenv("ALLOW_SQLITE_FALLBACK", "false")
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+psycopg2://u:p@localhost:1/db")

    if importlib.util.find_spec("psycopg2") is not None:
        engine = database._build_engine()
        try:
            assert engine.dialect.name == "postgresql"
        finally:
            engine.dispose()
        return

    captured: dict[str, str] = {}

    def _capture_create_engine(url, **kwargs):
        captured["url"] = url
        dialect_name = "sqlite" if str(url).startswith("sqlite") else "postgresql"
        return SimpleNamespace(dialect=SimpleNamespace(name=dialect_name), dispose=lambda: None)

    monkeypatch.setattr(database, "create_engine", _capture_create_engine)
    engine = database._build_engine()
    assert engine.dialect.name == "postgresql"
    assert captured["url"].startswith("postgresql")


def test_build_engine_uses_sqlite_when_fallback_enabled(monkeypatch):
    monkeypatch.setenv("ALLOW_SQLITE_FALLBACK", "true")
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+psycopg2://u:p@localhost:1/db")

    engine = database._build_engine()
    try:
        assert engine.dialect.name == "sqlite"
    finally:
        engine.dispose()


def test_health_check_checks_db_and_returns_503_when_unavailable(monkeypatch):
    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("DB down")

    monkeypatch.setattr(main, "engine", _BrokenEngine())
    response = asyncio.run(main.health_check())
    assert getattr(response, "status_code", 200) == 503
    assert json.loads(response.body) == {"status": "db_unavailable"}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, stmt):
            return 1

    class _OkEngine:
        def connect(self):
            return _Conn()

    monkeypatch.setattr(main, "engine", _OkEngine())
    ok_res = asyncio.run(main.health_check())
    assert ok_res == {"status": "ok"}
