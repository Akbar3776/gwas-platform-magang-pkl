from pathlib import Path

from app.services.annotation_service import (
    map_lead_snps_to_genes,
    normalize_chr_name,
    parse_gff3_genes,
)
from tests.fixtures.make_fixtures import ensure_fixtures


def test_normalize_chr_name():
    assert normalize_chr_name("chr1") == "1"
    assert normalize_chr_name("Chr01") == "1"
    assert normalize_chr_name("Chr22") == "22"
    assert normalize_chr_name("CHROMOSOME_X") == "X"
    assert normalize_chr_name("1") == "1"
    assert normalize_chr_name("NC_003070.9") == "NC_003070.9"


def test_parse_gff3_genes():
    gff_fixture = Path(__file__).parent / "fixtures" / "data" / "toy_annotation.gff3"
    ensure_fixtures()

    genes_by_chr, seqids = parse_gff3_genes(gff_fixture, feature_type="gene")
    assert len(genes_by_chr) > 0
    assert "1" in genes_by_chr or 1 in genes_by_chr

    # Check ordering
    for c, glist in genes_by_chr.items():
        for i in range(len(glist) - 1):
            assert glist[i]["start"] <= glist[i + 1]["start"]


def test_map_lead_snps_to_genes(tmp_path):
    # Buat mini GFF3 di tmp_path
    mini_gff = tmp_path / "mini.gff3"
    content = (
        "##gff-version 3\n"
        "1\tSOURCE\tgene\t1000\t2000\t.\t+\t.\tID=gene:GENE_A;Name=GeneA;Note=Test kinase\n"
        "1\tSOURCE\tgene\t100000\t105000\t.\t-\t.\tID=GENE_B;Name=GeneB;Note=Test receptor\n"
    )
    mini_gff.write_text(content, encoding="utf-8")

    genes_by_chr, _ = parse_gff3_genes(mini_gff)

    lead_snps = [
        {"snp": "SNP_inside", "chr": "chr1", "pos": 1500, "p": 1e-8},      # Inside GENE_A -> Genik
        {"snp": "SNP_near", "chr": "1", "pos": 110000, "p": 2e-7},        # 5000 bp from GENE_B -> Near-gen (window 50kb)
        {"snp": "SNP_inter", "chr": "1", "pos": 500000, "p": 5e-6},       # >100kb -> Intergenik
    ]

    mapped, counts = map_lead_snps_to_genes(lead_snps, genes_by_chr, window_bp=50000)
    assert len(mapped) == 3

    # 1. SNP_inside
    assert mapped[0]["category"] == "Genik"
    assert mapped[0]["nearest_gene"] == "GENE_A"
    assert mapped[0]["closest_gene"]["gene_id"] == "GENE_A"
    assert mapped[0]["closest_gene"]["gene_name"] == "GeneA"
    assert mapped[0]["distance_bp"] == 0
    assert mapped[0]["distance_to_closest_gene"] == 0
    assert mapped[0]["genes_in_window"][0]["gene_id"] == "GENE_A"
    assert mapped[0]["genes_in_window"][0]["distance"] == 0

    # 2. SNP_near
    assert mapped[1]["category"] == "Near-gen"
    assert mapped[1]["nearest_gene"] == "GENE_B"
    assert mapped[1]["closest_gene"]["gene_id"] == "GENE_B"
    assert mapped[1]["distance_bp"] == 5000
    assert mapped[1]["distance_to_closest_gene"] == 5000
    assert mapped[1]["genes_in_window"][0]["gene_id"] == "GENE_B"
    assert mapped[1]["genes_in_window"][0]["distance"] == 5000

    # 3. SNP_inter
    assert mapped[2]["category"] == "Intergenik"
    assert mapped[2]["distance_bp"] > 50000
    assert mapped[2]["distance_to_closest_gene"] > 50000

    # Check counts
    assert counts["Genik"] == 1
    assert counts["Near-gen"] == 1
    assert counts["Intergenik"] == 1


