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
    struct_dir = job_dir / "structure"
    workspace = job_dir / "multilocus"
    qc_dir.mkdir(parents=True)
    inspect_dir.mkdir(parents=True)
    struct_dir.mkdir(parents=True)
    workspace.mkdir(parents=True)
    log_file = workspace / "multilocus.log"

    pheno_lines = ["FID\tIID\tTRAIT"]
    for sid, y_val in zip(samples, y_vals):
        pheno_lines.append(f"{sid}\t{sid}\t{y_val:.6f}")
    (inspect_dir / "pheno_plink.txt").write_text("\n".join(pheno_lines) + "\n", encoding="utf-8")

    # Shuffled PCA eigenvec order -> gapit_CV.txt must reorder cleanly to match gapit_GD.txt
    pc_matrix = rng.normal(0.0, 1.0, size=(n_samples, 3))
    shuffled_indices = rng.permutation(n_samples)
    eigen_lines = []
    for idx in shuffled_indices:
        sid = samples[int(idx)]
        pcs_txt = "\t".join(f"{float(v):.6f}" for v in pc_matrix[int(idx)])
        eigen_lines.append(f"{sid}\t{sid}\t{pcs_txt}")
    (struct_dir / "pca_results.eigenvec").write_text("\n".join(eigen_lines) + "\n", encoding="utf-8")

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
        n_pcs=3,
    )

    cv_lines = [ln.split("\t") for ln in (workspace / "gapit_CV.txt").read_text(encoding="utf-8").splitlines()]
    gd_lines = [ln.split("\t") for ln in (workspace / "gapit_GD.txt").read_text(encoding="utf-8").splitlines()]
    assert cv_lines[0] == ["Taxa", "PC1", "PC2", "PC3"]
    assert [row[0] for row in cv_lines[1:]] == [row[0] for row in gd_lines[1:]]

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
