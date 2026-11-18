import io
import uuid

from fastapi import HTTPException
import pytest
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.api.routes import upload as upload_routes
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, UploadStatus, User, UserRole
from app.schemas.upload import InitUploadRequest
from app.services import upload_service


class _FakeSession:
    def __init__(self, dataset: Dataset, jobs: list[AnalysisJob] | None = None):
        self.dataset = dataset
        self.jobs = jobs or []
        self.committed = False

    def get(self, model, object_id):
        if model is Dataset and str(object_id) == str(self.dataset.id):
            return self.dataset
        return None

    def query(self, model):
        jobs = self.jobs

        class _Query:
            def filter(self, *args, **kwargs):
                return self

            def all(self):
                return list(jobs)

        if model is AnalysisJob:
            return _Query()
        return _Query()

    def commit(self):
        self.committed = True

    def add(self, item):
        pass

    def refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = uuid.uuid4()


def test_init_upload_request_sanitizes_path_traversal():
    req_abs = InitUploadRequest(original_filename="/app/app/main.py", total_size_bytes=1)
    assert req_abs.original_filename == "main.py"

    req_posix = InitUploadRequest(original_filename="../../x.vcf", total_size_bytes=1)
    assert req_posix.original_filename == "x.vcf"

    req_win = InitUploadRequest(original_filename="..\\..\\x.vcf", total_size_bytes=1)
    assert req_win.original_filename == "x.vcf"


@pytest.mark.parametrize("bad_name", ["..", "", ".", "/"])
def test_init_upload_request_rejects_invalid_filename(bad_name):
    with pytest.raises(ValidationError):
        InitUploadRequest(original_filename=bad_name, total_size_bytes=1)


def test_assemble_file_strips_traversal_and_stays_in_dataset_dir(tmp_path, monkeypatch):
    staging_root = tmp_path / "staging"
    datasets_root = tmp_path / "datasets"
    monkeypatch.setattr(upload_service, "_STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(upload_service, "_STAGING_ROOT", staging_root)
    monkeypatch.setattr(upload_service, "_DATASETS_ROOT", datasets_root)

    file_id = str(uuid.uuid4())
    staging_dir = upload_service.get_staging_dir(file_id)
    (staging_dir / "chunk_00000000.part").write_bytes(b"##fileformat=VCFv4.2\n")

    final_path, total_size = upload_service.assemble_file(file_id, 1, "../evil.vcf")

    expected = (datasets_root / file_id / "evil.vcf").resolve()
    assert final_path.resolve() == expected
    assert expected.is_file()
    assert not (datasets_root / "evil.vcf").exists()
    assert total_size == len(b"##fileformat=VCFv4.2\n")


def test_delete_uploaded_dataset_refuses_to_unlink_outside_storage(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True)
    outside_file = tmp_path / "secret.py"
    outside_file.write_text("do not delete", encoding="utf-8")

    monkeypatch.setattr(upload_routes.settings, "STORAGE_ROOT", str(storage_root))

    owner = User(
        id=uuid.uuid4(),
        email="owner@example.com",
        hashed_password="x",
        role=UserRole.RESEARCHER,
    )
    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="secret.py",
        storage_path=str(outside_file),
        normalized_vcf_path=str(outside_file),
        file_size_bytes=13,
        upload_status=UploadStatus.UPLOADED,
    )
    fake_db = _FakeSession(ds)

    res = upload_routes.delete_uploaded_dataset(file_id=ds.id, db=fake_db, user=owner)
    assert res["deleted"] is True
    assert outside_file.exists(), "File di luar STORAGE_ROOT tidak boleh terhapus!"


def test_upload_phenotype_file_detects_numeric_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_routes.settings, "STORAGE_ROOT", str(tmp_path))
    owner = User(
        id=uuid.uuid4(),
        email="owner@example.com",
        hashed_password="x",
        role=UserRole.RESEARCHER,
    )
    dataset = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="cohort.vcf.gz",
        storage_path="",
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
    )
    fake_db = _FakeSession(dataset)
    f = UploadFile(file=io.BytesIO(b"FID\tIID\tHeight\n1\tA\t10.5\n2\tB\t11.2\n3\tC\t9.8\n"), filename="pheno.tsv")

    res = upload_routes.upload_phenotype_file(file_id=dataset.id, file=f, db=fake_db, user=owner)
    assert res["numeric_columns"] == ["Height"]


