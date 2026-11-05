# 📂 Struktur Proyek & Panduan Komponen (`gwas-platform`)

Dokumen ini menjelaskan struktur repositori **GWAS Web Platform** secara menyeluruh, mencakup **kegunaan, fungsi spesifik, interaksi antar-modul, serta arsitektur keamanan dan keandalan sistem** untuk setiap file di lapisan *Backend*, *Workers*, *Frontend*, *Infrastructure*, dan *Documentation*.

---

## 🌳 1. Pohon Direktori Lengkap

```text
gwas-platform/
├── .env.example                            # Template variabel lingkungan tingkat root untuk Docker Compose
├── .gitignore                              # Pengecualian file Git (node_modules, venv, storage, file output, .env)
├── README.md                               # Dokumentasi utama proyek: Quick Start, Tabel Commands, Arsitektur, & ADRs
├── PROJECT_STRUCTURE.md                    # Dokumentasi struktur direktori & fungsi setiap file (dokumen ini)
├── Pipeline_Gwas.md                        # Panduan ilmiah rujukan tahapan GWAS lengkap (CLI bcftools, PLINK, GEMMA, R)
├── sampelkecil.vcf                         # Berkas contoh VCF kecil untuk pengujian cepat & verifikasi pipeline
│
├── docs/                                   # Dokumentasi Desain Sistem & Rekaman Keputusan Arsitektur
│   ├── design.md                           # Dokumen Desain Sistem (System Design Document) & spesifikasi teknis
│   ├── PLAN_INTEGRASI_PIPELINE_GWAS.md     # Rencana kerja & milestone integrasi 10 fase GWAS
│   └── decisions/                          # Architecture Decision Records (ADRs) - Dokumen Keputusan Formal
│       ├── README.md                       # Indeks dan ringkasan seluruh ADR
│       ├── ADR-001-database-strategy-and-orm.md                  # Relasional Ganda: PostgreSQL & SQLite Fallback
│       ├── ADR-002-authentication-and-data-isolation.md          # Autentikasi JWT Split-Token & Isolasi Data
│       ├── ADR-003-task-orchestration-and-pipeline-engine.md     # Asynchronous Task Queue Celery + Redis
│       ├── ADR-004-bioinformatics-execution-engine.md            # Native CLI Subprocess Wrapper & Fail-Fast
│       ├── ADR-005-variant-data-storage-and-caching.md           # Caching Data Varian Apache Parquet Snappy
│       ├── ADR-006-real-time-streaming-and-state-synchronization.md # Adaptive Polling & Real-time Log Streaming
│       ├── ADR-007-data-validation-and-serialization.md          # Skema Validasi Pydantic v2 Rust Core
│       ├── ADR-008-frontend-big-data-plotting-engine.md          # Visualisasi Big Data Manhattan Canvas 2D
│       └── ADR-009-frontend-architecture-and-build-toolchain.md  # Vite + React 18 SPA Toolchain
│
├── infra/                                  # Konfigurasi Infrastruktur & Orkestrasi Kontainer
│   ├── docker-compose.yml                  # Compose Development (Backend, PostgreSQL, Redis, Celery Worker)
│   ├── docker-compose.prod.yml             # Compose Production dengan Reverse Proxy Nginx & volume isolasi
│   └── nginx/
│       └── nginx.conf                      # Konfigurasi Nginx: Reverse Proxy, Static Caching, & 100MB Buffer Upload
│
├── backend/                                # Layanan Backend Python (FastAPI, SQLAlchemy, Celery Workers)
│   ├── Dockerfile                          # Definisi image Docker multi-stage (FastAPI, Celery, & CLI BioTools)
│   ├── requirements.txt                    # Dependensi Python (FastAPI, NumPy, SciPy, Pandas, PyArrow, Celery)
│   ├── alembic.ini                         # Konfigurasi migrasi skema database relasional Alembic
│   ├── .env.example                        # Template konfigurasi variabel lingkungan backend lengkap
│   │
│   ├── alembic/                            # Modul migrasi skema database relasional
│   │   ├── env.py                          # Konfigurasi runtime migrasi database
│   │   └── versions/
│   │       ├── 0001_initial_schema.py      # Migrasi tabel users, datasets, analysis_jobs, results
│   │       └── c6d8e9f01234_add_gwas_pipeline_phases_and_metadata.py # Tambahan metadata 10 fase & tipe hasil
│   │
│   ├── app/                                # Paket aplikasi utama Backend
│   │   ├── main.py                         # Titik masuk FastAPI, CORS, auto-init database/SQLite, & routing
│   │   │
│   │   ├── core/                           # Inti konfigurasi, koneksi database, & keamanan kriptografi
│   │   │   ├── config.py                   # Pydantic Settings untuk membaca env variables & path storage
│   │   │   ├── database.py                 # SQLAlchemy Engine, SessionLocal, & tipe data UUID/JSON lintas-dialek
│   │   │   ├── security.py                 # Hashing password (bcrypt), JWT generation/verification, & cookie setter
│   │   │   ├── logging_config.py           # Standardisasi format log console/container
│   │   │   └── resource_limits.py          # Pembatas memori OS (RLIMIT_AS) & pembangun opsi JVM untuk Beagle
│   │   │
│   │   ├── db/
│   │   │   ├── base.py                     # Deklarasi Base SQLAlchemy ORM
│   │   │   └── engine/
│   │   │       ├── database.py             # Instansiasi engine dan session provider
│   │   │       └── models.py               # Model relasional: User, Dataset, AnalysisJob, Result, ReferencePanel
│   │   │
│   │   ├── schemas/
│   │   │   └── pipeline.py                 # Skema Pydantic v2 untuk request/response seluruh 10 fase GWAS
│   │   │
│   │   ├── api/routes/                     # Pengendali REST API Endpoints (/api/v1)
│   │   │   ├── auth.py                     # Registrasi user, login, rotasi refresh token, & logout
│   │   │   ├── upload.py                   # Upload VCF chunked 5MB, SHA-256 validator, fenotipe, & GFF3
│   │   │   ├── capabilities.py             # Deteksi otomatis ketersediaan binary tool CLI di server host
│   │   │   ├── pipeline.py                 # Endpoint pemicu eksekusi pipeline GWAS (HTTP 202 Accepted)
│   │   │   ├── jobs.py                     # Status polling, pembatalan job, penghapusan, log, & file browser
│   │   │   ├── results.py                  # Endpoint penyedia data visualisasi seluruh fase hasil GWAS
│   │   │   ├── export.py                   # Generator dan streaming unduhan arsip .zip bundel artefak job
│   │   │   ├── report.py                   # Pembuatan dan unduhan dokumen laporan resmi berformat PDF
│   │   │   └── reference_panels.py         # Katalog panel referensi imputasi (1000 Genomes GRCh38/GRCh37)
│   │   │
│   │   ├── services/                       # Logika bisnis domain & algoritma bioinformatika/ilmiah
│   │   │   ├── phenotype_service.py        # Fase 1: Rekonsiliasi sampel, histogram Freedman-Diaconis, boxplot Tukey
│   │   │   ├── qc_service.py               # Fase 2: Pembangun perintah PLINK, filter inbred/outcross, metrik komparasi riil
│   │   │   ├── structure_service.py        # Fase 3+4D: LD pruning, PCA 10 PCs, Centered Kinship K, KING, & LD decay
│   │   │   ├── assoc_normalizer.py         # Fase 4A/4B: Normalisasi hasil GEMMA/PLINK2 ke TSV kanonikal 10 kolom
│   │   │   ├── postgwas_service.py         # Fase 4E/4F: Lambda GC tanpa batas, ambang batas dinamis, & clumping Lead SNPs
│   │   │   ├── annotation_service.py       # Fase 5: Normalisasi kromosom, streaming parser GFF3, klasifikasi biologis gen
│   │   │   ├── go_enrichment_service.py    # Fase 5.3: Ekstraksi gen ortholog & format pengayaan Gene Ontology
│   │   │   ├── primer_service.py           # Fase 5.4/6-8: Sekuens flanking 201 bp & desain otomatis primer PCR Primer3
│   │   │   ├── manhattan_parser.py         # Parsing streaming hasil asosiasi & caching ke Apache Parquet Snappy
│   │   │   ├── export_service.py           # Pengemasan streaming ZIP artefak analisis tanpa membebani memori
│   │   │   ├── report_generator.py         # Pembuat laporan PDF ringkasan eksekutif publikasi ilmiah
│   │   │   ├── storage_cleanup_service.py  # Pembersih direktori kedaluwarsa sesuai kebijakan retensi data
│   │   │   └── upload_service.py           # Manajemen chunk biner, perakitan file, & validasi checksum SHA-256
│   │   │
│   │   └── tasks/                          # Lapisan eksekusi asinkron Celery Tasks & Chaining
│   │       ├── celery_app.py               # Inisialisasi aplikasi Celery & konfigurasi antrean/logger
│   │       ├── _common.py                  # Utilitas task: pembatalan job, penandaan status, resolver tool binary
│   │       ├── pipeline_orchestrator.py    # Perangkai rantai eksekusi 10 tahap Celery chain & sync runner
│   │       ├── inspection_tasks.py         # Task Fase 1: Rekonsiliasi sampel & validasi distribusi fenotipe
│   │       ├── normalization_tasks.py      # Task Fase 1B: Validasi, sort, filter posisi >2.14Gb, & indeks VCF (bcftools)
│   │       ├── qc_tasks.py                 # Task Fase 2: Quality Control (PLINK 2 / 1.9 dengan organism mode)
│   │       ├── imputation_tasks.py         # Task Imputasi: Beagle 5.4 JVM (none / reference / reference_free)
│   │       ├── structure_tasks.py          # Task Fase 3+4D: Analisis struktur PCA, matriks Kinship K, & peluruhan LD
│   │       ├── gemma_tasks.py              # Task Fase 4A/4B: Linear Mixed Model GWAS (GEMMA + kovariat PC)
│   │       ├── multilocus_tasks.py         # Task Fase 4C/4H: Multi-Locus GWAS (FarmCPU/BLINK via R) & komparasi puncak
│   │       ├── postgwas_tasks.py           # Task Fase 4E/4F: Lambda GC, perhitungan ambang dinamis, & clumping
│   │       ├── visualization_tasks.py      # Task Fase 4G: Pra-pemrosesan cache kolom Parquet untuk plotting instan
│   │       ├── annotation_tasks.py         # Task Fase 5: Anotasi gen kandidat GFF3, daftar GO, & desain Primer3
│   │       ├── cleanup_tasks.py            # Task pembersihan staging & intermediate files per job
│   │       ├── retention_tasks.py          # Periodic cron Celery untuk penegakan kebijakan retensi penyimpanan
│   │       └── upload_tasks.py             # Task validasi asinkron berkas genotipe pasca-upload lengkap
│   │
│   └── tests/                              # Suite Pengujian Otomatis Komprehensif (55 Passed)
│       ├── fixtures/                       # Generator dataset uji deterministik (toy_gwas, toy_pheno, toy_gff3)
│       ├── conftest.py                     # Fixture pytest: in-memory DB, client FastAPI, mocking Celery
│       ├── test_annotation_service.py      # Uji streaming GFF3 parser, normalisasi kromosom, & pemetaan gen
│       ├── test_assoc_normalizer.py        # Uji normalisasi asosiasi GEMMA/PLINK2 ke format TSV kanonikal
│       ├── test_go_and_primers.py          # Uji isolasi ID gen untuk GO enrichment & desain primer PCR Primer3
│       ├── test_imputation_tasks.py        # Uji eksekusi Beagle 5.4, parsing argumen, & alokasi heap RAM
│       ├── test_jobs_api.py                # Uji endpoint manajemen status job, log streaming, & file browser
│       ├── test_m0_foundation.py           # Uji fungsi helper bersama, pembatalan task, & formula Lambda GC
│       ├── test_m1_models_schemas.py       # Uji integritas skema Pydantic v2 & enum relasional database
│       ├── test_multilocus_models.py       # Uji parsing keluaran model multi-lokus FarmCPU/BLINK
│       ├── test_normalization_indexing.py  # Uji normalisasi VCF, penanganan overflow 32-bit (>2.14Gb), & Tabix
│       ├── test_phenotype_service.py       # Uji rekonsiliasi ID, histogram Freedman-Diaconis, boxplot outlier
│       ├── test_pipeline_e2e.py            # Uji integrasi end-to-end 10 tahap pipeline secara tersinkronisasi
│       ├── test_postgwas_service.py        # Uji kalkulasi unclamped Lambda GC, ambang batas, & LD clumping
│       ├── test_process.py                 # Uji keamanan eksekusi subprocess CLI & streaming error log
│       ├── test_qc_commands.py             # Uji argumen PLINK 2 inbred vs outcross & kalkulasi histogram QC
│       └── test_structure_service.py       # Uji dekomposisi PCA, matriks Centered Kinship K, & kurva LD decay
│
└── frontend/                               # Antarmuka Pengguna Klien React 18 SPA (Vite, HTML5 Canvas)
    ├── package.json                        # Konfigurasi dependensi frontend (React, Vite, ESLint, Jest)
    ├── vite.config.js                      # Konfigurasi bundler Vite, proxy API dev, & server port
    │
    └── src/
        ├── App.jsx                         # Konfigurasi perutean halaman (*routes*) & navigasi aplikasi
        ├── index.css                       # Penataan gaya global (Tailwind / styling utilitas modern)
        │
        ├── api/                            # Layanan komunikasi HTTP dengan Backend API
        │   ├── apiClient.js                # Wrapper fetch dengan penanganan otentikasi & error handling
        │   ├── chunkedUpload.js            # Algoritma pengunggah VCF terpotong (5 MB) dengan SHA-256
        │   ├── gwasApi.js                  # Klien API untuk mengambil data visualisasi dari seluruh tahap
        │   ├── httpClient.js               # Konfigurasi instance Axios dasar
        │   └── jobsApi.js                  # Klien API untuk pemantauan job, download log, & manipulasi file
        │
        ├── context/                        # Penyedia konteks aplikasi global
        │   └── AuthContext.jsx             # Pengelola sesi otentikasi, login, logout, & auto-refresh token
        │
        ├── hooks/                          # Custom React Hooks
        │   ├── useChunkUpload.js           # Hook pengelola progres, jeda, dan resume upload chunked
        │   ├── useGwasResults.js           # Hook pengambil data hasil GWAS Manhattan & QQ Plot
        │   └── useJobPolling.js            # Hook adaptive polling untuk memantau status job secara berkala
        │
        ├── pages/                          # Halaman Utama Aplikasi (*Views*)
        │   ├── GwasUploadPage.jsx          # Formulir unggah berkas & konfigurasi parameter 10 tahap GWAS
        │   ├── JobStatusPage.jsx           # Halaman pemantauan alur kerja aktif, log proses, & pembatalan
        │   ├── LoginPage.jsx               # Halaman masuk (*sign-in*) pengguna
        │   ├── RegisterPage.jsx            # Halaman pendaftaran akun peneliti baru
        │   └── ResultsPage.jsx             # Halaman eksplorasi hasil komprehensif GWAS 7 Tab Interaktif
        │
        ├── components/                     # Komponen Antarmuka & Panel Visualisasi
        │   ├── GwasResultsPanel.jsx        # Wadah utama 7 tab interaktif hasil analisis GWAS
        │   ├── ManhattanPlot.jsx           # Manhattan Plot berbasis HTML5 2D Canvas (Anti-Crash, <5ms)
        │   ├── QQPlot.jsx                  # Quantile-Quantile Plot interaktif dengan garis referensi 1:1
        │   ├── ThresholdSelector.jsx       # Pemilih ambang signifikansi dinamis (Bonferroni, FDR, LOD, dsb.)
        │   ├── LeadSnpTable.jsx            # Tabel Lead SNPs independen hasil clumping dengan paginasi
        │   ├── ModelRecommendationCard.jsx # Kartu rekomendasi pemilihan model statistik GLM vs LMM
        │   ├── PcaScatterPlot.jsx          # Plot sebaran PCA (PC1 vs PC2) dengan pewarnaan klaster
        │   ├── ScreePlot.jsx               # Diagram persentase varians terjelaskan (PVE) per komponen PCA
        │   ├── KinshipHeatmap.jsx          # Heatmap matriks kekerabatan Centered Kinship
        │   ├── LdDecayPlot.jsx             # Kurva peluruhan LD ($r^2$ vs pasangan basa) & garis half-decay
        │   ├── DataInspectionPanel.jsx     # Panel Fase 1: Rekonsiliasi sampel & statistik fenotipe
        │   ├── HistogramChart.jsx          # Visualisasi histogram distribusi fenotipe aturan Freedman-Diaconis
        │   ├── BoxPlotChart.jsx            # Boxplot deteksi outlier Tukey dengan tooltip interaktif
        │   ├── QcComparisonPanel.jsx       # Panel Fase 2: Metrik before/after QC & histogram MAF/missingness
        │   ├── QcComparisonPanel.test.jsx  # Pengujian unit untuk komponen QcComparisonPanel
        │   ├── PeakComparisonTable.jsx     # Tabel komparasi lokus Single-Locus vs Multi-Locus GWAS berpaginasi
        │   ├── CandidateGenesTable.jsx     # Tabel pemetaan gen GFF3 berpaginasi & unduh Daftar_Gen_Untuk_GO
        │   ├── GoEnrichmentPanel.jsx       # Panel pengayaan Gene Ontology dengan visualisasi dotplot & tabel
        │   ├── PrimerDesignPanel.jsx       # Panel desain primer PCR otomatis Primer3 untuk Lead SNPs
        │   ├── Pagination.jsx              # Komponen reusable kontrol navigasi halaman dan ukuran entri
        │   ├── Pagination.test.jsx         # Pengujian unit komprehensif untuk komponen Pagination
        │   ├── JobStatusTracker.jsx        # Indikator visual progres alur 10 tahap secara real-time
        │   ├── JobStatusTracker.test.jsx   # Pengujian unit pelacak status job
        │   ├── JobProcessLogs.jsx          # Penampil log teks per tahap dengan fitur pencarian & auto-refresh
        │   ├── JobFilesPanel.jsx           # Penjelajah berkas job dengan pratinjau teks dan unduhan artefak
        │   ├── WorkspaceExplorer.jsx       # Penjelajah riwayat pekerjaan dan manajemen dataset
        │   ├── VcfUploader.jsx             # Pengunggah VCF terkompresi dengan verifikasi integritas SHA-256
        │   ├── PhenotypeUploader.jsx       # Pengunggah dan pratinjau file fenotipe
        │   ├── AnnotationUploader.jsx      # Pengunggah dan pratinjau file anotasi genom GFF3
        │   ├── UploadProgressBar.jsx       # Bilah progres visual unggahan berkas
        │   └── ResultDashboard.jsx         # Kartu ringkasan metrik & tombol ekspor ZIP / PDF
        │
        └── utils/                          # Utilitas Pembantu Frontend
            ├── canvas.js                   # Utilitas inisialisasi HiDPI canvas dan penghitungan nice-ticks
            ├── manhattan.js                # Algoritma pemetaan koordinat varian & deduplikasi piksel Manhattan
            ├── manhattan.test.js           # Pengujian unit logika pembagian kromosom Manhattan
            └── qqplot.js                   # Utilitas komputasi nilai $p$ teoritis vs teramati pada QQ plot
```

