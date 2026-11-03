# ADR-003: Orkestrasi Asynchronous Pipeline & Task Engine (Celery + Redis vs. Airflow, Nextflow, & Temporal)

## Status
Accepted

## Tanggal
2026-11-02

## Konteks
Aplikasi **GWAS Platform** menjalankan alur komputasi genomik skala besar yang heterogen, intensif CPU/RAM, dan membutuhkan waktu eksekusi mulai dari hitungan detik (inspeksi sampel dan normalisasi VCF) hingga puluhan menit atau jam (imputasi Beagle, dekomposisi matriks kekerabatan GEMMA, dan analisis asosiasi jutaan varian). 

Alur kerja analisis GWAS terdiri dari 10 tahapan sekuensial terstruktur:
1. `Inspection`: Rekonsiliasi ID sampel VCF vs. Fenotipe, audit duplikasi, dan kalkulasi distribusi fenotipe.
2. `Normalization`: Validasi header VCF, sorting genomik, dan normalisasi multialelik (`bcftools`).
3. `QC Pipeline`: Filter *Quality Control* genomik via PLINK (MAF, HWE, Call Rate/Missingness).
4. `Imputation`: Imputasi genotipe berbasis LD (Beagle 5.4) untuk menangani missing data.
5. `Structure Analysis`: Pruning LD, estimasi Principal Component Analysis (PCA), dan evaluasi populasi.
6. `GWAS Association`: Perhitungan model GLM/LMM via PLINK 2 atau GEMMA (Linear Mixed Models).
7. `Multilocus GWAS`: Model multi-lokus (FarmCPU / BLINK) untuk mendeteksi QTL independen.
8. `Post-GWAS Analysis`: Koreksi inflasi genomik ($\lambda_{GC}$), ambang signifikansi dinamis (Bonferroni & FDR), serta clumping LD untuk isolasi *Lead SNPs*.
9. `Visualization Cache`: Ekstraksi koordinat Manhattan Plot dan QQ Plot ke dalam cache teroptimasi (Parquet) untuk *high-performance frontend rendering*.
10. `Annotation`: Pemetaan fungsional gen kandidat dari berkas GFF3/GTF ke Lead SNPs.

### Kebutuhan dan Kendala Sistem:
1. **Responsivitas Web & Non-blocking I/O**: Permintaan HTTP dari pengguna tidak boleh mengalami *timeout*. Web server FastAPI harus mengembalikan ID pekerjaan (*job submission*) dalam waktu $< 200\text{ ms}$, sementara pemrosesan berat berjalan di lingkungan *background worker* terisolasi.
2. **Streaming Log & State Tracking Real-time**: Pengguna di browser membutuhkan visualisasi status per tahapan (*live progress tracker*) dan log terminal interaktif dari proses komputasi yang sedang berlangsung.
3. **Isolasi Kegagalan & Fault Tolerance**: Engine bioinformatika eksternal (C++ binary PLINK/GEMMA dan Java Beagle) berisiko mengalami *Out-Of-Memory* (OOM) atau *segmentation fault* jika alokasi RAM server terlampaui. Kegagalan worker komputasi tidak boleh menumbangkan web server API.
4. **Dual-Environment (Lokal Tanpa Beban Ops vs. Server Kluster Produksi)**: Pengembang atau peneliti harus dapat menjalankan platform di laptop/workstation lokal tanpa keharusan mengonfigurasi kluster orkestrasi yang berat, namun infrastruktur yang sama harus dapat diskalakan secara horizontal (*distributed worker pool*) di server produksi.

---

## Keputusan
Kami memutuskan untuk mengadopsi **Celery** dengan broker pesan dan result backend **Redis** (`CELERY_BROKER_URL` & `CELERY_RESULT_BACKEND`), dipadukan dengan abstraksi rantai eksekusi **Celery Canvas (`chain`)** dan mekanisme **Fallback Threading Lokal** saat Celery/Redis tidak terpasang.

### Implementasi Teknis Inti:
1. **Pipeline Composition via Celery Canvas**:
   Orkestrator pada [`pipeline_orchestrator.py`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/tasks/pipeline_orchestrator.py) merangkai 10 sub-task atomik menggunakan `chain(*signatures).apply_async()`. Setiap sub-task mewakili satu tahapan terisolasi yang memperbarui status database (`JobStatus` dan `PipelineStep`) saat dimulai dan diselesaikan.
2. **Durabilitas Worker & Dead-Letter Handling**:
   Konfigurasi Celery pada [`celery_app.py`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/tasks/celery_app.py) mengaktifkan:
   - `task_acks_late = True`: Task baru diakui (*acknowledged*) setelah fungsi selesai dieksekusi, mencegah hilangnya job di antrean saat worker mati di tengah proses.
   - `task_reject_on_worker_lost = True`: Jika worker bioinformatika mengalami *crash* mendadak (misal OOM killer OS), task akan otomatis di-*requeue* atau ditandai sebagai gagal secara elegan tanpa menggantung status job.
