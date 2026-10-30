# 🧬 Backend GWAS Platform (FastAPI + Celery 10-Stage Bioinformatics Engine)

Dokumentasi teknis lengkap untuk layanan **Backend GWAS Platform** — mesin orkestrasi bioinformatika berbasis **FastAPI**, **SQLAlchemy 2.0**, dan **Celery 5.4** yang menjalankan **10 tahapan analisis Genome-Wide Association Study (GWAS)** dari hulu ke hilir (*end-to-end*) dengan prinsip **"Inspect $\rightarrow$ Validate $\rightarrow$ Run"**.

---

## 📌 Daftar Isi

1. [Arsitektur & Keunggulan Backend](#-1-arsitektur--keunggulan-backend)
2. [Struktur Direktori Kode Sumber](#-2-struktur-direktori-kode-sumber)
3. [Alur Kerja 10 Tahap Pipeline & Artefak Output](#-3-alur-kerja-10-tahap-pipeline--artefak-output)
4. [Fitur Smart Retry / Resume & Manajemen Job](#-4-fitur-smart-retry--resume--manajemen-job)
5. [Referensi Lengkap Endpoint REST API](#-5-referensi-lengkap-endpoint-rest-api)
6. [Prasyarat & Konfigurasi Environment (`.env`)](#-6-prasyarat--konfigurasi-environment-env)
7. [Panduan Menjalankan Backend (Lokal & Docker Compose)](#-7-panduan-menjalankan-backend-lokal--docker-compose)
8. [Database, Model SQLAlchemy, & Migrasi Alembic](#-8-database-model-sqlalchemy--migrasi-alembic)
9. [Pengujian Otomatis (Unit & End-to-End Tests)](#-9-pengujian-otomatis-unit--end-to-end-tests)
10. [Production Deployment & Troubleshooting](#-10-production-deployment--troubleshooting)

---

## 🏗️ 1. Arsitektur & Keunggulan Backend

Backend dirancang dengan arsitektur **Dual-Mode Execution** sehingga dapat berjalan baik di lingkungan produksi berbasis kontainer (**Docker Compose** dengan PostgreSQL, Redis, dan binary CLI Linux) maupun secara langsung di **Windows / Linux lokal** tanpa Docker:

- **Automatic Database Fallback**: Jika `DATABASE_URL` PostgreSQL (`db:5432`) tidak terjangkau saat pengembangan lokal, [`app/db/engine/database.py`](app/db/engine/database.py) secara otomatis beralih ke database lokal **SQLite (`backend/gwas_local.db`)** dan menginisialisasi skema tabel secara transparan.
- **Dual-Mode Task Runner (Celery & In-Process Thread Fallback)**: [`app/tasks/celery_app.py`](app/tasks/celery_app.py) mendukung eksekusi terdistribusi melalui broker Redis maupun *in-process background thread chain* apabila dijalankan tanpa worker eksternal.
- **Hybrid Bioinformatics Engine (CLI Tools + Vectorized NumPy/SciPy Fallback)**:
  - Menggunakan *tool* standar industri (**bcftools**, **PLINK 2 / 1.9**, **Beagle 5.4**, **GEMMA**, **Rscript / GAPIT**) ketika tersedia di `PATH`.
  - Menyediakan mesin komputasi statistik **NumPy/SciPy tervektorisasi** (pembaca biner `.bed/.bim/.fam` langsung ke matriks `int8`, dekomposisi SVD/PCA, Centered Kinship Matrix $K = \frac{1}{p}WW^T$, estimator kekerabatan KING-robust, kurva LD decay streaming, regresi Wald LMM/GLM dengan proyeksi ortogonal kovariat *Frisch-Waugh-Lovell*, dan *bisect-indexed greedy clumping* $O(N \log N)$) yang mampu menganalisis $>360.000$ SNP dalam hitungan detik.
- **High-Performance Visualization Cache**: Menyimpan hasil asosiasi ke dalam format kolumnar biner **Apache Parquet (Snappy)** dengan *importance sampling* (mempertahankan 100% SNP signifikan dan mengambil sampel merata pada SNP non-signifikan) agar visualisasi Manhattan & QQ Plot di browser tetap instan dan anti-crash.

---

## 📂 2. Struktur Direktori Kode Sumber

```text
backend/
├── app/
│   ├── main.py                        # Entrypoint aplikasi FastAPI, CORS, & registrasi router
│   ├── api/
│   │   └── routes/
│   │       ├── auth.py                # Registrasi, login JWT, refresh token cookie, logout
│   │       ├── upload.py              # Chunked upload VCF (5MB), upload fenotipe & GFF3, pre-inspection
│   │       ├── pipeline.py            # Trigger pipeline 10 tahap & deteksi kapabilitas binary CLI
│   │       ├── jobs.py                # Status job, log real-time, file explorer, cancel, retry/resume, delete
│   │       ├── results.py             # 13 endpoint hasil analisis, export streaming ZIP, & laporan PDF
│   │       ├── reference_panels.py    # Katalog panel referensi imputasi (1000 Genomes GRCh37/GRCh38)
│   │       └── health.py              # Pemeriksaan kesehatan layanan (/health)
│   ├── core/
│   │   ├── config.py                  # Konfigurasi berbasis pydantic-settings (.env)
│   │   ├── security.py                # Hashing password Bcrypt & verifikasi token JWT
│   │   └── capabilities.py            # Pemeriksaan ketersediaan plink2, plink, bcftools, gemma, beagle, Rscript
│   ├── db/
│   │   ├── base.py                    # DeclarativeBase SQLAlchemy 2.0
│   │   └── engine/
│   │       ├── database.py            # Koneksi PostgreSQL dengan fallback otomatis ke SQLite lokal
│   │       └── models.py              # Model ORM: User, Dataset, AnalysisJob, Result, ReferencePanel
│   ├── schemas/
│   │   ├── jobs.py                    # Skema respons status job & log per tahap (StepLogEntry)
│   │   └── pipeline.py                # Skema validasi konfigurasi 10 tahap pipeline (PipelineTriggerRequest)
│   ├── services/
│   │   ├── phenotype_service.py       # Rekonsiliasi ID VCF vs Fenotipe, histogram Freedman-Diaconis, outlier Tukey
│   │   ├── structure_service.py       # Parser biner PLINK (.bed), SVD PCA, Kinship K, KING-robust, LD decay, rekomendasi model
│   │   ├── assoc_normalizer.py        # Normalisasi output GEMMA/PLINK2 ke format kanonik 10 kolom TSV
│   │   ├── postgwas_service.py        # Kalkulasi Lambda GC murni, threshold dinamis (Bonferroni/FDR/LOD), clumping O(N log N)
│   │   ├── manhattan_parser.py        # Downsampler chunked & generator cache Parquet untuk Manhattan & QQ Plot
│   │   ├── annotation_service.py      # Parser GFF3 streaming, pemetaan gen kandidat dalam window LD, & daftar GO
│   │   └── report_generator.py        # Pembuat laporan ilmiah PDF (WeasyPrint / HTML)
│   ├── tasks/
│   │   ├── celery_app.py              # Konfigurasi Celery & Beat schedule + fallback local runner
│   │   ├── pipeline_orchestrator.py   # Perangkai 10 tahap pipeline & mesin Smart Retry / Resume
│   │   ├── _common.py                 # Helper penulisan log, pembatalan job, & upsert Result
│   │   ├── upload_tasks.py            # Task perakitan chunk VCF & verifikasi SHA-256
│   │   ├── inspection_tasks.py        # Tahap 1: Inspeksi Data & Konsistensi Fenotipe
│   │   ├── normalization_tasks.py     # Tahap 2: Normalisasi & Pengurutan VCF (bcftools)
│   │   ├── qc_tasks.py                # Tahap 3: Quality Control Lanjutan (PLINK 2 / 1.9)
│   │   ├── imputation_tasks.py        # Tahap 4: Imputasi Genotipe (Beagle 5.4 / Skip)
│   │   ├── structure_tasks.py         # Tahap 5: Struktur Populasi, Kinship & LD Decay
│   │   ├── gemma_tasks.py             # Tahap 6: Analisis Asosiasi GWAS (GEMMA LMM / GLM)
│   │   ├── multilocus_tasks.py        # Tahap 7: Multi-Locus GWAS (FarmCPU & BLINK via R)
│   │   ├── postgwas_tasks.py          # Tahap 8: Pasca-GWAS, Dynamic Thresholds & Clumping
│   │   ├── visualization_tasks.py     # Tahap 9: Visualisasi & Parquet Cache (Manhattan & QQ)
│   │   ├── annotation_tasks.py        # Tahap 10: Anotasi Gen Kandidat GFF3 & Daftar GO
│   │   ├── cleanup_tasks.py           # Pembersihan artefak sementara
│   │   └── retention_tasks.py         # Scheduled task pembersihan berkala (Celery Beat)
│   └── utils/
│       ├── process.py                 # Eksekutor subprocess aman dengan batas memori RLIMIT_AS & streaming log
│       └── vcf_validator.py           # Validasi struktur header & format bialelik VCF
├── tests/                             # 35 pengujian unit & integrasi end-to-end 10 tahap (pytest)
├── Dockerfile                         # Image multi-tool (Python, PLINK, GEMMA, bcftools, Beagle, WeasyPrint)
├── requirements.txt                   # Dependensi utama Python
└── README.md                          # Dokumentasi ini
```

---

## 🔬 3. Alur Kerja 10 Tahap Pipeline & Artefak Output

Setiap `AnalysisJob` memiliki direktori kerja terisolasi di `{STORAGE_ROOT}/jobs/{job_id}/` yang dibagi menjadi 10 subdirektori sesuai urutan tahapan kanonis:

| Urutan | Kode Tahap | Task Module | Status Job (`JobStatus`) | Artefak Utama di `{STORAGE_ROOT}/jobs/{job_id}/{stage}/` |
| :---: | :--- | :--- | :--- | :--- |
| **1** | `inspection` | `inspection_tasks.py` | `running_inspection` | `inspection_report.json`, `pheno_plink.txt`, `keep_ids.txt`, `sampel_vcf.txt`, `inspection.log` |
| **2** | `normalization` | `normalization_tasks.py` | `running_normalization` | `normalized.vcf.gz`, `normalized.vcf.gz.tbi`, `bcftools.log` |
| **3** | `qc` | `qc_tasks.py` | `running_qc` | `plink_qc.bed`, `plink_qc.bim`, `plink_qc.fam`, `plink_qc.vcf.gz`, `qc_comparison.json`, `plink_qc.log` |
| **4** | `imputation` | `imputation_tasks.py` | `running_imputation` | `imputed.vcf.gz` *(atau `skipped.json` bila mode `none`)*, `imputation_qc.json`, `beagle.log` |
| **5** | `structure` | `structure_tasks.py` | `running_structure` | `pca.json`, `pca_results.eigenvec`, `pca_results.eigenval`, `kinship.cXX.txt`, `kinship.json`, `ld_decay.json`, `model_recommendation.json`, `covariates.txt`, `structure.log` |
| **6** | `gwas` | `gemma_tasks.py` | `running_gwas` | `output/association.assoc.txt`, `output/kinship.cXX.txt`, `gwas_results.normalized.tsv`, `gemma.log` |
| **7** | `multilocus` | `multilocus_tasks.py` | `running_multilocus` | `farmcpu_results.tsv`, `blink_results.tsv`, `multilocus_summary.json`, `multilocus.log` |
| **8** | `postgwas` | `postgwas_tasks.py` | `running_postgwas` | `thresholds.json`, `lead_snps.json`, `lead_snps.tsv`, `Ringkasan_Puncak_GWAS.csv`, `peak_comparison.json`, `postgwas.log` |
| **9** | `visualization` | `visualization_tasks.py` | `running_parsing` | `manhattan_cache.parquet`, `visualization.log` |
| **10** | `annotation` | `annotation_tasks.py` | `running_annotation` $\rightarrow$ `completed` | `candidate_genes.json`, `Kandidat_Gen_Teranotasi.tsv`, `Daftar_Gen_Untuk_GO.txt`, `annotation.log` |

---

## 🔄 4. Fitur Smart Retry / Resume & Manajemen Job

Untuk mencegah pengulangan komputasi yang memakan waktu lama (seperti Normalisasi VCF dan PLINK QC pada file ratusan megabita) ketika sebuah job berhenti akibat kesalahan konfigurasi atau dibatalkan oleh pengguna, backend menyediakan mekanisme **Smart Retry / Resume** pada [`app/tasks/pipeline_orchestrator.py`](app/tasks/pipeline_orchestrator.py):

1. **Deteksi Tahap Selesai Otomatis (`discover_resume_state`)**:
   - Memeriksa keberadaan dan keutuhan artefak keluaran dari Tahap 1 hingga Tahap 10 secara berurutan di `{STORAGE_ROOT}/jobs/{job_id}/`.
   - Membangun kembali kamus konteks (`initial_ctx`) yang berisi path file fenotipe terselaraskan (`pheno_plink.txt`), biner PLINK hasil QC (`plink_qc.bed/.bim/.fam`), matriks kekerabatan (`kinship.cXX.txt`), kovariat (`covariates.txt`), rekomendasi model (`recommended_model`), serta jarak LD decay (`chosen_ld_decay_kb`).
2. **Dua Mode Eksekusi Ulang (`POST /api/v1/jobs/{job_id}/retry`)**:
   - **Mode Lanjutkan (`?from_scratch=false`, default)**: Membersihkan hanya direktori tahap yang gagal/berhenti beserta tahap setelahnya, lalu langsung mengeksekusi rantai task (`chain`) mulai dari tahap yang terhenti tersebut.
   - **Mode Ulangi dari Awal (`?from_scratch=true`)**: Menghapus direktori sementara Tahap 1–10 pada job tersebut dan menjalankan ulang seluruh 10 tahapan dari Tahap 1 (`inspection`) dengan parameter konfigurasi yang sama.

---

## 🔌 5. Referensi Lengkap Endpoint REST API

Seluruh endpoint berada di bawah prefix `/api/v1` (kecuali `/health`). Dokumentasi interaktif OpenAPI/Swagger tersedia di `http://127.0.0.1:8000/docs`.

### A. Autentikasi (`/api/v1/auth`)
| Metode | Endpoint | Deskripsi |
| :--- | :--- | :--- |
| `POST` | `/api/v1/auth/register` | Mendaftarkan akun peneliti baru (`email`, `password`, `full_name`). |
| `POST` | `/api/v1/auth/login` | Masuk dengan kredensial; mengembalikan JWT access token dan menyetel HttpOnly refresh cookie. |
| `POST` | `/api/v1/auth/refresh` | Memperbarui access token menggunakan refresh token cookie. |
| `POST` | `/api/v1/auth/logout` | Mengakhiri sesi dan menghapus cookie refresh token. |
| `GET` | `/api/v1/auth/me` | Mengambil profil pengguna yang sedang masuk. |

### B. Unggahan Dataset, Fenotipe, & GFF3 (`/api/v1/upload`)
| Metode | Endpoint | Deskripsi |
| :--- | :--- | :--- |
| `GET` | `/api/v1/upload/datasets` | Mengambil daftar seluruh dataset VCF yang telah diunggah oleh pengguna. |
| `POST` | `/api/v1/upload/chunk` | Mengunggah potongan (*chunk*) biner file VCF/VCF.gz. |
| `POST` | `/api/v1/upload/complete` | Merakit seluruh chunk, memverifikasi SHA-256, dan memvalidasi header VCF. |
| `GET` | `/api/v1/upload/{file_id}/download` | Mengunduh atau melihat pratinjau (`?preview=true`, 120 baris pertama) file VCF. |
| `DELETE` | `/api/v1/upload/{file_id}` | Menghapus dataset beserta file fisik di storage. |
| `POST` | `/api/v1/upload/{file_id}/phenotype` | Mengunggah file fenotipe (`.txt`/`.tsv`/`.csv`) dan mendeteksi kolom trait numerik. |
| `DELETE` | `/api/v1/upload/{file_id}/phenotype` | Menghapus file fenotipe dari dataset. |
| `POST` | `/api/v1/upload/{file_id}/annotation` | Mengunggah file anotasi genom referensi (`.gff3`/`.gff`/`.gff3.gz`). |
| `DELETE` | `/api/v1/upload/{file_id}/annotation` | Menghapus file anotasi GFF3 dari dataset. |
| `GET` | `/api/v1/upload/{file_id}/inspection` | Menjalankan inspeksi sinkron (rekonsiliasi sampel VCF vs Fenotipe, histogram Freedman-Diaconis). |
| `GET` | `/api/v1/upload/{file_id}/inspection/files/{filename}` | Mengunduh file hasil rekonsiliasi pra-eksekusi (`keep_ids.txt`, `pheno_plink.txt`, dll.). |

### C. Orkestrasi Pipeline & Kontrol Job (`/api/v1/pipeline` & `/api/v1/jobs`)
| Metode | Endpoint | Deskripsi |
| :--- | :--- | :--- |
| `GET` | `/api/v1/pipeline/capabilities` | Memeriksa ketersediaan binary CLI (`plink2`, `plink`, `bcftools`, `gemma`, `beagle`, `Rscript`). |
| `POST` | `/api/v1/pipeline/{dataset_id}/start` | Memulai eksekusi penuh 10 tahap pipeline GWAS (`HTTP 202 Accepted`). |
| `GET` | `/api/v1/jobs` | Mengambil daftar seluruh job milik pengguna beserta status, durasi, dan jumlah file. |
| `GET` | `/api/v1/jobs/{job_id}/status` | Mengambil status job, tahap aktif (`current_step`), waktu berjalan, dan `step_logs` 10 tahap. |
| `GET` | `/api/v1/jobs/{job_id}/logs` | Mengambil seluruh teks log per tahap (`inspection` s.d. `annotation`). |
| `GET` | `/api/v1/jobs/{job_id}/files` | Mengambil daftar seluruh file input & output dari semua 10 tahap secara terurut. |
| `GET` | `/api/v1/jobs/{job_id}/files/content` | Pratinjau teks (`?preview=true`, 150 baris pertama) atau unduhan file spesifik dari job. |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | Menghentikan eksekusi job yang sedang berjalan dan mencabut task Celery. |
| `POST` | `/api/v1/jobs/{job_id}/retry` | Melanjutkan job dari tahap terakhir yang terhenti (`?from_scratch=false`) atau ulangi dari Tahap 1 (`?from_scratch=true`). |
| `DELETE` | `/api/v1/jobs/{job_id}` | Menghapus job (soft-delete di DB) dan membersihkan seluruh file direktori kerjanya di disk. |

### D. Hasil Analisis & Ekspor (`/api/v1/results`)
| Metode | Endpoint | Deskripsi |
| :--- | :--- | :--- |
| `GET` | `/api/v1/results/{job_id}/manhattan-data` | Data Manhattan & QQ Plot dari cache Parquet beserta threshold dan statistik ringkasan. |
| `GET` | `/api/v1/results/{job_id}/inspection` | Laporan inspeksi sampel, statistik fenotipe, histogram, dan boxplot (Tahap 1). |
| `GET` | `/api/v1/results/{job_id}/qc-comparison` | Perbandingan jumlah sampel & varian sebelum vs sesudah QC (Tahap 3). |
| `GET` | `/api/v1/results/{job_id}/pca` | Koordinat 10 komponen utama (PCA), eigenvalues, PVE, klaster k-means, & outlier (Tahap 5). |
| `GET` | `/api/v1/results/{job_id}/kinship` | Matriks kekerabatan Centered Kinship dan tabel pasangan kekerabatan KING-robust (Tahap 5). |
| `GET` | `/api/v1/results/{job_id}/ld-decay` | Data kurva peluruhan LD ($r^2$) per bin jarak kb dan jarak cutoff LD decay (Tahap 5). |
| `GET` | `/api/v1/results/{job_id}/model-recommendation` | Rekomendasi model berbasis aturan genetika (GLM vs LMM) beserta alasannya (Tahap 5). |
| `GET` | `/api/v1/results/{job_id}/thresholds` | Nilai ambang signifikansi Bonferroni, FDR, LOD, dan Suggestive (Tahap 8). |
| `GET` | `/api/v1/results/{job_id}/lead-snps` | Daftar Lead SNPs independen hasil clumping beserta varian yang ter-clump (Tahap 8). |
| `GET` | `/api/v1/results/{job_id}/peak-comparison` | Perbandingan lokus puncak antara Single-Locus vs Multi-Locus FarmCPU/BLINK (Tahap 7 & 8). |
| `GET` | `/api/v1/results/{job_id}/candidate-genes` | Hasil anotasi gen kandidat GFF3 dan daftar ID gen untuk pengayaan GO (Tahap 10). |
| `GET` | `/api/v1/results/{job_id}/candidate-genes/go-list` | Unduh file teks `Daftar_Gen_Untuk_GO.txt` (satu ID gen per baris). |
| `GET` | `/api/v1/results/{job_id}/export` | Unduhan arsip `.zip` streaming yang berisi seluruh file hasil analisis job. |
| `GET` | `/api/v1/results/{job_id}/report` | Unduhan laporan ringkasan eksekutif ilmiah dalam format `.pdf`. |

---

## ⚙️ 6. Prasyarat & Konfigurasi Environment (`.env`)

### Prasyarat
- **Python 3.10 – 3.13**
- **Docker Desktop** dengan Docker Compose *(opsional untuk mode lokal, wajib untuk deployment kontainer penuh)*
- **PostgreSQL 15 & Redis 7** *(opsional pada pengembangan lokal karena backend memiliki fallback otomatis ke SQLite & local thread runner)*

### Konfigurasi File `backend/.env`

Salin template `.env.example` menjadi `.env` di dalam direktori `backend/`:

```powershell
Copy-Item .env.example .env
```

Contoh konfigurasi lengkap `backend/.env`:

```dotenv
# Database
# Jika PostgreSQL tidak aktif saat pengembangan lokal, backend otomatis beralih ke SQLite (backend/gwas_local.db)
DATABASE_URL=postgresql+psycopg2://gwas_user:gwas_password@localhost:5432/gwas_db

# Celery Broker & Result Backend
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# Direktori penyimpanan dataset & artefak job
STORAGE_ROOT=./data/storage

# Keamanan & JWT (Gunakan minimal 32 karakter acak)
JWT_SECRET_KEY=ganti-dengan-secret-random-minimal-32-karakter
SECRET_KEY=ganti-dengan-secret-random-minimal-32-karakter
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Environment aplikasi
ENV=development

# Konfigurasi Binary Bioinformatika (Opsional — menggunakan fallback Python jika binary tidak ditemukan di PATH)
PLINK2_BINARY_PATH=plink2
PLINK_BINARY_PATH=plink
BCFTOOLS_BINARY_PATH=bcftools
GEMMA_BINARY_PATH=gemma
BEAGLE_JAR_PATH=/opt/tools/beagle.jar
RSCRIPT_BINARY_PATH=Rscript
IMPUTATION_CONTAINER_MEM_LIMIT_MB=8192
```

Untuk menghasilkan `JWT_SECRET_KEY` acak yang aman:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 🚀 7. Panduan Menjalankan Backend (Lokal & Docker Compose)

### Opsi A: Local Development dengan Python (Windows / Linux / macOS)

1. **Siapkan Virtual Environment & Instal Dependensi**:

   ```powershell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

   *(Opsional)* Pasang dependensi tambahan untuk pembuatan laporan PDF:
   ```powershell
   python -m pip install -r app/requirements-addition.txt
   ```

2. **Jalankan Server FastAPI**:

   ```powershell
   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

   - Jika PostgreSQL (`localhost:5432`) belum berjalan, terminal akan menampilkan pesan informasi:
     `PostgreSQL tidak terjangkau; beralih otomatis ke SQLite lokal (backend/gwas_local.db)`
   - Periksa kesehatan server di `http://127.0.0.1:8000/health` dan dokumentasi interaktif di `http://127.0.0.1:8000/docs`.

3. **Jalankan Celery Worker *(Jika Menggunakan Redis Eksternal)***:

   ```powershell
   celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2
   ```

---

### Opsi B: Menjalankan Stack Penuh dengan Docker Compose

File konfigurasi Compose berada di `infra/docker-compose.yml`.

1. **Build Image Backend & Worker**:

   ```powershell
   cd infra
   docker compose build backend celery_worker
   ```

2. **Jalankan Seluruh Layanan**:

   ```powershell
   docker compose up -d
   ```

   | Service | Fungsi | Port Host |
   | :--- | :--- | :---: |
   | `backend` | FastAPI REST API + Uvicorn | `8000` |
   | `db` | PostgreSQL 15 Metadata Database | `5432` |
   | `redis` | Broker & Result Backend Celery | `6379` |
   | `celery_worker` | Eksekutor 10 Tahap Pipeline Bioinformatika | Internal |

3. **Pantau Log & Hentikan Layanan**:

   ```powershell
   docker compose ps
   docker compose logs -f backend celery_worker
   docker compose down
   ```

---

## 🗄️ 8. Database, Model SQLAlchemy, & Migrasi Alembic

Model ORM didefinisikan di [`app/db/engine/models.py`](app/db/engine/models.py):
- **`User`**: Akun pengguna (`id`, `email`, `hashed_password`, `full_name`, `role`).
- **`Dataset`**: Metadata berkas genotipe VCF, fenotipe, dan anotasi GFF3 (`id`, `owner_id`, `storage_path`, `upload_status`, `metadata_json`, `deleted_at`).
- **`AnalysisJob`**: Rekaman eksekusi pipeline (`id`, `dataset_id`, `overall_status`, `current_step`, `failed_step`, `parameters`, `error_message`, `started_at`, `completed_at`, `deleted_at`).
- **`Result`**: Indeks artefak keluaran tiap tahap (`id`, `job_id`, `result_type`, `file_path`, `summary_stats`).
- **`ReferencePanel`**: Panel referensi untuk imputasi Beagle (`id`, `name`, `file_path`, `genome_build`).

### Menggunakan Alembic untuk Migrasi PostgreSQL Produksi

Jika ingin mengelola migrasi skema dengan Alembic pada PostgreSQL:

```powershell
cd backend
$env:PYTHONPATH = (Get-Location).Path
alembic revision --autogenerate -m "sync 10-stage gwas schema"
alembic upgrade head
```

---

## 🧪 9. Pengujian Otomatis (Unit & End-to-End Tests)

Backend dilengkapi dengan **35 pengujian otomatis (`pytest`)** yang menguji seluruh layanan bioinformatika, normalisasi asosiasi, parser GFF3, rekonsiliasi fenotipe, struktur populasi, pasca-GWAS clumping, endpoint Retry/Resume, hingga integrasi penuh 10 tahap pipeline dari hulu ke hilir.

Jalankan seluruh pengujian dari root repositori atau folder `backend`:

```powershell
pytest backend/tests -v
```

Daftar modul pengujian di `backend/tests/`:
- [`test_annotation_service.py`](tests/test_annotation_service.py): Pengujian normalisasi nama kromosom, parser GFF3, klasifikasi gen kandidat (*Genik*, *Near-gen*, *Intergenik*), dan daftar GO.
- [`test_assoc_normalizer.py`](tests/test_assoc_normalizer.py): Pengujian normalisasi output asosiasi GEMMA dan PLINK2 ke format kanonik TSV.
- [`test_jobs_api.py`](tests/test_jobs_api.py): Pengujian pembuatan job QC dan endpoint **Smart Retry / Resume** (`POST /api/v1/jobs/{job_id}/retry`).
- [`test_m0_foundation.py`](tests/test_m0_foundation.py) & [`test_m1_models_schemas.py`](tests/test_m1_models_schemas.py): Pengujian konfigurasi dasar, kapabilitas CLI, model ORM, dan validasi skema Pydantic 10 tahap.
- [`test_phenotype_service.py`](tests/test_phenotype_service.py): Pengujian rekonsiliasi ID sampel VCF vs Fenotipe, auto-fallback trait numerik, dan histogram Freedman-Diaconis.
- [`test_pipeline_e2e.py`](tests/test_pipeline_e2e.py): Pengujian *End-to-End (E2E)* penuh 10 tahap pipeline (`inspection` $\rightarrow$ `annotation`), verifikasi seluruh `step_logs`, daftar file per tahap, dan seluruh endpoint `/results/{job_id}/*`.
- [`test_postgwas_service.py`](tests/test_postgwas_service.py): Pengujian kalkulasi $\lambda_{GC}$, threshold Bonferroni/FDR/LOD, dan algoritma greedy clumping.
- [`test_process.py`](tests/test_process.py) & [`test_qc_commands.py`](tests/test_qc_commands.py): Pengujian pembangun perintah PLINK (`inbred` tanpa HWE vs `outcross` dengan HWE) dan subprocess runner.
- [`test_structure_service.py`](tests/test_structure_service.py): Pengujian pembaca biner `.bed`, dekomposisi PCA, matriks Kinship, KING-robust, kurva LD decay, dan mesin rekomendasi model.

---

## 🚢 10. Production Deployment & Troubleshooting

### Production Deployment (`infra/docker-compose.prod.yml`)

Pada mode produksi, Nginx menyajikan berkas statis frontend Vite (`dist/`) dan meneruskan trafik `/api/` ke Gunicorn (`uvicorn.workers.UvicornWorker`):

```powershell
$env:POSTGRES_PASSWORD = "gunakan-password-kuat-di-secret-manager"
docker compose -f infra/docker-compose.prod.yml build
docker compose -f infra/docker-compose.prod.yml up -d
```

### Troubleshooting Umum

1. **Job Berhenti di Tengah Tahap (Gagal / Dibatalkan)**:
   - Gunakan tombol **↻ Lanjutkan Job (Retry)** di UI atau panggil `POST /api/v1/jobs/{job_id}/retry`. Backend akan otomatis memeriksa tahap yang sudah selesai di disk dan melanjutkan langsung dari tahap yang terhenti tanpa mengulang dari Tahap 1.
2. **Kolom Trait `PHENO1` Tidak Ditemukan pada File CSV Fenotipe Kustom**:
   - Layanan [`phenotype_service.py`](app/services/phenotype_service.py) kini otomatis mendeteksi dan memilih kolom numerik pertama apabila nama trait masih bernilai placeholder (`PHENO1`), serta menerima parameter `phenotype.trait` dan `phenotype.id_column` langsung dari UI inspeksi data.
3. **Port `8000`, `5432`, atau `6379` Sudah Digunakan**:
   - Periksa proses yang menggunakan port dengan `Get-NetTCPConnection -LocalPort 8000,5432,6379 -ErrorAction SilentlyContinue` atau sesuaikan port pada `.env` / `docker-compose.yml`.
4. **File VCF atau Artefak Hasil Tidak Ditemukan Antar-Container**:
   - Pastikan container `backend` dan `celery_worker` berbagi volume `vcf_storage` yang sama dan memiliki nilai `STORAGE_ROOT` yang identik.