---

## 🔬 2. Hubungan Alur Kerja & Interaksi Antar-Modul

Platform mengimplementasikan prinsip utama **"Inspect $\rightarrow$ Validate $\rightarrow$ Run"**:

1. **Fase Persiapan & Inspeksi Data (Fase 1)**:
   - Pengguna mengunggah VCF genotipe via [`VcfUploader.jsx`](frontend/src/components/VcfUploader.jsx) (chunk 5MB ke [`upload.py`](backend/app/api/routes/upload.py)).
   - Pengguna mengunggah file fenotipe via [`PhenotypeUploader.jsx`](frontend/src/components/PhenotypeUploader.jsx).
   - Layanan [`phenotype_service.py`](backend/app/services/phenotype_service.py) merekonsiliasi kesesuaian ID sampel, menghitung histogram Freedman-Diaconis, mendeteksi outlier Tukey pada [`DataInspectionPanel.jsx`](frontend/src/components/DataInspectionPanel.jsx), serta menghasilkan `keep_ids.txt` dan `pheno_plink.txt`.

2. **Orkestrasi Eksekusi 10 Tahap Asinkron**:
   - Pemicu dikirim via `POST /api/v1/pipeline/{dataset_id}/start` ke [`pipeline_orchestrator.py`](backend/app/tasks/pipeline_orchestrator.py).
   - Rantai tugas Celery dieksekusi secara terurut:
     $$\text{Inspeksi} \rightarrow \text{Normalisasi VCF} \rightarrow \text{QC PLINK} \rightarrow \text{Imputasi} \rightarrow \text{Struktur/Kinship} \rightarrow \text{GEMMA GWAS} \rightarrow \text{Multi-Locus} \rightarrow \text{Post-GWAS} \rightarrow \text{Visualisasi Parquet} \rightarrow \text{Anotasi GFF3/Primer3}$$
   - Jika terjadi kegagalan pada alat bioinformatika CLI, sistem langsung berhenti (*fail-fast*) tanpa fallback Python yang lambat, mencatat log kesalahan secara presisi, dan status job diperbarui ke `FAILED`.