3. **Dual-Mode Execution Fallback**:
   Untuk kenyamanan pengembangan lokal (sejalan dengan filosofi fallback SQLite di ADR-001), [`celery_app.py`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/tasks/celery_app.py) menyediakan kelas emulasi `_LocalCelery`, `_LocalTask`, dan `_LocalChain`. Jika modul `celery` atau koneksi Redis tidak tersedia, pipeline dialihkan secara transparan ke *daemon thread* lokal (`threading.Thread`) dengan API signature yang identik.

---

## Alternatif yang Dipertimbangkan

### 1. Celery + Redis (Stack Terpilih)
- **Kelebihan**:
  - Ekosistem Python asli yang sangat matang, berbobot ringan, dan terintegrasi langsung dengan model domain SQLAlchemy dan skema Pydantic di FastAPI.
  - Redis bertindak ganda sebagai message broker antrean tugas berlatensi sangat rendah ($< 1\text{ ms}$) sekaligus media pub/sub untuk penyiaran log terminal secara real-time ke WebSocket/SSE client.
  - Dukungan native untuk konkurensi granular (`--concurrency`), alokasi task per-queue (misalnya antrean `high_memory` untuk imputasi/GEMMA dan `io_bound` untuk ekstraksi file), serta penjadwalan periodik via `celery beat`.
  - Mudah di-mock dan di-fallback untuk pengembang lokal tanpa mewajibkan daemon eksternal.
- **Kekurangan**:
  - Celery Canvas (`chord` / `chain`) memiliki keterbatasan visualisasi DAG native jika dibandingkan dengan UI orchestrator khusus data pipeline.
  - Memerlukan pengelolaan state eksplisit di database relasional untuk riwayat langkah granular.
- **Kesimpulan**: Dipilih sebagai arsitektur primer karena menyajikan rasio performa, kesederhanaan operasional, dan integrasi kode Python terbaik untuk web application.

---

### 2. Apache Airflow
- **Kelebihan**:
  - UI visualisasi DAG bawaan yang kaya fitur untuk memantau dependensi antar task.
  - Penjadwalan berbasis waktu (*cron-like scheduling*) dan ekosistem operator data pipeline yang sangat luas.
  - Penanganan *retry* otomatis dan audit riwayat eksekusi tingkat enterprise.
- **Kekurangan**:
  - **Overhead Operasional Sangat Berat**: Airflow dirancang untuk batch ETL terjadwal (hourly/daily), bukan untuk *event-driven job execution* instan yang dipicu oleh interaksi klik pengguna web (latensi inisiasi DAG Airflow rata-rata beberapa detik hingga menit karena siklus *scheduler loop*).
  - Kebutuhan resource server tinggi: Memerlukan webserver tersendiri, scheduler terpisah, database metadata terdedikasi, serta worker Celery/Kubernetes.
  - Kompleksitas setup lokal yang tidak realistis untuk laptop peneliti non-DevOps.
- **Alasan Ditolak**: Latensi inisiasi tugas terlalu tinggi untuk aplikasi web interaktif, dan *operational footprint*-nya terlalu berlebihan untuk beban kerja orkestrasi per-pengguna.

---

### 3. Nextflow / Snakemake (Bioinformatics Workflow Engines)
- **Kelebihan**:
  - Standar *de facto* di kalangan bioinformatikawan untuk reproduksibilitas komputasi genomik.
  - Manajemen kontainer otomatis (Docker, Singularity/Apptainer) per proses komputasi.
  - Integrasi native dengan scheduler kluster HPC (SLURM, PBS, AWS Batch).
- **Kekurangan**:
  - **Isolasi dari Siklus Hidup Web Application**: Nextflow dan Snakemake berjalan sebagai CLI eksternal yang membaca/menulis file flat di disk, bukan pustaka terprogram (*programmable in-memory library*) yang mudah berkoordinasi langsung dengan transaksi database ORM dan sesi pengguna.
  - Pelacakan progres status per-baris dan integrasi interaktif ke frontend browser membutuhkan pembacaan parsing log file kustom atau integrasi REST listener eksternal (*Nextflow Tower / Seqera Platform*) yang menambah kompleksitas lisensi dan deployment.
  - Tidak cocok untuk tugas non-bioinformatika berdurasi mikro (seperti kalkulasi ringkasan statistik cepat, inspeksi header, dan pembersihan file sementara).
