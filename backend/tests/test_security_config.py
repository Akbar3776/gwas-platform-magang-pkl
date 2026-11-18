from pathlib import Path
import re

import pytest

from app.core import config, security


def test_resolve_jwt_secret_rejects_placeholder_and_short_secrets_in_production():
    with pytest.raises(RuntimeError):
        security.resolve_jwt_secret("change-this-jwt-secret-key-in-production-min-32-chars", "production")

    with pytest.raises(RuntimeError):
        security.resolve_jwt_secret("short", "production")


def test_resolve_jwt_secret_accepts_strong_prod_secret_and_dev_fallback():
    strong = "a" * 64
    assert security.resolve_jwt_secret(strong, "production") == strong
    assert security.resolve_jwt_secret("", "development") == "dev-only-insecure-secret"


def test_config_no_longer_defaults_secret_key_to_literal_placeholder():
    cfg_src = Path(config.__file__).read_text(encoding="utf-8")
    assert '"change-me-in-production"' not in cfg_src


def test_prod_compose_sets_env_production_for_api_and_workers():
    compose_path = Path(__file__).resolve().parents[2] / "infra" / "docker-compose.prod.yml"
    text = compose_path.read_text(encoding="utf-8")
    for service in ("backend", "celery_worker", "flower"):
        match = re.search(rf"(?ms)^\s{{2}}{service}:\n(.*?)(?=^\s{{2}}[a-z_]+:|\Z)", text)
        assert match is not None, f"Service {service} tidak ditemukan"
        assert "ENV: production" in match.group(1), f"{service} harus menyetel ENV: production"