def test_delete_uploaded_dataset_rejects_active_structure_job(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_routes.settings, "STORAGE_ROOT", str(tmp_path))
    owner = User(
        id=uuid.uuid4(),
        email="owner@example.com",
        hashed_password="x",
        role=UserRole.RESEARCHER,
    )
    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="cohort.vcf.gz",
        storage_path="",
        file_size_bytes=10,
        upload_status=UploadStatus.UPLOADED,
    )
    active_job = AnalysisJob(
        id=uuid.uuid4(),
        dataset_id=ds.id,
        triggered_by=owner.id,
        pipeline_type="full_gwas",
        overall_status=JobStatus.RUNNING_STRUCTURE,
    )
    fake_db = _FakeSession(ds, jobs=[active_job])

    with pytest.raises(HTTPException) as exc:
        upload_routes.delete_uploaded_dataset(file_id=ds.id, db=fake_db, user=owner)
    assert exc.value.status_code == 409


def test_save_chunk_rejects_oversized_chunk_and_cleans_temp_files(tmp_path, monkeypatch):
    staging_root = tmp_path / "staging"
    monkeypatch.setattr(upload_service, "_STAGING_ROOT", staging_root)
    file_id = str(uuid.uuid4())
    oversized = UploadFile(file=io.BytesIO(b"x" * (2 * 1024 * 1024)), filename="chunk.bin")

    with pytest.raises(ValueError, match="Chunk terlalu besar"):
        upload_service.save_chunk(file_id, 0, oversized, max_bytes=1 * 1024 * 1024)

    staging_dir = staging_root / file_id
    assert list(staging_dir.glob("chunk_*")) == []


def test_upload_chunk_validates_chunk_count_and_accepts_10mib_chunk(tmp_path, monkeypatch):
    staging_root = tmp_path / "staging"
    monkeypatch.setattr(upload_service, "_STAGING_ROOT", staging_root)
    monkeypatch.setattr(upload_routes.settings, "STORAGE_ROOT", str(tmp_path))

    owner = User(
        id=uuid.uuid4(),
        email="owner@example.com",
        hashed_password="x",
        role=UserRole.RESEARCHER,
    )
    ds = Dataset(
        id=uuid.uuid4(),
        owner_id=owner.id,
        original_filename="big.vcf.gz",
        storage_path="",
        file_size_bytes=25 * 1024 * 1024,
        upload_status=UploadStatus.UPLOADING,
    )
    fake_db = _FakeSession(ds)

    tiny_chunk = UploadFile(file=io.BytesIO(b"a"), filename="c.bin")
    with pytest.raises(HTTPException) as exc_many:
        upload_routes.upload_chunk(
            file_id=ds.id,
            chunk_index=0,
            total_chunks=1000,
            chunk=tiny_chunk,
            db=fake_db,
            user=owner,
        )
    assert exc_many.value.status_code == 400

    ten_mib_chunk = UploadFile(file=io.BytesIO(b"a" * (10 * 1024 * 1024)), filename="c10.bin")
    res = upload_routes.upload_chunk(
        file_id=ds.id,
        chunk_index=0,
        total_chunks=3,
        chunk=ten_mib_chunk,
        db=fake_db,
        user=owner,
    )
    assert res.chunks_received == 1

    # Total staged bytes exceeding file_size_bytes + MAX_CHUNK_SIZE_BYTES must return 413
    monkeypatch.setattr(upload_routes.settings, "MAX_CHUNK_SIZE_BYTES", 16 * 1024 * 1024)
    # 10 MiB already staged; add three 12 MiB chunks -> exceeds 25 + 16 = 41 MiB
    for idx in (1, 2):
        upload_routes.upload_chunk(
            file_id=ds.id,
            chunk_index=idx,
            total_chunks=4,
            chunk=UploadFile(file=io.BytesIO(b"b" * (12 * 1024 * 1024)), filename=f"c{idx}.bin"),
            db=fake_db,
            user=owner,
        )
    with pytest.raises(HTTPException) as exc_overflow:
        upload_routes.upload_chunk(
            file_id=ds.id,
            chunk_index=3,
            total_chunks=4,
            chunk=UploadFile(file=io.BytesIO(b"c" * (12 * 1024 * 1024)), filename="c3.bin"),
            db=fake_db,
            user=owner,
        )
    assert exc_overflow.value.status_code == 413
    assert not (staging_root / str(ds.id) / "chunk_00000003.part").exists()


def test_init_upload_enforces_max_upload_size_bytes(monkeypatch):
    monkeypatch.setattr(upload_routes.settings, "MAX_UPLOAD_SIZE_BYTES", 1024)
    owner = User(
        id=uuid.uuid4(),
        email="owner@example.com",
        hashed_password="x",
        role=UserRole.RESEARCHER,
    )
    fake_db = _FakeSession(
        Dataset(
            id=uuid.uuid4(),
            owner_id=owner.id,
            original_filename="x.vcf",
            storage_path="",
            file_size_bytes=10,
            upload_status=UploadStatus.UPLOADING,
        )
    )
    with pytest.raises(HTTPException) as exc:
        upload_routes.init_upload(
            payload=InitUploadRequest(original_filename="x.vcf", total_size_bytes=2048),
            db=fake_db,
            user=owner,
        )
    assert exc.value.status_code == 413
