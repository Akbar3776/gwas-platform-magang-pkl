import tempfile
from pathlib import Path
import pandas as pd

from app.services.phenotype_service import (
    inspect_phenotype,
    load_phenotype_table,
    read_vcf_sample_ids,
)
from tests.fixtures.make_fixtures import ensure_fixtures


def test_phenotype_table_loading_and_delimiters():
    with tempfile.TemporaryDirectory() as tmp_dir:
        # 1. Tab separated
        tsv_path = Path(tmp_dir) / "test.tsv"
        tsv_path.write_text("FID\tIID\tTRAIT\nFAM1\tIND1\t10.5\nFAM2\tIND2\t12.0\n", encoding="utf-8")
        df_tsv = load_phenotype_table(tsv_path)
        assert list(df_tsv.columns) == ["FID", "IID", "TRAIT"]
        assert len(df_tsv) == 2
        assert df_tsv["IID"].tolist() == ["IND1", "IND2"]

        # 2. Comma separated
        csv_path = Path(tmp_dir) / "test.csv"
        csv_path.write_text("FID,IID,TRAIT\nFAM1,IND1,10.5\nFAM2,IND2,12.0\n", encoding="utf-8")
        df_csv = load_phenotype_table(csv_path)
        assert list(df_csv.columns) == ["FID", "IID", "TRAIT"]
        assert len(df_csv) == 2

        # 3. Space separated
        space_path = Path(tmp_dir) / "test.txt"
        space_path.write_text("FID  IID   TRAIT\nFAM1 IND1  10.5\nFAM2 IND2  12.0\n", encoding="utf-8")
        df_space = load_phenotype_table(space_path)
        assert list(df_space.columns) == ["FID", "IID", "TRAIT"]
        assert len(df_space) == 2


def test_inspect_phenotype_with_fixtures():
    vcf_path, pheno_path, _ = ensure_fixtures()
    vcf_ids = read_vcf_sample_ids(vcf_path)
    assert len(vcf_ids) == 120
    assert "SAMPLE_001" in vcf_ids

    df = load_phenotype_table(pheno_path)
    report = inspect_phenotype(
        vcf_ids=vcf_ids,
        df=df,
        trait="TRAIT1",
        id_column="IID",
    )

    # Acceptance criteria verification:
    # 120 VCF samples total
    assert report["n_vcf_samples"] == 120
    # Exactly 3 IDs that only exist in phenotype (EXTRA_001, EXTRA_002, EXTRA_003)
    assert report["only_in_pheno_count"] == 3
    # Exactly 1 duplicate IID (SAMPLE_001 repeated)
    assert len(report["pheno_duplicate_ids"]) == 1
    # Missing count (5 'NA' + 2 '-9' = 7)
    assert report["missing_count"] == 7
    # Exactly 1 non-numeric value ('INVALID_VALUE')
    assert report["non_numeric_count"] == 1
    assert any(item["value"] == "INVALID_VALUE" for item in report["non_numeric_examples"])

    # Matched samples should be around 120 - 7 missing - 1 invalid = 112
    assert report["n_matched"] == 112
    assert len(report["matched_ids"]) == 112

    # Stats, histogram and boxplot checks
    assert report["stats"]["N"] == 112
    assert report["stats"]["mean"] > 0
    assert len(report["histogram"]["edges"]) > 5
    assert len(report["histogram"]["counts"]) == len(report["histogram"]["edges"]) - 1
    assert "q1" in report["boxplot"]
    assert "median" in report["boxplot"]
    assert "q3" in report["boxplot"]


