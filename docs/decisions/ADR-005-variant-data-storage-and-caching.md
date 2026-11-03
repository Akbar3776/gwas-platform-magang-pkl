# ADR-005: Strategi Penyimpanan & Caching Data Varian Genomik (Apache Parquet + Polars vs. Raw TSV & Tabel RDBMS)

## Status
Accepted

## Tanggal
2026-11-02

## Konteks
Keluaran utama dari analisis asosiasi genomik (*Genome-Wide Association Study*) melalui engine PLINK atau GEMMA adalah matriks statistik varian berskala sangat besar. Satu berkas hasil asosiasi (`.assoc.txt` atau `.assoc.linear`) umumnya memuat antara **100.000 hingga lebih dari 10.000.000 baris varian SNP**, dengan setidaknya 10-15 atribut per varian:
- Identitas lokus: `chr` (kromosom), `rs` (identitas SNP), `ps` (posisi pasang basa genetik).
- Frekuensi alel dan model: `allele1`, `allele0`, `af` (frekuensi alel minor).
- Estimasi efek dan varians: `beta` (ukuran efek), `se` (standard error), estimasi varians $V_g / V_e$.
- Statistik inferensi: `p_wald` (p-value Wald test), `p_lrt` (likelihood ratio test), `p_score`.

Ukuran berkas teks mentah (*raw tab-separated values*) berkisar antara **50 MB hingga 2 GB per job analisis**.

### Kendala dan Kebutuhan Teknis:
1. **Latensi Respon API Visualisasi (< 300 ms)**:
   Saat pengguna membuka dasbor visualisasi (Manhattan Plot dan QQ Plot), endpoint `/results/{job_id}/manhattan-data` harus mengembalikan data yang telah terfilter dan terstruktur dalam hitungan ratusan milidetik agar transisi antarmuka web tetap mulus.
2. **Efisiensi Memori Server & Browser Client**:
   Browser web tidak dapat dan tidak boleh memuat jutaan titik data JSON ke memori JavaScript (berisiko membekukan antarmuka atau memicu *browser crash* akibat *Out of Memory*). Server harus mampu melakukan *downsampling* cerdas: mempertahankan 100% varian paling signifikan ($\text{top } N$ dengan $p$-value terendah) dan mengambil sampel representatif merata untuk latar belakang kromosom dengan total kuota aman (misal $\le 12.000$ titik).
3. **Efisiensi Penyimpanan Disk (Storage Footprint)**:
   Sebagai platform multi-pengguna (*multi-tenant*), menyimpan puluhan berkas teks keluaran berukuran gigabyte akan dengan cepat menghabiskan kuota *storage root* server.
4. **Isolasi Siklus Hidup Data (Workspace Lifecycle)**:
   Data varian hasil komputasi harus terikat secara deterministik pada direktori workspace analisis (`/jobs/{job_id}/visualization`). Penghapusan job tidak boleh meninggalkan data yatim (*orphan data*) atau memicu operasi pembersihan yang membebani database utama.

---

## Keputusan
Kami memutuskan untuk menggunakan **Format Kolom Biner Apache Parquet** yang dikompresi dengan **Snappy** sebagai media penyimpanan cache data varian, dipadukan dengan strategi **Column Projection Pushdown** dan **Significance-Preserving Downsampling** saat menyajikan data ke antarmuka pengguna.

### Detail Arsitektur & Alur Implementasi:
1. **Generasi Cache Otomatis di Tahap Pipeline 9 ([`visualization_tasks.py`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/tasks/visualization_tasks.py))**:
   - Setelah kalkulasi asosiasi selesai, sub-task `run_visualization` memicu [`generate_manhattan_cache`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/services/manhattan_parser.py).
   - Berkas asosiasi mentah dibaca secara *chunked* (`chunksize=100.000`) dengan tipe data presisi rendah (`uint8` untuk CHR, `uint32` untuk BP, `float32` untuk $-\log_{10}P$) guna mencegah lonjakan konsumsi RAM worker.
   - Hasil ekstraksi disimpan ke berkas Parquet lokal: `/jobs/{job_id}/visualization/manhattan_cache.parquet`.
