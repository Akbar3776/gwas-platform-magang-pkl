# 📐 System Design Document: Automated End-to-End GWAS Web Platform (10-Phase Pipeline)

**Dokumen Spesifikasi Teknis & Desain Arsitektur Sistem**
- **Status**: Production-Ready / Fully Implemented (Milestones M0–M10 Completed)
- **Prinsip Utama**: *"Inspect $\rightarrow$ Validate $\rightarrow$ Run"*
- **Cakupan**: Arsitektur Sistem, Rasional Pemilihan Tech Stack, Orkestrasi Pipeline Bioinformatika 10 Tahap, Kalkulasi Statistik GEMMA & PLINK 2, Struktur Populasi & LD Decay, Post-GWAS & Clumping, Anotasi Gen Kandidat GFF3, Optimasi Visualisasi Data Besar, Skema Penyimpanan, Keamanan, dan Spesifikasi API.

---

## 1. Ringkasan Eksekutif & Filosofi Desain

Analisis **Genome-Wide Association Study (GWAS)** bertujuan menemukan asosiasi statistik antara varian genetik (*Single Nucleotide Polymorphisms* / SNPs) dan sifat fenotipe atau penyakit tertentu. Secara tradisional, alur kerja ini terfragmentasi di berbagai *command-line tools* (**bcftools**, **PLINK 2 / 1.9**, **Beagle 5.4**, **GEMMA**, **GAPIT/R**, parser **GFF3**) dengan format input/output yang berbeda-beda serta tuntutan memori (RAM) dan CPU yang tinggi.

Platform ini mengintegrasikan seluruh metodologi dari panduan standar `Pipeline_Gwas.md` ke dalam satu sistem berbasis web yang modern, modular, dan transparan:

1. **Filosofi "Inspect $\rightarrow$ Validate $\rightarrow$ Run"**:
   - Memastikan integritas data dan rekonsiliasi ID sampel antara file VCF dan file fenotipe pada tahap awal (Fase 1) sebelum proses komputasi berat dimulai.
   - Deteksi dini sampel yang hilang (*missingness*), token invalid, dan ID duplikat sehingga pipeline tidak mengalami kegagalan fatal di tengah jalan.
2. **Observabilitas Penuh (*Full Pipeline Transparency*)**:
   - Menyediakan akses *real-time* ke log eksekusi setiap tahap (`inspection.log`, `bcftools.log`, `plink_qc.log`, `beagle.log`, `structure.log`, `gemma.log`, `multilocus.log`, `postgwas.log`, `visualization.log`, `annotation.log`).
   - Penjelajah file kerja job (*Job File Browser & Inline Preview*) untuk memantau artefak di setiap tahapan.
   - Status `[SKIPPED]` yang jujur dan transparan jika tahapan opsional dilewati (mis. imputasi Beagle atau multi-locus R jika runtime tidak tersedia).
3. **Dual-Mode Execution (Docker & Windows Standalone Native)**:
   - Berjalan secara terdistribusi penuh menggunakan **Docker Compose** (PostgreSQL + Redis + Celery Worker) atau berjalan **secara lokal langsung di Windows/Linux** tanpa Docker berkat *automatic fallback* ke **SQLite** dan *pure-Python/NumPy statistical fallback engines*.
4. **Ketahanan & Keselamatan Memori (*Memory Safety*)**:
   - Melindungi *host server* dari kegagalan *Out-Of-Memory (OOM)* saat menjalankan Beagle 5.4 atau mem-parsing jutaan SNP melalui pembatas memori tingkat OS (`RLIMIT_AS`), batas *heap* JVM (`-Xmx`), pembacaan bertahap (*chunked streaming*), dan pembuatan arsip ZIP secara *on-the-fly*.
5. **Visualisasi Anti-Crash untuk Data Genomik Besar**:
   - Menggabungkan *stratified downsampling* berbasis **Apache Parquet** di *backend* dengan perenderan **HTML5 2D `<canvas>`** + deduplikasi piksel di *frontend* agar plot jutaan SNP tetap interaktif dalam waktu $< 5\text{ ms}$.

