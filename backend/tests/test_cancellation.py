import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus, User, UserRole
from app.tasks import _common, pipeline_orchestrator
from app.tasks._common import JobCancelledError, ensure_not_cancelled, is_job_cancelled, mark_job


def _setup_sqlite_job(tmp_path, initial_status: JobStatus):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hash",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path="/tmp/cohort.vcf.gz",
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.GWAS,
        overall_status=initial_status,
        parameters={},
    )
    db.add_all([user, dataset, job])
    db.commit()
    return engine, Session, db, job


def test_mark_job_raises_when_job_already_cancelled_and_preserves_status(tmp_path):
    _, Session, db, job = _setup_sqlite_job(tmp_path, JobStatus.CANCELLED)
    try:
        with pytest.raises(JobCancelledError):
            mark_job(db, job, overall_status=JobStatus.RUNNING_STRUCTURE)

        verify_db = Session()
        try:
            refreshed = verify_db.get(AnalysisJob, job.id)
            assert refreshed.overall_status == JobStatus.CANCELLED
        finally:
            verify_db.close()
    finally:
        db.close()


def test_mark_job_does_not_overwrite_cancelled_with_failed(tmp_path):
    _, Session, db, job = _setup_sqlite_job(tmp_path, JobStatus.CANCELLED)
    try:
        mark_job(db, job, overall_status=JobStatus.FAILED, error_message="x")

        verify_db = Session()
        try:
            refreshed = verify_db.get(AnalysisJob, job.id)
            assert refreshed.overall_status == JobStatus.CANCELLED
            assert refreshed.error_message == "x"
        finally:
            verify_db.close()
    finally:
        db.close()


def test_mark_job_updates_status_normally_when_not_cancelled(tmp_path):
    _, Session, db, job = _setup_sqlite_job(tmp_path, JobStatus.RUNNING_QC)
    try:
        mark_job(db, job, overall_status=JobStatus.RUNNING_IMPUTATION)

        verify_db = Session()
        try:
            refreshed = verify_db.get(AnalysisJob, job.id)
            assert refreshed.overall_status == JobStatus.RUNNING_IMPUTATION
        finally:
            verify_db.close()
    finally:
        db.close()


def test_ensure_not_cancelled_sees_commit_from_other_session(tmp_path):
    _, Session, session1, job = _setup_sqlite_job(tmp_path, JobStatus.RUNNING_GWAS)
    try:
        loaded_in_s1 = session1.get(AnalysisJob, job.id)
        assert loaded_in_s1.overall_status == JobStatus.RUNNING_GWAS

        session2 = Session()
        try:
            job_in_s2 = session2.get(AnalysisJob, job.id)
            job_in_s2.overall_status = JobStatus.CANCELLED
            session2.commit()
        finally:
            session2.close()

        with pytest.raises(JobCancelledError):
            ensure_not_cancelled(str(job.id), session1)
    finally:
        session1.close()


def test_ensure_not_cancelled_rejects_stale_run_id(tmp_path):
    _, _, db, job = _setup_sqlite_job(tmp_path, JobStatus.RUNNING_IMPUTATION)
    try:
        job.parameters = {"run_id": "B"}
        db.commit()

        ensure_not_cancelled(str(job.id), db, run_id="B")
        ensure_not_cancelled(str(job.id), db, run_id=None)

        with pytest.raises(JobCancelledError):
            ensure_not_cancelled(str(job.id), db, run_id="A")
    finally:
        db.close()


def test_is_job_cancelled_detects_stale_run_id(tmp_path, monkeypatch):
    _, Session, db, job = _setup_sqlite_job(tmp_path, JobStatus.RUNNING_IMPUTATION)
    job_id = str(job.id)
    try:
        job.parameters = {"run_id": "B"}
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(_common, "SessionLocal", Session)
    assert is_job_cancelled(job_id, run_id="A") is True
    assert is_job_cancelled(job_id, run_id="B") is False


