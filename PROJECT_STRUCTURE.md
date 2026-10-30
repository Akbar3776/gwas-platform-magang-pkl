# 📂 Struktur Proyek & Panduan Komponen (`gwas-platform`)

Dokumen ini menjelaskan struktur repositori **GWAS Web Platform** secara menyeluruh, mencakup **kegunaan, fungsi spesifik, interaksi antar-modul, dan alasan arsitektural** untuk setiap file di lapisan *Backend*, *Workers*, *Frontend*, dan *Infrastructure*.

---

## 🌳 1. Pohon Direktori Lengkap

```text
gwas-platform/
├── .env.example                            # Template variabel lingkungan (Database, Redis, JWT, Storage)
├── .gitignore                              # Daftar pengecualian file Git (node_modules, venv, storage, dll.)
├── README.md                               # Dokumentasi utama proyek, arsitektur, tech stack, & panduan jalan
├── PROJECT_STRUCTURE.md                    # Dokumentasi struktur direktori & fungsi setiap file (dokumen ini)
├── Pipeline_Gwas.md                        # Panduan ilmiah rujukan tahapan GWAS lengkap
│
├── docs/
│   ├── design.md                           # Dokumen Desain Sistem (System Design Document) & spesifikasi teknis
│   └── PLAN_INTEGRASI_PIPELINE_GWAS.md     # Rencana kerja & milestone integrasi 10 fase GWAS
│
├── backend/                                # Layanan Backend Python (FastAPI, SQLAlchemy, Celery Workers)
│   ├── Dockerfile                          # Definisi image Docker untuk API Server & Celery Worker (+ CLI BioTools)
│   ├── requirements.txt                    # Daftar dependensi paket Python (FastAPI, NumPy, SciPy, Pandas, PyArrow)
│   ├── alembic.ini                         # Konfigurasi Alembic untuk migrasi skema database
│   │
│   ├── alembic/                            # Modul migrasi skema database relasional
│   │   ├── env.py                          # Konfigurasi runtime migrasi Alembic
│   │   └── versions/
│   │       ├── 0001_initial_schema.py      # Migrasi awal pembuatan tabel users, jobs, dan qc_metrics
│   │       └── c6d8e9f01234_add_gwas_pipeline_phases_and_metadata.py # Migrasi 10 tahap & tipe hasil baru
│   │
│   ├── app/                                # Paket aplikasi utama Backend
│   │   ├── main.py                         # Titik masuk FastAPI, CORS, migrasi SQLite otomatis, & registrasi router
│   │   │
│   │   ├── api/routes/                     # Lapisan HTTP Controller / REST API Endpoints
│   │   │   ├── auth.py                     # Endpoint otentikasi JWT, registrasi, login, & refresh token
│   │   │   ├── upload.py                   # Endpoint upload chunked VCF, fenotipe, & anotasi GFF3
│   │   │   ├── capabilities.py             # Endpoint deteksi ketersediaan tool CLI di host (bcftools, plink, beagle, gemma, R)
│   │   │   ├── pipeline.py                 # Endpoint pemicu eksekusi pipeline GWAS 10 tahap (HTTP 202 Accepted)
│   │   │   ├── jobs.py                     # Endpoint daftar job, status polling, pembatalan, penghapusan, log, & file browser
│   │   │   ├── results.py                  # Endpoint data visualisasi (Manhattan, Inspection, QC, PCA, Kinship, LD, Genes)
│   │   │   ├── export.py                   # Endpoint pengemasan arsip .zip streaming seluruh artefak job
│   │   │   ├── report.py                   # Endpoint unduh laporan ilmiah eksekutif berformat PDF
│   │   │   └── reference_panels.py         # Endpoint katalog panel referensi imputasi (1000 Genomes GRCh38/GRCh37)
│   │   │
│   │   ├── core/                           # Konfigurasi inti, koneksi database, & keamanan kriptografi
│   │   │   ├── config.py                   # Manajemen konfigurasi aplikasi (Pydantic Settings)
│   │   │   ├── database.py                 # Inisialisasi SQLAlchemy Engine, SessionLocal, & tipe UUID/JSON lintas-dialek
│   │   │   ├── security.py                 # Hashing password (bcrypt) & pembuatan/verifikasi token JWT
│   │   │   └── resource_limits.py          # Pembatas memori OS (RLIMIT_AS) & pembangun opsi JVM untuk Beagle
│   │   │
│   │   ├── db/engine/models.py             # Model database ORM: User, Dataset, AnalysisJob, Result
│   │   ├── schemas/pipeline.py             # Skema validasi Pydantic v2 untuk seluruh 10 fase GWAS
│   │   │
│   │   ├── services/                       # Logika bisnis domain & algoritma komputasi ilmiah
│   │   │   ├── phenotype_service.py        # Fase 1: Rekonsiliasi sampel, validasi missing, Freedman-Diaconis histogram & boxplot
│   │   │   ├── qc_service.py               # Fase 2: Pembangun perintah PLINK 2 / 1.9 (inbred vs outcross) & fallback Python
│   │   │   ├── structure_service.py        # Fase 3+4D: LD pruning, PCA, Centered Kinship K, tabel KING, & kurva LD decay
│   │   │   ├── assoc_normalizer.py         # Fase 4A/4B: Normalisasi hasil asosiasi PLINK2/GEMMA ke format kanonikal TSV
│   │   │   ├── postgwas_service.py         # Fase 4E/4F: Unclamped Lambda GC, ambang batas dinamis, & clumping Lead SNPs
│   │   │   ├── annotation_service.py       # Fase 5: Normalisasi kromosom, parser GFF3 streaming, & klasifikasi biologis gen
│   │   │   ├── export_service.py           # Generator bundel arsip .zip streaming hemat RAM (stream-zip / zipfile)
│   │   │   ├── report_generator.py         # Generator laporan PDF ilmiah (WeasyPrint / pure-Python PDF fallback)
│   │   │   └── manhattan_parser.py         # Parser chunked asosiasi, kalkulator Lambda GC, & Parquet caching
│   │   │
│   │   └── tasks/                          # Lapisan eksekusi asinkron Celery Workers & Chaining
│   │       ├── celery_app.py               # Inisialisasi Celery app & worker task logging
│   │       ├── _common.py                  # Helper bersama: ensure_not_cancelled, mark_job, stage_dir, upsert_result, which_tool
│   │       ├── pipeline_orchestrator.py    # Perangkai rantai eksekusi 10 tahap Celery chain & sync runner
│   │       ├── inspection_tasks.py         # Task Fase 1: Inspeksi fenotipe & sinkronisasi ID sampel
│   │       ├── normalization_tasks.py      # Task Fase 1B: Validasi, sort, normalisasi, & pengindeksan VCF (bcftools)
│   │       ├── qc_tasks.py                 # Task Fase 2: Quality Control (PLINK 2 / 1.9 dengan organism mode)
│   │       ├── imputation_tasks.py         # Task Imputasi: Beagle 5.4 (none / reference / reference_free)
│   │       ├── structure_tasks.py          # Task Fase 3+4D: Struktur populasi, kinship matrix, & LD decay
│   │       ├── gemma_tasks.py              # Task Fase 4A/4B: Asosiasi GWAS (GEMMA LMM/GLM + Covariates)
│   │       ├── multilocus_tasks.py         # Task Fase 4C/4H: Multi-Locus GWAS (FarmCPU/BLINK via R) & Peak comparison
│   │       ├── postgwas_tasks.py           # Task Fase 4E/4F: Pasca-GWAS, Lambda GC, thresholds dinamis, & clumping
│   │       ├── visualization_tasks.py      # Task Fase 4G: Pra-pemrosesan cache Parquet untuk Manhattan & QQ plot
│   │       └── annotation_tasks.py         # Task Fase 5: Pemetaan gen kandidat GFF3 & ekspor daftar gen GO
│   │
│   └── tests/                              # Suite pengujian otomatis komprehensif (34 unit & E2E tests)
│       ├── fixtures/                       # Generator data uji deterministik (toy_gwas.vcf.gz, toy_pheno.txt, toy_annotation.gff3)
│       ├── test_annotation_service.py      # Pengujian normalisasi kromosom, GFF3 parser, & pemetaan SNP-ke-gen
│       ├── test_assoc_normalizer.py        # Pengujian normalisasi format asosiasi GEMMA & PLINK2 ke TSV kanonikal
│       ├── test_jobs_api.py                # Pengujian status jobs, log, & file browser
│       ├── test_m0_foundation.py           # Pengujian helper _common & formula Lambda GC tanpa clamping
│       ├── test_m1_models_schemas.py       # Pengujian enum models & schema validasi Pydantic
│       ├── test_phenotype_service.py       # Pengujian inspeksi fenotipe, rekonsiliasi ID, & Freedman-Diaconis histogram
│       ├── test_pipeline_e2e.py            # Pengujian integrasi end-to-end 10 tahap pipeline secara sinkron
│       ├── test_postgwas_service.py        # Pengujian formula Lambda GC, ambang batas dinamis, & clumping
│       ├── test_process.py                 # Pengujian CLI executor, timeout, & error streaming
│       ├── test_qc_commands.py             # Pengujian argumen PLINK 2 / 1.9 inbred vs outcross
│       └── test_structure_service.py       # Pengujian PCA, Kinship, KING table, & LD decay curve
│
└── frontend/                               # Antarmuka Klien React 18 SPA (Vite, HTML5 Canvas)
    ├── package.json                        # Konfigurasi dependensi npm (React, Vite, ESLint)
    ├── vite.config.js                      # Konfigurasi Vite build & reverse proxy development
    │
    └── src/
        ├── App.jsx                         # Rute aplikasi & navigasi halaman
        ├── api/
        │   ├── apiClient.js                # Wrapper fetch dengan auto-bearer & refresh token interceptor
        │   ├── gwasApi.js                  # Klien API untuk seluruh tahap pipeline hasil GWAS
        │   └── jobsApi.js                  # Klien API untuk kontrol job, upload, dan log tahapan
        │
        ├── components/                     # Komponen antarmuka & visualisasi interaktif
        │   ├── HistogramChart.jsx          # Visualisasi histogram distribusi fenotipe (Freedman-Diaconis)
        │   ├── BoxPlotChart.jsx            # Visualisasi boxplot dengan batas outlier Tukey
        │   ├── DataInspectionPanel.jsx     # Panel Fase 1: Inspeksi konsistensi sampel & ringkasan fenotipe
        │   ├── QcComparisonPanel.jsx       # Panel Fase 2: Perbandingan metrik sebelum vs sesudah QC
        │   ├── PcaScatterPlot.jsx          # Visualisasi sebaran PCA (PC1 vs PC2) berbasis Canvas
        │   ├── ScreePlot.jsx               # Visualisasi varians terjelaskan (PVE) per komponen utama
        │   ├── KinshipHeatmap.jsx          # Heatmap matriks kekerabatan Centered Kinship
        │   ├── LdDecayPlot.jsx             # Visualisasi kurva peluruhan Linkage Disequilibrium
        │   ├── ModelRecommendationCard.jsx # Kartu rekomendasi model statistik GLM vs LMM
        │   ├── ThresholdSelector.jsx       # Pemilih ambang signifikansi dinamis (Bonferroni, Suggestive, FDR, LOD)
        │   ├── LeadSnpTable.jsx            # Tabel varian Lead SNPs independen hasil clumping
        │   ├── PeakComparisonTable.jsx     # Tabel perbandingan puncak asosiasi Single-Locus vs Multi-Locus
        │   ├── CandidateGenesTable.jsx     # Tabel pemetaan gen kandidat GFF3 & unduh Daftar_Gen_Untuk_GO.txt
        │   ├── ManhattanPlot.jsx           # Plot Manhattan genomik berbasis HTML5 2D Canvas (Anti-Crash)
        │   ├── QQPlot.jsx                  # Plot Quantile-Quantile interaktif berbasis Canvas
        │   ├── GwasResultsPanel.jsx        # Panel utama 6 tab hasil analisis komprehensif
        │   ├── ResultDashboard.jsx         # Kartu ringkasan metrik & tombol unduh ZIP / PDF
        │   ├── JobProcessLogs.jsx          # Penampil log proses per tahap dengan auto-refresh & filter
        │   ├── JobFilesPanel.jsx           # Penjelajah file kerja job dengan pratinjau teks & unduhan
        │   ├── WorkspaceExplorer.jsx       # Manajemen dataset & riwayat pekerjaan pengguna
        │   ├── PhenotypeUploader.jsx       # Komponen upload & pratinjau file fenotipe
        │   ├── AnnotationUploader.jsx      # Komponen upload & pratinjau file anotasi genom GFF3
        │   └── VcfUploader.jsx             # Komponen upload VCF chunked 5 MB dengan verifikasi SHA-256
        │
        └── pages/
            ├── GwasUploadPage.jsx          # Formulir konfigurasi pipeline lengkap 10 fase
            ├── JobProgressPage.jsx         # Halaman pemantauan progres job & pembatalan
            └── ResultsPage.jsx             # Halaman eksplorasi hasil analisis GWAS menyeluruh
```