---

## 2. Arsitektur Tingkat Tinggi & Pemilihan Tech Stack

### 2.1 Diagram Arsitektur Komponen

```mermaid
flowchart TB
    subgraph Browser["💻 Lapisan Klien (React 18 SPA)"]
        UI["UI Komponen & Visualisasi\n- DataInspectionPanel (Histogram/Boxplot)\n- QcComparisonPanel (Before/After QC)\n- PcaScatterPlot & ScreePlot\n- KinshipHeatmap & LdDecayPlot\n- ManhattanPlot & QQPlot (Canvas)\n- ThresholdSelector & LeadSnpTable\n- PeakComparisonTable\n- CandidateGenesTable (GFF3)"]
    end

    subgraph Edge["🌐 Reverse Proxy (Produksi)"]
        NGINX["Nginx 1.25 Alpine\n- Static Asset Serving\n- Proxy /api/v1/* (Chunk 100MB)"]
    end

    subgraph APILayer["⚙️ FastAPI Application Layer"]
        AUTH["/auth (JWT)"]
        UPLOAD["/upload (Chunked SHA-256)"]
        PIPELINE["/pipeline (Orkestrasi)"]
        JOBS["/jobs (Status, Log, File Explorer)"]
        RESULTS["/results (Manhattan, Inspection, QC, PCA, Kinship, LD, Thresholds, Clump, Genes)"]
        EXPORT["/export (Streaming ZIP & PDF)"]
    end

    subgraph WorkerLayer["🚀 Celery Asynchronous Workers (10 Tahap)"]
        T1["Fase 1: Inspeksi Fenotipe & ID\n(phenotype_service)"]
        T2["Fase 1B: Normalisasi VCF\n(bcftools / fallback)"]
        T3["Fase 2: Quality Control\n(PLINK 2 / 1.9: Inbred vs Outcross)"]
        T4["Fase Imputasi: Beagle 5.4\n(None / Ref / Ref-Free)"]
        T5["Fase 3+4D: Struktur & LD Decay\n(PCA, Kinship K, KING, LD curve)"]
        T6["Fase 4A/4B: Asosiasi GWAS\n(GEMMA LMM/GLM + Covariates)"]
        T7["Fase 4C: Multi-Locus GWAS\n(FarmCPU / BLINK / Skipped)"]
        T8["Fase 4E/4F: Pasca-GWAS & Clumping\n(Lambda GC, Dynamic Thresh, Lead SNPs)"]
        T9["Fase 4G: Visualisasi & Parquet Cache\n(Parquet Engine + Lead Annotation)"]
        T10["Fase 5: Anotasi Gen Kandidat GFF3\n(Genik/Near-gen/Intergenik + GO List)"]
    end

    subgraph DataLayer["💾 Persistence Layer"]
        DB[("PostgreSQL 15 / SQLite")]
        FS[("Storage Root /jobs, /datasets, /fixtures")]
    end

    Browser <--> Edge <--> APILayer
    APILayer <--> DB
    APILayer <--> FS
    APILayer --> WorkerLayer
    WorkerLayer <--> FS
    WorkerLayer --> DB
    T1 --> T2 --> T3 --> T4 --> T5 --> T6 --> T7 --> T8 --> T9 --> T10
```

### 2.2 Matriks Keputusan Pemilihan Tech Stack (*Architectural Decision Records*)