def test_normalize_candidate_genes_payload():
    from app.api.routes.results import _normalize_candidate_genes_payload

    legacy_payload = {
        "window_bp": 100000,
        "window_kb": 100,
        "counts": {"Genik": 1, "Near-gen": 1, "Intergenik": 0},
        "rows": [
            {
                "snp": "Chr2_43381920",
                "chr": "2",
                "pos": 43381920,
                "p": 0.00018,
                "nearest_gene": "Asa2G01356",
                "nearest_gene_name": "Asa2G01356",
                "distance_bp": 0,
                "category": "Genik",
                "strand": "-",
                "description": "",
                "genes_in_window": [
                    {
                        "id": "Asa2G01356",
                        "name": "Asa2G01356",
                        "start": 43381845,
                        "end": 43382210,
                        "strand": "-",
                        "desc": "",
                    },
                    {
                        "id": "Asa2G_WINDOW_ONLY",
                        "name": "Asa2G_WINDOW_ONLY",
                        "start": 43390000,
                        "end": 43391000,
                        "strand": "+",
                        "desc": "",
                    },
                ],
            }
        ],
    }

    normalized = _normalize_candidate_genes_payload(legacy_payload)
    assert normalized["status"] == "completed"
    assert normalized["total_lead_snps"] == 1
    assert len(normalized["mappings"]) == 1
    assert normalized["candidate_genes_list"] == ["Asa2G01356"]
    row = normalized["mappings"][0]
    assert row["closest_gene"]["gene_id"] == "Asa2G01356"
    assert row["distance_to_closest_gene"] == 0
    assert row["genes_in_window"][0]["gene_id"] == "Asa2G01356"
    assert row["genes_in_window"][0]["distance"] == 0
    assert row["source"] == "GEMMA"


