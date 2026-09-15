# Struktur Project GWAS Platform

Dokumen ini merangkum struktur aktual repository `gwas-platform` dan fungsi
komponen utamanya berdasarkan file yang tersedia saat ini.

## Gambaran Umum

Project ini dirancang sebagai platform web untuk menjalankan pipeline analisis
GWAS secara asynchronous:

1. Frontend React menerima upload VCF dan menampilkan status pekerjaan.
2. Backend FastAPI menyediakan API, autentikasi, metadata dataset, job, dan hasil.
3. Celery menjalankan tahap komputasi berat seperti QC, imputasi, dan GWAS.
4. PostgreSQL menyimpan metadata, sedangkan Redis menjadi broker dan result backend Celery.
5. Volume Docker menyimpan file VCF, file intermediate, dan hasil analisis.

## Tree Project

```text
gwas-platform/
|-- README.md                         # Dokumentasi utama, deployment, dan testing
|-- PROJECT_STRUCTURE.md              # Dokumentasi struktur project ini
|-- .gitignore
|-- .github/
|   `-- workflows/
|       `-- test.yml                  # CI lint, test, dan build
|
|-- backend/
|   |-- Dockerfile                    # Image FastAPI + tool bioinformatika
|   |-- .dockerignore                 # Mengecualikan secret dan artefak build
|   |-- .env.example                  # Template environment backend
|   |-- requirements.txt              # Dependensi Python utama
|   |-- requirements-test.txt         # Dependensi Pytest, TestClient, Flake8
|   |-- app/
|   |   |-- main.py                   # Entry point FastAPI, CORS, /api/v1 router, health
|   |   |-- requirements-addition.txt # Dependensi tambahan report/export
|   |   |-- api/
|   |   |   `-- routes/
|   |   |       |-- auth.py            # Endpoint autentikasi
|   |   |       |-- datasets_and_jobs_isolation_example.py
|   |   |       |                       # Contoh isolasi akses data
|   |   |       |-- export.py          # Endpoint export hasil
|   |   |       |-- jobs.py            # Endpoint job dan status job
|   |   |       |-- pipeline.py        # Endpoint trigger pipeline GWAS
|   |   |       |-- reference_panels.py# Endpoint reference panel
|   |   |       |-- report.py          # Endpoint pembuatan report
|   |   |       |-- results.py         # Endpoint data hasil dan plot
|   |   |       `-- upload.py          # Endpoint upload dataset/chunk
|   |   |-- core/
|   |   |   |-- config.py             # Konfigurasi dari environment/.env
|   |   |   |-- logging_config.py     # Logging stdout untuk container
|   |   |   |-- progress.py           # Boundary publikasi progress worker
|   |   |   |-- resource_limits.py    # Perhitungan batas resource task
|   |   |   `-- security.py           # Security dan autentikasi
|   |   |-- db/
|   |   |   |-- base.py               # Base SQLAlchemy
|   |   |   `-- engine/
|   |   |       |-- database.py       # Engine dan koneksi database
|   |   |       `-- models.py         # Model User, Dataset, Job, Result
|   |   |-- schemas/
|   |   |   |-- jobs.py               # Schema request/response job
|   |   |   |-- pipeline.py           # Schema pipeline
|   |   |   |-- results.py            # Schema data hasil plot
|   |   |   `-- upload.py             # Schema upload
|   |   |-- services/
|   |   |   |-- export_service.py     # Penyusunan file export
|   |   |   |-- manhattan_parser.py   # Parsing data Manhattan Plot
|   |   |   |-- qc_service.py         # Logika QC
|   |   |   |-- report_generator.py   # Render report HTML ke PDF
|   |   |   `-- upload_service.py     # Logika penyimpanan dan upload
|   |   |-- tasks/
|   |   |   |-- celery_app.py         # Instance dan konfigurasi Celery
|   |   |   |-- gemma_tasks.py        # Task analisis GEMMA
|   |   |   |-- imputation_tasks.py   # Task imputasi
|   |   |   |-- pipeline_orchestrator.py # Penyusunan Celery chain
|   |   |   |-- pipeline_tasks.py     # Task tahap-tahap pipeline
|   |   |   |-- qc_tasks.py           # Task QC
|   |   |   `-- upload_tasks.py       # Task finalisasi/upload asynchronous
|   |   |-- templates/
|   |   |   `-- report_template.html  # Template report hasil GWAS
|   |   `-- utils/
|   |       `-- process.py            # Utilitas eksekusi process/subprocess
|   |-- alembic/                      # Direktori migrasi; belum ada file
|   |-- tests/
|   |   |-- test_jobs_api.py          # TestClient endpoint QC dengan mock Celery
|   |   `-- test_process.py            # Test wrapper subprocess dengan mock Popen
|   |-- .env                          # Environment lokal; jangan commit secret
|   |-- IMPUTATION_NOTES.md           # Catatan konfigurasi worker imputasi
|   `-- INTEGRATION_NOTES.md          # Catatan integrasi auth dan isolasi data
|
|-- frontend/
|   |-- dist/                        # Output build produksi; artefak generated
|   |-- public/                       # Asset public; belum ada file terdeteksi
|   `-- src/
|       |-- App.jsx                   # Layout dan routing aplikasi
|       |-- main.jsx                  # Bootstrap React dan AuthProvider
|       |-- api/
|       |   |-- apiClient.js           # HTTP client umum
|       |   |-- chunkedUpload.js       # API upload berbasis chunk
|       |   |-- gwasApi.js             # API data hasil GWAS
|       |   |-- httpClient.js          # Helper HTTP
|       |   `-- jobsApi.js             # API job/QC
|       |-- components/
|       |   |-- GwasResultsPanel.jsx   # Panel tab Manhattan/QQ Plot
|       |   |-- JobStatusTracker.jsx   # Tampilan progress job
|       |   |-- ManhattanPlot.jsx      # Visualisasi Manhattan Plot
|       |   |-- QQPlot.jsx             # Visualisasi QQ Plot
|       |   |-- ResultDashboard.jsx    # Ringkasan dan download hasil
|       |   |-- UploadProgressBar.jsx  # Progress upload
|       |   |-- VcfUploader.jsx        # Komponen upload VCF
|       |   `-- JobStatusTracker.test.jsx # Test RTL status tracker
|       |-- context/
|       |   `-- AuthContext.jsx        # State autentikasi frontend
|       |-- hooks/
|       |   |-- useChunkUpload.js      # State dan proses chunk upload
|       |   |-- useGwasResults.js      # Pengambilan data hasil GWAS
|       |   `-- useJobPolling.js       # Polling status job
|       |-- pages/
|       |   |-- GwasUploadPage.jsx     # Halaman upload dan trigger QC
|       |   |-- JobStatusPage.jsx      # Halaman progress job
|       |   |-- LoginPage.jsx          # Halaman login
|       |   |-- RegisterPage.jsx       # Halaman registrasi
|       |   `-- ResultsPage.jsx        # Halaman hasil analisis
|       `-- utils/
|           |-- manhattan.js           # Transformasi data Manhattan Plot
|           `-- qqplot.js              # Transformasi data QQ Plot
|   |-- index.html                    # HTML entry Vite
|   |-- package.json                  # Script dan dependensi frontend
|   |-- package-lock.json             # Lockfile npm
|   |-- babel.config.cjs              # Transform JSX untuk Jest
|   |-- jest.config.cjs               # Konfigurasi Jest + jsdom
|   |-- .eslintrc.cjs                 # Konfigurasi lint React
|   `-- vite.config.js                # Plugin React dan proxy API/WebSocket
|
|-- docs/
|   `-- design.md                      # Desain arsitektur dan alur sistem
|
|-- infra/
|   |-- docker-compose.yml             # Service development
|   |-- docker-compose.prod.yml        # Stack production + Nginx
|   `-- nginx/
|       |-- Dockerfile                 # Multi-stage React build + Nginx
|       `-- nginx.conf                 # Static hosting dan reverse proxy
|
`-- storage/                           # Area storage lokal; belum ada file
```

