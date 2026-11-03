# ADR-004: Strategi Eksekusi Engine Bioinformatika (Native CLI Subprocess Wrapper vs. Container-per-Task & CWL)

## Status
Accepted

## Tanggal
2026-11-02

## Konteks
Platform **GWAS Web Platform** bergantung pada algoritma bioinformatika komputasional yang sangat berat untuk memproses data varian skala genomik (puluhan gigabyte file VCF berisi jutaan penanda SNP pada ribuan sampel individu). Algoritma-algoritma ini mencakup:
- **bcftools**: Normalisasi varian multialelik, sorting genomik, dan indexing bcf/vcf.
- **PLINK 1.9 & PLINK 2.0**: Filtrasi *Quality Control* tingkat sampel dan varian (MAF, HWE, Missingness), LD pruning, dan Principal Component Analysis (PCA).
- **Beagle 5.4**: Imputasi genotipe berbasis model Hidden Markov Model (HMM) yang berjalan di Java Virtual Machine (JVM).
- **GEMMA (Genome-wide Efficient Mixed-model Association)**: Perhitungan matriks kekerabatan (*Centered Relatedness Matrix*) dan dekomposisi eigen untuk Linear Mixed Models (LMM) guna mengontrol struktur populasi dan kriptik famili.

### Kendala dan Kebutuhan Teknis:
1. **Performa Komputasi & Efisiensi Memori (Bare-Metal Speed)**:
   Perhitungan matriks LMM dan filtrasi jutaan SNP membutuhkan optimasi instruksi CPU biner (AVX2, AVX-512, OpenMP, BLAS/LAPACK) serta representasi genotipe biner 2-bit (format `.bed` / `.pgen`). Setiap *overhead* virtualisasi atau translasi data akan melipatgandakan waktu eksekusi.
2. **Streaming Log Interaktif & Pembatalan Seketika (*Job Cancellation*)**:
   Pengguna di browser memantau progres analisis langkah-demi-langkah melalui antarmuka web. Sistem harus dapat menangkap *standard output* (stdout) dan *standard error* (stderr) secara non-blocking baris-demi-baris, menyimpannya ke berkas log workspace, dan menyiarkannya ke antarmuka pengguna. Jika pengguna membatalkan job, pohon proses (*process tree*) harus dapat dihentikan seketika tanpa meninggalkan proses *zombie* di CPU.
3. **Portabilitas & Dual-Environment**:
   Aplikasi harus dapat dijalankan oleh peneliti di mesin lokal tanpa dependensi rumit, namun tetap siap dideploy dalam arsitektur kluster berbasis kontainer di lingkungan produksi.

---

## Keputusan
Kami memutuskan untuk menggunakan pendekatan **Native CLI Subprocess Execution** yang dibungkus oleh modul pengendali proses terisolasi ([`run_cli_command`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/utils/process.py)), didukung oleh **Python Fallback Khusus Lingkungan Dev** dan **Image Worker Kontainer Tunggal (*Single-Worker Container*)** di lingkungan produksi.

### Arsitektur Implementasi:
1. **Wrapper Subproses Terkendali ([`app.utils.process.run_cli_command`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/utils/process.py))**:
   - Menjalankan biner beralamat tetap (`PLINK_BIN`, `GEMMA_BIN`, `BCFTOOLS_BIN`, `BEAGLE_JAR`) langsung pada tingkat sistem operasi worker.
   - Menggunakan *background reader threads* dengan *queue non-blocking* untuk membaca aliran stdout/stderr secara real-time, menulis ke berkas log panggung (`plink_qc.log`, `gemma_assoc.log`), dan memicu callback streaming.
   - Manajemen *Process Group* (`start_new_session=True` pada POSIX atau `CREATE_NEW_PROCESS_GROUP` pada Windows) yang memungkinkan pembunuhan seluruh sub-proses secara bersih saat sinyal pembatalan diterima atau terjadi *timeout* (`CLITimeoutError`).
   - Penangkapan bounded error log (`deque(maxlen=30)`) sehingga pesan galat kritis bioinformatika langsung tersaji di UI database tanpa memotong konteks penting.
