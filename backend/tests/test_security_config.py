from pathlib import Path
import re

from pydantic import ValidationError
import pytest

from app.core import config, security


def test_security_uses_pyjwt_directly_without_python_jose():
    sec_src = Path(security.__file__).read_text(encoding="utf-8")
    assert "from jose import" not in sec_src
    assert "PyJWTError" in sec_src


def test_requirements_all_pinned_and_addition_file_removed():
    backend_dir = Path(__file__).resolve().parents[1]
    reqs = [
        line.strip()
        for line in (backend_dir / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert all("==" in line for line in reqs), f"Ada paket belum di-pin ==: {[x for x in reqs if '==' not in x]}"
    joined = "\n".join(reqs)
    assert "python-jose" not in joined
    assert "PyJWT==" in joined
    assert "weasyprint==" in joined
    assert not (backend_dir / "app" / "requirements-addition.txt").exists()
    dockerfile_src = (backend_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements-addition.txt" not in dockerfile_src


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


def test_workflow_settings_defaults(monkeypatch):
    for key in (
        "CHROM_SPLIT_THRESHOLD_BP",
        "CHROM_SPLIT_UNIT_BP",
        "CANDIDATE_REGION_MAX_TOTAL_BP",
        "KASP_FLANK_BP",
        "NON_PRIMARY_CONTIG_REGEX",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = config.Settings()
    assert cfg.CHROM_SPLIT_THRESHOLD_BP == 2_147_483_647
    assert cfg.CHROM_SPLIT_UNIT_BP == 1_000_000_000
    assert cfg.CANDIDATE_REGION_MAX_TOTAL_BP == 50_000_000
    assert cfg.KASP_FLANK_BP == 150
    assert cfg.NON_PRIMARY_CONTIG_REGEX == config.DEFAULT_NON_PRIMARY_CONTIG_REGEX


@pytest.mark.parametrize(
    "overrides",
    [
        {"CHROM_SPLIT_THRESHOLD_BP": 1000, "CHROM_SPLIT_UNIT_BP": 2000},
        {"CHROM_SPLIT_THRESHOLD_BP": 0, "CHROM_SPLIT_UNIT_BP": 0},
        {"CHROM_SPLIT_UNIT_BP": 0},
        {"CANDIDATE_REGION_MAX_TOTAL_BP": 0},
        {"KASP_FLANK_BP": 59},
    ],
)
def test_workflow_settings_validation_rejects_invalid_values(overrides):
    with pytest.raises(ValueError):
        config.Settings(**overrides)


def test_non_primary_contig_regex_invalid_raises_validation_error():
    with pytest.raises(ValidationError, match="NON_PRIMARY_CONTIG_REGEX"):
        config.Settings(NON_PRIMARY_CONTIG_REGEX="[invalid")

    cfg_empty = config.Settings(NON_PRIMARY_CONTIG_REGEX="")
    assert cfg_empty.NON_PRIMARY_CONTIG_REGEX == ""


def test_env_example_files_share_same_keys_except_compose_only():
    repo_root = Path(__file__).resolve().parents[2]
    root_text = (repo_root / ".env.example").read_text(encoding="utf-8")
    backend_text = (repo_root / "backend" / ".env.example").read_text(encoding="utf-8")
    root_keys = set(re.findall(r"(?m)^([A-Z0-9_]+)=", root_text))
    backend_keys = set(re.findall(r"(?m)^([A-Z0-9_]+)=", backend_text))
    assert root_keys - backend_keys == {"POSTGRES_HOST_PORT", "TLS_CERT_DIR", "ACME_WEBROOT"}
    assert backend_keys - root_keys == set()


def test_go_upload_limit_settings_defaults_and_validation(monkeypatch):
    for key in (
        "GO_MAPPING_MAX_UPLOAD_BYTES",
        "GO_MAPPING_MAX_DECOMPRESSED_BYTES",
        "GO_MAPPING_MAX_LINES",
        "GO_MAPPING_MIN_VALID_RATIO",
        "UPLOAD_TEXT_MAX_LINE_BYTES",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = config.Settings()
    assert cfg.GO_MAPPING_MAX_UPLOAD_BYTES == 52_428_800
    assert cfg.GO_MAPPING_MAX_DECOMPRESSED_BYTES == 134_217_728
    assert cfg.GO_MAPPING_MAX_LINES == 1_000_000
    assert cfg.GO_MAPPING_MIN_VALID_RATIO == 0.5
    assert cfg.UPLOAD_TEXT_MAX_LINE_BYTES == 65_536

    for bad_overrides in (
        {"GO_MAPPING_MAX_UPLOAD_BYTES": 0},
        {"GO_MAPPING_MAX_DECOMPRESSED_BYTES": -1},
        {"GO_MAPPING_MAX_LINES": 0},
        {"UPLOAD_TEXT_MAX_LINE_BYTES": 0},
        {"GO_MAPPING_MIN_VALID_RATIO": 0.0},
        {"GO_MAPPING_MIN_VALID_RATIO": 1.1},
        {"GO_OBO_MAX_BYTES": 0},
        {"GO_OBO_MAX_LINES": 0},
    ):
        with pytest.raises(ValueError):
            config.Settings(**bad_overrides)


def test_ortholog_settings_defaults_and_validation(monkeypatch):
    for key in (
        "ORTHOLOG_MAX_UPLOAD_BYTES",
        "ORTHOLOG_MAX_DECOMPRESSED_BYTES",
        "ORTHOLOG_MAX_LINES",
        "ORTHOLOG_MAX_QUERIES",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = config.Settings()
    assert cfg.ORTHOLOG_MAX_UPLOAD_BYTES == 99_614_720
    assert cfg.ORTHOLOG_MAX_DECOMPRESSED_BYTES == 536_870_912
    assert cfg.ORTHOLOG_MAX_LINES == 10_000_000
    assert cfg.ORTHOLOG_MAX_QUERIES == 500_000

    for bad_overrides in (
        {"ORTHOLOG_MAX_UPLOAD_BYTES": 0},
        {"ORTHOLOG_MAX_DECOMPRESSED_BYTES": -1},
        {"ORTHOLOG_MAX_LINES": 0},
        {"ORTHOLOG_MAX_QUERIES": 0},
    ):
        with pytest.raises(ValueError):
            config.Settings(**bad_overrides)