## Backend

### API dan entry point

`backend/app/main.py` adalah titik masuk FastAPI. File tersebut saat ini
membuat instance aplikasi, mengaktifkan CORS, memakai prefix API `/api/v1`,
mendaftarkan seluruh router yang tersedia, dan menyediakan endpoint `/health`.

Router dikelompokkan berdasarkan tanggung jawab:

- `auth.py`: registrasi, login, dan identitas pengguna.
- `upload.py`: inisiasi, pengiriman chunk, status, dan finalisasi dataset.
- `jobs.py`: pembuatan dan pemantauan job.
- `pipeline.py`: trigger pipeline end-to-end.
- `results.py`: pengambilan data hasil untuk visualisasi.
- `export.py` dan `report.py`: download arsip hasil dan report PDF.
- `reference_panels.py`: pengelolaan atau akses panel referensi imputasi.

### Model data

`backend/app/db/engine/models.py` menggunakan SQLAlchemy dan mendefinisikan
model `User`, `Dataset`, `AnalysisJob`, `Result`, dan `ReferencePanel`, beserta
relasi utama berikut:

```text
User 1 --- N Dataset
User 1 --- N AnalysisJob
Dataset 1 --- N AnalysisJob
AnalysisJob 1 --- N Result
```

Database hanya menyimpan metadata. File VCF dan output pipeline disimpan pada
storage, sedangkan database menyimpan `storage_path`, status, parameter,
checksum, dan ringkasan statistik.