| Komponen | Pilihan Teknologi | Alternatif yang Dipertimbangkan | Alasan Teknis Pemilihan (*Rationale*) |
| :--- | :--- | :--- | :--- |
| **API Framework** | **FastAPI (Python)** | Django REST, Flask | Mendukung *async/await* native untuk unggahan *chunk* dan *StreamingResponse*, validasi skema otomatis berbasis Pydantic v2 (inti Rust), serta integrasi langsung dengan ekosistem ilmiah Python (NumPy, SciPy, Pandas, PyArrow). |
| **Database ORM & Engine** | **SQLAlchemy 2.0 + PostgreSQL 15 (Fallback SQLite)** | MongoDB, Raw SQL | Menjamin konsistensi transaksional ACID (relasi `users` $\rightarrow$ `datasets` $\rightarrow$ `jobs` $\rightarrow$ `results`) sekaligus fleksibilitas parameter dinamis (`JSONB`). Melalui `TypeDecorator` kustom pada [`database.py`](../backend/app/db/engine/database.py), sistem berjalan otomatis di atas SQLite untuk lingkungan pengujian dan pengembangan lokal. |
| **Task Queue & Broker** | **Celery 5.4 + Redis 7 (Fallback Synchronous)** | RabbitMQ, RQ, Cron | Mendukung *task chaining* 10 tahap (`chain(t1, t2, ..., t10)`), penyampaian *piped context* antar-tahap, pemisahan antrean (`normalization`, `qc`, `imputation`, `structure`, `gwas`), serta pencabutan tugas (`control.revoke`) saat pengguna menghentikan job. |
| **Cache Visualisasi** | **Apache Parquet (PyArrow + Snappy)** | CSV, JSON, SQLite | Format kolumnar Parquet memangkas ukuran file hingga $>85\%$ dan memuat puluhan ribu koordinat CHR/BP/P-value ke DataFrame dalam hitungan milidetik tanpa membebani memori browser. |
| **Engine Visualisasi Frontend** | **HTML5 2D `<canvas>` + Pixel Deduplication** | D3.js SVG, Recharts, Plotly SVG | Pustaka berbasis SVG membuat node DOM untuk setiap SNP, menyebabkan *browser freeze* pada $>20.000$ titik. HTML5 `<canvas>` menggambar ratusan ribu titik pada satu bitmap dalam $<5\text{ ms}$. |
| **Generator Laporan Ilmiah** | **Jinja2 + WeasyPrint (+ Pure-Python PDF Fallback)** | ReportLab murni, LaTeX | Merender laporan eksekutif ilmiah berstandar publikasi dalam format PDF 1.4 menggunakan template HTML/CSS, lengkap dengan generator PDF murni Python jika pustaka C GTK/Pango tidak terpasang di host Windows. |

---

## 3. Spesifikasi Rinci Pipeline Bioinformatika 10 Tahap

### Fase 1: Fenotipe & Inspeksi Data ([`inspection_tasks.py`](../backend/app/tasks/inspection_tasks.py) & [`phenotype_service.py`](../backend/app/services/phenotype_service.py))
- **Tujuan**: Mencegah kegagalan runtime di tahap komputasi lanjut melalui rekonsiliasi ID sampel dan verifikasi distribusi data.
- **Logika & Operasi**:
  1. Rekonsiliasi ID sampel antara VCF header (`#CHROM POS ID ...`) dan tabel fenotipe (`FID`, `IID`).
  2. Pengecekan sampel duplikat, token missing (`""`, `"."`, `"NA"`, `"-9"`, `"NaN"`), dan nilai non-numerik.
  3. Perhitungan statistik deskriptif: $N$, mean, median, standar deviasi, IQR, skewness, min, max.
  4. Histogram otomatis menggunakan aturan **Freedman-Diaconis**:
     $$h = 2 \frac{\text{IQR}}{n^{1/3}}, \quad k = \left\lceil \frac{\max(x) - \min(x)}{h} \right\rceil$$
  5. Boxplot dengan identifikasi outlier berbasis aturan Tukey ($< Q_1 - 1.5\text{IQR}$ atau $> Q_3 + 1.5\text{IQR}$).
  6. Ekspor artefak: `keep_ids.txt` (daftar sampel lolos sinkronisasi) dan `pheno_plink.txt` (format PLINK 3 kolom `FID IID PHENO`).

