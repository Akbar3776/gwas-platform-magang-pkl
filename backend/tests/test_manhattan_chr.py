import json
import uuid

from app.api.routes import results as results_routes
from app.db.engine.models import AnalysisJob, JobStatus
from app.services import manhattan_parser


def test_generate_manhattan_cache_preserves_distinct_indices_for_non_numeric_chromosomes(tmp_path):
    assoc_path = tmp_path / "gwas_results.assoc.txt"
    assoc_path.write_text(
        "chr\trs\tps\tp_wald\n"
        "Gm01\tsnp1\t1000\t1e-6\n"
        "Gm02\tsnp2\t2000\t1e-8\n"
        "scaffold_1\tsnp3\t500\t1e-4\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "manhattan_cache.parquet"

    manhattan_parser.generate_manhattan_cache(
        assoc_file_path=str(assoc_path),
        output_cache_path=str(cache_path),
    )

    df = manhattan_parser.load_manhattan_cache(str(cache_path))
    by_snp = dict(zip(df["SNP"], df["CHR"]))
    assert len({by_snp["snp1"], by_snp["snp2"], by_snp["snp3"]}) == 3
    assert by_snp["snp2"] > by_snp["snp1"]

    labels_path = cache_path.with_name("manhattan_chr_labels.json")
    assert labels_path.is_file()
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    assert labels[str(by_snp["snp1"])] == "Gm01"
    assert labels[str(by_snp["snp2"])] == "Gm02"
    assert labels[str(by_snp["snp3"])] == "scaffold_1"

    # API response includes chr_labels and preserves distinct integer indices
    job_id = uuid.uuid4()
    job = AnalysisJob(
        id=job_id,
        dataset_id=uuid.uuid4(),
        triggered_by=uuid.uuid4(),
        pipeline_type="full_pipeline",
        overall_status=JobStatus.COMPLETED,
        parameters={"manhattan_cache_path": str(cache_path)},
    )

    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return job

    class _FakeDB:
        def query(self, model):
            return _FakeQuery()

    resp = results_routes.get_manhattan_data(job_id=job_id, db=_FakeDB())
    assert resp.chr == df["CHR"].tolist()
    assert resp.chr_labels == {
        int(by_snp["snp1"]): "Gm01",
        int(by_snp["snp2"]): "Gm02",
        int(by_snp["snp3"]): "scaffold_1",
    }


def test_generate_manhattan_cache_keeps_numeric_and_sex_chr_behavior(tmp_path):
    assoc_path = tmp_path / "numeric.assoc.txt"
    assoc_path.write_text(
        "chr\trs\tps\tp_wald\n"
        "1\ts1\t100\t1e-5\n"
        "2\ts2\t200\t1e-6\n"
        "X\ts3\t300\t1e-7\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "manhattan_cache.parquet"

    manhattan_parser.generate_manhattan_cache(
        assoc_file_path=str(assoc_path),
        output_cache_path=str(cache_path),
    )

    df = manhattan_parser.load_manhattan_cache(str(cache_path))
    by_snp = dict(zip(df["SNP"], df["CHR"]))
    assert by_snp == {"s1": 1, "s2": 2, "s3": 23}
    labels_path = cache_path.with_name("manhattan_chr_labels.json")
    assert labels_path.is_file()
    assert json.loads(labels_path.read_text(encoding="utf-8")) == {}
    assert manhattan_parser.load_manhattan_chr_labels(str(cache_path)) is None


def test_generate_manhattan_cache_same_prefix_and_stale_labels_cleanup(tmp_path):
    assoc_prefix = tmp_path / "soybean.assoc.txt"
    assoc_prefix.write_text(
        "chr\trs\tps\tp_wald\n"
        "Gm01\ts1\t100\t1e-6\n"
        "Gm02\ts2\t200\t1e-7\n"
        "Gm10\ts10\t300\t1e-8\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "manhattan_cache.parquet"

    manhattan_parser.generate_manhattan_cache(
        assoc_file_path=str(assoc_prefix),
        output_cache_path=str(cache_path),
    )

    df = manhattan_parser.load_manhattan_cache(str(cache_path))
    assert dict(zip(df["SNP"], df["CHR"])) == {"s1": 1, "s2": 2, "s10": 10}
    assert manhattan_parser.load_manhattan_chr_labels(str(cache_path)) == {
        1: "Gm01",
        2: "Gm02",
        10: "Gm10",
    }

    # Overwriting same cache path with purely numeric data resets labels file to {}
    assoc_num = tmp_path / "human.assoc.txt"
    assoc_num.write_text(
        "chr\trs\tps\tp_wald\n1\ts1\t100\t1e-5\n2\ts2\t200\t1e-6\n",
        encoding="utf-8",
    )
    manhattan_parser.generate_manhattan_cache(
        assoc_file_path=str(assoc_num),
        output_cache_path=str(cache_path),
    )
    assert manhattan_parser.load_manhattan_chr_labels(str(cache_path)) is None
    assert json.loads(cache_path.with_name("manhattan_chr_labels.json").read_text(encoding="utf-8")) == {}


def test_get_manhattan_data_rebuilds_legacy_cache_missing_labels_file_once(tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd

    assoc_path = tmp_path / "legacy.assoc.txt"
    assoc_path.write_text(
        "chr\trs\tps\tp_wald\n"
        "Gm01\tsnp1\t1000\t1e-6\n"
        "Gm02\tsnp2\t2000\t1e-8\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "manhattan_cache.parquet"
    # Simulasikan cache lama sebelum T21: semua kromosom menumpuk di Chr 1 dan tanpa file label
    legacy_df = pd.DataFrame(
        {
            "CHR": np.array([1, 1], dtype="uint32"),
            "BP": np.array([1000, 2000], dtype="uint32"),
            "SNP": ["snp1", "snp2"],
            "LOG10_P": np.array([6.0, 8.0], dtype="float32"),
        }
    )
    manhattan_parser._save_cache_df(legacy_df, str(cache_path))
    labels_path = cache_path.with_name("manhattan_chr_labels.json")
    assert not labels_path.exists()

    job_id = uuid.uuid4()
    orig_params = {"manhattan_cache_path": str(cache_path)}
    job = AnalysisJob(
        id=job_id,
        dataset_id=uuid.uuid4(),
        triggered_by=uuid.uuid4(),
        pipeline_type="full_pipeline",
        overall_status=JobStatus.COMPLETED,
        assoc_output_path=str(assoc_path),
        parameters=dict(orig_params),
    )

    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return job

    class _FakeDB:
        def __init__(self):
            self.commits = 0

        def query(self, model):
            return _FakeQuery()

        def commit(self):
            self.commits += 1

    db = _FakeDB()
    resp1 = results_routes.get_manhattan_data(job_id=job_id, db=db)
    assert labels_path.is_file()
    assert resp1.chr == [1, 2]
    assert resp1.chr_labels == {1: "Gm01", 2: "Gm02"}
    assert job.parameters == orig_params
    assert db.commits == 0

    calls = []
    monkeypatch.setattr(
        results_routes,
        "generate_manhattan_cache",
        lambda **kw: (calls.append(kw), {"output_path": str(cache_path)})[1],
    )
    resp2 = results_routes.get_manhattan_data(job_id=job_id, db=db)
    assert calls == []
    assert resp2.chr == [1, 2]
    assert resp2.chr_labels == {1: "Gm01", 2: "Gm02"}


def test_get_manhattan_data_uses_legacy_cache_when_assoc_missing(tmp_path):
    assoc_path = tmp_path / "temp.assoc.txt"
    assoc_path.write_text("chr\trs\tps\tp_wald\n1\ts1\t100\t1e-5\n", encoding="utf-8")
    cache_path = tmp_path / "manhattan_cache.parquet"
    manhattan_parser.generate_manhattan_cache(
        assoc_file_path=str(assoc_path),
        output_cache_path=str(cache_path),
    )
    cache_path.with_name("manhattan_chr_labels.json").unlink()
    assoc_path.unlink()

    job_id = uuid.uuid4()
    job = AnalysisJob(
        id=job_id,
        dataset_id=uuid.uuid4(),
        triggered_by=uuid.uuid4(),
        pipeline_type="full_pipeline",
        overall_status=JobStatus.COMPLETED,
        assoc_output_path=str(assoc_path),
        parameters={"manhattan_cache_path": str(cache_path)},
    )

    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return job

    class _FakeDB:
        def query(self, model):
            return _FakeQuery()

    resp = results_routes.get_manhattan_data(job_id=job_id, db=_FakeDB())
    assert resp.chr == [1]
    assert resp.chr_labels is None


def test_get_manhattan_data_supports_multilocus_models_and_per_model_cache(tmp_path, monkeypatch):
    from fastapi import HTTPException
    import pytest
    from app.core.config import settings

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))

    job_id = uuid.uuid4()
    job_root = tmp_path / "jobs" / str(job_id)
    vis_dir = job_root / "visualization"
    ml_dir = job_root / "multilocus"
    vis_dir.mkdir(parents=True)
    ml_dir.mkdir(parents=True)

    primary_assoc = job_root / "gwas.assoc.txt"
    primary_assoc.write_text(
        "chr\trs\tps\tp_wald\n1\tgemma_snp0\t100\t1e-6\n",
        encoding="utf-8",
    )
    primary_cache = vis_dir / "manhattan_cache.parquet"
    manhattan_parser.generate_manhattan_cache(str(primary_assoc), str(primary_cache))

    (ml_dir / "farmcpu_results.tsv").write_text(
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn\n"
        "Gm01\t1500\tfarmcpu_snp1\tA\tG\t0.3\t0.5\t0.1\t1e-9\t50\n",
        encoding="utf-8",
    )
    (ml_dir / "blink_results.tsv").write_text(
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn\n"
        "Gm02\t2500\tblink_snp2\tC\tT\t0.2\t-0.4\t0.1\t1e-7\t50\n",
        encoding="utf-8",
    )

    job = AnalysisJob(
        id=job_id,
        dataset_id=uuid.uuid4(),
        triggered_by=uuid.uuid4(),
        pipeline_type="full_pipeline",
        overall_status=JobStatus.COMPLETED,
        assoc_output_path=str(primary_assoc),
        parameters={"manhattan_cache_path": str(primary_cache)},
    )

    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return job

    class _FakeDB:
        def query(self, model):
            return _FakeQuery()

        def commit(self):
            pass

    db = _FakeDB()

    farmcpu_resp = results_routes.get_manhattan_data(job_id=job_id, model="FARMCPU", db=db)
    assert farmcpu_resp.snp == ["farmcpu_snp1"]
    assert farmcpu_resp.model == "FARMCPU"
    assert farmcpu_resp.chr_labels == {1: "Gm01"}
    assert (vis_dir / "multilocus" / "farmcpu" / "manhattan_cache.parquet").is_file()

    blink_resp = results_routes.get_manhattan_data(job_id=job_id, model="blink", db=db)
    assert blink_resp.snp == ["blink_snp2"]
    assert blink_resp.model == "BLINK"
    assert blink_resp.chr_labels == {2: "Gm02"}

    # Primary GEMMA cache and chr_labels must remain intact
    gemma_resp = results_routes.get_manhattan_data(job_id=job_id, db=db)
    assert gemma_resp.snp == ["gemma_snp0"]
    assert gemma_resp.chr_labels is None

    with pytest.raises(HTTPException) as missing_exc:
        results_routes.get_manhattan_data(job_id=job_id, model="MLMM", db=db)
    assert missing_exc.value.status_code == 404
