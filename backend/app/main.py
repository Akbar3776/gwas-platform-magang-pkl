from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    auth,
    upload,
    jobs,
    pipeline,
    results,
    export,
    report,
    reference_panels,
)
from app.core.config import settings
from app.core.logging_config import configure_logging

configure_logging()

app = FastAPI(
    title="GWAS Platform API",
    description="Orkestrator pipeline GWAS end-to-end (normalisasi -> QC -> imputasi -> GWAS -> visualisasi)",
    version="0.1.0",
)

# CORS wajib allow_credentials=True + origin eksplisit (BUKAN "*") karena
# apiClient.js & AuthContext.jsx mengirim `credentials: "include"` untuk
# cookie refresh token HttpOnly (lihat POST /auth/refresh, /auth/login).
# ASUMSI: app.core.config.settings menyediakan `CORS_ORIGINS` (list of str).
app.add_middleware(
    CORSMiddleware,
    allow_origins=getattr(settings, "CORS_ORIGINS", ["http://localhost:5173"]),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# PENTING — dua hal yang harus disamakan dengan frontend:
#
# 1. API_PREFIX = "/api/v1" agar endpoint memiliki versioning yang eksplisit.
#
# 2. TERBUKTI dari auth.py: setiap router sudah men-declare prefix-nya
#    SENDIRI di file masing-masing, contoh:
#        router = APIRouter(prefix="/auth", tags=["auth"])
#    Jadi di sini kita HANYA boleh menambahkan API_PREFIX ("/api/v1"), TIDAK
#    boleh menambahkan lagi "/auth", "/jobs", dst. — kalau ditambahkan lagi
#    hasilnya path ganda ("/api/auth/auth/login") dan semua endpoint 404.
#
#    Pola ini kemungkinan besar konsisten di semua router lain
#    (upload.py, jobs.py, pipeline.py, results.py, export.py, report.py,
#    reference_panels.py). VERIFIKASI baris `APIRouter(prefix=...)` di
#    masing-masing file itu — kalau ada yang TIDAK punya prefix sendiri,
#    tambahkan manual hanya untuk router itu, mis.:
#        app.include_router(some_router.router, prefix=f"{API_PREFIX}/some-path", ...)
API_PREFIX = "/api/v1"

app.include_router(auth.router, prefix=API_PREFIX, tags=["Auth"])
app.include_router(upload.router, prefix=API_PREFIX, tags=["Datasets & Upload"])
app.include_router(jobs.router, prefix=API_PREFIX, tags=["Jobs"])
app.include_router(pipeline.router, prefix=API_PREFIX, tags=["Pipeline"])
app.include_router(results.router, prefix=API_PREFIX, tags=["Results"])
app.include_router(export.router, prefix=API_PREFIX, tags=["Export"])
app.include_router(report.router, prefix=API_PREFIX, tags=["Report"])
app.include_router(reference_panels.router, prefix=API_PREFIX, tags=["Reference Panels"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}