def test_retry_job_updates_run_id_and_passes_to_orchestrator(monkeypatch):
    from app.api.routes import jobs

    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="hash",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=user.id,
        original_filename="cohort.vcf.gz",
        storage_path="/tmp/cohort.vcf.gz",
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.IMPUTATION,
        overall_status=JobStatus.CANCELLED,
        parameters={"run_id": "old-run-id"},
    )

    class _FakeDB:
        def __init__(self):
            self.committed_run_ids = []

        def get(self, model, object_id):
            if model is AnalysisJob and str(object_id) == str(job.id):
                return job
            if model is Dataset and str(object_id) == str(dataset.id):
                return dataset
            return None

        def commit(self):
            self.committed_run_ids.append((job.parameters or {}).get("run_id"))

    captured: dict[str, object] = {}

    def _fake_retry(**kwargs):
        captured["kwargs"] = kwargs
        captured["run_id_committed_before_trigger"] = list(fake_db.committed_run_ids)
        return "retry-celery-id", 3, "imputation"

    fake_db = _FakeDB()
    monkeypatch.setattr(pipeline_orchestrator, "trigger_retry_pipeline", _fake_retry)

    jobs.retry_job(job_id=job.id, from_scratch=False, db=fake_db, user=user)

    new_run_id = job.parameters["run_id"]
    assert new_run_id != "old-run-id"
    assert captured["kwargs"]["run_id"] == new_run_id
    assert new_run_id in captured["run_id_committed_before_trigger"]


def test_build_stage_signatures_attaches_run_id_to_all_stages():
    sigs = pipeline_orchestrator._build_stage_signatures(
        job_id=str(uuid.uuid4()),
        dataset_id=str(uuid.uuid4()),
        run_id="R",
    )
    assert len(sigs) == 10
    for sig in sigs:
        assert sig.kwargs["run_id"] == "R"


def _make_linked_async_chain(n: int = 10):
    class _FakeNode:
        def __init__(self, tid: str, parent=None):
            self.id = tid
            self.parent = parent

    head = None
    ids = []
    for idx in range(1, n + 1):
        tid = f"chain-task-{idx}"
        ids.append(tid)
        head = _FakeNode(tid, parent=head)
    return head, ids


def test_trigger_full_pipeline_stores_all_10_chain_ids_and_cancel_revokes_all(tmp_path, monkeypatch):
    from app.api.routes import jobs as jobs_routes

    _, Session, db, job = _setup_sqlite_job(tmp_path, JobStatus.PENDING)
    try:
        tail_res, expected_ids = _make_linked_async_chain(10)

        class _FakeChain:
            def __init__(self, *sigs):
                self.sigs = sigs

            def apply_async(self):
                return tail_res

        monkeypatch.setattr(pipeline_orchestrator, "SessionLocal", Session, raising=False)
        monkeypatch.setattr(pipeline_orchestrator, "chain", lambda *sigs: _FakeChain(*sigs))

        last_id = pipeline_orchestrator.trigger_full_pipeline(
            job_id=str(job.id),
            dataset_id=str(job.dataset_id),
        )
        assert last_id == expected_ids[-1]

        db.expire_all()
        reloaded = db.get(AnalysisJob, job.id)
        assert (reloaded.parameters or {}).get("celery_task_ids") == expected_ids
        assert len(reloaded.parameters["celery_task_ids"]) == 10

        reloaded.celery_task_id = last_id
        db.commit()

        revoked = []

        class _FakeControl:
            def revoke(self, task_id, terminate=False):
                revoked.append((task_id, terminate))

        monkeypatch.setattr(jobs_routes.celery_app, "control", _FakeControl(), raising=False)
        user = db.get(User, reloaded.triggered_by)
        resp = jobs_routes.cancel_job(job_id=job.id, db=db, user=user)
        assert resp["cancelled"] is True
        assert [tid for tid, _ in revoked] == expected_ids
        assert all(term is True for _, term in revoked)

        revoked.clear()
        del_resp = jobs_routes.delete_job(job_id=job.id, db=db, user=user)
        assert del_resp["deleted"] is True
        assert [tid for tid, _ in revoked] == expected_ids
    finally:
        db.close()