def test_inspect_phenotype_blocking_errors():
    vcf_ids = ["SAMPLE1", "SAMPLE2", "SAMPLE3"]
    df = pd.DataFrame({
        "IID": ["SAMPLE1", "SAMPLE2", "SAMPLE3"],
        "TRAIT_WRONG": ["1.0", "2.0", "3.0"],
    })

    # Missing trait column -> blocking error
    res = inspect_phenotype(vcf_ids, df, trait="TRAIT_NONEXISTENT", id_column="IID")
    assert len(res["blocking_errors"]) > 0
    assert any("TRAIT_NONEXISTENT" in err for err in res["blocking_errors"])

    # Too few matched samples (< 3)
    df_few = pd.DataFrame({
        "IID": ["SAMPLE1", "OTHER1"],
        "TRAIT": ["10.0", "20.0"],
    })
    res_few = inspect_phenotype(vcf_ids, df_few, trait="TRAIT", id_column="IID")
    assert len(res_few["blocking_errors"]) > 0
    assert any("< 3" in err for err in res_few["blocking_errors"])


def test_run_inspection_keeps_first_valid_value_for_duplicate_iid(tmp_path, monkeypatch):
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.config import settings
    from app.db.base import Base
    from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus, User, UserRole
    from app.tasks import _common as task_common, gemma_tasks, inspection_tasks

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    engine = create_engine(f"sqlite:///{tmp_path / 'a22.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(inspection_tasks, "SessionLocal", Session)
    monkeypatch.setattr(task_common, "SessionLocal", Session)

    vcf_path = tmp_path / "small.vcf"
    vcf_path.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\tS3\tS4\n"
        "1\t100\trs1\tA\tG\t99\tPASS\t.\tGT\t0/0\t0/1\t1/1\t0/1\n",
        encoding="utf-8",
    )
    pheno_path = tmp_path / "pheno.tsv"
    pheno_path.write_text(
        "FID\tIID\tTRAIT1\n"
        "S1\tS1\t10\n"
        "S2\tS2\t12\n"
        "S3\tS3\t15\n"
        "S4\tS4\tNA\n"
        "S1\tS1\t20\n"
        "S4\tS4\t30\n",
        encoding="utf-8",
    )

    db = Session()
    try:
        user = User(id=uuid.uuid4(), email="a22@test.local", hashed_password="x", role=UserRole.RESEARCHER)
        ds = Dataset(
            id=uuid.uuid4(),
            owner_id=user.id,
            original_filename="small.vcf",
            storage_path=str(vcf_path),
            phenotype_file_path=str(pheno_path),
            upload_status=UploadStatus.UPLOADED,
        )
        job = AnalysisJob(
            id=uuid.uuid4(),
            dataset_id=ds.id,
            triggered_by=user.id,
            pipeline_type="full_pipeline",
            current_step=PipelineStep.INSPECTION,
            overall_status=JobStatus.PENDING,
            parameters={},
        )
        db.add_all([user, ds, job])
        db.commit()
        job_id = str(job.id)
        ds_id = str(ds.id)
    finally:
        db.close()

    res = inspection_tasks.run_inspection(
        job_id=job_id,
        dataset_id=ds_id,
        phenotype_cfg={"trait": "TRAIT1", "id_column": "IID", "fid_column": "FID"},
    )
    pheno_lines = Path(res["pheno_path"]).read_text(encoding="utf-8").splitlines()[1:]
    s1_lines = [ln for ln in pheno_lines if ln.startswith("S1\t")]
    s4_lines = [ln for ln in pheno_lines if ln.startswith("S4\t")]
    assert len(s1_lines) == 1
    assert s1_lines[0].split("\t")[-1] == "10"
    assert len(s4_lines) == 1
    assert s4_lines[0].split("\t")[-1] == "30"

    gemma_ws = tmp_path / "jobs" / job_id / "gwas"
    gemma_ws.mkdir(parents=True, exist_ok=True)
    vec, _ = gemma_tasks._prepare_phenotype_vector(
        ["S1", "S2", "S3", "S4"],
        [],
        res["pheno_path"],
        gemma_ws,
        gemma_ws / "gemma.log",
    )
    assert vec.tolist() == [10.0, 12.0, 15.0, 30.0]