- **Alasan Ditolak**: Mengubah seluruh platform menjadi wrapper Nextflow akan mengaburkan batas arsitektur antara backend web dan engine komputasi. Pipeline 10 tahap GWAS kami lebih fleksibel dikontrol dan diinspeksi secara deterministik melalui kode Python modular Celery.

---

### 4. Temporal.io / Cadence (Stateful Orchestrator)
- **Kelebihan**:
  - Konsep *code-as-configuration* dengan *durable execution* (jika worker mati, eksekusi dapat dilanjutkan tepat dari baris kode terakhir).
  - Manajemen state kompleks dan timeout deterministik tanpa perlu mendesain *state machine* manual di database.
- **Kekurangan**:
  - Memerlukan kluster Temporal Server terpisah yang bergantung pada database Cassandra atau PostgreSQL tersendiri.
  - Kurva pembelajaran (*learning curve*) tinggi dan SDK Python Temporal masih relatif baru dibanding ekosistem Celery yang telah teruji lebih dari satu dekade.
  - Menghilangkan kemungkinan adanya *zero-dependency local fallback* yang menjadi prinsip portabilitas platform ini.
- **Alasan Ditolak**: Kompleksitas infrastruktur pendukung tidak sebanding dengan kebutuhan alur komputasi GWAS yang pada dasarnya bersifat sekuensial linear (*pipelined execution*).

---

### 5. In-process FastAPI BackgroundTasks Murni
- **Kelebihan**:
  - Nol dependensi pihak ketiga; tidak memerlukan Redis, Celery, atau message broker apa pun.
  - Sangat mudah diimplementasikan langsung di handler route FastAPI.
- **Kekurangan**:
  - **Berjalan di Proses Web Server yang Sama**: Jika analisis GEMMA atau PLINK memicu CPU 100% atau *memory leak*, web server FastAPI akan menjadi tidak responsif (*hung*), menyebabkan *HTTP 502/504* bagi seluruh pengguna lain.
  - Ketiadaan *distributed scaling*: Beban kerja tidak dapat dipindahkan ke server komputasi lain; server web dan worker terikat mati pada mesin yang sama.
  - State tugas hilang seketika saat web server di-*restart* atau di-*reload* oleh Uvicorn.
- **Alasan Ditolak**: Berisiko fatal terhadap stabilitas sistem di lingkungan produksi, melanggar prinsip dasar pemisahan *I/O-bound web serving* dan *CPU-intensive scientific computation*.

---

## Konsekuensi

### Konsekuensi Positif:
1. **Pemisahan Peran Bersih (*Separation of Concerns*)**: Web server FastAPI fokus melayani permintaan API, otentikasi, dan validasi skema input dengan latensi rendah; seluruh komputasi intensif didelegasikan ke worker Celery yang terisolasi.
2. **Skalabilitas Horizontal Bebas**: Di lingkungan kluster, worker Celery dapat diperbanyak secara independen pada beberapa server komputasi (GPU/CPU nodes) hanya dengan mengarahkan `CELERY_BROKER_URL` ke Redis sentral yang sama.
3. **Pengalaman Developer Lokal Tanpa Gesekan**: Mekanisme `_LocalCelery` memungkinkan pengembang baru memulai repositori dengan satu perintah (`python -m uvicorn app.main:app`), dan pipeline 10 tahap tetap berjalan mulus menggunakan thread lokal.
4. **Resiliensi Tinggi**: Fitur `task_acks_late=True` dan pembaruan berkala status tiap langkah ke PostgreSQL menjamin platform dapat mendeteksi kegagalan secara granular dan menyajikan pesan diagnostik yang jelas kepada pengguna.

### Konsekuensi Negatif & Mitigasi:
1. **Dependensi Operasional Redis di Produksi**:
   - *Mitigasi*: Redis dikonfigurasi dengan mode persistensi AOF (*Append Only File*) dan limit memori berbasis eviction policy `noeviction` untuk antrean tugas, serta dimonitor via health check container Docker Compose.
2. **Sinkronisasi State Antara Celery Result & Database**:
   - *Mitigasi*: Sistem tidak mengandalkan Celery Result Backend sebagai *single source of truth*. Setiap task langsung memperbarui tabel `AnalysisJob` di database relasional melalui transaksi sesi SQLAlchemy yang aman, sehingga status di UI selalu konsisten meskipun Celery worker selesai lebih cepat daripada polling frontend.
3. **Penyimpanan Berkas Sementara Lintas Mesin**:
   - *Mitigasi*: Dalam skenario kluster multi-node, direktori `STORAGE_ROOT` (`/data/workspaces`) harus dimount sebagai Shared Volume (NFS atau CephFS) agar berkas VCF dan output perantara dapat diakses secara transparan oleh semua worker.
