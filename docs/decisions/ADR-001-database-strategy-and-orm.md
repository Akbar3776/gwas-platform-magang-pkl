# ADR-001: Strategi Database Relasional Ganda (PostgreSQL Produksi & Fallback Otomatis SQLite)

## Status
Accepted

## Tanggal
2026-10-30

## Konteks
Aplikasi **GWAS Platform** adalah sistem bioinformatika berbasis web yang mengelola data sensitif berskala besar: dataset genotipe varian (VCF), berkas fenotipe, konfigurasi analisis multivariat, dan riwayat komputasi *AnalysisJob*. Kebutuhan utama sistem mencakup:
1. **Integritas Relasional & Transaksi ACID**: Relasi hierarkis antar-entitas (`User` $\rightarrow$ `Dataset` $\rightarrow$ `AnalysisJob` $\rightarrow$ `Result`) memerlukan integritas referensial yang ketat (`CASCADE delete` saat dataset/job dihapus agar tidak meninggalkan artefak yatim).
2. **Metadata Dinamis**: Setiap algoritma GWAS (GEMMA, PLINK 2, Beagle, FarmCPU) membutuhkan parameter input yang berbeda-beda dan ringkasan metrik heterogen (seperti nilai $\lambda_{GC}$, ambang Bonferroni, FDR, PVE PCA, matriks kekerabatan) yang paling efisien disimpan dalam format JSON.
3. **Dual-Environment (Dev vs Prod)**: Platform harus dapat dijalankan secara instan di lingkungan pengembangan lokal (misalnya workstation peneliti atau pengembang Windows tanpa Docker/PostgreSQL terpasang) maupun di kluster server produksi menggunakan Docker Compose / Kubernetes.

## Keputusan
Kami memutuskan untuk menggunakan **SQLAlchemy 2.0 ORM** dengan strategi **Dual-Engine Auto-Fallback**:
- **PostgreSQL 15+** sebagai engine utama di lingkungan produksi (memanfaatkan tipe native `JSONB` dan performa konkurensi tinggi untuk Celery worker).
- **SQLite lokal (`gwas_local.db`)** sebagai fallback otomatis saat socket koneksi PostgreSQL tidak dapat dijangkau dalam batas waktu 300 ms saat startup.
- Abstraksi tipe data lintas-dialek (`JSONVariant = JSON().with_variant(JSONB(), "postgresql")` dan penanganan UUID string/native) pada model ORM.
- **Alembic** untuk migrasi terstruktur di PostgreSQL produksi, dikombinasikan dengan mekanisme *lightweight auto-migration* (`_ensure_sqlite_columns`) pada siklus *lifespan* FastAPI untuk pengembang lokal.

## Alternatif yang Dipertimbangkan

### 1. PostgreSQL Murni (Wajib di Semua Lingkungan)
- **Kelebihan**: Konsistensi 100% antara development dan production; fitur canggih seperti `JSONB` indexing dan *concurrent write locks*.
- **Kekurangan**: Mengharuskan setiap peneliti dan kontributor lokal untuk menginstal, mengonfigurasi service daemon PostgreSQL, atau menjalankan Docker desktop yang berat.
- **Alasan Ditolak**: Hambatan adopsi lokal terlalu tinggi untuk peneliti bioinformatika yang hanya ingin menjalankan aplikasi secara langsung di terminal host.

### 2. SQLite Murni (Di Semua Lingkungan Termasuk Produksi)
- **Kelebihan**: Nol konfigurasi, tanpa service eksternal, sangat cepat untuk *read-heavy*.
- **Kekurangan**: Keterbatasan *write concurrency* (database table-level locking saat worker Celery menulis status job secara paralel) dan ketiadaan indexing JSONB mendalam.
- **Alasan Ditolak**: Berisiko tinggi terjadi *database locked errors* saat banyak Celery worker paralel memperbarui progres pipeline 10 tahap secara bersamaan.

### 3. Document Store (MongoDB)
- **Kelebihan**: Fleksibilitas skema alami untuk menyimpan berbagai variasi parameter bioinformatika.
- **Kekurangan**: Tidak memiliki integritas relasional bawaan untuk lifecycle kepemilikan user dan job; biaya transaksi lintas-koleksi lebih tinggi; tidak sesuai dengan ekosistem SQLAlchemy yang matang.
- **Alasan Ditolak**: Struktur relasi entitas inti sistem (`User`, `Dataset`, `AnalysisJob`) bersifat relasional; hanya kolom parameter dan metrik hasil yang memerlukan format semi-terstruktur.

## Konsekuensi
- **Positif**:
  - Peneliti/pengembang dapat langsung menjalankan backend (`uvicorn app.main:app`) tanpa dependensi database eksternal.
  - Produksi mendapatkan ketahanan konkurensi penuh dari PostgreSQL dengan `pool_pre_ping=True`, `pool_size=10`, dan `max_overflow=20`.
  - Satu set model ORM terpadu di [`models.py`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/db/engine/models.py) berlaku untuk kedua dialek.
- **Negatif / Mitigasi**:
  - Diperlukan kedisiplinan pengujian pada kedua dialek: suite pengujian otomatis (`pytest`) dikonfigurasi untuk memvalidasi fungsi `JSONVariant` dan integritas skema di SQLite memori maupun PostgreSQL.
  - Perubahan kolom baru memerlukan registrasi di skrip migrasi Alembic sekaligus di helper `_ensure_sqlite_columns` agar pengembang SQLite lokal tidak mengalami kegagalan query.