def test_python_multilocus_and_structure_loops_call_ensure_not_cancelled(tmp_path, monkeypatch):
    import numpy as np
    from app.services import structure_service
    from app.tasks import multilocus_tasks, structure_tasks
    from app.utils.process import CLIExecutionResult

    _, _, db, job = _setup_sqlite_job(tmp_path, JobStatus.RUNNING_MULTILOCUS)
    try:
        ws = tmp_path / "jobs" / str(job.id)
        ml_dir = ws / "multilocus"
        insp_dir = ws / "inspection"
        norm_dir = ws / "normalization"
        ml_dir.mkdir(parents=True)
        insp_dir.mkdir(parents=True)
        norm_dir.mkdir(parents=True)

        vcf_path = norm_dir / "normalized.vcf"
        vcf_path.write_text(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\tS3\tS4\tS5\n"
            "1\t100\trs1\tA\tG\t99\tPASS\t.\tGT\t0/0\t0/1\t1/1\t0/0\t0/1\n"
            "1\t200\trs2\tC\tT\t99\tPASS\t.\tGT\t0/1\t1/1\t1/1\t0/0\t0/0\n",
            encoding="utf-8",
        )
        (insp_dir / "pheno_plink.txt").write_text(
            "FID\tIID\tPHENO1\nS1\tS1\t1.0\nS2\tS2\t2.0\nS3\tS3\t3.0\nS4\tS4\t1.5\nS5\tS5\t2.5\n",
            encoding="utf-8",
        )

        ml_checks = []

        def fake_ml_ensure(jid, session=None, run_id=None):
            ml_checks.append(jid)
            if len(ml_checks) >= 2:
                raise JobCancelledError("cancelled inside multilocus")

        monkeypatch.setattr(multilocus_tasks, "ensure_not_cancelled", fake_ml_ensure)
        with pytest.raises(JobCancelledError, match="cancelled inside multilocus"):
            multilocus_tasks._run_python_multilocus_engine(
                job_id=str(job.id),
                dataset_id=str(job.dataset_id),
                methods=["FarmCPU", "BLINK"],
                workspace=ml_dir,
                log_file=ml_dir / "multilocus.log",
                ctx={"normalized_vcf_path": str(vcf_path)},
                db=db,
            )
        assert len(ml_checks) >= 2

        struct_checks = []

        def fake_struct_ensure(jid, session=None, run_id=None):
            struct_checks.append(jid)
            if len(struct_checks) >= 4:
                raise JobCancelledError("cancelled inside structure")

        def fake_run_cli(cmd, **kwargs):
            out_prefix = tmp_path / "noop"
            for i, part in enumerate(cmd):
                if part == "--out" and i + 1 < len(cmd):
                    from pathlib import Path
                    out_prefix = Path(cmd[i + 1])
                    break
            out_prefix.parent.mkdir(parents=True, exist_ok=True)
            if "--indep-pairwise" in cmd:
                out_prefix.with_suffix(".prune.in").write_text("rs1\nrs2\n", encoding="utf-8")
            elif "--make-bed" in cmd:
                for ext in (".bed", ".bim", ".fam"):
                    out_prefix.with_suffix(ext).write_bytes(b"stub")
            return CLIExecutionResult(command=tuple(cmd), returncode=0, log_file=tmp_path / "x.log")

        monkeypatch.setattr(structure_tasks, "SessionLocal", lambda: db)
        monkeypatch.setattr(structure_tasks, "which_tool", lambda name: "/usr/local/bin/plink", raising=False)
        monkeypatch.setattr(structure_tasks, "run_cli_command", fake_run_cli, raising=False)
        monkeypatch.setattr(
            structure_tasks,
            "read_plink_binary",
            lambda p: (
                [f"S{i}" for i in range(6)],
                [{"chrom": "1", "rsid": f"rs{i}", "pos": 100 * i, "a1": "A", "a2": "G"} for i in range(6)],
                np.ones((6, 6), dtype=np.int8),
            ),
        )
        monkeypatch.setattr(structure_tasks, "ensure_not_cancelled", fake_struct_ensure)
        (tmp_path / "dummy.bed").write_bytes(b"stub")
        with pytest.raises(JobCancelledError, match="cancelled inside structure"):
            structure_tasks.run_structure_analysis(
                {"genotype_bfile_prefix": str(tmp_path / "dummy")},
                job_id=str(job.id),
                dataset_id=str(job.dataset_id),
            )
        assert len(struct_checks) >= 4
        # Verify structure_service accepts cancel_check cleanly in long loop
        assert structure_service.compute_king_robust_table(np.zeros((2, 5), dtype=np.int8), ["A", "B"])
    finally:
        db.close()


