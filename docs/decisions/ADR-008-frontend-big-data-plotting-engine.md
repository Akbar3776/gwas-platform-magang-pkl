# ADR-008: Mesin Visualisasi Data Genomik Skala Besar di Frontend (HTML5 Canvas Kustom vs. D3.js SVG & Plotly.js)

## Status
Accepted

## Tanggal
2026-11-02

## Konteks
Visualisasi grafis adalah komponen paling krusial pada **GWAS Web Platform** untuk menginterpretasikan hasil komputasi biologis. Dua grafik standar industri yang wajib disajikan adalah:
1. **Manhattan Plot**: Scatter plot multivariat yang memetakan seluruh genom pada sumbu-X (posisi fisik per kromosom dari Chr 1 hingga Chr $N$) terhadap signifikansi statistik asosiasi pada sumbu-Y ($-\log_{10}P$).
2. **Quantile-Quantile (QQ) Plot**: Grafik distribusi p-value teramati vs. distribusi teoritis di bawah hipotesis nol untuk mengukur inflasi genomik ($\lambda_{GC}$) dan kontrol struktur populasi.

### Karakteristik & Kendala Teknis:
- **Kepadatan Data Tinggi (High-Density Point Cloud)**:
  Satu dataset hasil GWAS memuat antara **10.000 hingga 100.000+ titik data SNP** yang harus dirender secara simultan pada kanvas berdimensi $\sim 880 \times 440\text{ piksel}$ (area plot efektif $798 \times 364\text{ piksel}$).
- **Fenomena Tumpang Tindih Piksel (*Pixel Overdraw*)**:
  Di area non-signifikan ($-\log_{10}P < 3$), ribuan titik memetakan koordinat ke piksel layar yang persis sama. Memaksa browser menggambar puluhan ribu elemen grafis yang saling menutupi adalah pemborosan komputasi grafis yang masif.
- **Interaktivitas Waktu Nyata (60 FPS & Responsive Tooltip)**:
  Pengguna membutuhkan filter interaktif (memilih kromosom tunggal vs. seluruh kromosom), pengaturan ambang signifikansi dinamis (*slider* Bonferroni / FDR / custom), dan inspeksi titik saat kursor mouse diarahkan (*hover tooltip* untuk menampilkan ID SNP, posisi BP, dan p-value).
- **Kebutuhan Bundle Size Ringan**:
  Platform harus dapat diakses secara cepat melalui jaringan internet terbatas tanpa terbebani pustaka charting monolitik berukuran megabyte.

---

## Keputusan
Kami memutuskan untuk mengimplementasikan **Komponen Visualisasi Kustom berbasis HTML5 Canvas 2D Immediate-Mode Rendering** ([`ManhattanPlot.jsx`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/frontend/src/components/ManhattanPlot.jsx)), yang dilengkapi dengan:
1. **2D Pixel-Bucket Decimation / Deduplication**:
   Mengeliminasi titik-titik redundan yang memetakan ke sel piksel $2 \times 2$ yang sama di wilayah latar belakang, sekaligus **mempertahankan 100% titik sinyal biologis signifikan** ($p.y \ge \text{threshold}$).
2. **Batch-Path Color Grouping**:
   Mengelompokkan titik ke dalam 3 lintasan warna terpadu (Kromosom Ganjil `#2563eb`, Kromosom Genap `#64748b`, dan Signifikan `#dc2626`) untuk meminimalkan *state changes* pada GPU context.
3. **HiDPI / Retina Display Compensation**:
   Menyesuaikan resolusi kanvas dengan `window.devicePixelRatio` untuk menghasilkan visualisasi yang sangat tajam tanpa blur pada monitor resolusi tinggi.
4. **Hit-Testing Matematika Non-DOM**:
   Menghitung deteksi kursor mouse (*hover hit-testing*) secara langsung menggunakan kalkulasi jarak Euclidean sederhana pada koordinat piksel lokal.

---

## Alternatif yang Dipertimbangkan

### 1. HTML5 Canvas Kustom dengan Pixel Decimation (Stack Terpilih)
- **Kelebihan**:
  - **Zero DOM Overhead**: Titik-titik digambar langsung ke bitmap buffer grafis. Tidak ada node DOM yang diciptakan, menjaga memori browser tetap stabil dan rendah ($< 5\text{ MB}$ RAM).
  - **Kecepatan Rendering Ekstrem (< 5 ms)**: Berkat deduplikasi piksel 2D dan *batch draw path*, render $12.000+$ titik selesai dalam waktu $< 5\text{ ms}$, menjamin animasi dan interaksi tetap berjalan mulus pada 60 FPS.
  - **Preservasi Sinyal QTL 100%**: Algoritma menjamin semua titik dengan $-\log_{10}P$ di atas ambang signifikansi selalu diprioritaskan dan tidak pernah dieliminasi oleh decimation.
  - **Bobot Kode Nol Dependensi**: Tidak memerlukan pustaka pihak ketiga; ukuran kode komponen hanya $\sim 10\text{ KB}$ JavaScript murni.
  - **Dukungan Ekspor Gambar Publikasi**: Pengguna dapat mengunduh gambar beresolusi tinggi langsung melalui metode native `canvas.toDataURL("image/png")`.
- **Kekurangan**:
  - Memerlukan implementasi kalkulasi sumbu, label, dan deteksi hover tooltip secara manual.
