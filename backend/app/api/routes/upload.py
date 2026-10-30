"""Authenticated, resumable chunked upload endpoints for large VCF files."""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
from pathlib import Path
import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user
from app.db.engine.database import get_db
from app.db.engine.models import AnalysisJob, Dataset, JobStatus, UploadStatus, User
from app.schemas.upload import (
    ChunkUploadResponse,
    FinalizeUploadResponse,
    InitUploadRequest,
    InitUploadResponse,
    UploadStatusResponse,
)
from app.services import upload_service
from app.services.phenotype_service import (
    inspect_phenotype,
    load_phenotype_table,
    read_vcf_sample_ids,
)
from app.tasks.upload_tasks import assemble_dataset_task

router = APIRouter(prefix="/upload", tags=["upload"])


def _is_admin(user: User) -> bool:
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    return role_val == "admin"


def _owned_dataset(file_id: uuid.UUID, user: User, db: Session) -> Dataset:
    dataset = db.get(Dataset, file_id)
    if dataset is None or (dataset.owner_id != user.id and not _is_admin(user)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload tidak ditemukan")
    return dataset


@router.get("/datasets")
def list_uploaded_datasets(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Mengembalikan daftar file dataset yang sudah diunggah oleh user."""
    query = db.query(Dataset).filter(Dataset.deleted_at.is_(None))
    if not _is_admin(user):
        query = query.filter(Dataset.owner_id == user.id)
    datasets = query.order_by(Dataset.uploaded_at.desc()).limit(50).all()

    items = []
    for ds in datasets:
        status_val = ds.upload_status.value if hasattr(ds.upload_status, "value") else str(ds.upload_status)
        file_exists = bool(ds.storage_path and Path(ds.storage_path).is_file())
        meta = ds.extra_metadata or {}
        pheno_info = meta.get("phenotype", {})
        has_pheno = bool(ds.phenotype_file_path and Path(ds.phenotype_file_path).is_file())
        has_anno = bool(ds.annotation_file_path and Path(ds.annotation_file_path).is_file())
        items.append(
            {
                "file_id": str(ds.id),
                "original_filename": ds.original_filename,
                "file_size_bytes": ds.file_size_bytes,
                "upload_status": status_val,
                "genome_build": ds.genome_build or "GRCh38",
                "checksum_sha256": ds.checksum_sha256,
                "uploaded_at": ds.uploaded_at.isoformat() if ds.uploaded_at else None,
                "file_exists": file_exists,
                "has_phenotype": has_pheno,
                "phenotype_filename": Path(ds.phenotype_file_path).name if has_pheno else None,
                "phenotype_columns": pheno_info.get("columns", []),
                "numeric_traits": pheno_info.get("numeric_columns", []),
                "has_annotation": has_anno,
                "annotation_filename": Path(ds.annotation_file_path).name if has_anno else None,
            }
        )
    return items


@router.post("/init", response_model=InitUploadResponse, status_code=status.HTTP_201_CREATED)
def init_upload(
    payload: InitUploadRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> InitUploadResponse:
    dataset = Dataset(
        owner_id=user.id,
        original_filename=payload.original_filename,
        storage_path="",
        file_size_bytes=payload.total_size_bytes,
        genome_build=payload.genome_build,
        upload_status=UploadStatus.UPLOADING,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    upload_service.get_staging_dir(str(dataset.id))
    return InitUploadResponse(file_id=dataset.id, upload_status=dataset.upload_status.value)


@router.post("/chunk", response_model=ChunkUploadResponse)
def upload_chunk(
    file_id: uuid.UUID = Form(...),
    chunk_index: int = Form(..., ge=0),
    total_chunks: int = Form(..., gt=0),
    chunk: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChunkUploadResponse:
    dataset = _owned_dataset(file_id, user, db)
    if dataset.upload_status != UploadStatus.UPLOADING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload tidak sedang menerima chunk")
    if chunk_index >= total_chunks:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="chunk_index di luar rentang")

    try:
        upload_service.save_chunk(str(file_id), chunk_index, chunk)
    except upload_service.InvalidFileIdError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file_id tidak valid") from exc

    received_indices = upload_service.get_received_chunk_indices(str(file_id))
    return ChunkUploadResponse(
        file_id=file_id,
        chunk_index=chunk_index,
        chunks_received=len(received_indices),
        total_chunks=total_chunks,
        is_complete=len(received_indices) == total_chunks,
    )


@router.post("/finalize", response_model=FinalizeUploadResponse, status_code=status.HTTP_202_ACCEPTED)
def finalize_upload(
    file_id: uuid.UUID = Form(...),
    total_chunks: int = Form(..., gt=0),
    expected_sha256: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FinalizeUploadResponse:
    dataset = _owned_dataset(file_id, user, db)
    if dataset.upload_status != UploadStatus.UPLOADING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload sudah difinalisasi atau gagal")

    received_indices = upload_service.get_received_chunk_indices(str(file_id))
    if len(received_indices) != total_chunks or received_indices != list(range(total_chunks)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload belum lengkap; kirim ulang chunk yang hilang sebelum finalize",
        )

    dataset.upload_status = UploadStatus.VALIDATING
    db.commit()
    task = assemble_dataset_task.delay(str(file_id), total_chunks, expected_sha256)
    return FinalizeUploadResponse(
        file_id=file_id,
        upload_status=dataset.upload_status.value,
        assembly_task_id=task.id,
    )


@router.get("/{file_id}/status", response_model=UploadStatusResponse)
def get_upload_status(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UploadStatusResponse:
    dataset = _owned_dataset(file_id, user, db)
    received_indices = []
    if dataset.upload_status == UploadStatus.UPLOADING:
        received_indices = upload_service.get_received_chunk_indices(str(file_id))
    return UploadStatusResponse(
        file_id=dataset.id,
        upload_status=dataset.upload_status.value,
        received_chunk_indices=received_indices,
        storage_path=dataset.storage_path or None,
        file_size_bytes=dataset.file_size_bytes,
    )


@router.get("/{file_id}/download")
def download_uploaded_dataset(
    file_id: uuid.UUID,
    preview: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mengunduh atau melihat pratinjau isi file VCF yang sudah diunggah."""
    dataset = _owned_dataset(file_id, user, db)
    if not dataset.storage_path or not Path(dataset.storage_path).is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File fisik dataset tidak ditemukan di storage")

    file_path = Path(dataset.storage_path)
    if preview:
        open_fn = gzip.open if file_path.name.endswith(".gz") else open
        lines = []
        with open_fn(file_path, "rt", encoding="utf-8", errors="replace") as f:
            for _ in range(120):
                line = f.readline()
                if not line:
                    break
                lines.append(line.rstrip("\r\n"))
        return PlainTextResponse("\n".join(lines))

    return FileResponse(
        path=str(file_path),
        filename=dataset.original_filename,
        media_type="application/octet-stream",
    )


@router.delete("/{file_id}")
def delete_uploaded_dataset(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Menghapus file dataset VCF yang sudah diunggah beserta artefak penyimpanan fisiknya."""
    dataset = _owned_dataset(file_id, user, db)
    if dataset.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload sudah dihapus")

    active_statuses = {
        JobStatus.RUNNING,
        JobStatus.RUNNING_NORMALIZATION,
        JobStatus.RUNNING_QC,
        JobStatus.RUNNING_IMPUTATION,
        JobStatus.RUNNING_GWAS,
        JobStatus.RUNNING_PARSING,
    }
    related_jobs = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.dataset_id == dataset.id)
        .all()
    )
    for job in related_jobs:
        if job.deleted_at is None and job.overall_status in active_statuses:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="File tidak dapat dihapus karena sedang digunakan oleh job yang sedang berjalan.",
            )

    # Hapus file fisik dataset dan direktori staging
    if dataset.storage_path:
        raw_path = Path(dataset.storage_path)
        raw_path.unlink(missing_ok=True)
        if raw_path.parent.is_dir() and raw_path.parent.name == str(dataset.id):
            shutil.rmtree(raw_path.parent, ignore_errors=True)

    if dataset.normalized_vcf_path:
        Path(dataset.normalized_vcf_path).unlink(missing_ok=True)

    staging_dir = Path(settings.STORAGE_ROOT) / "staging" / str(dataset.id)
    if staging_dir.is_dir():
        shutil.rmtree(staging_dir, ignore_errors=True)

    now = datetime.now(timezone.utc)
    dataset.deleted_at = now
    dataset.storage_path = ""

    for job in related_jobs:
        job.deleted_at = now
        job_dir = Path(settings.STORAGE_ROOT) / "jobs" / str(job.id)
        if job_dir.is_dir():
            shutil.rmtree(job_dir, ignore_errors=True)

    db.commit()
    return {
        "file_id": str(dataset.id),
        "original_filename": dataset.original_filename,
        "deleted": True,
    }


@router.post("/{file_id}/phenotype")
def upload_phenotype_file(
    file_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Mengunggah file fenotipe (.txt/.tsv/.csv) untuk dataset tertentu."""
    dataset = _owned_dataset(file_id, user, db)

    ext = Path(file.filename or "pheno.txt").suffix.lower()
    if ext not in {".txt", ".tsv", ".csv", ".pheno"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format file fenotipe harus berupa .txt, .tsv, atau .csv",
        )

    ds_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id)
    ds_dir.mkdir(parents=True, exist_ok=True)
    target_path = ds_dir / f"phenotype{ext}"

    # Read up to 50 MB
    content = file.file.read(50 * 1024 * 1024 + 1)
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Ukuran file fenotipe melebihi batas 50 MB.",
        )
    target_path.write_bytes(content)

    try:
        df = load_phenotype_table(target_path)
    except Exception as exc:
        target_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Gagal membaca format file fenotipe: {exc}",
        ) from exc

    columns = list(df.columns)
    if not columns:
        target_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File fenotipe tidak memiliki kolom atau baris header.",
        )

    # Detect candidate numeric columns (excluding ID-like columns)
    numeric_cols = []
    for c in columns:
        if c.upper() in {"FID", "IID", "ID", "FAMILY", "INDIVIDUAL"}:
            continue
        try:
            # Check non-missing values
            sample_vals = df[c].dropna().tolist()[:100]
            non_missing = [v for v in sample_vals if v not in {"", ".", "NA", "-9", "nan"}]
            if non_missing:
                # If at least 70% of non-missing values are parseable as float
                valid_num = sum(1 for v in non_missing if re.match(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$", v))
                if valid_num / len(non_missing) >= 0.7:
                    numeric_cols.append(c)
        except Exception:
            pass

    dataset.phenotype_file_path = str(target_path)
    meta = dataset.extra_metadata or {}
    meta["phenotype"] = {
        "filename": file.filename,
        "columns": columns,
        "numeric_columns": numeric_cols,
        "n_rows": len(df),
    }
    dataset.extra_metadata = meta
    db.commit()

    return {
        "filename": file.filename,
        "columns": columns,
        "numeric_columns": numeric_cols,
        "n_rows": len(df),
        "preview": df.head(5).to_dict(orient="records"),
    }


@router.delete("/{file_id}/phenotype")
def delete_phenotype_file(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Menghapus file fenotipe dari dataset."""
    dataset = _owned_dataset(file_id, user, db)

    # Check active jobs
    active_statuses = {
        JobStatus.PENDING,
        JobStatus.RUNNING,
        JobStatus.RUNNING_INSPECTION,
        JobStatus.RUNNING_NORMALIZATION,
        JobStatus.RUNNING_QC,
        JobStatus.RUNNING_IMPUTATION,
        JobStatus.RUNNING_STRUCTURE,
        JobStatus.RUNNING_GWAS,
        JobStatus.RUNNING_MULTILOCUS,
        JobStatus.RUNNING_POSTGWAS,
        JobStatus.RUNNING_PARSING,
        JobStatus.RUNNING_ANNOTATION,
    }
    jobs = db.query(AnalysisJob).filter(AnalysisJob.dataset_id == dataset.id).all()
    for j in jobs:
        if j.deleted_at is None and j.overall_status in active_statuses:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="File fenotipe tidak dapat dihapus karena ada job yang sedang berjalan.",
            )

    if dataset.phenotype_file_path:
        Path(dataset.phenotype_file_path).unlink(missing_ok=True)
    dataset.phenotype_file_path = None
    meta = dataset.extra_metadata or {}
    meta.pop("phenotype", None)
    dataset.extra_metadata = meta
    db.commit()
    return {"deleted": True}


@router.post("/{file_id}/annotation")
def upload_annotation_file(
    file_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Mengunggah file anotasi genom GFF3 (.gff3 / .gff / .gff3.gz) untuk dataset tertentu."""
    dataset = _owned_dataset(file_id, user, db)

    fname = file.filename or "annotation.gff3"
    lower_fname = fname.lower()
    if not (lower_fname.endswith(".gff3") or lower_fname.endswith(".gff") or lower_fname.endswith(".gff3.gz")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File anotasi harus berformat .gff3, .gff, atau .gff3.gz",
        )

    ds_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id)
    ds_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".gff3.gz" if lower_fname.endswith(".gz") else ".gff3"
    target_path = ds_dir / f"annotation{suffix}"

    # Read up to 95 MB
    content = file.file.read(95 * 1024 * 1024 + 1)
    if len(content) > 95 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Ukuran file GFF3 melebihi batas 95 MB.",
        )
    target_path.write_bytes(content)

    # Basic GFF3 sanity scan
    open_fn = gzip.open if suffix.endswith(".gz") else open
    n_genes = 0
    seqids = set()
    try:
        with open_fn(target_path, "rt", encoding="utf-8", errors="replace") as f:
            for line_idx, line in enumerate(f):
                if line.startswith("#"):
                    continue
                parts = line.strip().split("\t")
                if len(parts) >= 9:
                    seqids.add(parts[0])
                    if parts[2].lower() == "gene":
                        n_genes += 1
                if line_idx > 500_000:
                    break
    except Exception as exc:
        target_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File GFF3 tidak valid atau korup: {exc}",
        ) from exc

    dataset.annotation_file_path = str(target_path)
    meta = dataset.extra_metadata or {}
    meta["annotation"] = {
        "filename": file.filename,
        "n_genes": n_genes,
        "seqids": sorted(list(seqids))[:50],
    }
    dataset.extra_metadata = meta
    db.commit()

    return {
        "filename": file.filename,
        "n_genes": n_genes,
        "seqids": sorted(list(seqids))[:50],
    }


