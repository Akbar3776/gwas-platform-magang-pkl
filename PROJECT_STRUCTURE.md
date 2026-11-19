# 📂 Struktur Proyek & Panduan Komponen (`gwas-platform`)

Dokumen ini menjelaskan struktur repositori **GWAS Web Platform** secara menyeluruh (diselaraskan dengan `git ls-files`), mencakup kegunaan, fungsi spesifik, interaksi antar-modul, serta arsitektur keamanan dan keandalan sistem di lapisan *Backend*, *Workers*, *Frontend*, *Infrastructure*, dan *Documentation*.

---

## 🌳 1. Pohon Direktori Lengkap

```text
gwas-platform/
├── .dockerignore                           # Pengecualian konteks build Docker tingkat root
├── .env.example                            # Template variabel lingkungan tingkat root untuk Docker Compose
├── .github/workflows/test.yml              # Workflow CI GitHub Actions (backend flake8/pytest + frontend lint/jest/build)
├── .gitignore                              # Pengecualian file Git (node_modules, venv, storage, output lokal, .env)
├── README.md                               # Dokumentasi utama proyek: Quick Start, Referensi Perintah, Arsitektur, & ADRs
├── PROJECT_STRUCTURE.md                    # Dokumentasi struktur direktori & fungsi setiap file (dokumen ini)
├── Pipeline_Gwas.md                        # Panduan ilmiah rujukan tahapan GWAS lengkap (CLI bcftools, PLINK, GEMMA, R)
│
├── docs/                                   # Dokumentasi Desain Sistem & Rekaman Keputusan Arsitektur
│   ├── design.md                           # Dokumen Desain Sistem (System Design Document) & spesifikasi teknis
│   ├── PLAN_INTEGRASI_PIPELINE_GWAS.md     # Rencana kerja historis integrasi 10 fase GWAS
│   ├── presentation/                       # Materi presentasi arsitektur & laporan progres HTML
│   │   ├── gwas-architecture.html
│   │   └── progress-report.html
│   └── decisions/                          # Architecture Decision Records (ADRs) - Dokumen Keputusan Formal
│       ├── README.md                       # Indeks dan ringkasan seluruh ADR
│       ├── ADR-001-database-strategy-and-orm.md                  # Relasional Ganda: PostgreSQL & SQLite Fallback Opt-In
│       ├── ADR-002-authentication-and-data-isolation.md          # Autentikasi JWT Split-Token & Isolasi Data
│       ├── ADR-003-task-orchestration-and-pipeline-engine.md     # Asynchronous Task Queue Celery + Redis
│       ├── ADR-004-bioinformatics-execution-engine.md            # Native CLI Subprocess Wrapper & Fail-Fast
│       ├── ADR-005-variant-data-storage-and-caching.md           # Caching Data Varian Apache Parquet Snappy
│       ├── ADR-006-real-time-streaming-and-state-synchronization.md # Adaptive REST Polling & Log Tail Retrieval
│       ├── ADR-007-data-validation-and-serialization.md          # Skema Validasi Pydantic v2 Rust Core
│       ├── ADR-008-frontend-big-data-plotting-engine.md          # Visualisasi Big Data Manhattan Canvas 2D
│       └── ADR-009-frontend-architecture-and-build-toolchain.md  # Vite + React 18 SPA Toolchain
│
├── infra/                                  # Konfigurasi Infrastruktur & Orkestrasi Kontainer
│   ├── docker-compose.yml                  # Compose Development (backend, db, redis, celery_worker, celery_beat, flower)
│   ├── docker-compose.prod.yml             # Compose Production dengan Nginx TLS reverse proxy & volume terisolasi
│   ├── nginx/
│   │   ├── Dockerfile                      # Multi-stage build Frontend React + Nginx 1.27 Alpine
│   │   └── nginx.conf                      # Konfigurasi Nginx: TLSv1.2/1.3, Reverse Proxy, & 100MB Buffer Upload
│   └── scripts/
│       └── gen-selfsigned-cert.sh          # Utilitas sertifikat self-signed untuk uji lokal HTTPS produksi
│
├── backend/                                # Layanan Backend Python (FastAPI, SQLAlchemy, Celery Workers)
│   ├── .dockerignore
│   ├── .env.example                        # Template konfigurasi variabel lingkungan backend lengkap
│   ├── .gitignore
│   ├── Dockerfile                          # Image Docker produksi (Python, PLINK 1.9/2, GEMMA, bcftools, Beagle, R/GAPIT3)
│   ├── IMPUTATION_NOTES.md                 # Catatan alokasi RAM & queue worker imputasi Beagle
│   ├── INTEGRATION_NOTES.md                # Catatan integrasi Auth, CORS, isolasi kepemilikan, & frontend client
│   ├── README.md                           # Dokumentasi teknis khusus Backend & endpoint REST API
│   ├── requirements.txt                    # Dependensi produksi ter-pin (FastAPI, SQLAlchemy, NumPy, SciPy, PyArrow, primer3-py)
│   ├── requirements-test.txt               # Dependensi lint/testing tambahan (pytest, flake8, pip-audit)
│   ├── alembic.ini                         # Konfigurasi migrasi skema database relasional Alembic
│   │
│   ├── alembic/                            # Modul migrasi skema database relasional
│   │   ├── README
│   │   ├── env.py                          # Konfigurasi runtime migrasi database
│   │   ├── script.py.mako                  # Template skrip revisi Alembic
│   │   └── versions/
│   │       ├── b5f7c68dd45f_create_users_table.py                # Skema awal users, datasets, analysis_jobs, results, reference_panels
│   │       ├── c6d8e9f01234_add_gwas_pipeline_phases_and_metadata.py # Tambahan kolom & metadata 10 fase GWAS
│   │       └── d7e9f1a23456_fix_enum_values.py                   # Penyelarasan nama enum PostgreSQL & GWAS_NORMALIZED_TSV
│   │
│   ├── scripts/
│   │   └── prepare_reference_fasta.py      # CLI streaming O(1) RAM rename/merge/re-wrap FASTA referensi & cek ##contig VCF
│   │
│   ├── app/                                # Paket aplikasi utama Backend
│   │   ├── main.py                         # Titik masuk FastAPI, CORS, auto-init SQLite dev, /health, & registrasi 9 router
│   │   │
│   │   ├── core/                           # Konfigurasi, logging, limit memori, & keamanan kriptografi
│   │   │   ├── config.py                   # Pydantic Settings untuk membaca env variables & path storage
│   │   │   ├── logging_config.py           # Standardisasi format log terstruktur stdout container
│   │   │   ├── resource_limits.py          # Pembatas memori OS (RLIMIT_AS) & kalkulasi 75% heap JVM Beagle
│   │   │   └── security.py                 # Hashing bcrypt, JWT PyJWT, rotasi refresh cookie, & require_job_owner/require_admin
│   │   │
│   │   ├── db/
│   │   │   ├── base.py                     # DeclarativeBase SQLAlchemy 2.0
│   │   │   └── engine/
│   │   │       ├── database.py             # Instansiasi engine PostgreSQL (+ opt-in ALLOW_SQLITE_FALLBACK)
│   │   │       └── models.py               # Model ORM: User, Dataset, AnalysisJob, Result, ReferencePanel
│   │   │
│   │   ├── schemas/                        # Kontrak validasi request & response Pydantic v2
│   │   │   ├── jobs.py                     # Skema QCJobRequest, JobStatusResponse, & StepLogEntry
│   │   │   ├── pipeline.py                 # Skema FullPipelineRequest & validasi silang 10 fase GWAS
│   │   │   ├── results.py                  # Skema kolumnar ManhattanDataResponse
│   │   │   └── upload.py                   # Skema chunked upload + sanitasi nama file & pola SHA-256
│   │   │
│   │   ├── api/routes/                     # Pengendali REST API Endpoints (/api/v1)
│   │   │   ├── auth.py                     # Registrasi user, login, rotasi refresh token, logout, & /me
│   │   │   ├── capabilities.py             # Deteksi otomatis ketersediaan CLI bioinformatika & GAPIT di server
│   │   │   ├── export.py                   # Generator streaming arsip .zip bundel artefak hasil job
│   │   │   ├── jobs.py                     # Daftar job, status, log, file explorer, cancel, retry/resume, delete
│   │   │   ├── pipeline.py                 # Endpoint pemicu pipeline 10 tahap (HTTP 202 Accepted)
│   │   │   ├── reference_panels.py         # Katalog panel referensi imputasi aktif
│   │   │   ├── report.py                   # Pembuatan & unduhan laporan PDF status dinamik (WeasyPrint / fallback PDF)
│   │   │   ├── results.py                  # 14 endpoint hasil visualisasi, GO enrichment nyata, & primer3-py
│   │   │   └── upload.py                   # Chunked upload VCF/FASTA, fenotipe, GFF3, pemetaan GO TSV/GAF, & inspeksi
│   │   │
│   │   ├── services/                       # Logika bisnis domain & algoritma bioinformatika/ilmiah
│   │   │   ├── annotation_service.py       # Fase 5: Streaming parser GFF3, normalisasi chr, & pemetaan gen kandidat
│   │   │   ├── assoc_normalizer.py         # Fase 4A/4B: Normalisasi GEMMA/PLINK2 ke TSV kanonikal 10 kolom (inkl. n `.fam`)
│   │   │   ├── export_service.py           # Pengemasan streaming ZIP artefak hasil analisis tanpa muat penuh ke RAM
│   │   │   ├── go_enrichment_service.py    # Fase 5.3: Parser GAF/TSV/GFF3 & uji hipergeometrik GO nyata (opt-in sintetik)
│   │   │   ├── manhattan_parser.py         # Streaming chunked & Apache Parquet Snappy + jaminan 100% Lead SNPs
│   │   │   ├── phenotype_service.py        # Fase 1: Rekonsiliasi sampel, histogram Freedman-Diaconis, boxplot Tukey
│   │   │   ├── postgwas_service.py         # Fase 4E/4F: Lambda GC tanpa clamping, threshold dinamis, & clumping O(N log N)
│   │   │   ├── primer_service.py           # Fase 5.4/6-8: Pengindeksan .fai streaming, flanking 201 bp, & desain primer3-py
│   │   │   ├── qc_service.py               # Fase 2: Pembangun perintah PLINK 2/1.9 (inbred/outcross) & metrik QC
│   │   │   ├── report_generator.py         # Pembuat laporan ilmiah PDF (WeasyPrint + status per tahap)
│   │   │   ├── structure_service.py        # Fase 3+4D: LD pruning PLINK, SVD PCA, Kinship K, KING eksak, & LD decay
│   │   │   └── upload_service.py           # Manajemen chunk biner, perakitan atomik, & checksum SHA-256
│   │   │
│   │   ├── tasks/                          # Lapisan eksekusi asinkron Celery Tasks & Chaining
│   │   │   ├── celery_app.py               # Konfigurasi Celery, Beat retention schedule, local fallback, & hook cleanup
│   │   │   ├── _common.py                  # Helper pembatalan job, `chr_label`, proteksi storage, & `which_tool`
│   │   │   ├── pipeline_orchestrator.py    # Perangkai 10 tahap Celery chain, revoke multi-task, & Smart Retry/Resume
│   │   │   ├── annotation_tasks.py         # Task Fase 5: GFF3, daftar GO gen tervalidasi, GO enrichment, & Primer3
│   │   │   ├── gemma_tasks.py              # Task Fase 4A/4B: GEMMA LMM/GLM fail-fast & streaming count
│   │   │   ├── imputation_tasks.py         # Task Fase 4: Beagle 5.4 JVM dengan pra-filter 32-bit/single-marker
│   │   │   ├── inspection_tasks.py         # Task Fase 1: Rekonsiliasi ID duplikat/hilang & statistik fenotipe
│   │   │   ├── multilocus_tasks.py         # Task Fase 4C/4H: FarmCPU/BLINK/MLMM via GAPIT3 atau Python Native Engine
│   │   │   ├── normalization_tasks.py      # Task Fase 1B: Normalisasi bcftools, filter >2.10 Gb, & indeks TBI/CSI
│   │   │   ├── postgwas_tasks.py           # Task Fase 4E/4F: Lambda GC, threshold dinamis, clumping & suggestive peaks
│   │   │   ├── qc_tasks.py                 # Task Fase 2: Quality Control PLINK 2 / 1.9 fail-fast
│   │   │   ├── retention_tasks.py          # Periodic cron Celery & guard anti-wipe untuk retensi storage
│   │   │   ├── structure_tasks.py          # Task Fase 3+4D: LD pruning, PCA (`n_pc_covariates`), Kinship, KING, & LD decay
│   │   │   ├── upload_tasks.py             # Perakitan & verifikasi SHA-256 VCF serta pengindeksan .fai FASTA referensi
│   │   │   └── visualization_tasks.py      # Task Fase 4G: Pembuatan cache Apache Parquet utama & per-model multi-locus
│   │   │
│   │   ├── templates/
│   │   │   ├── gapit_runner.R              # Skrip R GAPIT3 untuk FarmCPU, BLINK, dan MLMM
│   │   │   └── report_template.html        # Template HTML Jinja2 laporan ringkasan eksekutif PDF
│   │   │
│   │   └── utils/
│   │       └── process.py                  # Eksekutor subprocess CLI aman (`shell=False`, process group kill, & log tail)
│   │
│   └── tests/                              # Suite Pengujian Otomatis Komprehensif (28 modul uji pytest)
│       ├── conftest.py                     # Fixture pytest: DB lokal, in-process DirectTestClient, & isolasi Env
│       ├── fixtures/                       # Generator dataset uji deterministik & contoh TSV pemetaan GO
│       ├── test_access_control.py          # Uji isolasi kepemilikan dataset/job (IDOR) & admin guard
│       ├── test_annotation_service.py      # Uji streaming GFF3 parser, normalisasi chr, & zero-match fail-fast
│       ├── test_assoc_normalizer.py        # Uji normalisasi GEMMA/PLINK2 & pemetaan sample count `n`
│       ├── test_cancellation.py            # Uji pencabutan seluruh rantai task Celery saat cancel/delete
│       ├── test_celery_routing.py          # Uji konsistensi queue Celery vs docker-compose dev/prod
│       ├── test_database_engine.py         # Uji PostgreSQL default & opt-in ALLOW_SQLITE_FALLBACK
│       ├── test_gapit_integration.py       # Uji integrasi R GAPIT3 nyata & fallback Python Native
│       ├── test_gemma_tasks.py             # Uji fail-fast GEMMA CLI & pembacaan log streaming
│       ├── test_go_and_primers.py          # Uji GO TSV/GAF/Ontology_term, indeks .fai streaming, & primer3-py
│       ├── test_imputation_tasks.py        # Uji argumen Beagle, filter overflow/single-marker, & heap JVM
│       ├── test_job_storage_cleanup.py     # Uji guard retensi anti-wipe, staging sweep, & post-task cleanup
│       ├── test_jobs_api.py                # Uji endpoint job, QC job_id kwarg, Smart Retry/Resume, & hapus job
│       ├── test_m0_foundation.py           # Uji helper bersama, pembatalan, & Lambda GC tanpa clamping
│       ├── test_m1_models_schemas.py       # Uji integritas skema Pydantic v2 & enum relasional Alembic
│       ├── test_manhattan_chr.py           # Uji label kromosom non-numerik & retensi 100% Lead SNPs di Parquet
│       ├── test_multilocus_models.py       # Uji FarmCPU/BLINK/MLMM PVE(%), FDR, & label engine
│       ├── test_normalization_indexing.py  # Uji filter overflow 32-bit (>2.10 Gb), atomic write, & indeks TBI/CSI
│       ├── test_phenotype_service.py       # Uji rekonsiliasi ID, deteksi duplikat, & Freedman-Diaconis
│       ├── test_pipeline_e2e.py            # Uji integrasi end-to-end 10 tahap pipeline tersinkronisasi
│       ├── test_pipeline_paths.py          # Uji discover_resume_state & artifact guard multilocus/imputation
│       ├── test_postgwas_service.py        # Uji Lambda GC, threshold dinamis, p1 signifikan, & suggestive peaks
│       ├── test_prepare_reference_fasta.py # Uji CLI prepare_reference_fasta.py (split chr7, TSV map, --vcf, RAM)
│       ├── test_process.py                 # Uji keamanan subprocess CLI & streaming tail log
│       ├── test_qc_commands.py             # Uji argumen PLINK 2/1.9 inbred vs outcross & histogram QC
│       ├── test_security_config.py         # Uji PyJWT, resolve_jwt_secret prod, & pinned requirements
│       ├── test_structure_service.py       # Uji LD pruning, PCA, Kinship, rumus KING eksak, & LD decay
│       ├── test_upload_security.py         # Uji validasi chunked upload, SHA-256, & tolak dataset terhapus
│       └── test_which_tool.py              # Uji isolasi lookup `plink2` vs `plink` & biner sistem
│
└── frontend/                               # Antarmuka Pengguna Klien React 18 SPA (Vite, HTML5 Canvas)
    ├── .eslintrc.cjs                       # Aturan ESLint nol-warning untuk React/Hooks
    ├── .gitignore
    ├── babel.config.cjs                    # Konfigurasi transformasi Babel untuk Jest
    ├── index.html                          # HTML root Vite
    ├── jest.config.cjs                     # Konfigurasi Jest + jsdom
    ├── package.json                        # Dependensi frontend (React 18, React Router v6, Axios, Vite, Jest)
    ├── package-lock.json
    ├── vite.config.js                      # Konfigurasi bundler Vite & proxy API dev
    │
    └── src/
        ├── App.jsx                         # Perutean lazy-loaded terproteksi, 404 fallback, judul dokumen, & brand link
        ├── App.test.jsx                    # Pengujian routing, 404, judul halaman, brand link, & status sesi
        ├── index.css                       # Penataan gaya global, gutter responsif 16px, & kelas aksesibilitas
        ├── main.jsx                        # Entry root React + ErrorBoundary + BrowserRouter (Router v7 future flags)
        │
        ├── constants/
        │   └── pipelineStages.js           # Sumber tunggal metadata 10 tahap pipeline & helper formatBytes
        │
        ├── api/                            # Layanan komunikasi HTTP dengan Backend API
        │   ├── apiClient.js                # Wrapper fetch & single-flight mutex /auth/refresh + token di memori
        │   ├── apiClient.test.js           # Uji 14 request 401 paralel hanya memicu 1 /auth/refresh
        │   ├── formatApiError.js           # Normalizer error FastAPI (string/array 422/objek -> string aman)
        │   ├── formatApiError.test.js      # Pengujian formatApiError
        │   ├── gwasApi.js                  # Klien API endpoint hasil 7 tab via httpClient terpusat
        │   ├── httpClient.js               # Instance Axios tunggal berbagi interceptor refresh 401 dengan apiClient
        │   └── jobsApi.js                  # Klien API upload fenotipe/GFF3/GO, capabilities, & kontrol job
        │
        ├── context/                        # Penyedia konteks aplikasi global
        │   ├── AuthContext.jsx             # Pengelola sesi autentikasi split-token, role status, & ProtectedRoute
        │   └── AuthContext.test.jsx        # Uji ketahanan logout & status sesi
        │
        ├── hooks/                          # Custom React Hooks
        │   ├── useChunkUpload.js           # Hook upload chunked 10 MiB + SHA-256 bertahap, cancel, & unmount cleanup
        │   ├── useChunkUpload.test.js      # Uji cancel IDLE, anti-loop ganda, & unmount safety
        │   ├── useGwasResults.js           # Hook pengambil data hasil Manhattan/QQ dengan guard jobId basi
        │   ├── useGwasResults.test.js      # Uji stale guard saat jobId berganti
        │   ├── useJobPolling.js            # Hook adaptive polling status job dengan exponential backoff
        │   ├── useJobPolling.test.js       # Uji terminasi polling & toleransi gangguan transien
        │   ├── useResource.js              # Hook generik loading/error/data per-tab hasil
        │   └── useResource.test.js         # Uji perilaku useResource saat status 409/error
        │
        ├── pages/                          # Halaman Utama Aplikasi (*Views*)
        │   ├── AuthPages.test.jsx          # Uji aksesibilitas form auth & pesan validasi Bahasa Indonesia
        │   ├── GwasUploadPage.jsx          # Wizard konfigurasi 10 tahap, deteksi capabilities, & uploader dataset
        │   ├── GwasUploadPage.test.jsx     # Uji integrasi capabilities & label Field aksesibel
        │   ├── JobStatusPage.jsx           # Halaman pemantauan alur kerja aktif, log, file, & kontrol job
        │   ├── LoginPage.jsx               # Halaman masuk (*sign-in*) dengan validasi Bahasa Indonesia
        │   ├── NotFoundPage.jsx            # Halaman 404 untuk URL tak dikenal
        │   ├── RegisterPage.jsx            # Halaman pendaftaran akun peneliti dengan validasi Bahasa Indonesia
        │   └── ResultsPage.jsx             # Halaman eksplorasi hasil komprehensif GWAS 7 Tab Interaktif
        │
        ├── components/                     # Komponen Antarmuka, Visualisasi, & Pengujian Unit
        │   ├── AnnotationPanels.test.jsx   # Uji banner data simulasi vs data nyata GO & Primer3
        │   ├── AnnotationUploader.jsx      # Pengunggah file anotasi GFF3 aksesibel keyboard
        │   ├── BoxPlotChart.jsx            # Boxplot deteksi outlier Tukey (+ role="img" aria-label)
        │   ├── CandidateGenesTable.jsx     # Tabel pemetaan gen GFF3 berpaginasi & ekspor CSV aman injeksi
        │   ├── ChartsAccessibilityA.test.jsx & ChartsAccessibilityB.test.jsx # Uji teks alternatif 8 grafik canvas
        │   ├── DataInspectionPanel.jsx     # Panel Fase 1: Rekonsiliasi sampel & statistik fenotipe
        │   ├── ErrorBoundary.jsx           # Penangkap error render global dengan tombol muat ulang
        │   ├── ErrorBoundary.test.jsx      # Uji fallback ErrorBoundary & simulasi error 422 array
        │   ├── GoEnrichmentPanel.jsx       # Dotplot & tabel Gene Ontology nyata (atau pesan unavailable)
        │   ├── GoMappingUploader.jsx       # Pengunggah file pemetaan GO (.tsv/.txt/.gaf/.gaf.gz)
        │   ├── GwasResultsPanel.jsx        # Wadah 7 tab hasil dengan isolasi error per-tab
        │   ├── GwasResultsPanel.test.jsx   # Uji ketahanan error per-tab & job FAILED tetap bisa buka QC
        │   ├── HistogramChart.jsx          # Histogram Freedman-Diaconis (+ role="img" aria-label)
        │   ├── JobFilesPanel.jsx           # Penjelajah berkas job dengan AbortController & stop polling terminal
        │   ├── JobFilesPanel.test.jsx      # Uji siklus polling & penanganan error unduh Blob
        │   ├── JobProcessLogs.jsx          # Penampil log teks per tahap & unduh `.txt`
        │   ├── JobStatusTracker.jsx        # Pelacak progres 10 tahap real-time (aria-live hanya pada headline)
        │   ├── JobStatusTracker.test.jsx   # Uji pelacak status & isolasi aria-live
        │   ├── KinshipHeatmap.jsx          # Heatmap matriks kekerabatan (+ role="img" aria-label)
        │   ├── LdDecayPlot.jsx             # Kurva peluruhan LD & half-decay (+ role="img" aria-label)
        │   ├── LeadSnpTable.jsx            # Tabel Lead SNPs & suggestive peaks (tombol sortir aria-sort)
        │   ├── ManhattanPlot.jsx           # Manhattan Plot HTML5 2D Canvas dengan memo terpisah
        │   ├── ManhattanPlot.test.jsx      # Benchmark performa decimation Manhattan Plot
        │   ├── ModelRecommendationCard.jsx # Kartu rekomendasi model GLM vs LMM
        │   ├── Pagination.jsx & Pagination.test.jsx # Kontrol paginasi reusable + unit test
        │   ├── PcaScatterPlot.jsx          # Plot sebaran PCA (+ role="img" aria-label)
        │   ├── PeakComparisonTable.jsx     # Tabel komparasi Single vs Multi-Locus + pilih plot model
        │   ├── PhenotypeUploader.jsx       # Pengunggah fenotipe aksesibel keyboard & sinkron state stabil
        │   ├── PrimerDesignPanel.jsx       # Panel sekuens flanking 201 bp & primer `primer3-py` (.txt/.csv)
        │   ├── QQPlot.jsx                  # Quantile-Quantile Plot interaktif (+ role="img" aria-label)
        │   ├── QcComparisonPanel.jsx & QcComparisonPanel.test.jsx # Komparasi metrik & histogram QC
        │   ├── ReferenceUploader.jsx       # Pengunggah bertahap FASTA referensi (.fa/.fna/.gz) + progress indeks .fai
        │   ├── ResultDashboard.jsx         # Ringkasan metrik, unduh ZIP, & laporan PDF
        │   ├── ScreePlot.jsx               # Diagram PVE komponen PCA (+ role="img" aria-label)
        │   ├── TablesAccessibility.test.jsx # Uji aksesibilitas sortir & tombol filter keyboard
        │   ├── ThresholdSelector.jsx       # Pemilih ambang signifikansi dinamis
        │   ├── UploadProgressBar.jsx       # Bilah progres unggahan berkas & ETA
        │   ├── Uploaders.test.jsx          # Uji stabilitas state & aksesibilitas keyboard ketiga uploader
        │   ├── VcfUploader.jsx             # Pengunggah VCF 10 MiB terkompresi + SHA-256
        │   ├── WorkspaceExplorer.jsx & WorkspaceExplorer.test.jsx # Penjelajah dataset & riwayat job
        │   └── __tests__/CandidateGenesTable.test.jsx
        │
        └── utils/                          # Utilitas Pembantu Frontend + Unit Tests
            ├── canvas.js                   # Inisialisasi HiDPI canvas & nice-ticks
            ├── csv.js & csv.test.js        # Pemformat CSV terproteksi dari formula injection
            ├── download.js & download.test.js # Helper saveBlob & ekstraksi error Blob JSON
            ├── jobFiles.js & jobFiles.test.js # Pengelompokan stage file job
            ├── manhattan.js & manhattan.test.js # Koordinat kromosom & deduplikasi piksel Manhattan
            ├── qqplot.js                   # Komputasi ekspektasi vs observasi -log10(p) QQ plot
            └── sha256.js & sha256.test.js  # Hash SHA-256 streaming WebCrypto / JS fallback di browser
```