def test_run_inspection_populates_fase0_chromosome_summary_and_api_returns_it(
    tmp_path,
    monkeypatch,
    overflow_dataset,
):
    import json
    import uuid
    from pathlib import Path
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.api.routes.results import get_inspection_data
    from app.core.config import settings
    from app.db.base import Base
    from app.db.engine.models import (
        AnalysisJob,
        Dataset,
        JobStatus,
        PipelineStep,
        UploadStatus,
        User,
        UserRole,
    )
    from app.tasks import _common as task_common, inspection_tasks

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    engine = create_engine(f"sqlite:///{tmp_path / 'w10.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(inspection_tasks, "SessionLocal", Session)
    monkeypatch.setattr(task_common, "SessionLocal", Session)

    db = Session()
    try:
        user = User(
            id=uuid.uuid4(),
            email="w10@test.local",
            hashed_password="x",
            role=UserRole.RESEARCHER,
        )
        ds = Dataset(
            id=uuid.uuid4(),
            owner_id=user.id,
            original_filename="overflow.vcf.gz",
            storage_path=str(overflow_dataset["vcf_path"]),
            phenotype_file_path=str(overflow_dataset["pheno_path"]),
            upload_status=UploadStatus.UPLOADED,
        )
        job = AnalysisJob(
            id=uuid.uuid4(),
            dataset_id=ds.id,
            triggered_by=user.id,
            pipeline_type="full_pipeline",
            current_step=PipelineStep.INSPECTION,
            overall_status=JobStatus.PENDING,
            parameters={},
        )
        db.add_all([user, ds, job])
        db.commit()
        job_id = str(job.id)
        ds_id = str(ds.id)
    finally:
        db.close()

    res = inspection_tasks.run_inspection(
        job_id=job_id,
        dataset_id=ds_id,
        phenotype_cfg={"trait": "TRAIT1", "id_column": "IID", "fid_column": "FID"},
    )

    summary_tsv = Path(res["chromosome_summary_path"])
    assert summary_tsv.is_file()
    assert summary_tsv.name == "chromosome_characteristics_summary.txt"
    tsv_text = summary_tsv.read_text(encoding="utf-8")
    assert "chr7\t11\t0\t6500\t5\t1" in tsv_text
    assert "scaffold10019\t1\t200\t200\t0\t0" in tsv_text
    assert "scaffold10098\t3\t300\t900\t0\t0" in tsv_text

    stage_summary_file = tmp_path / "jobs" / job_id / "inspection" / "stage_summary.json"
    assert stage_summary_file.is_file()
    stage_payload = json.loads(stage_summary_file.read_text(encoding="utf-8"))
    assert stage_payload["overflow_chromosomes"] == ["chr7"]
    assert stage_payload["total_overflow"] == 5
    assert stage_payload["total_invalid_pos"] == 1
    assert stage_payload["needs_split"] is True
    assert stage_payload["counts"]["non_primary_contigs"] == 2
    assert stage_payload["counts"]["non_primary_variants"] == 4

    inspection_log = (tmp_path / "jobs" / job_id / "inspection" / "inspection.log").read_text(encoding="utf-8")
    assert "[Fase 0]" in inspection_log

    verify_db = Session()
    try:
        api_payload = get_inspection_data(uuid.UUID(job_id), db=verify_db)
        assert api_payload["chromosome_summary"]["overflow_chromosomes"] == ["chr7"]
        assert api_payload["chromosome_summary"]["total_invalid_pos"] == 1
        assert [row["chrom"] for row in api_payload["chromosome_summary"]["chromosomes"]] == ["chr1", "chr7"]
        assert api_payload["chromosome_summary"]["excluded_contigs"]["contigs"] == 2
        assert api_payload["chromosome_summary"]["excluded_contigs"]["variants"] == 4
        assert api_payload["blocking_errors"] == []
        assert any("INVALID_POS" in w for w in api_payload["warnings"])
        db_job = verify_db.get(AnalysisJob, uuid.UUID(job_id))
        assert db_job.parameters["stage_summaries"]["inspection"]["overflow_chromosomes"] == ["chr7"]
    finally:
        verify_db.close()
