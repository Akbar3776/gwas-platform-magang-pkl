# Architecture Decision Records (ADRs)

Dokumen ini mencatat keputusan-keputusan arsitektural teknis penting (*Architecture Decision Records*) yang diambil dalam perancangan dan pengembangan **GWAS Web Platform**. Setiap keputusan mendokumentasikan konteks, alternatif yang dievaluasi, alasan pemilihan, dan konsekuensi teknisnya.

## Daftar ADR

| Nomor | Judul | Status | Tanggal | Cakupan |
| :--- | :--- | :--- | :--- | :--- |
| [ADR-001](./ADR-001-database-strategy-and-orm.md) | **Strategi Database Relasional Ganda (PostgreSQL & SQLite Fallback)** | `Accepted` | 2026-10-30 | Database, ORM, Migrasi, Toleransi Lingkungan Dev/Prod |
| [ADR-002](./ADR-002-authentication-and-data-isolation.md) | **Arsitektur Autentikasi JWT (Split-Token) dan Isolasi Data** | `Accepted` | 2026-10-30 | Keamanan, JWT, Proteksi XSS/CSRF, Multi-tenancy |
| [ADR-003](./ADR-003-task-orchestration-and-pipeline-engine.md) | **Orkestrasi Asynchronous Pipeline & Task Engine (Celery + Redis vs. Airflow, Nextflow, & Temporal)** | `Accepted` | 2026-11-02 | Asynchronous Jobs, Task Queue, Redis Broker, Fault Tolerance, Fallback Lokal |
| [ADR-004](./ADR-004-bioinformatics-execution-engine.md) | **Strategi Eksekusi Engine Bioinformatika (Native CLI Subprocess Wrapper vs. Container-per-Task & CWL)** | `Accepted` | 2026-11-02 | Bioinformatika, Subprocess Wrapper, PLINK/GEMMA, Streaming I/O, Keamanan Kontainer |
| [ADR-005](./ADR-005-variant-data-storage-and-caching.md) | **Strategi Penyimpanan & Caching Data Varian Genomik (Apache Parquet + Polars vs. Raw TSV & Tabel RDBMS)** | `Accepted` | 2026-11-02 | Format Data Biner, Apache Parquet, Kompresi Snappy, Downsampling Cerdas, Visualisasi Cepat |
| [ADR-006](./ADR-006-real-time-streaming-and-state-synchronization.md) | **Arsitektur Streaming Real-time & Sinkronisasi Status Job (Adaptive REST Polling + SSE vs. WebSockets & gRPC)** | `Accepted` | 2026-11-02 | Real-time Streaming, Adaptive Polling, Log Streaming, Toleransi Jaringan, Stateless API |
| [ADR-007](./ADR-007-data-validation-and-serialization.md) | **Validasi Skema Data & Serialisasi API (Pydantic v2 Rust Core vs. Marshmallow & Cerberus)** | `Accepted` | 2026-11-02 | Skema Validasi, Pydantic v2, Rust Core, OpenAPI, Fail-Fast, Type Safety |
| [ADR-008](./ADR-008-frontend-big-data-plotting-engine.md) | **Mesin Visualisasi Data Genomik Skala Besar di Frontend (HTML5 Canvas Kustom vs. D3.js SVG & Plotly.js)** | `Accepted` | 2026-11-02 | Visualisasi Frontend, Manhattan Plot, HTML5 Canvas, Pixel Decimation, 60 FPS |
| [ADR-009](./ADR-009-frontend-architecture-and-build-toolchain.md) | **Arsitektur Frontend & Perangkat Pembundel (Vite + React 18 SPA vs. Next.js SSR & Webpack CRA)** | `Accepted` | 2026-11-02 | Frontend, Vite, React 18, SPA, No-Node-in-Prod, Client Rendering, HMR |