class _FakeAnnotationDB:
    def __init__(self, dataset, job):
        self.dataset = dataset
        self.job = job
        self.results = []

    def get(self, model, object_id):
        from app.db.engine.models import AnalysisJob, Dataset

        if model is Dataset and str(object_id) == str(self.dataset.id):
            return self.dataset
        if model is AnalysisJob and str(object_id) == str(self.job.id):
            return self.job
        return None

    def query(self, *models):
        class _Q:
            def filter(self, *args):
                return self

            def first(self):
                return None

        return _Q()

    def add(self, obj):
        self.results.append(obj)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _setup_annotation_job(tmp_path, monkeypatch, gff_text: str):
    import uuid
    from app.core.config import settings
    from app.db.engine.models import AnalysisJob, Dataset, JobStatus, PipelineStep, UploadStatus
    from app.tasks import _common as task_common, annotation_tasks

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))

    ds_id = uuid.uuid4()
    job_id = uuid.uuid4()
    ds_dir = tmp_path / "datasets" / str(ds_id)
    ds_dir.mkdir(parents=True, exist_ok=True)
    gff_path = ds_dir / "annotation.gff3"
    gff_path.write_text(gff_text, encoding="utf-8")

    dataset = Dataset(
        id=ds_id,
        owner_id=uuid.uuid4(),
        original_filename="cohort.vcf",
        storage_path=str(ds_dir / "cohort.vcf"),
        annotation_file_path=str(gff_path),
        file_size_bytes=100,
        upload_status=UploadStatus.UPLOADED,
    )
    job = AnalysisJob(
        id=job_id,
        dataset_id=ds_id,
        triggered_by=dataset.owner_id,
        pipeline_type="full_pipeline",
        current_step=PipelineStep.ANNOTATION,
        overall_status=JobStatus.RUNNING_ANNOTATION,
        parameters={"annotation": {"enabled": True, "window_kb": 50, "feature_type": "gene"}},
    )
    fake_db = _FakeAnnotationDB(dataset, job)
    monkeypatch.setattr(annotation_tasks, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(task_common, "SessionLocal", lambda: fake_db)
    return job, dataset, tmp_path / "jobs" / str(job_id)


def test_run_annotation_combines_gemma_and_significant_multilocus_with_source(tmp_path, monkeypatch):
    import csv
    import json
    from app.db.engine.models import JobStatus
    from app.tasks import annotation_tasks

    gff_text = (
        "##gff-version 3\n"
        "1\tPhytozome\tgene\t1000\t2000\t.\t+\t.\tID=GENE_A;Name=GeneA;Note=Alpha\n"
        "1\tPhytozome\tgene\t20000\t22000\t.\t+\t.\tID=GENE_WIN;Name=GeneWin;Note=WindowOnly\n"
        "2\tPhytozome\tgene\t50000\t52000\t.\t-\t.\tID=GENE_B;Name=GeneB;Note=Beta\n"
        "3\tPhytozome\tgene\t90000\t91000\t.\t+\t.\tID=GENE_C;Name=GeneC;Note=NonSig\n"
    )
    job, dataset, job_root = _setup_annotation_job(tmp_path, monkeypatch, gff_text)
    (job_root / "postgwas").mkdir(parents=True, exist_ok=True)
    (job_root / "multilocus").mkdir(parents=True, exist_ok=True)

    (job_root / "postgwas" / "lead_snps.json").write_text(
        json.dumps({
            "count": 1,
            "snps": [{"snp": "rs_gemma", "chr": "1", "pos": 1500, "p": 1e-8, "beta": 0.5}],
        }),
        encoding="utf-8",
    )
    (job_root / "multilocus" / "multilocus_summary.json").write_text(
        json.dumps({
            "lead_snps": [
                {"snp": "rs_blink", "chr": "2", "pos": 51000, "p": 2e-8, "beta": -0.4, "method": "BLINK", "is_significant": True},
                {"snp": "rs_nonsig", "chr": "3", "pos": 90500, "p": 0.2, "beta": 0.01, "method": "FARMCPU", "is_significant": False},
            ]
        }),
        encoding="utf-8",
    )

    result = annotation_tasks.run_annotation(
        prev_result={},
        job_id=str(job.id),
        dataset_id=str(dataset.id),
    )

    assert job.overall_status == JobStatus.COMPLETED
    cand = result["candidate_genes"]
    sources_by_snp = {r["snp"]: r["source"] for r in cand["rows"]}
    assert sources_by_snp == {"rs_gemma": "GEMMA", "rs_blink": "BLINK"}
    assert cand["candidate_genes_list"] == ["GENE_A", "GENE_B"]

    go_txt = (job_root / "annotation" / "Daftar_Gen_Untuk_GO.txt").read_text(encoding="utf-8").splitlines()
    assert go_txt == ["GENE_A", "GENE_B"]

    with (job_root / "annotation" / "Ekstraksi_Gen_Mentah.csv").open("r", encoding="utf-8") as cf:
        rows = list(csv.DictReader(cf))
    assert {r["SNP"]: r["Sumber"] for r in rows} == {"rs_gemma": "GEMMA", "rs_blink": "BLINK"}


def test_run_annotation_fails_when_zero_chromosomes_match(tmp_path, monkeypatch):
    import json
    import pytest
    from app.db.engine.models import JobStatus
    from app.tasks import annotation_tasks

    gff_text = (
        "##gff-version 3\n"
        "Scaffold_01\tPhytozome\tgene\t1000\t2000\t.\t+\t.\tID=GENE_S1;Name=GeneS1\n"
    )
    job, dataset, job_root = _setup_annotation_job(tmp_path, monkeypatch, gff_text)
    (job_root / "postgwas").mkdir(parents=True, exist_ok=True)
    (job_root / "postgwas" / "lead_snps.json").write_text(
        json.dumps({"count": 1, "snps": [{"snp": "rs_1", "chr": "1", "pos": 1500, "p": 1e-8}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        annotation_tasks.run_annotation(
            prev_result={},
            job_id=str(job.id),
            dataset_id=str(dataset.id),
        )

    assert job.overall_status == JobStatus.FAILED
    assert job.failed_step == "annotation"
    err = str(exc_info.value)
    assert "1" in err
    assert "Scaffold_01" in err


def test_run_annotation_restores_split_coords_for_gff3_and_fasta_flank(
    tmp_path,
    monkeypatch,
    overflow_dataset,
):
    import gzip
    import json
    import shutil
    from app.services.chromosome_service import save_split_map
    from app.tasks import annotation_tasks

    gff_text = overflow_dataset["gff_path"].read_text(encoding="utf-8")
    job, dataset, job_root = _setup_annotation_job(tmp_path, monkeypatch, gff_text)
    ds_dir = tmp_path / "datasets" / str(dataset.id)
    shutil.copyfile(overflow_dataset["fasta_path"], ds_dir / "reference.fa")

    # Parse actual base at chr7 position 5500 from overflow.fa
    fasta_lines = overflow_dataset["fasta_path"].read_text(encoding="utf-8").splitlines()
    chr7_chunks: list[str] = []
    in_chr7 = False
    for ln in fasta_lines:
        if ln.startswith(">"):
            in_chr7 = ln[1:].strip().split()[0] == "chr7"
            continue
        if in_chr7:
            chr7_chunks.append(ln.strip())
    chr7_seq = "".join(chr7_chunks)
    expected_ref = chr7_seq[5500 - 1]
    expected_alt = {"A": "G", "G": "A", "C": "T", "T": "C"}[expected_ref]

    save_split_map(
        str(job.id),
        {
            "threshold_bp": 5_000,
            "unit_bp": 2_000,
            "split_chroms": {"chr7": {"max_orig_pos": 6500, "num_parts": 4, "parts": ["chr7_p0", "chr7_p1", "chr7_p2", "chr7_p3"]}},
            "parts": {
                "chr7_p2": {"orig_chr": "chr7", "part": 2, "offset_bp": 4_000},
            },
        },
    )

    qc_dir = job_root / "qc"
    qc_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(qc_dir / "plink_qc.vcf.gz", "wt", encoding="utf-8") as gz:
        gz.write(
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
            f"chr7_p2\t1500\tchr7:5500\t{expected_ref}\t{expected_alt}\t100\tPASS\t.\tGT\t0/1\n"
        )

    (job_root / "postgwas").mkdir(parents=True, exist_ok=True)
    (job_root / "postgwas" / "lead_snps.json").write_text(
        json.dumps({
            "count": 2,
            "snps": [
                {"snp": "chr7:5500", "chr": "7_p2", "pos": 1500, "p": 1e-10, "beta": 0.9},
                {"snp": "unmatched:100", "chr": "Scaffold_Unknown", "pos": 100, "p": 1e-8, "beta": 0.2},
            ],
        }),
        encoding="utf-8",
    )

    result = annotation_tasks.run_annotation(
        prev_result={},
        job_id=str(job.id),
        dataset_id=str(dataset.id),
    )

    cand = result["candidate_genes"]
    by_snp = {r["snp"]: r for r in cand["rows"]}
    chr7_row = by_snp["chr7:5500"]
    assert chr7_row["chr"] == "chr7"
    assert chr7_row["pos"] == 5500
    assert chr7_row["orig_chr"] == "chr7"
    assert chr7_row["orig_pos"] == 5500
    assert chr7_row["pseudo_chr"] == "7_p2"
    assert chr7_row["pseudo_pos"] == 1500
    assert chr7_row["nearest_gene"] == overflow_dataset["overflow_gene_id"]
    assert chr7_row["category"] == "Genik"
    assert chr7_row["ref"] == expected_ref
    assert chr7_row["alt"] == expected_alt

    primers_payload = json.loads((job_root / "annotation" / "primers.json").read_text(encoding="utf-8"))
    primer_by_snp = {m["snp"]: m for m in primers_payload["markers"]}
    chr7_marker = primer_by_snp["chr7:5500"]
    assert chr7_marker["chr"] == "7"
    assert chr7_marker["pos"] == 5500
    assert chr7_marker["synthetic"] is False
    assert chr7_marker["fasta_ref_base"] == expected_ref
    assert chr7_marker["vcf_ref_base"] == expected_ref
    assert chr7_marker["ref_mismatch"] is False
    assert len(chr7_marker["flanking_sequence"]) > 200

    anno_log = (job_root / "annotation" / "annotation.log").read_text(encoding="utf-8")
    assert "[WARN]" in anno_log
    assert "scaffold_unknown" in anno_log.lower()
