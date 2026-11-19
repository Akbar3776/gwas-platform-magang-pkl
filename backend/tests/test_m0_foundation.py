import tempfile
from pathlib import Path
import numpy as np

from app.services.manhattan_parser import compute_assoc_summary_stats
from app.tasks._common import (
    append_log,
    read_json,
    which_tool,
    write_json,
)
from tests.fixtures.make_fixtures import ensure_fixtures


def test_lambda_gc_unclamped_under_uniform_pvalues():
    """Verify that lambda_gc under null hypothesis (uniform p-values) evaluates to ~1.0 without clamp."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        assoc_path = Path(tmp_dir) / "test.assoc.txt"
        rng = np.random.default_rng(123)
        # 20,000 uniform p-values in (0, 1)
        p_vals = rng.uniform(1e-5, 0.99999, size=20_000)
        lines = ["chr\trs\tps\tn_miss\ta1\ta0\taf\tbeta\tse\tp_wald"]
        for idx, p in enumerate(p_vals):
            lines.append(f"1\trs{idx}\t{idx * 100}\t0\tA\tG\t0.3\t0.05\t0.02\t{p:.6e}")
        assoc_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        summary = compute_assoc_summary_stats(str(assoc_path))
        assert summary["total_snps"] == 20_000
        # For uniform distribution, median is ~0.5, chi2 is ~0.4549, lambda_gc should be around 1.0 (within 0.05)
        assert 0.95 <= summary["lambda_gc"] <= 1.05


def test_common_task_helpers():
    """Verify _common helpers for logging, staging, JSON I/O, and tool resolution."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_file = Path(tmp_dir) / "test.log"
        append_log(log_file, "Test message 1")
        append_log(log_file, "Test message 2", with_timestamp=True)
        content = log_file.read_text(encoding="utf-8")
        assert "Test message 1" in content
        assert "Test message 2" in content

        json_file = Path(tmp_dir) / "data.json"
        data = {"hello": "world", "numbers": [1, 2, 3]}
        write_json(json_file, data)
        assert read_json(json_file) == data

    # which_tool resolution
    assert which_tool("NON_EXISTENT_TOOL_ENV", "definitely_not_a_tool_xyz_123") is None


def test_fixtures_generation():
    """Verify deterministic fixture generation."""
    vcf, pheno, gff = ensure_fixtures()
    assert vcf.is_file()
    assert pheno.is_file()
    assert gff.is_file()
    assert vcf.stat().st_size > 1000
    assert pheno.stat().st_size > 500
    assert gff.stat().st_size > 300


def test_capabilities_uses_which_tool_tool_names_and_multilocus_availability(monkeypatch):
    from app.api.routes import capabilities as cap_routes

    fake_tools = {
        "plink2": "/usr/local/bin/plink2",
        "plink": "/usr/local/bin/plink",
        "gemma": "/usr/local/bin/gemma",
        "bcftools": "/usr/bin/bcftools",
        "Rscript": None,
        "beagle_jar": None,
        "java": None,
    }
    monkeypatch.setattr(cap_routes, "which_tool", lambda name, *a, **kw: fake_tools.get(name))
    monkeypatch.delenv("ALLOW_PYTHON_MULTILOCUS", raising=False)

    caps = cap_routes.get_pipeline_capabilities()
    assert caps["plink2"] is True
    assert caps["plink"] is True
    assert caps["gemma"] is True
    assert caps["bcftools"] is True
    assert caps["rscript"] is False
    assert caps["beagle"] is False
    assert caps["multilocus_available"] is True

    monkeypatch.setenv("ALLOW_PYTHON_MULTILOCUS", "false")
    caps_no_py = cap_routes.get_pipeline_capabilities()
    assert caps_no_py["multilocus_available"] is False

    fake_tools["Rscript"] = "/usr/bin/Rscript"
    caps_with_r = cap_routes.get_pipeline_capabilities()
    assert caps_with_r["rscript"] is True
    assert caps_with_r["multilocus_available"] is True