### Fase 1B: Normalisasi VCF ([`normalization_tasks.py`](../backend/app/tasks/normalization_tasks.py))
- **Tujuan**: Memastikan file VCF terurut secara koordinat kromosom (*sorted*), indel terstandarisasi, dan terindeks Tabix.
- **Perintah CLI**:
  ```bash
  bcftools view -h <vcf>
  bcftools sort -Oz -o sorted.vcf.gz <vcf>
  bcftools norm -f <fasta> -Oz -o normalized.vcf.gz sorted.vcf.gz  # jika FASTA tersedia
  bcftools index -t normalized.vcf.gz
  ```
- **Fallback**: Validasi header VCF murni Python dan kompresi gzip jika `bcftools` tidak terinstal.

### Fase 2: Quality Control / QC ([`qc_tasks.py`](../backend/app/tasks/qc_tasks.py) & [`qc_service.py`](../backend/app/services/qc_service.py))
- **Tujuan**: Mengeliminasi artefak genotipe dan varian langka dengan membedakan mode organisme.
- **Aturan Mode Organisme**:
  - `inbred` (klonal, galur murni, vegetatif, selfing): **TANPA** filter HWE (`--hwe` dimatikan).
  - `outcross` (kawin silang): **DENGAN** filter HWE (default `--hwe 1e-6`).
- **Perintah PLINK 2**:
  ```bash
  plink2 --vcf normalized.vcf.gz --snps-only just-acgt --max-alleles 2 \
         --set-all-var-ids @:# --keep keep_ids.txt \
         --maf 0.05 --geno 0.05 --mind 0.05 \
         [--hwe 1e-6 bila outcross] \
         --make-bed --out plink_qc
  plink2 --bfile plink_qc --export vcf bgz --out plink_qc
  ```
- **Hasil & Evaluasi**: Menghasilkan `qc_comparison.json` yang membandingkan metrik sebelum vs sesudah filter, serta memberikan peringatan jika kehilangan SNP $> 50\%$.

### Fase Imputasi Genotipe Opsional ([`imputation_tasks.py`](../backend/app/tasks/imputation_tasks.py))
- **Tujuan**: Mengestimasi alel yang tidak terukur menggunakan **Beagle 5.4**.
- **Mode Eksekusi**:
  1. `none`: Dilewati secara sadar dengan pencatatan status `[SKIPPED]`.
  2. `reference`: Imputasi terpandu menggunakan panel referensi populasi (1000 Genomes).
  3. `reference_free`: Phasing dan imputasi mandiri tanpa panel luar.
- **Batas Memori**: JVM `-Xmx` dialokasikan maksimal 80% dari kuota worker dengan proteksi `-XX:+ExitOnOutOfMemoryError`. Jika Java atau panel tidak tersedia, task menghasilkan status `[SKIPPED]` yang bersih dan melanjutkan analisis menggunakan genotipe hasil QC.

### Fase 3 + 4D: Struktur Populasi, Kinship & LD Decay ([`structure_tasks.py`](../backend/app/tasks/structure_tasks.py) & [`structure_service.py`](../backend/app/services/structure_service.py))
- **Tujuan**: Mengukur stratifikasi populasi, kekerabatan antar-sampel, dan laju peluruhan LD untuk rekomendasi model statistik.
- **Langkah-langkah Komputasi**:
  1. **LD Pruning**: `--indep-pairwise 50 5 0.2` untuk membuang redundansi korelasi antar-varian.
  2. **PCA (Principal Component Analysis)**: Dekomposisi 10 PC utama, perhitungan PVE (*Proportion of Variance Explained*), dan pengelompokan k-means.
  3. **Centered Kinship Matrix ($K$)**:
     $$K = \frac{1}{p} W W^T, \quad w_{ik} = \frac{g_{ik} - 2p_k}{\sqrt{2p_k(1 - p_k)}}$$
  4. **KING Kinship Matrix**: Menghitung koefisien kekerabatan robust $\phi_{ij}$ untuk mendeteksi sampel kembar/duplikat ($\phi > 0.354$) dan kerabat tingkat pertama ($\phi > 0.177$).
  5. **LD Decay Curve**: Menghitung $r^2$ berpasangan dalam jendela jarak fisik (bp), mengestimasi titik *half-decay* dan jarak saat $r^2 < 0.2$.
  6. **Rekomendasi Model Otomatis**: Menentukan penggunaan **GLM** (jika tidak ada stratifikasi & tidak ada kekerabatan) vs **LMM** (jika terdeteksi struktur populasi atau kekerabatan signifikan).
  7. **Ekspor Kovariat**: Menghasilkan `covariates.txt` untuk GEMMA (kolom pertama bernilai 1 sebagai intercept) dan `covariates_plink.txt` untuk PLINK 2.

