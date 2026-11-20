# 🧬 Backend GWAS Platform (FastAPI + Celery 10-Stage Bioinformatics Engine)

Dokumentasi teknis untuk layanan **Backend GWAS Platform** — mesin orkestrasi bioinformatika berbasis **FastAPI**, **SQLAlchemy 2.0**, dan **Celery 5.4** yang menjalankan **10 tahapan analisis Genome-Wide Association Study (GWAS)** dari hulu ke hilir dengan prinsip **"Inspect $\rightarrow$ Validate $\rightarrow$ Run"**.

---

## 📌 Daftar Isi

1. [Arsitektur & Karakteristik Eksekusi Backend](#-1-arsitektur--karakteristik-eksekusi-backend)
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

## 🏗️ 1. Arsitektur & Karakteristik Eksekusi Backend

Backend dirancang agar dapat berjalan baik di lingkungan kontainer penuh (**Docker Compose** dengan PostgreSQL, Redis, serta binary CLI bioinformatika Linux + R/GAPIT3) maupun secara lokal untuk pengujian/pengembangan:

- **PostgreSQL Produksi + Opt-In SQLite Fallback (`ALLOW_SQLITE_FALLBACK`)**: Secara bawaan (`ALLOW_SQLITE_FALLBACK=false`), kegagalan koneksi PostgreSQL akan ditolak pada `/health` (`HTTP 503`) tanpa diam-diam beralih ke database kosong. Saat pengembangan lokal tanpa PostgreSQL (`ALLOW_SQLITE_FALLBACK=true`), [`app/db/engine/database.py`](app/db/engine/database.py) otomatis membuat dan memakai `backend/gwas_local.db`.
- **Celery Multi-Queue Orchestrator**: [`app/tasks/celery_app.py`](app/tasks/celery_app.py) mendaftarkan task ke lima antrean pekerja (`celery`, `normalization`, `qc`, `imputation`, `gwas`) beserta fallback thread in-process bila paket `celery` tidak terpasang saat uji unit lokal.
- **Hybrid Bioinformatics Execution**:
  - **Tahap CLI Utama (*Fail-Fast*)**: `bcftools` (normalisasi & indeks TBI/CSI), `plink2` / `plink` 1.9 (Quality Control & LD pruning), dan `gemma` (LMM `-lmm 4` / GLM `-lm 1`) harus tersedia saat menjalankan pipeline; kegagalan biner langsung menghentikan job dengan error log yang jelas.
  - **Tahap Multi-Locus (GAPIT3 R + Python Native Engine)**: Menggunakan R/GAPIT3 (`gapit_runner.R`) untuk `FarmCPU`, `BLINK`, dan `MLMM`; bila R/GAPIT tidak dipasang (mis. `WITH_GAPIT=false`) dan `ALLOW_PYTHON_MULTILOCUS=true`, sistem beralih ke engine vektorisasi NumPy/SciPy dengan label engine sumber yang tercatat eksplisit.
  - **Tahap Anotasi, GO, & Primer3 Nyata**: Uji hipergeometrik GO hanya memakai istilah GO asli (atribut `Ontology_term` GFF3 atau berkas pemetaan TSV/GAF), serta ekstraksi flanking 201 bp + desain primer menggunakan FASTA referensi terindeks `.fai` dan `primer3-py` asli (status jujur `"unavailable"` / `"no_reference"` tanpa `ALLOW_SYNTHETIC_ANNOTATION=true`).
- **Cache Visualisasi Apache Parquet**: Menyimpan dataset Manhattan Plot dalam format kolumnar **Apache Parquet (Snappy)** dengan menjaga 100% Lead SNPs signifikan dan sampling terstrata pada varian latar belakang.

---

## 📂 2. Struktur Direktori Kode Sumber

```text
backend/
├── app/
│   ├── main.py                        # Entrypoint FastAPI, CORS, /health, auto-init SQLite dev, & 9 router
│   ├── api/routes/
│   │   ├── auth.py                    # Registrasi, login JWT, refresh token cookie, logout, & /me
│   │   ├── capabilities.py            # GET /pipeline/capabilities (status plink2, plink, gemma, bcftools, beagle, R/gapit)
│   │   ├── export.py                  # GET /results/{job_id}/export (streaming arsip .zip artefak job)
│   │   ├── jobs.py                    # Daftar job, status, log, file explorer, cancel, retry/resume, delete
│   │   ├── pipeline.py                # Trigger pipeline 10 tahap (POST /pipeline/{dataset_id}/start)
│   │   ├── reference_panels.py        # Katalog panel referensi imputasi aktif
│   │   ├── report.py                  # GET /results/{job_id}/report (laporan PDF ringkasan eksekutif)
│   │   ├── results.py                 # 14 endpoint data hasil 7 tab UI, GO enrichment, & primer3-py
│   │   └── upload.py                  # Chunked upload VCF/FASTA, fenotipe, GFF3, pemetaan GO TSV/GAF, & inspeksi
│   ├── core/
│   │   ├── config.py                  # Konfigurasi Pydantic Settings (.env)
│   │   ├── logging_config.py          # Konfigurasi logging standar stdout
│   │   ├── resource_limits.py         # Heap JVM Beagle dari limit cgroup; override dipotong maksimal 75%
│   │   └── security.py                # Hashing bcrypt, PyJWT, require_job_owner, require_admin, & resolve_jwt_secret
│   ├── db/
│   │   ├── base.py                    # DeclarativeBase SQLAlchemy 2.0
│   │   └── engine/
│   │       ├── database.py            # Koneksi PostgreSQL + opt-in SQLite lokal
│   │       └── models.py              # Model ORM: User, Dataset, AnalysisJob, Result, ReferencePanel
│   ├── schemas/
│   │   ├── jobs.py                    # Skema status job, QC request, & log per tahap (StepLogEntry)
│   │   ├── pipeline.py                # Skema validasi konfigurasi 10 tahap (FullPipelineRequest)
│   │   ├── results.py                 # Skema respons kolumnar ManhattanDataResponse
│   │   └── upload.py                  # Skema chunked upload & sanitasi nama berkas
│   ├── services/
│   │   ├── annotation_service.py      # Streaming parser GFF3, normalisasi kromosom, & pemetaan gen kandidat
│   │   ├── assoc_normalizer.py        # Normalisasi output GEMMA/PLINK2 ke TSV kanonikal 10 kolom
│   │   ├── export_service.py          # Streaming generator arsip ZIP artefak hasil
│   │   ├── go_enrichment_service.py   # Parser GO TSV/GAF/GFF3 & uji hipergeometrik + koreksi BH FDR
│   │   ├── manhattan_parser.py        # Chunked reader & cache Apache Parquet Snappy + chr labels
│   │   ├── phenotype_service.py       # Rekonsiliasi sampel, deteksi duplikat, Freedman-Diaconis, outlier Tukey
│   │   ├── postgwas_service.py        # Lambda GC murni, threshold dinamis, & LD-aware clumping O(N log N)
│   │   ├── primer_service.py          # Indeks .fai streaming O(1) RAM, flanking 201 bp, & desain primer primer3-py
│   │   ├── qc_service.py              # Pembangun perintah PLINK 2/1.9, filter POS >2.10 Gb, & histogram komparasi QC
│   │   ├── report_generator.py        # Generator PDF WeasyPrint + fallback PDF Python
│   │   ├── structure_service.py       # Pembaca biner .bed/.bim/.fam, SVD PCA, Kinship K, KING eksak, & LD decay
│   │   └── upload_service.py          # Penyimpanan & perakitan chunk biner + SHA-256
│   ├── tasks/
│   │   ├── celery_app.py              # Konfigurasi Celery, Beat schedule retensi, & post-task cleanup hook
│   │   ├── _common.py                 # Utilitas pembatalan, `chr_label`, `is_under_storage`, & `which_tool`
│   │   ├── pipeline_orchestrator.py   # Celery chain 10 tahap, revoke chain penuh, & Smart Retry / Resume
│   │   ├── upload_tasks.py            # Perakitan VCF serta dekompresi & pengindeksan `.fai` FASTA referensi
│   │   ├── inspection_tasks.py        # Tahap 1: Inspeksi Data & Konsistensi Fenotipe
│   │   ├── normalization_tasks.py     # Tahap 2: Normalisasi & Pengurutan VCF (`bcftools` + TBI/CSI)
│   │   ├── qc_tasks.py                # Tahap 3: Quality Control (`PLINK 2 / 1.9`)
│   │   ├── imputation_tasks.py        # Tahap 4: Imputasi Genotipe (`Beagle 5.4` + re-QC pasca-imputasi)
│   │   ├── structure_tasks.py         # Tahap 5: LD pruning, PCA (`n_pc_covariates`), Kinship, KING, & LD Decay
│   │   ├── gemma_tasks.py             # Tahap 6: Analisis Asosiasi GWAS (`GEMMA` `-lmm 4` / `-lm 1`)
│   │   ├── multilocus_tasks.py        # Tahap 7: Multi-Locus (`FarmCPU`/`BLINK`/`MLMM`) & PVE(%) / FDR
│   │   ├── postgwas_tasks.py          # Tahap 8: Pasca-GWAS, Dynamic Thresholds & Clumping
│   │   ├── visualization_tasks.py     # Tahap 9: Cache Apache Parquet utama & per-model multi-locus
│   │   ├── annotation_tasks.py        # Tahap 10: Anotasi Gen GFF3, GO Enrichment, & Primer3
│   │   └── retention_tasks.py         # Cron pembersihan retensi berkala + guard anti-wipe DB
│   ├── templates/
│   │   ├── gapit_runner.R             # Runner R GAPIT3 untuk model FarmCPU, BLINK, dan MLMM
│   │   └── report_template.html       # Template laporan PDF
│   └── utils/
│       └── process.py                 # Eksekutor subprocess aman (`shell=False`, batas RAM, process group kill)
├── scripts/
│   └── prepare_reference_fasta.py     # CLI streaming persiapan FASTA NCBI (rename, merge 7_1+7_2, wrap, cek --vcf)
├── alembic/                           # Skrip & versi migrasi PostgreSQL
├── tests/                             # 31 modul uji pytest (242 passed, 5 skipped di host Windows)
├── Dockerfile                         # Image Docker bioinformatika lengkap (PLINK 1.9/2, GEMMA, bcftools, Beagle, R/GAPIT3)
├── requirements.txt                   # Dependensi Python produksi ter-pin
├── requirements-test.txt              # Dependensi pengujian (pytest, flake8, pip-audit)
└── README.md                          # Dokumentasi ini
```

---

## 🔬 3. Alur Kerja 10 Tahap Pipeline & Artefak Output

Setiap `AnalysisJob` memiliki direktori kerja terisolasi di `{STORAGE_ROOT}/jobs/{job_id}/` dengan 10 subdirektori tahap kanonis:

| Urutan | Kode Tahap | Queue | Task Module | Status Job (`JobStatus`) | Artefak di `{STORAGE_ROOT}/jobs/{job_id}/{stage}/` |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **1** | `inspection` | `celery` | `inspection_tasks.py` | `running_inspection` | `inspection_report.json`, `pheno_plink.txt`, `keep_ids.txt`, `sampel_vcf.txt`, `inspection.log` |
| **2** | `normalization` | `normalization` | `normalization_tasks.py` | `running_normalization` | `normalized.vcf.gz`, `normalized.vcf.gz.tbi` *(atau `.csi`)*, `bcftools.log` |
| **3** | `qc` | `qc` | `qc_tasks.py` | `running_qc` | `plink_qc.bed`, `plink_qc.bim`, `plink_qc.fam`, `plink_qc.vcf.gz`, `qc_comparison.json`, `plink_qc.log` |
| **4** | `imputation` | `imputation` | `imputation_tasks.py` | `running_imputation` | `imputed.vcf.gz`, `imputed.bed/.bim/.fam` *(atau `skipped.json`)*, `beagle.log` |
| **5** | `structure` | `gwas` | `structure_tasks.py` | `running_structure` | `pca.json`, `pca_results.eigenvec`, `pca_results.eigenval`, `kinship.cXX.txt`, `kinship.json`, `ld_decay.json`, `model_recommendation.json`, `covariates.txt`, `covariates_plink.txt`, `structure.log` |
| **6** | `gwas` | `gwas` | `gemma_tasks.py` | `running_gwas` | `output/association.assoc.txt`, `output/kinship.cXX.txt`, `gwas_results.normalized.tsv`, `gemma.log` |
| **7** | `multilocus` | `gwas` | `multilocus_tasks.py` | `running_multilocus` | `farmcpu_results.tsv`, `blink_results.tsv`, `mlmm_results.tsv`, `*_Significant_SNPs_PVE.csv`, `multilocus_summary.json` *(atau `skipped.json`)*, `multilocus.log` |
| **8** | `postgwas` | `gwas` | `postgwas_tasks.py` | `running_postgwas` | `thresholds.json`, `lead_snps.json`, `lead_snps.tsv`, `Ringkasan_Puncak_GWAS.csv`, `peak_comparison.json`, `postgwas.log` |
| **9** | `visualization` | `gwas` | `visualization_tasks.py` | `running_parsing` | `manhattan_cache.parquet`, `manhattan_chr_labels.json`, `multilocus/<model>/manhattan_cache.parquet`, `visualization.log` |
| **10** | `annotation` | `gwas` | `annotation_tasks.py` | `running_annotation` $\rightarrow$ `completed` | `candidate_genes.json`, `Kandidat_Gen_Teranotasi.tsv`, `Ekstraksi_Gen_Mentah.csv`, `Daftar_Gen_Untuk_GO.txt`, `go_enrichment.json`, `primers.json`, `annotation.log` |

---

## 🔄 4. Fitur Smart Retry / Resume & Manajemen Job

Mekanisme **Smart Retry / Resume** pada [`app/tasks/pipeline_orchestrator.py`](app/tasks/pipeline_orchestrator.py):

1. **Deteksi Tahap Selesai (`discover_resume_state`)**: Memeriksa integritas artefak keluaran Tahap 1 hingga 10 secara berurutan (Tahap 4 dianggap selesai hanya jika `imputed.vcf.gz` atau `skipped.json` ada; Tahap 7 hanya jika `multilocus_summary.json` atau `skipped.json` ada) lalu membangun ulang `initial_ctx` termasuk `genotype_bfile_prefix` hasil imputasi bila tersedia.
2. **Dua Mode Retry (`POST /api/v1/jobs/{job_id}/retry`)**:
   - `?from_scratch=false` *(default)*: mencabut sisa task lama, menghapus subdirektori mulai dari tahap yang gagal/terhenti hingga tahap akhir, dan melanjutkan `chain` dari tahap tersebut.
   - `?from_scratch=true`: menghapus seluruh direktori Tahap 1–10 dan memulai kembali dari Tahap 1 (`inspection`).

---

## 🔌 5. Referensi Lengkap Endpoint REST API

Seluruh endpoint berada di bawah prefix `/api/v1` (kecuali `/health`). Dokumentasi OpenAPI/Swagger tersedia di `http://127.0.0.1:8000/docs`.

### A. Autentikasi (`/api/v1/auth`)
| Metode | Endpoint | Deskripsi |
| :--- | :--- | :--- |
| `POST` | `/api/v1/auth/register` | Mendaftarkan akun peneliti baru (`email`, `password`, `full_name`). |
| `POST` | `/api/v1/auth/login` | Masuk dengan kredensial OAuth2 password form; mengembalikan Access Token & HttpOnly refresh cookie. |
| `POST` | `/api/v1/auth/refresh` | Memperbarui access token & merotasi refresh token cookie. |
| `POST` | `/api/v1/auth/logout` | Menaikkan `refresh_token_version` & menghapus cookie refresh token. |
| `GET` | `/api/v1/auth/me` | Mengambil profil pengguna yang sedang masuk. |

### B. Unggahan Dataset, Fenotipe, GFF3, GO, & Referensi FASTA (`/api/v1/upload`)
| Metode | Endpoint | Deskripsi |
| :--- | :--- | :--- |
| `GET` | `/api/v1/upload/datasets` | Daftar dataset VCF beserta status fenotipe, GFF3, pemetaan GO, dan FASTA referensi. |
| `POST` | `/api/v1/upload/init` | Memulai sesi upload bertahap VCF (`original_filename`, `total_size_bytes`). |
| `POST` | `/api/v1/upload/chunk` | Mengunggah potongan (*chunk*, batas `MAX_CHUNK_SIZE_BYTES = 16 MiB`) berkas VCF. |
| `POST` | `/api/v1/upload/finalize` | Memvalidasi kelengkapan chunk & checksum SHA-256 lalu merakit file VCF asinkron. |
| `GET` | `/api/v1/upload/{file_id}/status` | Memantau progres perakitan/validasi unggahan dataset. |
| `GET` | `/api/v1/upload/{file_id}/download` | Mengunduh atau pratinjau (`?preview=true`, 120 baris pertama) file VCF secara streaming. |
| `DELETE` | `/api/v1/upload/{file_id}` | Menghapus dataset (soft-delete DB) dan berkas fisiknya. |
| `POST` / `DELETE` | `/api/v1/upload/{file_id}/phenotype` | Mengunggah atau menghapus file fenotipe (`.txt`/`.tsv`/`.csv`). |
| `POST` / `DELETE` | `/api/v1/upload/{file_id}/annotation` | Mengunggah atau menghapus file anotasi GFF3 (`.gff3`/`.gff`/`.gff3.gz`). |
| `POST` / `DELETE` | `/api/v1/upload/{file_id}/go-annotation` | Mengunggah atau menghapus pemetaan Gene Ontology (`.tsv`/`.txt`/`.gaf`/`.gaf.gz`). |
| `POST` | `/api/v1/upload/{file_id}/reference/init` | Memulai sesi upload bertahap FASTA referensi (`.fa`/`.fasta`/`.fna`/`.gz`). |
| `POST` | `/api/v1/upload/{file_id}/reference/chunk` | Mengunggah satu potongan (*chunk*) berkas FASTA referensi. |
| `POST` | `/api/v1/upload/{file_id}/reference/finalize` | Finalisasi FASTA referensi + penjadwalan dekompresi & pembangunan indeks `.fai`. |
| `GET` / `DELETE` | `/api/v1/upload/{file_id}/reference/status` & `/reference` | Memeriksa progres indeks `.fai` atau menghapus FASTA referensi. |
| `GET` | `/api/v1/upload/{file_id}/inspection` | Inspeksi sinkron (rekonsiliasi ID sampel VCF vs Fenotipe, Freedman-Diaconis, Tukey). |
| `GET` | `/api/v1/upload/{file_id}/inspection/files/{name}` | Mengunduh berkas hasil rekonsiliasi pra-eksekusi (`keep_ids.txt`, `pheno_plink.txt`, dll.). |

### C. Orkestrasi Pipeline, Capabilities, Panel Referensi, & Kontrol Job
| Metode | Endpoint | Deskripsi |
| :--- | :--- | :--- |
| `GET` | `/api/v1/pipeline/capabilities` | Ketersediaan CLI (`plink2`, `plink`, `gemma`, `bcftools`, `beagle`, `rscript`, `gapit`) & mode runtime. |
| `POST` | `/api/v1/pipeline/{dataset_id}/start` | Memicu eksekusi penuh 10 tahap pipeline GWAS (`HTTP 202 Accepted`). |
| `GET` | `/api/v1/reference-panels` | Daftar panel referensi imputasi yang aktif (tanpa mengekspos path server). |
| `GET` | `/api/v1/jobs` | Daftar seluruh job milik pengguna beserta status, durasi, dan jumlah file. |
| `POST` | `/api/v1/jobs/qc` | Memicu job QC mandiri (dengan `job_id=` keyword argument). |
| `GET` | `/api/v1/jobs/{job_id}/status` | Status polling terkini, `current_step`, `elapsed_seconds`, dan `step_logs` 10 tahap. |
| `GET` | `/api/v1/jobs/{job_id}/logs` | Seluruh teks log tail per tahap (`inspection` s.d. `annotation`). |
| `GET` | `/api/v1/jobs/{job_id}/files` | Daftar seluruh file input & artefak output dari 10 tahap. |
| `GET` | `/api/v1/jobs/{job_id}/files/content` | Pratinjau teks (`?preview=true`, 150 baris) atau unduh file spesifik job. |
| `GET` | `/api/v1/jobs/{job_id}/results` | Ringkasan statistik hasil GWAS untuk dashboard. |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | Menghentikan job aktif dan mencabut seluruh `celery_task_ids` pada rantai chain. |
| `POST` | `/api/v1/jobs/{job_id}/retry` | Melanjutkan job (`?from_scratch=false`) atau ulang dari Tahap 1 (`?from_scratch=true`). |
| `DELETE` | `/api/v1/jobs/{job_id}` | Menghapus job (soft-delete DB) dan membersihkan direktori kerjanya di disk. |

### D. Hasil Analisis, GO, Primer3, & Ekspor (`/api/v1/results`)
| Metode | Endpoint | Deskripsi |
| :--- | :--- | :--- |
| `GET` | `/api/v1/results/{job_id}/manhattan-data` | Data Manhattan & QQ Plot kolumnar dari cache Parquet (mendukung `?model=GEMMA|FARMCPU|BLINK|MLMM`). |
| `GET` | `/api/v1/results/{job_id}/inspection` | Laporan inspeksi sampel, statistik fenotipe, histogram, dan boxplot (Tahap 1). |
| `GET` | `/api/v1/results/{job_id}/qc-comparison` | Perbandingan sampel & varian before/after QC serta histogram MAF/missingness (Tahap 3). |
| `GET` | `/api/v1/results/{job_id}/pca` | Koordinat PC, eigenvalues, PVE, klaster k-means, & outlier (Tahap 5). |
| `GET` | `/api/v1/results/{job_id}/kinship` | Matriks kekerabatan Centered Kinship dan estimasi kekerabatan KING eksak (Tahap 5). |
| `GET` | `/api/v1/results/{job_id}/ld-decay` | Kurva peluruhan LD ($r^2$) per bin jarak kb dan estimasi cutoff half-decay (Tahap 5). |
| `GET` | `/api/v1/results/{job_id}/model-recommendation` | Rekomendasi model statistik GLM vs LMM beserta alasannya (Tahap 5). |
| `GET` | `/api/v1/results/{job_id}/thresholds` | Nilai ambang signifikansi Bonferroni, FDR, LOD, dan Suggestive (Tahap 8). |
| `GET` | `/api/v1/results/{job_id}/lead-snps` | Daftar Lead SNPs signifikan independen dan `suggestive_peaks` pasca-clumping (Tahap 8, read-only). |
| `GET` | `/api/v1/results/{job_id}/peak-comparison` | Perbandingan lokus puncak antara Single-Locus vs Multi-Locus (Tahap 7 & 8). |
| `GET` | `/api/v1/results/{job_id}/multilocus-results` | Rincian SNP signifikan, `PVE(%)`, `H&B.P.Value`, dan label engine Multi-Locus (Tahap 7). |
| `GET` | `/api/v1/results/{job_id}/candidate-genes` | Hasil pemetaan gen GFF3 gabungan Single+Multi-locus & `Daftar_Gen_Untuk_GO.txt` (Tahap 10). |
| `GET` | `/api/v1/results/{job_id}/go-enrichment` | Hasil uji hipergeometrik Gene Ontology (GO) & tabel GO nyata (Tahap 10). |
| `GET` | `/api/v1/results/{job_id}/primers` | Sekuens flanking 201 bp (FASTA `.fai`) & desain primer `primer3-py` (Tahap 10). |
| `GET` | `/api/v1/results/{job_id}/export` | Unduhan arsip `.zip` streaming bundel artefak hasil analisis job. |
| `GET` | `/api/v1/results/{job_id}/report` | Unduhan laporan ringkasan eksekutif ilmiah dalam format `.pdf`. |

---

## ⚙️ 6. Prasyarat & Konfigurasi Environment (`.env`)

### Prasyarat
- **Python 3.11 – 3.13**
- **Docker Desktop** + Docker Compose v2 *(direkomendasikan untuk eksekusi pipeline CLI lengkap)*

### Konfigurasi File `backend/.env`

Salin template `backend/.env.example` menjadi `backend/.env`:

```powershell
Copy-Item .env.example .env
```

Variabel inti (rujuk `backend/.env.example` untuk daftar lengkap):

```dotenv
ENV=development
ALLOW_SQLITE_FALLBACK=true
DATABASE_URL=postgresql+psycopg2://gwas_user:change-this-postgres-password@db:5432/gwas_db

CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
STORAGE_ROOT=./data/storage

# Wajib >= 32 karakter acak non-placeholder di ENV=production:
# python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET_KEY=change-this-jwt-secret-key-in-production-min-32-chars
SECRET_KEY=change-this-secret-key-in-production-min-32-chars
ACCESS_TOKEN_EXPIRE_MINUTES=60

PLINK_BINARY_PATH=plink
PLINK2_BINARY_PATH=plink2
BCFTOOLS_BINARY_PATH=bcftools
GEMMA_BINARY_PATH=gemma
IMPUTATION_BINARY_PATH=beagle
BEAGLE_JAR_PATH=/opt/tools/beagle.jar
R_BINARY_PATH=Rscript

BEAGLE_MAX_MEMORY=
ALLOW_PYTHON_MULTILOCUS=true
ALLOW_SYNTHETIC_ANNOTATION=false
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

2. **Jalankan Server FastAPI** (pastikan `ALLOW_SQLITE_FALLBACK=true` di `.env` jika PostgreSQL tidak berjalan):

   ```powershell
   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

3. **Jalankan Celery Worker *(Jika Menggunakan Redis Eksternal)***:

   ```powershell
   celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2 -Q celery,normalization,qc,imputation,gwas
   ```

---

### Opsi B: Menjalankan Stack Penuh dengan Docker Compose

File konfigurasi Compose berada di `infra/docker-compose.yml` dan `infra/docker-compose.prod.yml`.

```powershell
cd infra
docker compose build backend celery_worker celery_beat flower
docker compose up -d
docker compose logs -f backend celery_worker
```

| Service Dev (`infra/docker-compose.yml`) | Fungsi | Port Host |
| :--- | :--- | :---: |
| `backend` | FastAPI REST API + `alembic upgrade head` | `127.0.0.1:8000` |
| `db` | PostgreSQL 15 Metadata Database | `127.0.0.1:5432` |
| `redis` | Broker & Result Backend Celery | `127.0.0.1:6379` |
| `celery_worker` | Worker 10 Tahap (`-Q celery,normalization,qc,imputation,gwas`) | Internal |
| `celery_beat` | Scheduler retensi harian (`purge_expired_records`) | Internal |
| `flower` | Dashboard monitoring Celery (`celery,tasks`) | `127.0.0.1:5555` |

---

## 🗄️ 8. Database, Model SQLAlchemy, & Migrasi Alembic

Model ORM didefinisikan di [`app/db/engine/models.py`](app/db/engine/models.py) (`User`, `Dataset`, `AnalysisJob`, `Result`, `ReferencePanel`) beserta versi Alembic di `backend/alembic/versions/`.

- Pada `ENV=production` atau database PostgreSQL, container `backend` menjalankan `alembic upgrade head` saat startup; `Base.metadata.create_all` di `app/main.py` hanya aktif untuk dialek SQLite lokal.
- Jika melakukan upgrade atas database PostgreSQL lama yang sebelumnya dibuat dengan `create_all` (belum memiliki tabel `alembic_version`), jalankan `alembic stamp head` sekali sebelum `alembic upgrade head`.

---

## 🧪 9. Pengujian Otomatis (Unit & End-to-End Tests)

Backend dilengkapi **31 modul uji `pytest`** (`242 passed, 5 skipped` pada host Windows; `247 passed, 0 skipped` pada reproduksi CI Linux non-root di container Docker `gwas-backend-gapit`):

```powershell
cd backend
python -m flake8 app scripts tests --ignore=E501,E402,W503 --max-line-length=120
python -m pytest tests -q -p no:cacheprovider
```

---

## 🚢 10. Production Deployment & Troubleshooting

### Production Deployment (`infra/docker-compose.prod.yml`)

```powershell
$env:POSTGRES_PASSWORD = "gunakan-password-kuat-di-secret-manager"
$env:JWT_SECRET_KEY = "string-acak-minimal-32-karakter-kriptografis"
docker compose -f infra/docker-compose.prod.yml build
docker compose -f infra/docker-compose.prod.yml up -d
```

> **Build R/GAPIT (`WITH_GAPIT`) & Memori:** Image `backend/Dockerfile` menginstal R + GAPIT3 (`WITH_GAPIT=true`) serta `plink2` dengan verifikasi SHA-256. Untuk build cepat tanpa R/GAPIT, gunakan `docker build --build-arg WITH_GAPIT=false backend/`; tahap Multi-Locus akan menggunakan Python Native Engine bila `ALLOW_PYTHON_MULTILOCUS=true`.

### Troubleshooting Umum

1. **Job Berhenti di Tengah Tahap (Gagal / Dibatalkan)**: Gunakan tombol **↻ Lanjutkan Job (Retry)** di UI atau `POST /api/v1/jobs/{job_id}/retry` untuk melanjutkan langsung dari tahap terhenti.
2. **Error 503 di `/health`**: Periksa koneksi `DATABASE_URL` PostgreSQL; pada mode produksi (`ALLOW_SQLITE_FALLBACK=false`), API tidak akan fallback ke SQLite lokal.
3. **FASTA Referensi Kontig NCBI Tidak Cocok dengan VCF**: Gunakan [`scripts/prepare_reference_fasta.py`](scripts/prepare_reference_fasta.py) sebelum mengunggah FASTA referensi ke dataset.
4. **File VCF atau Artefak Hasil Tidak Ditemukan Antar-Container**: Pastikan `backend`, `celery_worker`, dan `celery_beat` berbagi volume `vcf_storage` (`/data/storage`) dan `STORAGE_ROOT` yang identik.
