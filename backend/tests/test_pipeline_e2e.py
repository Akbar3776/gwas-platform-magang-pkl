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
    ReferencePanel,
    Result,
    UploadStatus,
    User,
    UserRole,
)
from app.schemas.pipeline import (
    AnnotationConfig,
    FullPipelineRequest,
    GWASPipelineConfig,
    ImputationPipelineConfig,
    PhenotypeConfig,
    QCPipelineConfig,
)
from app.services.export_service import generate_zip_stream
from app.tasks import (
    _common as task_common,
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
        self.reference_panels = {}
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
        elif isinstance(obj, ReferencePanel):
            self.reference_panels[str(obj.id)] = obj
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
        if model is ReferencePanel:
            return self.reference_panels.get(key)
        return None

    def query(self, *models):
        db = self
        model = models[0] if len(models) == 1 else None

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

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _patch_all_pipeline_sessions(monkeypatch, db: InMemoryDB) -> None:
    for mod in (
        task_common,
        inspection_tasks,
        normalization_tasks,
        qc_tasks,
        imputation_tasks,
        structure_tasks,
        gemma_tasks,
        multilocus_tasks,
        postgwas_tasks,
        visualization_tasks,
        annotation_tasks,
    ):
        monkeypatch.setattr(mod, "SessionLocal", lambda: db)


def _run_pipeline_stages_sync(db: InMemoryDB, **kwargs) -> str:
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


def test_full_gwas_pipeline_and_results_end_to_end(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))

    db = InMemoryDB()
    _patch_all_pipeline_sessions(monkeypatch, db)

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

    monkeypatch.setattr(
        "app.api.routes.pipeline.trigger_full_pipeline",
        lambda **kwargs: _run_pipeline_stages_sync(db, **kwargs),
    )

    panel = ReferencePanel(
        id=uuid.uuid4(),
        name="1000 Genomes Phase 3",
        population="ALL",
        genome_build="GRCh38",
        vcf_paths={"ALL": "/data/reference-panels/1000g_phase3_grch38/reference.vcf.gz"},
        genetic_map_paths={},
        is_active=True,
    )
    db.add(panel)

    payload = FullPipelineRequest(
        qc=QCPipelineConfig(maf=0.01, geno=0.05, mind=0.1, hwe=1e-6),
        imputation=ImputationPipelineConfig(
            reference_panel_id=panel.id,
            nthreads=2,
            beagle_max_memory="-Xmx4g",
        ),
        gwas=GWASPipelineConfig(model="lmm"),
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


def test_full_gwas_pipeline_overflow_dataset_end_to_end(monkeypatch, tmp_path: Path, overflow_dataset: dict):
    import csv
    import gzip
    import json
    import shutil

    from app.services.kasp_service import FAM_TAIL, HEX_TAIL
    from app.tasks.pipeline_orchestrator import STAGE_SEQUENCE

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))

    db = InMemoryDB()
    _patch_all_pipeline_sessions(monkeypatch, db)

    user = User(
        id=uuid.uuid4(),
        email="overflow-e2e@example.com",
        hashed_password="hashed",
        role=UserRole.RESEARCHER,
        is_active=True,
        refresh_token_version=0,
    )
    db.add(user)

    dataset_id = uuid.uuid4()
    dataset_dir = tmp_path / "datasets" / str(dataset_id)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    uploaded_vcf = dataset_dir / "overflow.vcf.gz"
    pheno_file = dataset_dir / "phenotype.txt"
    gff_file = dataset_dir / "annotation.gff3"
    ref_fa = dataset_dir / "reference.fa"
    shutil.copyfile(overflow_dataset["vcf_path"], uploaded_vcf)
    shutil.copyfile(overflow_dataset["pheno_path"], pheno_file)
    shutil.copyfile(overflow_dataset["gff_path"], gff_file)
    shutil.copyfile(overflow_dataset["fasta_path"], ref_fa)

    dataset = Dataset(
        id=dataset_id,
        owner_id=user.id,
        original_filename="overflow.vcf.gz",
        storage_path=str(uploaded_vcf),
        phenotype_file_path=str(pheno_file),
        annotation_file_path=str(gff_file),
        extra_metadata={"reference_fasta": {"path": str(ref_fa), "status": "uploaded"}},
        file_size_bytes=uploaded_vcf.stat().st_size,
        upload_status=UploadStatus.UPLOADED,
    )
    db.add(dataset)

    monkeypatch.setattr(
        "app.api.routes.pipeline.trigger_full_pipeline",
        lambda **kwargs: _run_pipeline_stages_sync(db, **kwargs),
    )

    payload = FullPipelineRequest(
        phenotype=PhenotypeConfig(trait="TRAIT1"),
        qc=QCPipelineConfig(maf=0.01, geno=0.05, mind=0.1, population_type="inbred"),
        imputation=ImputationPipelineConfig(mode="none"),
        gwas=GWASPipelineConfig(model="glm", n_pc_covariates=0),
        annotation=AnnotationConfig(enabled=True),
    )

    start_resp = start_pipeline_endpoint(dataset_id=dataset.id, payload=payload, db=db, user=user)
    job_uuid = uuid.UUID(start_resp.job_id)
    job_root = tmp_path / "jobs" / str(job_uuid)
    job_obj = db.get(AnalysisJob, job_uuid)

    # 1. Pipeline completes cleanly
    status_resp = get_job_status(job_id=job_uuid, db=db, user=user)
    assert status_resp.overall_status == "completed"
    assert job_obj.overall_status == JobStatus.COMPLETED

    # 2. Normalization splits overflow chr7, drops non-primary scaffolds, and retains all 17 valid variants (22 - 1 INVALID_POS - 4 scaffold)
    norm_gz = job_root / "normalization" / "normalized.vcf.gz"
    assert norm_gz.is_file()
    with gzip.open(norm_gz, "rt", encoding="utf-8") as nf:
        norm_all_lines = [ln.strip() for ln in nf if ln.strip()]
    norm_contig_headers = [ln for ln in norm_all_lines if ln.startswith("##contig=")]
    norm_lines = [ln for ln in norm_all_lines if not ln.startswith("#")]
    assert not any("scaffold" in ln.lower() for ln in norm_contig_headers)
    assert len(norm_lines) == 17
    assert not any("scaffold" in ln.split("\t", 1)[0].lower() for ln in norm_lines)
    assert any(ln.startswith("chr7_p2\t1500\tchr7:5500\t") for ln in norm_lines)
    assert (job_root / "normalization" / "chrom_split_map.json").is_file()
    assert job_obj.parameters.get("qc_overflow_removed") == 0
    norm_sum = json.loads((job_root / "normalization" / "stage_summary.json").read_text(encoding="utf-8"))
    assert norm_sum["counts"]["variants_in"] == 22
    assert norm_sum["counts"]["variants_out"] == 17
    assert norm_sum["counts"]["invalid_pos_removed"] == 1
    assert norm_sum["counts"]["non_primary_removed_contigs"] == 2
    assert norm_sum["counts"]["non_primary_removed_variants"] == 4

    # 3. Lead SNP on chr7 preserves orig_chr='chr7' and orig_pos=5500
    lead_data = json.loads((job_root / "postgwas" / "lead_snps.json").read_text(encoding="utf-8"))
    causal_leads = [s for s in lead_data.get("snps", []) if s.get("orig_pos") == 5500]
    assert len(causal_leads) == 1
    assert causal_leads[0]["orig_chr"] == "chr7"
    causal_snp_id = causal_leads[0]["snp"]
    assert causal_snp_id in {"chr7_p2:1500", "chr7:5500"}

    # 4. Candidate gene mapping maps chr7:5500 to GENE_CHR7_OVERFLOW
    cand_data = json.loads((job_root / "annotation" / "candidate_genes.json").read_text(encoding="utf-8"))
    mapped_5500 = [r for r in cand_data.get("rows", []) if r.get("orig_pos") == 5500 or r.get("pos") == 5500]
    assert mapped_5500
    assert mapped_5500[0]["nearest_gene"] == "GENE_CHR7_OVERFLOW"
    assert mapped_5500[0]["category"] == "Genik"

    # 5. LD decay source_bfile is unpruned QC bfile and reports method
    ld_data = json.loads((job_root / "structure" / "ld_decay.json").read_text(encoding="utf-8"))
    assert Path(ld_data["source_bfile"]).name != "pruned"
    assert ld_data.get("method")

    # 6. KASP exports exist and flank uses original chr7 coords with +40 neighbor masked as N
    kasp_input_tsv = job_root / "annotation" / "kasp_input.tsv"
    kasp_primers_tsv = job_root / "annotation" / "kasp_primers.tsv"
    kasp_no_tail_fa = job_root / "annotation" / "kasp_primers_no_tail.fa"
    assert kasp_input_tsv.is_file()
    assert kasp_primers_tsv.is_file()
    assert kasp_no_tail_fa.is_file()

    # Parse original chr7 FASTA sequence to verify exact unmasked flanking coordinates
    chr7_chunks: list[str] = []
    in_chr7 = False
    for raw_ln in ref_fa.read_text(encoding="utf-8").splitlines():
        if raw_ln.startswith(">"):
            in_chr7 = raw_ln[1:].strip().split()[0] == "chr7"
            continue
        if in_chr7:
            chr7_chunks.append(raw_ln.strip())
    orig_chr7_seq = "".join(chr7_chunks).upper()

    with kasp_input_tsv.open("r", encoding="utf-8") as kf:
        kasp_rows = list(csv.DictReader(kf, delimiter="\t"))
    row_5500 = next(r for r in kasp_rows if r["SNP ID"] == causal_snp_id)
    flank_str = row_5500["Flanking Sequence"]
    left_flank, rest = flank_str.split("[", 1)
    bracket_alleles, right_flank = rest.split("]", 1)
    assert len(left_flank) == settings.KASP_FLANK_BP
    assert len(right_flank) == settings.KASP_FLANK_BP
    assert bracket_alleles == f"{row_5500['Allele 1']}/{row_5500['Allele 2']}"
    assert left_flank == orig_chr7_seq[5499 - settings.KASP_FLANK_BP:5499]
    assert right_flank[39] == "N"  # neighbor SNP chr7:5540 (+40 bp) masked as N
    assert right_flank[:39] == orig_chr7_seq[5500:5539]

    with kasp_primers_tsv.open("r", encoding="utf-8") as pf:
        primer_rows = list(csv.DictReader(pf, delimiter="\t"))
    primer_5500 = next(r for r in primer_rows if r["SNP_ID"] == causal_snp_id)
    assert primer_5500["status"] == "OK"
    assert primer_5500["primer_X"] and primer_5500["primer_Y"] and primer_5500["primer_C"]

    no_tail_txt = kasp_no_tail_fa.read_text(encoding="utf-8")
    assert FAM_TAIL not in no_tail_txt
    assert HEX_TAIL not in no_tail_txt

    # 7. All 10 stage summaries written both on disk and in job.parameters with started_at < finished_at and duration_s > 0
    for stage in STAGE_SEQUENCE:
        sf = job_root / stage / "stage_summary.json"
        assert sf.is_file()
        disk_sum = json.loads(sf.read_text(encoding="utf-8"))
        param_sum = job_obj.parameters.get("stage_summaries", {})[stage]
        assert datetime.fromisoformat(disk_sum["started_at"]) < datetime.fromisoformat(disk_sum["finished_at"])
        assert disk_sum["duration_s"] > 0
        assert param_sum["duration_s"] > 0

    # 8. Manhattan endpoint restores int64/original chr7 coordinate
    manhattan_resp = get_manhattan_data(job_id=str(job_uuid), db=db)
    assert manhattan_resp.total_points == 17
    assert overflow_dataset["overflow_snp_pos"] in manhattan_resp.bp

    # 9. Zero scaffolds across all downstream artifacts & Beagle single-marker guard sees 0 singles
    import pandas as pd

    for downstream_file in (
        job_root / "qc" / "plink_qc.bim",
        job_root / "gwas" / "output" / "association.assoc.txt",
        job_root / "gwas" / "gwas_results.normalized.tsv",
        job_root / "visualization" / "manhattan_chr_labels.json",
        job_root / "annotation" / "candidate_regions.csv",
        job_root / "annotation" / "candidate_regions.bed",
        job_root / "annotation" / "kasp_input.tsv",
    ):
        assert downstream_file.is_file(), f"Missing downstream file {downstream_file}"
        assert "scaffold" not in downstream_file.read_text(encoding="utf-8").lower()

    m_df = pd.read_parquet(job_root / "visualization" / "manhattan_cache.parquet")
    assert not m_df["CHR"].astype(str).str.lower().str.contains("scaffold").any()
    assert not m_df["SNP"].astype(str).str.lower().str.contains("scaffold").any()

    _, _, beagle_singles_count = imputation_tasks._prepare_beagle_safe_vcf(
        str(job_root / "qc" / "plink_qc.vcf.gz"),
        tmp_path / "beagle_scaffold_check",
    )
    assert beagle_singles_count == 0