### Fase 4A/4B: Analisis Asosiasi GWAS GLM / LMM ([`gemma_tasks.py`](../backend/app/tasks/gemma_tasks.py) & [`assoc_normalizer.py`](../backend/app/services/assoc_normalizer.py))
- **Tujuan**: Melakukan uji asosiasi varian terhadap fenotipe dengan koreksi kovariat dan matriks kekerabatan.
- **Model Matematika LMM**:
  $$y = W\alpha + x\beta + u + \epsilon, \quad u \sim \mathcal{N}(0, \lambda \tau^{-1} K), \quad \epsilon \sim \mathcal{N}(0, \tau^{-1} I_n)$$
- **Penanganan Kovariat**:
  - Menggunakan kovariat PC hasil PCA (`-c covariates.txt`).
  - Nilai fenotipe disinkronkan secara ketat berdasarkan IID ke kolom ke-6 file `.fam`.
- **Normalisasi Hasil Asosiasi**:
  - `assoc_normalizer.py` memetakan output PLINK2 (`--glm`) atau GEMMA (`.assoc.txt`) ke dalam format kanonikal 10 kolom:
    `chr`, `pos`, `rs`, `a1`, `a0`, `af`, `beta`, `se`, `p`, `n`.
  - Disimpan sebagai `gwas_results.normalized.tsv`.

### Fase 4C: Multi-Locus GWAS ([`multilocus_tasks.py`](../backend/app/tasks/multilocus_tasks.py))
- **Tujuan**: Mencegah *false positives* dan mendeteksi lokus independen menggunakan model multi-lokus iteratif (**FarmCPU** dan **BLINK**).
- **Eksekusi**: Memanggil skrip R `gapit_runner.R` jika lingkungan R tersedia. Jika tidak diaktifkan atau R tidak terinstal, tahap ini dicatat sebagai `[SKIPPED]` secara elegan tanpa menggagalkan pipeline.

### Fase 4E/4F: Pasca-GWAS, Lambda GC, Dynamic Thresholds & Clumping ([`postgwas_tasks.py`](../backend/app/tasks/postgwas_tasks.py) & [`postgwas_service.py`](../backend/app/services/postgwas_service.py))
- **Kalkulasi Faktor Inflasi Genomik ($\lambda_{GC}$)**:
  $$\lambda_{GC} = \frac{\text{median}(\chi^2_{\text{observed}})}{0.4549364}, \quad \text{dengan } \chi^2_i = \left(\frac{\beta_i}{\text{SE}_i}\right)^2$$
  *Catatan penting*: $\lambda_{GC}$ dihitung secara jujur tanpa *clamping* artifisial ($\lambda < 1.0$ tidak dipaksa menjadi 1.0) sehingga deflasi dapat terdeteksi.
- **Ambang Signifikansi Dinamis**:
  1. *Bonferroni*: $\alpha / M$ (dengan $M$ = jumlah uji varian).
  2. *Suggestive*: $1 \times 10^{-5}$ atau $1 / M$.
  3. *FDR (Benjamini-Hochberg)*: Nilai kritis $p$-value yang memenuhi $q < 0.05$.
  4. *LOD Score*: Konversi skor LOD ke $p$-value ($p = 10^{-\text{LOD}}$).