3. **Eksplorasi Hasil & Visualisasi Berkinerja Tinggi**:
   - Frontend [`JobStatusPage.jsx`](frontend/src/pages/JobStatusPage.jsx) memantau progres via adaptive polling ke `GET /api/v1/jobs/{job_id}/status`.
   - Setelah job berstatus `COMPLETED`, peneliti membuka [`ResultsPage.jsx`](frontend/src/pages/ResultsPage.jsx) yang menyajikan 7 tab interaktif:
     - **Tab 1: Manhattan & QQ Plot**: Mengambil data terindeks Parquet melalui `manhattan_parser.py` dan merendernya dalam $<5\text{ ms}$ di atas HTML5 Canvas 2D.
     - **Tab 2: Ambang Batas & Lead SNPs**: Mengatur ambang batas dinamis (Bonferroni, FDR, LOD) dan menavigasi tabel Lead SNPs dengan fitur paginasi.
     - **Tab 3: Struktur Populasi & LD**: Plot PCA, rekomendasi model GLM vs LMM, matriks Kinship, dan kurva peluruhan LD.
     - **Tab 4: Inspeksi & QC**: Analisis distribusi fenotipe dan perbandingan metrik riil before/after QC beserta histogram MAF/missingness.
     - **Tab 5: Multi-Locus GWAS**: Komparasi lokus puncak Single-Locus vs Multi-Locus (FarmCPU & BLINK).
     - **Tab 6: Anotasi Gen GFF3 & GO**: Pemetaan Lead SNPs ke fitur gen dalam jendela LD decay dan daftar gen untuk analisis Gene Ontology.
     - **Tab 7: Flanking & Primer3**: Visualisasi sekuens flanking 201 bp dan desain otomatis pasangan primer PCR menggunakan Primer3.
   - Peneliti dapat mengunduh bundel arsip `.zip` streaming via [`export.py`](backend/app/api/routes/export.py) atau dokumen resmi laporan ilmiah `.pdf` via [`report.py`](backend/app/api/routes/report.py).