---

## 🔬 2. Hubungan Alur Kerja & Interaksi Antar-Modul

Platform mengimplementasikan prinsip utama **"Inspect $\rightarrow$ Validate $\rightarrow$ Run"**:

1. **Fase Persiapan & Inspeksi Data (Fase 1)**:
   - Pengguna mengunggah VCF genotipe via [`VcfUploader.jsx`](frontend/src/components/VcfUploader.jsx) (potongan **10 MiB** + checksum **SHA-256** bertahap ke [`upload.py`](backend/app/api/routes/upload.py)), serta opsional file fenotipe, GFF3, pemetaan GO TSV/GAF, dan FASTA referensi.
   - Layanan [`phenotype_service.py`](backend/app/services/phenotype_service.py) merekonsiliasi ID sampel, menghitung histogram Freedman-Diaconis, mendeteksi outlier Tukey pada [`DataInspectionPanel.jsx`](frontend/src/components/DataInspectionPanel.jsx), serta menghasilkan `keep_ids.txt` dan `pheno_plink.txt`.

2. **Orkestrasi Eksekusi 10 Tahap Asinkron**:
   - Pemicu dikirim via `POST /api/v1/pipeline/{dataset_id}/start` ke [`pipeline_orchestrator.py`](backend/app/tasks/pipeline_orchestrator.py).
   - Rantai tugas Celery dieksekusi secara terurut di antrean `celery`, `normalization`, `qc`, `imputation`, dan `gwas`:
     $$\text{Inspeksi} \rightarrow \text{Normalisasi VCF} \rightarrow \text{QC PLINK} \rightarrow \text{Imputasi} \rightarrow \text{Struktur/Kinship} \rightarrow \text{GEMMA GWAS} \rightarrow \text{Multi-Locus} \rightarrow \text{Post-GWAS} \rightarrow \text{Visualisasi Parquet} \rightarrow \text{Anotasi GFF3 / GO / Primer3}$$
   - Untuk tahapan CLI utama (`bcftools`, `plink2`/`plink`, `gemma`), sistem bersikap *fail-fast* jika binary gagal atau tidak tersedia; untuk Multi-Locus (Tahap 7), sistem menggunakan **GAPIT3 R** (`gapit_runner.R`) atau fallback terlabel **Python Native Engine** (`ALLOW_PYTHON_MULTILOCUS=true`).