---

## 2. Hubungan Alur Kerja & Interaksi Antar-Modul

1. **Persiapan Data (Fase 1)**:
   - Pengguna mengunggah VCF, file fenotipe, dan file anotasi GFF3 opsional via [`GwasUploadPage.jsx`](frontend/src/pages/GwasUploadPage.jsx).
   - Pengguna meninjau statistik fenotipe dan histogram melalui [`DataInspectionPanel.jsx`](frontend/src/components/DataInspectionPanel.jsx).
2. **Pemicu Analisis**:
   - Frontend mengirim konfigurasi [`FullPipelineRequest`](backend/app/schemas/pipeline.py) ke `POST /api/v1/pipeline/{dataset_id}/start`.
   - Endpoint menyimpan parameter ke `analysis_jobs` dan memanggil `trigger_full_pipeline` di [`pipeline_orchestrator.py`](backend/app/tasks/pipeline_orchestrator.py).
3. **Eksekusi 10 Tahap Berantai (Celery Chain)**:
   - `run_inspection` $\rightarrow$ `run_vcf_normalization` $\rightarrow$ `run_qc_pipeline` $\rightarrow$ `run_imputation` $\rightarrow$ `run_structure_analysis` $\rightarrow$ `run_gemma_gwas` $\rightarrow$ `run_multilocus_gwas` $\rightarrow$ `run_postgwas_analysis` $\rightarrow$ `run_visualization` $\rightarrow$ `run_annotation`.
   - Setiap tahap meneruskan konteks hasil (`ctx`) ke tahap berikutnya dan memperbarui `current_step` serta status log.
4. **Eksplorasi Hasil & Ekspor (Fase Selesai)**:
   - Peneliti membuka [`ResultsPage.jsx`](frontend/src/pages/ResultsPage.jsx) yang menyajikan tab Manhattan/QQ, Post-GWAS Lead SNPs, Struktur Populasi, Inspeksi/QC, Multi-Locus, dan Anotasi Gen Kandidat.
   - Peneliti dapat mengunduh seluruh artefak dalam bundel `.zip` via `GET /results/{job_id}/export`, mengunduh laporan PDF resmi via `GET /results/{job_id}/report`, atau mengunduh `Daftar_Gen_Untuk_GO.txt` untuk analisis fungsional lebih lanjut.
