import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.api.routes.export import _collect_job_export_files, export_job_results
from app.api.routes.jobs import (
    cancel_job,
    delete_job,
    get_job_file_content,
    get_job_results_summary,
    get_job_status,
    list_job_files,
)
from app.api.routes.pipeline import start_pipeline_endpoint
from app.api.routes.report import download_summary_report
from app.api.routes.results import get_manhattan_data
from app.api.routes.upload import delete_uploaded_dataset
from app.core.config import settings
from app.db.engine.models import (
    AnalysisJob,
    Dataset,
    JobStatus,
    PipelineStep,
    Result,
    UploadStatus,
    User,
    UserRole,
)
from app.schemas.pipeline import FullPipelineRequest, GWASPipelineConfig, ImputationPipelineConfig, QCPipelineConfig
from app.services.export_service import generate_zip_stream
from app.tasks import (
    annotation_tasks,
    gemma_tasks,
    imputation_tasks,
    inspection_tasks,
    multilocus_tasks,
    normalization_tasks,
    postgwas_tasks,
    qc_tasks,
    structure_tasks,
    visualization_tasks,
)


class InMemoryDB:
    """Lightweight in-memory SQLAlchemy session substitute for fast end-to-end pipeline verification."""

    def __init__(self):
        self.users = {}
        self.datasets = {}
        self.jobs = {}
        self.results = []

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if isinstance(obj, User):
            self.users[str(obj.id)] = obj
        elif isinstance(obj, Dataset):
            self.datasets[str(obj.id)] = obj
        elif isinstance(obj, AnalysisJob):
            if obj.created_at is None:
                obj.created_at = datetime.now(timezone.utc)
            self.jobs[str(obj.id)] = obj
        elif isinstance(obj, Result):
            self.results.append(obj)

    def get(self, model, obj_id):
        key = str(obj_id)
        if model is User:
            return self.users.get(key)
        if model is Dataset:
            return self.datasets.get(key)
        if model is AnalysisJob:
            return self.jobs.get(key)
        return None

    def query(self, model):
        db = self

        class _Query:
            def __init__(self):
                if model is AnalysisJob:
                    self._items = list(db.jobs.values())
                elif model is Result:
                    self._items = list(db.results)
                elif model is Dataset:
                    self._items = list(db.datasets.values())
                elif model is User:
                    self._items = list(db.users.values())
                else:
                    self._items = []

            def filter(self, *predicates):
                return self

            def first(self):
                return self._items[0] if self._items else None

            def all(self):
                return list(self._items)

        return _Query()

    def commit(self):
        pass

    def rollback(self):
        pass

    def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    def close(self):
        pass


