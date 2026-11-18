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
    assert not cache_path.with_name("manhattan_chr_labels.json").exists()


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

    # Overwriting same cache path with purely numeric data removes stale labels file
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
    assert not cache_path.with_name("manhattan_chr_labels.json").exists()