---

## 🛡️ 3. Pilar Keamanan & Hardening Sistem (Security Architecture)

Mengacu pada panduan keahlian **`security-and-hardening`** dan standar OWASP, platform menerapkan mekanisme pertahanan mendalam (*defense-in-depth*):

1. **Autentikasi Split-Token & Perlindungan Sesi**:
   - **Access Token**: Berumur pendek (60 menit), disimpan hanya di memori klien React SPA, ditransmisikan via header `Authorization: Bearer <token>`.
   - **Refresh Token**: Berumur panjang (7 hari), disimpan secara aman dalam cookie beratribut `HttpOnly=True`, `SameSite="Lax"`, dan `Secure=True` pada mode produksi (`ENV=production`). Hal ini menutup vektor serangan pencurian token melalui celah *Cross-Site Scripting* (XSS).
   - **Rotasi Token & Pembatalan Instan**: Setiap penggunaan refresh token menerbitkan token baru. Pada saat logout, `refresh_token_version` pada database dinaikkan, secara seketika membatalkan seluruh sesi aktif di semua perangkat.

2. **Validasi Input Ketat & Integritas File di Boundary**:
   - Seluruh payload HTTP divalidasi ketat menggunakan skema **Pydantic v2 (Rust Core)** yang menolak parameter yang tidak dikenal (`extra="ignore"` atau penolakan tipe).
   - Unggahan berkas VCF dipecah menjadi chunk 5 MB dan diverifikasi integritasnya menggunakan *checksum* **SHA-256** sebelum berkas disatukan kembali di direktori staging.