def test_full_gwas_pipeline_and_results_end_to_end(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))

    db = InMemoryDB()
    monkeypatch.setattr(inspection_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(normalization_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(qc_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(imputation_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(structure_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(gemma_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(multilocus_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(postgwas_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(visualization_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(annotation_tasks, "SessionLocal", lambda: db)

    user = User(
        id=uuid.uuid4(),
        email="researcher@example.com",
        hashed_password="hashed",
        role=UserRole.RESEARCHER,
        is_active=True,
        refresh_token_version=0,
    )
    db.add(user)

    dataset_id = uuid.uuid4()
    dataset_dir = tmp_path / "datasets" / str(dataset_id)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    uploaded_vcf = dataset_dir / "sampelkecil.vcf"
    uploaded_vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=1>\n"
        "##contig=<ID=2>\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPEL_1\tSAMPEL_2\tSAMPEL_3\tSAMPEL_4\n"
        "1\t1000\trs1\tA\tG\t50\tPASS\t.\tGT\t0/0\t0/1\t1/1\t0/1\n"
        "1\t2000\trs2\tC\tT\t50\tPASS\t.\tGT\t1/1\t0/1\t0/0\t0/1\n"
        "1\t3000\trs3\tG\tA\t50\tPASS\t.\tGT\t0/1\t1/1\t0/0\t1/1\n"
        "2\t1500\trs4\tT\tC\t50\tPASS\t.\tGT\t0/0\t0/0\t0/1\t1/1\n",
        encoding="utf-8",
    )

    pheno_file = dataset_dir / "phenotype.txt"
    pheno_file.write_text(
        "FID\tIID\tPHENO1\n"
        "SAMPEL_1\tSAMPEL_1\t10.2\n"
        "SAMPEL_2\tSAMPEL_2\t14.5\n"
        "SAMPEL_3\tSAMPEL_3\t19.8\n"
        "SAMPEL_4\tSAMPEL_4\t15.1\n",
        encoding="utf-8",
    )

    dataset = Dataset(
        id=dataset_id,
        owner_id=user.id,
        original_filename="sampelkecil.vcf",
        storage_path=str(uploaded_vcf),
        phenotype_file_path=str(pheno_file),
        file_size_bytes=uploaded_vcf.stat().st_size,
        upload_status=UploadStatus.UPLOADED,
    )
    db.add(dataset)

    def _run_chain_sync(**kwargs):
        job_id = kwargs["job_id"]
        ds_id = kwargs["dataset_id"]

        # Tahap 1: Inspeksi Data
        insp_out = inspection_tasks.run_inspection(
            job_id=job_id,
            dataset_id=ds_id,
            phenotype_cfg=kwargs.get("phenotype_params") or {},
        )
        assert db.get(AnalysisJob, job_id).overall_status == JobStatus.RUNNING_NORMALIZATION

        # Tahap 2: Normalisasi VCF
        norm_out = normalization_tasks.run_vcf_normalization(insp_out, job_id=job_id, dataset_id=ds_id)
        assert db.get(AnalysisJob, job_id).overall_status == JobStatus.RUNNING_QC

        # Tahap 3: Quality Control
        qc_out = qc_tasks.run_qc_pipeline(norm_out, job_id=job_id, dataset_id=ds_id, qc_params=kwargs["qc_params"])
        assert db.get(AnalysisJob, job_id).overall_status == JobStatus.RUNNING_IMPUTATION

        # Tahap 4: Imputasi
        imp_out = imputation_tasks.run_imputation(
            qc_out,
            job_id=job_id,
            reference_vcf_path=kwargs["imputation_params"].get("reference_vcf_path") or "",
            params=kwargs["imputation_params"],
        )
        assert db.get(AnalysisJob, job_id).overall_status == JobStatus.RUNNING_STRUCTURE

        # Tahap 5: Struktur Populasi & LD Decay
        struct_out = structure_tasks.run_structure_analysis(
            imp_out,
            job_id=job_id,
            dataset_id=ds_id,
            structure_params=kwargs.get("structure_params") or {},
        )
        assert db.get(AnalysisJob, job_id).overall_status == JobStatus.RUNNING_GWAS

        # Tahap 6: Analisis Asosiasi GWAS (GEMMA / GLM)
        gwas_out = gemma_tasks.run_gemma_gwas(
            struct_out,
            job_id=job_id,
            phenotype_path=kwargs["gwas_params"].get("phenotype_path") or "",
            model=kwargs["gwas_params"].get("model", "lmm"),
            params=kwargs["gwas_params"],
        )
        assert db.get(AnalysisJob, job_id).overall_status == JobStatus.RUNNING_MULTILOCUS

        # Tahap 7: Multi-Locus GWAS
        ml_out = multilocus_tasks.run_multilocus_gwas(
            gwas_out,
            job_id=job_id,
            dataset_id=ds_id,
            params=kwargs.get("multilocus_params") or {},
        )
        assert db.get(AnalysisJob, job_id).overall_status == JobStatus.RUNNING_POSTGWAS

        # Tahap 8: Pasca-GWAS & Clumping
        post_out = postgwas_tasks.run_postgwas_analysis(
            ml_out,
            job_id=job_id,
            dataset_id=ds_id,
            threshold_params=kwargs.get("threshold_params") or {},
            clump_params=kwargs.get("clump_params") or {},
        )
        assert db.get(AnalysisJob, job_id).overall_status == JobStatus.RUNNING_PARSING

        # Tahap 9: Visualisasi & Plot Cache
        vis_out = visualization_tasks.run_visualization(
            post_out,
            job_id=job_id,
            dataset_id=ds_id,
        )
        assert db.get(AnalysisJob, job_id).overall_status == JobStatus.RUNNING_ANNOTATION

        # Tahap 10: Anotasi Gen Kandidat GFF3
        annotation_tasks.run_annotation(
            vis_out,
            job_id=job_id,
            dataset_id=ds_id,
            annotation_params=kwargs.get("annotation_params") or {},
        )
        assert db.get(AnalysisJob, job_id).overall_status == JobStatus.COMPLETED
        return "sync-task-id"

    monkeypatch.setattr("app.api.routes.pipeline.trigger_full_pipeline", _run_chain_sync)

    payload = FullPipelineRequest(
        qc=QCPipelineConfig(maf=0.01, geno=0.05, mind=0.1, hwe=1e-6),
        imputation=ImputationPipelineConfig(
            reference_vcf_path="/data/reference-panels/1000g_phase3_grch38/reference.vcf.gz",
            nthreads=2,
            beagle_max_memory="-Xmx4g",
        ),
        gwas=GWASPipelineConfig(phenotype_path=None, model="lmm"),
    )

    start_resp = start_pipeline_endpoint(dataset_id=dataset.id, payload=payload, db=db, user=user)
    job_uuid = uuid.UUID(start_resp.job_id)

    status_resp = get_job_status(job_id=job_uuid, db=db, user=user)
    assert status_resp.overall_status == "completed"
    assert status_resp.current_step == PipelineStep.ANNOTATION.value
    assert len(status_resp.step_logs) == 10
    assert all(entry.status in {"done", "skipped"} and entry.line_count > 0 for entry in status_resp.step_logs)

    summary_resp = get_job_results_summary(job_id=job_uuid, db=db, user=user)
    assert summary_resp["total_snps"] > 0
    assert summary_resp["significant_snps"] >= 0
    assert isinstance(summary_resp["lambda_gc"], float)

    manhattan_resp = get_manhattan_data(job_id=str(job_uuid), db=db)
    assert manhattan_resp.total_points > 0
    assert len(manhattan_resp.chr) == manhattan_resp.total_points
    assert len(manhattan_resp.log10_p) == manhattan_resp.total_points

    job_obj = db.get(AnalysisJob, job_uuid)
    export_files = _collect_job_export_files(job_obj, db.results)
    assert len(export_files) >= 3
    zip_bytes = b"".join(generate_zip_stream(export_files))
    assert zip_bytes.startswith(b"PK")

    zip_resp = export_job_results(job_id=str(job_uuid), db=db, user=user)
    assert zip_resp.media_type == "application/zip"

    pdf_resp = download_summary_report(job_id=str(job_uuid), db=db, user=user)
    assert pdf_resp.media_type == "application/pdf"
    assert int(pdf_resp.headers["Content-Length"]) > 100

    files_resp = list_job_files(job_id=job_uuid, db=db, user=user)
    assert len(files_resp["files"]) >= 12
    stages_present = {f["stage"] for f in files_resp["files"]}
    assert {
        "input",
        "inspection",
        "normalization",
        "qc",
        "imputation",
        "structure",
        "gwas",
        "multilocus",
        "postgwas",
        "visualization",
        "annotation",
    }.issubset(stages_present)

    preview_resp = get_job_file_content(
        job_id=job_uuid,
        file_key="dataset_input",
        preview=True,
        db=db,
        user=user,
    )
    assert b"##fileformat=VCFv4.2" in preview_resp.body

    # Test cancel_job on a running job and delete_job
    running_job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=dataset.id,
        triggered_by=user.id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.QC,
        overall_status=JobStatus.RUNNING_QC,
        parameters={},
    )
    db.add(running_job)
    cancel_resp = cancel_job(job_id=running_job.id, db=db, user=user)
    assert cancel_resp["cancelled"] is True
    assert running_job.overall_status == JobStatus.CANCELLED

    del_job_resp = delete_job(job_id=running_job.id, db=db, user=user)
    assert del_job_resp["deleted"] is True
    assert running_job.deleted_at is not None

    del_resp = delete_uploaded_dataset(file_id=dataset.id, db=db, user=user)
    assert del_resp["deleted"] is True
    assert dataset.deleted_at is not None
    assert not uploaded_vcf.exists()