3. **Eksplorasi Hasil & Visualisasi Berkinerja Tinggi**:
   - Frontend [`JobStatusPage.jsx`](frontend/src/pages/JobStatusPage.jsx) memantau progres via adaptive polling (`useJobPolling.js`) ke `GET /api/v1/jobs/{job_id}/status` hingga terminal state tercapai.
   - Setelah job selesai (`COMPLETED`/`SUCCESS`; atau tab inspeksi/QC saat job `FAILED`), peneliti membuka [`ResultsPage.jsx`](frontend/src/pages/ResultsPage.jsx) yang menyajikan 7 tab interaktif lengkap dengan paginasi, ekspor `.zip` streaming via [`export.py`](backend/app/api/routes/export.py), dan laporan PDF via [`report.py`](backend/app/api/routes/report.py).

---

## 🛡️ 3. Pilar Keamanan & Hardening Sistem (Security Architecture)

1. **Autentikasi Split-Token & Perlindungan Sesi**:
   - **Access Token**: Berumur default 60 menit (`ACCESS_TOKEN_EXPIRE_MINUTES=60`), disimpan hanya di memori runtime React melalui [`apiClient.js`](frontend/src/api/apiClient.js) dengan single shared refresh mutex.
   - **Refresh Token**: Berumur 7 hari dalam cookie `HttpOnly=True`, `SameSite="Lax"`, dan `Secure=True` di luar mode `development` (`ENV=production` juga memvalidasi `JWT_SECRET_KEY` >= 32 karakter non-placeholder via `resolve_jwt_secret`).
   - **Rotasi Token & Pembatalan Instan**: Setiap pemanggilan `/auth/refresh` merotasi token; `/auth/logout` menaikkan `refresh_token_version` di DB sehingga semua sesi lama langsung tidak berlaku.
2. **Validasi Input Ketat & Integritas File di Boundary**:
   - Skema Pydantic v2 menyaring ekstensi, membersihkan nama file terhadap path traversal, serta menolak parameter di luar rentang biologis sebelum task Celery berjalan.
   - Unggahan VCF dan FASTA referensi dipecah menjadi chunk **10 MiB** dengan verifikasi checksum **SHA-256** streaming di sisi klien & backend.
3. **Isolasi Data Multitenant & Pencegahan IDOR**:
   - Setiap endpoint dataset dan analisis menegakkan kepemilikan sumber daya melalui `_owned_dataset` (`upload.py`), `require_job_owner` (`security.py`), atau hak akses `admin`, termasuk menolak akses atas record bertanda `deleted_at`.
4. **Eksekusi Subproses Aman & Pencegahan Path Traversal**:
   - Seluruh perintah CLI dijalankan dengan array argumen (`shell=False`) serta pembunuhan process group bersih saat job dibatalkan atau timeout.
   - Operasi berkas staging/job selalu dijaga ketat di bawah `STORAGE_ROOT` melalui `is_under_storage` dan guard retensi anti-wipe.