def test_cancel_mid_split_leaves_no_valid_split_map_or_vcf(tmp_path, monkeypatch, overflow_dataset):
    import shutil
    from app.core.config import settings
    from app.services.chromosome_service import load_split_map
    from app.tasks import normalization_tasks
    from app.utils.process import CLICancelledError, CLIExecutionResult

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))

    _, Session, db, job = _setup_sqlite_job(tmp_path, JobStatus.RUNNING_NORMALIZATION)
    job_id_str = str(job.id)
    dataset_id_str = str(job.dataset_id)
    try:
        copied_vcf = tmp_path / "cohort_overflow.vcf.gz"
        shutil.copyfile(overflow_dataset["vcf_path"], copied_vcf)
        dataset = db.get(Dataset, job.dataset_id)
        dataset.storage_path = str(copied_vcf)
        db.commit()
    finally:
        db.close()

    norm_dir = tmp_path / "jobs" / job_id_str / "normalization"
    norm_dir.mkdir(parents=True, exist_ok=True)
    stale_map = norm_dir / "chrom_split_map.json"
    stale_map.write_text('{"stale": true}', encoding="utf-8")
    stale_norm = norm_dir / "normalized.vcf.gz"
    stale_norm.write_bytes(b"stale")

    seen_stages: list[str] = []

    def fake_run_cli(cmd, **kwargs):
        subcmd = cmd[1] if len(cmd) > 1 else ""
        seen_stages.append(subcmd)
        if subcmd == "view":
            return CLIExecutionResult(command=tuple(cmd), returncode=0, log_file=kwargs.get("log_file"))
        if subcmd == "sort":
            split_input = norm_dir / "split_input.vcf"
            assert split_input.is_file()
            assert "chr7_p2" in split_input.read_text(encoding="utf-8")
            out_path = cmd[cmd.index("-o") + 1]
            with open(out_path, "wb") as fh:
                fh.write(b"partial-sorted")
            raise CLICancelledError(cmd, kwargs.get("log_file"), ())
        return CLIExecutionResult(command=tuple(cmd), returncode=0, log_file=kwargs.get("log_file"))

    monkeypatch.setattr(normalization_tasks, "SessionLocal", Session)
    monkeypatch.setattr(_common, "SessionLocal", Session)
    monkeypatch.setattr(normalization_tasks, "which_tool", lambda name: "/usr/bin/bcftools")
    monkeypatch.setattr(normalization_tasks, "run_cli_command", fake_run_cli)

    with pytest.raises(CLICancelledError):
        normalization_tasks.run_vcf_normalization(
            None,
            job_id=job_id_str,
            dataset_id=dataset_id_str,
            params={},
        )

    assert seen_stages == ["view", "sort"]
    assert not stale_map.exists()
    assert load_split_map(job_id_str) is None
    assert not stale_norm.exists()
    assert not (norm_dir / "sorted.vcf.gz").exists()
    assert not (norm_dir / "split_input.vcf").exists()