2. **Projection Pushdown Saat Pembacaan ([`manhattan_parser.py`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/services/manhattan_parser.py))**:
   - Fungsi `load_manhattan_cache` hanya membaca kolom yang dibutuhkan untuk rendering koordinat grafik: `columns=["CHR", "BP", "SNP", "LOG10_P"]`.
   - Mengabaikan kolom-kolom berat lainnya (`beta`, `se`, `allele0`, dll.) langsung pada level *Parquet column chunk reader*, menghemat lebih dari $60\%$ I/O disk.
3. **Significance-Preserving Downsampling ([`cap_manhattan_dataframe`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/services/manhattan_parser.py))**:
   - Mempertahankan $100\%$ SNP paling signifikan (`top_preserve = 2.500` dengan nilai $-\log_{10}P$ tertinggi) untuk memastikan tidak ada sinyal biologi atau puncak QTL yang hilang.
   - Melakukan *stride downsampling* berjarak merata untuk varian non-signifikan di sepanjang kromosom sehingga bentuk siluet genomik tetap utuh dengan batas maksimum $\le 12.000$ titik JSON yang sangat ringan untuk WebGL/Canvas di frontend.

---

## Alternatif yang Dipertimbangkan

### 1. Apache Parquet + Polars/PyArrow (Stack Terpilih)
- **Kelebihan**:
  - **Rasio Kompresi Sangat Tinggi**: Kompresi berbasis kolom (Snappy/ZSTD) pada data bertipe seragam menghasilkan ukuran berkas $75\% - 85\%$ lebih kecil dibandingkan TSV teks mentah (misal $500\text{ MB}$ berkas asosiasi berkurang menjadi $\sim 45\text{ MB}$ Parquet).
  - **Projection & Predicate Pushdown**: Pustaka pembaca dapat melompati pembacaan byte kolom yang tidak diminta langsung dari metadata *file footer*.
  - **Eksekusi Multi-threaded C++**: Polars/PyArrow memanfaatkan instruksi SIMD dan CPU multi-core untuk membaca dan memfilter jutaan baris dalam hitungan milidetik.
  - **Skema Bertipe Statis (*Self-Describing*)**: Menghilangkan kebutuhan parsing string berulang atau risiko inkonsistensi tipe data *null/nan*.
  - **Isolasi Berkas Sempurna**: Berkas cache berada di folder fisik job; saat direktori job dihapus, seluruh cache otomatis terhapus tanpa membebani database relasional.
- **Kekurangan**:
  - Format biner yang tidak dapat dibaca langsung dengan teks editor biasa (membutuhkan perkakas Parquet atau skrip inspeksi).
  - Bersifat *immutable/append-only* (namun hasil GWAS memang bersifat statis setelah komputasi selesai).
- **Kesimpulan**: Solusi paling efisien dalam hal rasio kompresi, kecepatan pembacaan I/O, dan kesederhanaan manajemen siklus hidup berkas.

---

### 2. Raw Flat TSV / CSV Files (Parsing Teks Berulang)
- **Deskripsi**: Menyimpan hasil asosiasi hanya sebagai file flat teks tab-separated dan membacanya langsung menggunakan `read_csv` setiap kali ada request visualisasi dari pengguna.
- **Kelebihan**:
  - Format teks universal yang mudah diinspeksi menggunakan perkakas terminal (`head`, `awk`, `less`).
  - Nol waktu pemrosesan konversi di akhir pipeline komputasi.
- **Kekurangan**:
  - **Bottleneck I/O Disk & CPU Sangat Parah**: Setiap permintaan endpoint `/manhattan-data` memaksa server membaca seluruh file ratusan megabyte dari disk, melakukan *string splitting*, dan konversi string-to-float untuk jutaan baris.
  - Latensi respons API lambat ($5 - 20\text{ detik}$ per panggilan), merusak pengalaman pengguna di antarmuka web.
  - Menghabiskan kapasitas penyimpanan server $4\times - 6\times$ lebih boros dibanding Parquet.
- **Alasan Ditolak**: Latensi I/O disk yang tinggi dan pemborosan CPU untuk parsing teks berulang tidak layak untuk arsitektur aplikasi web berkinerja tinggi.

---

### 3. Tabel Database Relasional Raksasa (PostgreSQL `gwas_variants` Table)
- **Deskripsi**: Menyisipkan setiap baris dari jutaan hasil varian ke dalam tabel relasional PostgreSQL terindeks (`job_id`, `chr`, `bp`, `snp`, `p_value`).
- **Kelebihan**:
  - Fleksibilitas query SQL standar (`SELECT ... WHERE chr = 1 AND p_value < 1e-5 ORDER BY p_value ASC LIMIT 500`).
  - Integritas referensial foreign key langsung ke tabel `AnalysisJob`.
