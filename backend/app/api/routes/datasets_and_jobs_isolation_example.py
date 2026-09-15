"""
api/routers/datasets_and_jobs_isolation_example.py

Contoh KONKRET bagaimana `get_current_user` dipakai untuk memastikan setiap
query database selalu di-scope ke user yang login — mengikuti endpoint yang
sudah didefinisikan di design.md bagian 5.2 & 5.3.

Prinsip yang dipakai konsisten di semua endpoint di bawah:
1. Query LIST selalu punya `.filter(Model.owner_id == user.id)` — user hanya
   pernah melihat baris miliknya, bukan "lihat semua lalu sembunyikan di UI".
2. Query DETAIL/GET-by-id: ambil dulu row-nya, lalu cek kepemilikan. Kalau
   bukan milik user, kembalikan 404 (BUKAN 403) — supaya user tidak bisa
   membedakan "resource tidak ada" vs "resource ada tapi bukan milik saya",
   yang mencegah enumerasi ID job/dataset milik user lain.
3. Query WRITE (buat job dari dataset tertentu) memvalidasi kepemilikan
   dataset SEBELUM job dibuat — mencegah user A membuat job memakai
   dataset_id milik user B walau dia berhasil menebak UUID-nya.
4. Helper `get_owned_dataset` / `get_owned_job` dibuat sebagai dependency
   FastAPI supaya pengecekan ini tidak perlu ditulis ulang manual di setiap
   endpoint (dan tidak mudah lupa ditambahkan saat endpoint baru dibuat).

File ini bisa dipecah jadi `api/routers/datasets.py` dan `api/routers/jobs.py`
sesuai konvensi router yang sudah kamu pakai.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, Dataset, User

router = APIRouter()


# --- Dependency ownership yang reusable -------------------------------------
def get_owned_dataset(
    dataset_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dataset:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()

    # 404 untuk KEDUA kasus (tidak ada / bukan milik user) — lihat poin 2 di atas.
    if dataset is None or dataset.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset tidak ditemukan")
    return dataset


def get_owned_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AnalysisJob:
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()

    if job is None or job.triggered_by != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job tidak ditemukan")
    return job


# --- Dataset endpoints -------------------------------------------------------
@router.post("/datasets/init-upload", status_code=status.HTTP_201_CREATED)
def init_upload(
    original_filename: str,
    file_size_bytes: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    dataset = Dataset(
        owner_id=user.id,  # <-- ISOLASI: dataset otomatis terikat ke user yang login,
        original_filename=original_filename,  #     bukan diambil dari body request client.
        file_size_bytes=file_size_bytes,
        upload_status="UPLOADING",
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return {"upload_id": str(dataset.id)}


@router.get("/datasets")
def list_my_datasets(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Filter owner_id WAJIB ada di sini. Lupa menambahkan baris ini adalah
    # penyebab paling umum kebocoran data lintas-user pada aplikasi multi-tenant.
    datasets = (
        db.query(Dataset)
        .filter(Dataset.owner_id == user.id)
        .order_by(Dataset.uploaded_at.desc())
        .all()
    )
    return datasets


@router.get("/datasets/{dataset_id}")
def get_dataset_detail(dataset: Dataset = Depends(get_owned_dataset)):
    # Dependency `get_owned_dataset` sudah menjamin dataset ini milik user
    # yang login — endpoint tidak perlu cek ulang.
    return dataset


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(
    dataset: Dataset = Depends(get_owned_dataset),
    db: Session = Depends(get_db),
):
    db.delete(dataset)
    db.commit()
    return None


# --- Job endpoints ------------------------------------------------------------
@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
def create_job(
    dataset_id: str,
    parameters: dict,
    dataset: Annotated[Dataset, Depends(get_owned_dataset)],  # <-- validasi kepemilikan dataset
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    `dataset` di parameter sudah lolos `get_owned_dataset` — kalau
    `dataset_id` yang dikirim client bukan milik `user`, request berhenti
    di sana dengan 404, job TIDAK PERNAH dibuat memakai dataset orang lain.
    """
    job = AnalysisJob(
        dataset_id=dataset.id,
        triggered_by=user.id,  # <-- ISOLASI: job terikat ke user yang login
        pipeline_type="full",
        current_step="normalization",
        overall_status="PENDING",
        parameters=parameters,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # enqueue_pipeline(job.id)  # trigger Celery chain, lihat design.md bagian 3.3

    return {"job_id": str(job.id)}


@router.get("/jobs")
def list_my_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    jobs = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.triggered_by == user.id)
        .order_by(AnalysisJob.created_at.desc())
        .all()
    )
    return jobs


@router.get("/jobs/{job_id}")
def get_job_detail(job: AnalysisJob = Depends(get_owned_job)):
    return job