- **LD-Aware Clumping**:
  - Menggunakan jarak peluruhan LD decay dari Fase 3 sebagai radius clumping.
  - Mengekstrak **Lead SNPs** independen dan mencatat varian yang ter-clump di sekitarnya. Disimpan ke `lead_snps.json` dan `lead_snps.tsv`.

### Fase 4G: Visualisasi & Parquet Caching ([`visualization_tasks.py`](../backend/app/tasks/visualization_tasks.py))
- **Tujuan**: Membangun file cache Parquet terkompresi Snappy untuk perenderan instan di antarmuka web.
- **Metadata Tersemat**: Menyematkan seluruh nilai ambang batas dinamis, jumlah Lead SNPs, dan parameter visualisasi ke dalam metadata file Parquet.

### Fase 4H: Perbandingan Puncak GWAS ([`multilocus_tasks.py`](../backend/app/tasks/multilocus_tasks.py))
- **Tujuan**: Membandingkan konsistensi lokus puncak antara Single-Locus (GEMMA) dan Multi-Locus (FarmCPU/BLINK).
- **Output**: Menghasilkan `Ringkasan_Puncak_GWAS.csv` dan `peak_comparison.json` yang mengelompokkan puncak menjadi *Consistent*, *Single-Only*, dan *Multi-Only*.

### Fase 5: Anotasi Gen Kandidat GFF3 ([`annotation_tasks.py`](../backend/app/tasks/annotation_tasks.py) & [`annotation_service.py`](../backend/app/services/annotation_service.py))
- **Tujuan**: Menghubungkan varian signifikan secara statistik ke fungsi genetik biologis.
- **Logika Pemetaan**:
  1. Parser streaming GFF3 dengan normalisasi penamaan kromosom (`Chr1`, `chr01`, `1`, `NC_...`).
  2. Pemetaan Lead SNPs ke fitur gen dalam jendela LD decay ($\pm \text{window\_bp}$).
  3. Klasifikasi biologis varian:
     - **Genik**: Varian berada persis di dalam badan gen ($\text{start} \le \text{pos} \le \text{end}$).
     - **Near-gen**: Varian berada di luar gen tetapi masih dalam jarak $\le \text{window\_bp}$ (daerah promotor/regulatori).
     - **Intergenik**: Jarak varian ke gen terdekat $> \text{window\_bp}$.
  4. Ekspor daftar gen kandidat unik ke `Daftar_Gen_Untuk_GO.txt` yang siap digunakan langsung untuk analisis pengayaan *Gene Ontology* (GO).

---

## 4. Arsitektur Antarmuka & Visualisasi Frontend

### 4.1 Tab Panel Hasil Analisis Komprehensif ([`GwasResultsPanel.jsx`](../frontend/src/components/GwasResultsPanel.jsx))
Antarmuka hasil menyajikan 6 tab navigasi interaktif:
1. **📊 Manhattan & QQ Plot**:
   - Manhattan Plot & QQ Plot berbasis HTML5 2D Canvas dengan kecepatan render $< 5\text{ ms}$.
   - Bilah pengatur ambang batas dinamis (Bonferroni, Suggestive, FDR, LOD) yang mengubah garis ambang secara *real-time*.
   - Filter pemilihan kromosom spesifik (`ALL`, `1`, `2`, ..., `22`).
2. **🎯 Ambang Batas & Lead SNPs**:
   - Pemilih metode threshold dengan deskripsi formula dan kartu nilai $-\log_{10}(p)$.
   - Tabel Lead SNPs hasil clumping dengan rincian alel, efek beta, standar error, dan daftar varian yang ter-clump.
3. **🧬 Struktur Populasi & LD**:
   - Kartu rekomendasi model GLM vs LMM beserta temuan PCA dan Kinship.
   - Plot sebaran PCA (PC1 vs PC2) dengan pewarnaan klaster k-means dan Scree Plot (PVE varians terjelaskan).
   - Heatmap matriks kekerabatan (Centered Kinship) dan kurva peluruhan LD decay dengan penanda garis *half-decay*.