3. **Isolasi Data Multitenant & Pencegahan IDOR (Insecure Direct Object Reference)**:
   - Setiap entitas dataset dan analisis terikat pada `owner_id`.
   - Endpoint pada [`jobs.py`](backend/app/api/routes/jobs.py) dan [`upload.py`](backend/app/api/routes/upload.py) secara eksplisit memeriksa kepemilikan sumber daya: pengguna hanya dapat melihat, memicu, membatalkan, atau menghapus pekerjaan miliknya sendiri.

4. **Eksekusi Perangkat Bioinformatika Aman & Subprocess Isolation**:
   - Perintah ke alat CLI (`bcftools`, `plink2`, `gemma`) dibungkus menggunakan argumen array terpisah (`subprocess.run(["plink2", ...])`), bukan melalui string shell (`shell=False`), sehingga menutup risiko *Shell/Command Injection*.
   - Filter batas koordinat $> 2.14\text{ Gb}$ diterapkan sebelum eksekusi `bcftools sort` untuk mencegah *integer overflow* 32-bit yang dapat memicu crash pada pustaka C.

5. **Pencegahan Path Traversal & Keamanan Filesystem**:
   - Seluruh operasi manipulasi berkas divalidasi agar selalu berada di bawah batas direktori yang diizinkan ([`STORAGE_ROOT`](backend/.env.example)).
   - Setiap job analisis memiliki direktori kerja terisolasi berbasis UUID (`/data/jobs/{job_id}`).

6. **Proteksi Header & Konfigurasi CORS**:
   - CORS dibatasi secara eksplisit pada daftar origin terpercaya ([`CORS_ORIGINS`](backend/.env.example)) tanpa penggunaan wildcard terbuka (`*`), menjaga keamanan transmisi kredensial cookie.
   - Pesan kesalahan otentikasi bersifat generik (*"Email atau password salah"*) untuk mencegah serangan *user enumeration*.