2. **Mekanisme Fallback Otomatis di Lingkungan Dev**:
   Jika utilitas biner tidak terdeteksi di PATH sistem lokal (`which_tool` mengembalikan `None`), sistem tidak langsung gagal, melainkan beralih secara transparan ke algoritma Python lokal (seperti `run_python_fallback_qc` di [`qc_service.py`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/services/qc_service.py)) untuk dataset kecil pengujian.
3. **Model Kontainerisasi Tunggal di Produksi (*All-in-One Worker Image*)**:
   Di produksi, kontainer Celery Worker dibangun dengan menyertakan seluruh biner terkompilasi statis. Dengan demikian, eksekusi biner di dalam kontainer tetap berjalan sebagai proses native lokal tanpa perlu menyentuh socket Docker host.

---

## Alternatif yang Dipertimbangkan

### 1. Native CLI Subprocess via Controlled Wrapper (Stack Terpilih)
- **Kelebihan**:
  - **Zero Latency Overhead**: Biner langsung dieksekusi oleh OS kernel melalui syscall `execve` / `CreateProcess` tanpa penundaan inisialisasi kontainer ($< 5\text{ ms}$).
  - **Akses Penuh Fitur Hardware**: Mendukung utilisasi multithreading native (OpenMP) dan pustaka aljabar linear berkecepatan tinggi tanpa *throttling* virtualisasi.
  - **Streaming I/O Sangat Cepat**: Data streaming stdout/stderr langsung ditangkap via OS pipe ke worker Python.
  - **Kontrol Penuh Siklus Hidup Proses**: Sinyal terminasi (`SIGTERM` / `SIGKILL`) langsung dikirimkan ke PID proses biner terkait saat job dibatalkan oleh pengguna.
- **Kekurangan**:
  - Membutuhkan instalasi biner C++ dan runtime Java pada mesin worker.
- **Kesimpulan**: Pendekatan terbaik yang menggabungkan kecepatan bare-metal, responsivitas streaming web, dan kebersihan manajemen proses.

---

### 2. Docker-per-Task Execution (Ephemeral Containers)
- **Deskripsi**: Setiap tahapan analisis (misal QC atau GEMMA) mengeksekusi kontainer Docker baru secara dinamis menggunakan Docker SDK/CLI (`docker run --rm -v /storage:/data biocontainers/gemma:latest ...`).
- **Kelebihan**:
  - Isolasi dependensi biner total antar-tahap.
  - Pembatasan kuota resource keras per langkah via flag Docker (`--memory=8g`, `--cpus=4`).
- **Kekurangan**:
  - **Overhead Latensi Inisiasi**: Setiap eksekusi memerlukan waktu $1 - 4\text{ detik}$ hanya untuk inisiasi runtime Docker dan mounting volume per tahapan.
  - **Kerentanan Keamanan Ekstrem (Docker Socket Expose)**: Worker Celery yang berjalan di dalam kontainer harus memiliki akses mount ke `/var/run/docker.sock` (*Docker-out-of-Docker*), memberikan hak istimewa setara `root` pada host server.
  - **Konflik Izin Akses Berkas (Permission Mismatch)**: Kontainer ephemeral menulis berkas keluaran ke shared storage dengan user `root` (`UID 0`), menyebabkan worker Python reguler gagal membaca, memodifikasi, atau menghapus berkas sementara (*Permission Denied*).
  - Tidak dapat dijalankan di mesin pengembang lokal tanpa Docker Daemon aktif, atau di kluster HPC berbasis multi-tenant tanpa akses root.
- **Alasan Ditolak**: Latensi inisiasi terlalu tinggi, risiko keamanan fatal terkait docker socket, dan komplikasi izin berkas pada shared storage.

---

### 3. Common Workflow Language (CWL) / WDL via External Runner (Cromwell / Toil)
- **Deskripsi**: Mendefinisikan seluruh pipeline bioinformatika dalam spesifikasi file deklaratif CWL/WDL dan menjalankannya melalui runner pihak ketiga.
- **Kelebihan**:
  - Portabilitas standar internasional sains genomik lintas platform cloud (AWS Omics, Google Life Sciences).
  - Manajemen dependensi data pipeline yang ketat.