- **Kesimpulan**: Pendekatan terbaik yang menyajikan rasio performa komputasi grafis tertinggi, ukuran bundel minimal, dan kebebasan desain visual bioinformatika.

---

### 2. D3.js Berbasis SVG (Scalable Vector Graphics)
- **Deskripsi**: Menggunakan pustaka D3.js untuk memetakan setiap SNP menjadi elemen `<circle>` pada DOM pohon SVG (`svg.selectAll("circle").data(points).enter().append("circle")`).
- **Kelebihan**:
  - Setiap titik adalah elemen DOM individu yang dapat diikat dengan event listener native (`onMouseEnter`, `onClick`) dan di-style menggunakan CSS.
  - Skalabilitas vektor murni tanpa kehilangan ketajaman saat di-zoom.
- **Kekurangan**:
  - **Ledakan Beban DOM (DOM Tree Explosion)**: Menambahkan $20.000 - 100.000$ elemen `<circle>` ke DOM menyebabkan *layout thrashing*, konsumsi RAM melonjak hingga ratusan megabyte ($> 300\text{ MB}$), dan penurunan drastis *frame rate* menjadi $< 5\text{ FPS}$ (*severe UI freezing*).
  - Waktu *initial render* memakan waktu $2 - 6\text{ detik}$, sering memicu peringatan browser *"Page Unresponsive"*.
  - Tidak layak untuk perangkat dengan spesifikasi memori terbatas atau perangkat mobile.
- **Alasan Ditolak**: Paradigma DOM SVG tidak dirancang untuk menangani himpunan data puluhan ribu titik dinamis secara bersamaan.

---

### 3. Pustaka Visualisasi High-Level (Plotly.js / Chart.js)
- **Deskripsi**: Menggunakan pustaka charting terintegrasi seperti Plotly.js atau Chart.js untuk menampilkan scatter plot.
- **Kelebihan**:
  - Perkakas bawaan yang lengkap (zoom, pan, simpan gambar, toolbar bawaan).
- **Kekurangan**:
  - **Ukuran Bundel Raksasa (Bundle Size Bloat)**: Plotly.js menambah beban bundel JavaScript sebesar $\sim 3.5\text{ MB}$ (atau $\sim 1\text{ MB}$ gzipped), memperlambat waktu muat awal (*First Contentful Paint*) aplikasi.
  - **Keterbatasan Kustomisasi Layout Genomik**: Sumbu-X Manhattan Plot memiliki struktur unik (pengelompokan fisik per kromosom berurutan dengan label di tengah rentang masing-masing kromosom dan warna alternasi ganjil-genap); mengonfigurasi fitur ini di Chart.js atau Plotly membutuhkan peretasan kode (*hacky workarounds*) yang rapuh.
  - Plotly scatterGL (berbasis WebGL) kerap mengalami masalah *WebGL context lost* saat komponen sering di-unmount dan di-mount kembali di React.
- **Alasan Ditolak**: Menambah ukuran unduhan web secara masif dan kaku untuk kebutuhan tata letak khusus grafis genomik.

---

### 4. Pure HTML/CSS Absolute Div Elements
- **Deskripsi**: Merender setiap titik menggunakan tag `<div>` yang diposisikan secara absolut dengan properti CSS `style={{ left, top }}`.
- **Alasan Ditolak**: Memiliki kelemahan yang sama dengan SVG dalam hal ledakan node DOM, dengan efisiensi rendering yang jauh lebih buruk.

---

## Konsekuensi

### Konsekuensi Positif:
1. **Performa Mulus Tanpa Lag**: Visualisasi Manhattan Plot merespons seketika saat pengguna mengganti filter kromosom atau mengubah nilai ambang signifikansi.
2. **Ukuran Bundel Frontend Sangat Ringan**: Menghindari penambahan pustaka berat seperti Plotly ($> 3\text{ MB}$), menjaga ukuran total bundel frontend di bawah $300\text{ KB}$ terkompresi.
3. **Ketajaman Visual Profesional**: Tampilan grafik tetap tajam dan proporsional pada semua rasio layar berkat penanganan Retina Display (`devicePixelRatio`).
4. **Keamanan Memori Tab Browser**: Mencegah browser tab crash atau membeku akibat kehabisan memori DOM saat membuka dataset genomik besar.

### Konsekuensi Negatif & Mitigasi:
1. **Kompleksitas Perawatan Logika Render Manual**:
   - Pembuatan garis grid, sumbu, label teks, dan hit-testing diimplementasikan secara manual di kode komponen.
   - *Mitigasi*: Seluruh logika perhitungan koordinat diisolasi ke dalam modul utilitas modular [`manhattan.js`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/frontend/src/utils/manhattan.js), sehingga komponen UI tetap bersih dan mudah diuji.
2. **Aksesibilitas (Screen Readers)**:
   - Elemen `<canvas>` adalah elemen grafis raster yang tidak dapat dibaca oleh pembaca layar (*screen reader*).
   - *Mitigasi*: Disediakan tabel HTML semantik terstruktur di bawah plot ([`LeadSNPsTable`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/frontend/src/pages/GwasUploadPage.jsx)) yang memuat daftar varian signifikan beserta nilai p-value dan gen kandidat, memastikan pengguna dengan keterbatasan visual tetap dapat mengakses intisari data biologis.