4. **🔍 Inspeksi & QC**:
   - Panel inspeksi fenotipe dengan histogram Freedman-Diaconis, boxplot outlier, dan metrik rekonsiliasi sampel.
   - Panel perbandingan QC sebelum vs sesudah filter varian dan sampel.
5. **🔬 Multi-Locus GWAS**:
   - Tabel perbandingan lokus puncak asosiasi antara Single-Locus vs Multi-Locus (FarmCPU & BLINK).
6. **🌿 Anotasi Gen GFF3**:
   - Tabel pemetaan Lead SNPs ke fitur gen terdekat dengan badge kategori biologis (Genik, Near-gen, Intergenik).
   - Tombol unduh satu klik untuk file `Daftar_Gen_Untuk_GO.txt`.

---

## 5. Referensi Endpoint REST API Lengkap

| Metode | Endpoint | Deskripsi |
| :--- | :--- | :--- |
| `POST` | `/api/v1/pipeline/{dataset_id}/start` | Memicu eksekusi 10 tahap pipeline GWAS secara asinkron. |
| `GET` | `/api/v1/jobs/{job_id}/status` | Mengambil status keseluruhan, tahap aktif, waktu berjalan, dan log per tahap. |
| `GET` | `/api/v1/jobs/{job_id}/logs` | Mengambil teks log eksekusi lengkap dari seluruh tahapan. |
| `GET` | `/api/v1/jobs/{job_id}/files` | Daftar seluruh file input, intermediate, dan output dari sebuah job. |
| `GET` | `/api/v1/jobs/{job_id}/files/content` | Pratinjau teks (150 baris pertama) atau unduhan file fisik dari job. |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | Membatalkan job aktif dan mencabut task Celery terkait. |
| `DELETE` | `/api/v1/jobs/{job_id}` | Menghapus riwayat job dan membersihkan direktori kerjanya di disk. |
| `GET` | `/api/v1/results/{job_id}/manhattan-data` | Data terkompresi CHR, BP, SNP, $-\log_{10}(p)$ untuk Manhattan & QQ Plot. |
| `GET` | `/api/v1/results/{job_id}/inspection` | Data hasil inspeksi fenotipe, histogram, dan boxplot (Fase 1). |
| `GET` | `/api/v1/results/{job_id}/qc-comparison` | Data perbandingan metrik varian dan sampel sebelum/sesudah QC (Fase 2). |
| `GET` | `/api/v1/results/{job_id}/pca` | Data koordinat PCA 10 komponen utama dan nilai PVE (Fase 3). |
| `GET` | `/api/v1/results/{job_id}/kinship` | Data matriks kekerabatan Centered Kinship dan tabel KING (Fase 3). |
| `GET` | `/api/v1/results/{job_id}/ld-decay` | Data kurva penurunan nilai $r^2$ terhadap jarak fisik (Fase 4D). |
| `GET` | `/api/v1/results/{job_id}/model-recommendation`| Rekomendasi otomatis pemilihan model statistik GLM vs LMM (Fase 3). |
| `GET` | `/api/v1/results/{job_id}/thresholds` | Ambang signifikansi dinamis Bonferroni, FDR, LOD, Suggestive (Fase 4E). |
| `GET` | `/api/v1/results/{job_id}/lead-snps` | Daftar varian Lead SNPs independen hasil clumping (Fase 4F). |
| `GET` | `/api/v1/results/{job_id}/peak-comparison` | Perbandingan puncak Single-Locus vs Multi-Locus GWAS (Fase 4H). |
| `GET` | `/api/v1/results/{job_id}/candidate-genes` | Pemetaan Lead SNPs ke fitur gen GFF3 dan daftar GO enrichment (Fase 5). |
| `GET` | `/api/v1/results/{job_id}/export` | Aliran *streaming* unduhan berkas arsip `.zip` seluruh artefak job. |
| `GET` | `/api/v1/results/{job_id}/report` | Unduhan dokumen laporan ilmiah resmi berformat PDF. |