- **Kekurangan**:
  - **Rigid File-Centric Barrier**: CWL dirancang untuk eksekusi batch statis berbasis berkas. Sulit diintegrasikan secara dinamis dengan transaksi database ORM SQLAlchemy, pembaruan progres status UI secara real-time, atau injeksi parameter dinamis dari form frontend.
  - **Overhead Infrastruktur Berat**: Runner seperti Cromwell membutuhkan server JVM mandiri dengan konsumsi memori idle yang tinggi ($> 1\text{ GB}$).
  - Debugging dan pelacakan kegagalan sangat terisolasi di dalam abstraksi runner CWL, menyulitkan penyajian log diagnostik instan kepada pengguna di web.
- **Alasan Ditolak**: Mengorbankan fleksibilitas integrasi real-time aplikasi web modern demi kepatuhan format spesifikasi batch yang kaku.

---

### 4. Pure Python Rewrite (SciPy, statsmodels, scikit-allel)
- **Deskripsi**: Menulis ulang seluruh algoritma bioinformatika (QC, PCA, kinship, GLM/LMM) murni menggunakan pustaka Python tanpa biner eksternal.
- **Kelebihan**:
  - Nol dependensi biner; instalasi 100% via `pip install`.
  - Kode program homogen dan mudah di-debug dalam satu bahasa.
- **Kekurangan**:
  - **Performa Sangat Lambat (Performance Collapse)**: Algoritma LMM GEMMA dan format biner PLINK 2 dikembangkan selama bertahun-tahun dengan optimasi bitwise C/C++ tingkat rendah. Implementasi Python murni mengalami penurunan performa antara $50\times$ hingga $200\times$ pada matriks varian besar, serta menghabiskan RAM hingga puluhan gigabyte akibat overhead objek Python.
  - **Validitas Ilmiah**: PLINK dan GEMMA adalah standar industri yang telah divalidasi oleh puluhan ribu publikasi ilmiah peer-reviewed; menulis ulang algoritma secara mandiri berisiko memunculkan inkonsistensi numerik.
- **Alasan Ditolak**: Tidak layak secara komputasi untuk data genomik riil dan berisiko terhadap akurasi ilmiah hasil riset.

---

## Konsekuensi

### Konsekuensi Positif:
1. **Performa Maksimal Tanpa Bottleneck**: Seluruh operasi matematika intensif berjalan pada kecepatan bare-metal menggunakan biner C/C++ native yang teroptimasi CPU.
2. **Visibilitas Operasional Penuh**: Subprocess wrapper menangkap setiap baris keluaran log secara instan, memungkinkan pengguna melihat log komputasi langsung di dashboard saat proses berlangsung.
3. **Pembersihan Proses yang Andal**: Jika pengguna menekan tombol *Cancel Job* di UI, mekanisme penghentian process group menjamin tidak ada biner GEMMA atau PLINK yang tertinggal berjalan di background (*no orphan processes*).
4. **Fleksibilitas Deployment**: Bekerja mulus di laptop pengembang (dengan fallback Python otomatis) maupun di kluster server produksi (di dalam image Celery worker yang sudah terintegrasi).

### Konsekuensi Negatif & Mitigasi:
1. **Kebutuhan Kompilasi/Penyediaan Biner di Host/Container**:
   - *Mitigasi*: Disediakan `Dockerfile` worker produksi standar yang mengunduh dan memvalidasi biner resmi yang telah dikompilasi statis (*statically linked binaries*), serta panduan instalasi biner lokal pada dokumentasi repositori.
2. **Ketergantungan Format File Disk I/O**:
   - *Mitigasi*: Komunikasi antar-alat mengandalkan direktori staging berbasis per-job (`/jobs/{job_id}/{stage}`) dengan format standar genomik (`.vcf.gz`, `.bed/.bim/.fam`, `.assoc.txt`), meminimalkan translasi format yang tidak perlu.