### Task pipeline

`backend/app/tasks/celery_app.py` membuat instance Celery dengan Redis sebagai
broker/result backend. `pipeline_orchestrator.py` menyusun chain asynchronous
yang secara konseptual berjalan sebagai berikut:

```text
QC -> Imputation -> GEMMA GWAS -> Parse Results
```

`pipeline_tasks.py` berisi task tahap pipeline, sedangkan task khusus upload,
QC, imputasi, dan GEMMA berada pada modul masing-masing. Worker dijalankan
melalui command Celery dari Docker Compose.

### Service dan report

Folder `services/` memisahkan logika bisnis dari endpoint API. Contohnya,
`report_generator.py` membaca file association secara streaming, mengambil
SNP paling signifikan, lalu merender `templates/report_template.html` dengan
Jinja2 dan WeasyPrint menjadi PDF.

## Frontend

Frontend memakai struktur komponen React yang berpusat pada alur upload dan
monitoring job:

```text
VcfUploader
    -> useChunkUpload
    -> API upload chunk
    -> trigger job
    -> JobStatusTracker / useJobPolling
    -> GwasResultsPanel
    -> ManhattanPlot atau QQPlot
```

`ResultDashboard.jsx` melengkapi alur tersebut dengan ringkasan hasil serta
download full result ZIP dan summary report PDF.

Catatan kondisi saat ini: `frontend/package.json`, `package-lock.json`,
`vite.config.js`, dan `index.html` sudah tersedia. `App.jsx` sudah berisi
konfigurasi routing dan layout aplikasi, tetapi `main.jsx` masih kosong sehingga
bootstrap React belum meng-mount `App` ke elemen root HTML.

## Infrastruktur

`infra/docker-compose.yml` mendefinisikan service development berikut:

| Service | Fungsi |
|---|---|
| `backend` | FastAPI pada port 8000 |
| `db` | PostgreSQL 15 untuk metadata |
| `redis` | Broker/result backend Celery dan kanal progress |
| `celery_worker` | Eksekusi task PLINK/GEMMA dan pipeline |

Volume yang digunakan:

- `postgres_data`: data PostgreSQL.
- `redis_data`: data Redis.
- `vcf_storage`: VCF, staging, dan hasil pipeline yang dibagi backend-worker.

Network `gwas_network` menghubungkan semua service. `backend/Dockerfile`
menyiapkan Python, PLINK, GEMMA, library WeasyPrint, dependensi Python, dan
user non-root `appuser`.

## Dokumen Pendukung

- `docs/design.md`: rancangan arsitektur, strategi upload chunked, skema data,
  workflow asynchronous, dan endpoint yang direncanakan.
- `backend/INTEGRATION_NOTES.md`: catatan CORS, auth, isolasi data, dan
  integrasi frontend-backend.
- `backend/IMPUTATION_NOTES.md`: pertimbangan memory limit, queue, concurrency,
  dan konfigurasi worker imputasi.

## Catatan Implementasi Saat Ini

- `README.md` root masih kosong.
- Direktori `backend/alembic/` ada, tetapi belum berisi file migrasi yang terdeteksi.
- Direktori `backend/tests/` ada, tetapi belum berisi file pengujian yang terdeteksi.
- Direktori `storage/` dan `frontend/public/` belum berisi file.
- Direktori `infra/nginx/` ada, tetapi belum berisi konfigurasi reverse proxy yang terdeteksi.
- `frontend/node_modules/` tersedia sebagai hasil instalasi dependency npm dan tidak
  dirinci sebagai source project.
- `frontend/dist/` berisi output hasil `vite build` dan merupakan artefak generated,
  bukan source frontend.
- File Python `__pycache__/` juga muncul sebagai artefak hasil compile dan tidak
  dianggap bagian dari struktur source.
- File `backend/.env` tersedia untuk konfigurasi lokal; nilainya sengaja tidak
  didokumentasikan di sini karena dapat berisi credential atau secret.
- Struktur frontend sudah memiliki routing autentikasi, upload, status job, dan
  hasil analisis, tetapi bootstrap `main.jsx` dan migrasi database masih perlu
  dilengkapi sebelum deployment.