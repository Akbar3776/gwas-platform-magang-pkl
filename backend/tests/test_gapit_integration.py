import csv
from pathlib import Path
import subprocess

import numpy as np
import pytest

from app.tasks._common import which_tool
from app.tasks.multilocus_tasks import _run_gapit_models


def _has_gapit_runtime() -> bool:
    rscript = which_tool("Rscript")
    if not rscript:
        return False
    try:
        proc = subprocess.run(
            [rscript, "-e", "quit(status = if (requireNamespace('GAPIT', quietly = TRUE)) 0L else 1L)"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _has_gapit_runtime(), reason="Rscript/GAPIT tidak tersedia")
def test_gapit_runs_all_multilocus_models_end_to_end(tmp_path, monkeypatch):
    rng = np.random.default_rng(42)
    n_samples = 80
    n_snps = 400
    samples = [f"S{i + 1:03d}" for i in range(n_samples)]
    g_mat = rng.integers(0, 3, size=(n_samples, n_snps), dtype=np.int8)
    variants = [
        {
            "chrom": "Gm01" if j < n_snps // 2 else "Gm02",
            "rsid": f"rs{j + 1}",
            "pos": ((j % (n_snps // 2)) + 1) * 1000,
            "a1": "A",
            "a2": "G",
        }
        for j in range(n_snps)
    ]
    y_vals = 2.0 * g_mat[:, 50].astype(float) + rng.normal(0.0, 0.5, size=n_samples)

    job_dir = tmp_path / "job"
    qc_dir = job_dir / "qc"
    inspect_dir = job_dir / "inspection"
    workspace = job_dir / "multilocus"
    qc_dir.mkdir(parents=True)
    inspect_dir.mkdir(parents=True)
    workspace.mkdir(parents=True)
    log_file = workspace / "multilocus.log"

    pheno_lines = ["FID\tIID\tTRAIT"]
    for sid, y_val in zip(samples, y_vals):
        pheno_lines.append(f"{sid}\t{sid}\t{y_val:.6f}")
    (inspect_dir / "pheno_plink.txt").write_text("\n".join(pheno_lines) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.services.structure_service.read_plink_binary",
        lambda prefix: (samples, variants, g_mat),
    )

    rscript = which_tool("Rscript")
    assert rscript is not None
    runner_script = Path(__file__).resolve().parents[1] / "app" / "templates" / "gapit_runner.R"
    assert runner_script.is_file()

    results = _run_gapit_models(
        rscript=rscript,
        runner_script=runner_script,
        methods=["FarmCPU", "BLINK", "MLMM"],
        workspace=workspace,
        log_file=log_file,
    )

    expected_chr_by_snp = {v["rsid"]: v["chrom"] for v in variants}
    assert set(results.keys()) == {"FARMCPU", "BLINK", "MLMM"}
    for key in ("FARMCPU", "BLINK", "MLMM"):
        assert results[key]["engine"] == "gapit"
        tsv_path = Path(results[key]["tsv_file"])
        assert tsv_path.is_file()
        with tsv_path.open("r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        assert len(rows) == n_snps
        assert {r["chr"] for r in rows} == {"Gm01", "Gm02"}
        assert {r["rs"]: r["chr"] for r in rows} == expected_chr_by_snp