- **Kekurangan**:
  - **Database Bloat Ekstrem**: Menyimpan 5 juta baris varian per job untuk 100 job akan menghasilkan **500 juta baris**. Ukuran tabel data dan indeks B-tree akan membengkak hingga ratusan gigabyte (*storage bloat*).
  - **Write Amplification & WAL Overload**: Operasi *bulk insert* jutaan baris pasca-analisis membebani Write-Ahead Log (WAL), memperlambat transaksi operasi pengguna lain (seperti login dan navigasi UI).
  - **Biaya Pemeliharaan Tinggi**: Autovacuum berkala menjadi sangat berat, dan operasi `CASCADE delete` saat job dihapus akan memicu *table-level write lock* berkepanjangan pada database utama.
- **Alasan Ditolak**: Menyimpan data matriks numerik bervolume masif ke dalam database relasional transaksional (OLTP) adalah *anti-pattern* arsitektural yang merusak stabilitas dan performa sistem secara keseluruhan.

---

### 4. File Dump JSON Utuh / NoSQL Document Store (MongoDB)
- **Deskripsi**: Mengonversi hasil GWAS menjadi file `.json` raksasa atau menyimpannya sebagai dokumen di MongoDB.
- **Kelebihan**:
  - Format native JavaScript; endpoint API dapat langsung meneruskan file/dokumen tanpa transformasi skema.
- **Kekurangan**:
  - **Overhead Ukuran Lebih Buruk dari TSV**: Format JSON menyertakan pengulangan nama kunci (*key overhead* seperti `{"chr": 1, "bp": ..., "p": ...}`), membuat ukuran berkas membengkak hingga $2\times - 3\times$ lipat ukuran file teks mentah.
  - **Konsumsi Memori RAM Python Meledak**: Memuat berkas JSON berukuran $> 500\text{ MB}$ menggunakan modul bawaan `json.loads` di Python membutuhkan alokasi RAM $3\times - 5\times$ dari ukuran file, memicu *Out of Memory* (OOM) pada Uvicorn web worker.
- **Alasan Ditolak**: Tidak efisien dalam penggunaan ruang penyimpanan disk, memori server, dan bandwidth jaringan.

---

## Konsekuensi

### Konsekuensi Positif:
1. **Kecepatan Akses Sub-Detik**: Endpoint `/results/{job_id}/manhattan-data` merespons dalam waktu $< 250\text{ ms}$ untuk dataset jutaan varian berkat pembacaan kolom selektif Parquet dan downsampling cerdas.
2. **Penghematan Ruang Penyimpanan Signifikan**: Pengurangan ukuran berkas hingga $> 75\%$ memungkinkan server menampung riwayat analisis lebih banyak tanpa kehabisan ruang disk.
3. **Frontend Tetap Responsif & Stabil**: Payload data yang dibatasi secara deterministik ($\le 12.000$ titik dengan 100% puncak signifikansi utuh) menjamin visualisasi di browser berjalan pada 60 FPS tanpa risiko tab crash.
4. **Isolasi Data Sempurna**: Berkas cache berada di folder fisik job; saat direktori job dihapus melalui retensi berkas, seluruh cache otomatis terhapus tanpa menyisakan residu di database operasional.

### Konsekuensi Negatif & Mitigasi:
1. **Waktu Tambahan Konversi di Tahap Pipeline 9**:
   - Proses pembuatan Parquet cache membutuhkan waktu sekitar $3 - 8\text{ detik}$ di akhir pipeline.
   - *Mitigasi*: Konversi dijalankan di dalam background Celery worker sebelum job ditandai sebagai `COMPLETED`, sehingga pengguna tidak merasakan penundaan saat membuka halaman hasil.
2. **Dependensi Pustaka Tambahan (`pyarrow` / `pandas`)**:
   - Memerlukan instalasi pustaka kompresi biner pada environment Python backend.
   - *Mitigasi*: Pustaka telah didaftarkan pada `backend/requirements.txt`, dengan fallback sementara `df.to_pickle()` jika modul biner belum terpasang di lingkungan dev minimalis.