@router.delete("/{file_id}/annotation")
def delete_annotation_file(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Menghapus file anotasi GFF3 dari dataset."""
    dataset = _owned_dataset(file_id, user, db)

    if dataset.annotation_file_path:
        Path(dataset.annotation_file_path).unlink(missing_ok=True)
    dataset.annotation_file_path = None
    meta = dataset.extra_metadata or {}
    meta.pop("annotation", None)
    dataset.extra_metadata = meta
    db.commit()
    return {"deleted": True}


@router.get("/{file_id}/inspection")
def inspect_dataset_and_phenotype(
    file_id: uuid.UUID,
    trait: str | None = Query(None, description="Nama kolom trait numerik di file fenotipe (opsional, otomatis jika kosong)"),
    id_column: str = Query("IID", description="Nama kolom ID sampel"),
    fid_column: str | None = Query("FID", description="Nama kolom Family ID opsional"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Pemeriksaan pra-analisis (Fase 1: Pre-run Inspection) sinkron untuk VCF dan fenotipe."""
    dataset = _owned_dataset(file_id, user, db)
    if not dataset.storage_path or not Path(dataset.storage_path).is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File VCF dataset belum selesai diunggah atau tidak ditemukan.",
        )
    if not dataset.phenotype_file_path or not Path(dataset.phenotype_file_path).is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dataset belum memiliki file fenotipe. Silakan unggah file fenotipe terlebih dahulu.",
        )

    try:
        vcf_ids = read_vcf_sample_ids(dataset.storage_path)
        pheno_df = load_phenotype_table(dataset.phenotype_file_path)
        report = inspect_phenotype(
            vcf_ids=vcf_ids,
            df=pheno_df,
            trait=trait,
            id_column=id_column,
            fid_column=fid_column,
        )

        # Cache inspection output files inside dataset dir for user download
        inspect_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id) / "inspection"
        inspect_dir.mkdir(parents=True, exist_ok=True)
        (inspect_dir / "sampel_vcf.txt").write_text("\n".join(vcf_ids) + "\n", encoding="utf-8")
        (inspect_dir / "keep_ids.txt").write_text("\n".join(f"{s} {s}" for s in report["matched_ids"]) + "\n", encoding="utf-8")
        (inspect_dir / "id_hanya_vcf.txt").write_text("\n".join(report.get("only_in_vcf_sample", [])) + "\n", encoding="utf-8")
        (inspect_dir / "id_hanya_fenotipe.txt").write_text("\n".join(report.get("only_in_pheno_sample", [])) + "\n", encoding="utf-8")

        return report
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Gagal melakukan inspeksi dataset dan fenotipe: {exc}",
        ) from exc


@router.get("/{file_id}/inspection/files/{name}")
def download_inspection_file(
    file_id: uuid.UUID,
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mengunduh file hasil rekonsiliasi ID (keep_ids.txt, id_hanya_vcf.txt, id_hanya_fenotipe.txt, sampel_vcf.txt)."""
    dataset = _owned_dataset(file_id, user, db)
    allowed_files = {"keep_ids.txt", "id_hanya_vcf.txt", "id_hanya_fenotipe.txt", "sampel_vcf.txt"}
    if name not in allowed_files:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File tidak ditemukan")

    inspect_dir = Path(settings.STORAGE_ROOT) / "datasets" / str(dataset.id) / "inspection"
    file_path = inspect_dir / name
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File inspeksi belum dibuat. Silakan jalankan inspeksi terlebih dahulu.",
        )

    return FileResponse(
        path=str(file_path),
        filename=name,
        media_type="text/plain",
    )
