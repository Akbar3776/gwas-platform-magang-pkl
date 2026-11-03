# ADR-009: Arsitektur Frontend & Perangkat Pembundel (Vite + React 18 SPA vs. Next.js SSR & Webpack CRA)

## Status
Accepted

## Tanggal
2026-11-02

## Konteks
Antarmuka pengguna pada **GWAS Platform** adalah aplikasi web ilmiah terpadu (*scientific dashboard application*) yang melayani peneliti bioinformatika dalam mengunggah dataset varian genomik (VCF gigabyte), mengonfigurasi parameter alur kerja multi-tahap, memantau kemajuan komputasi Celery worker secara real-time, serta mengeksplorasi visualisasi data berdensitas tinggi (Manhattan Plot, QQ Plot, Principal Component Analysis, dan tabel lokus anotasi fungsional).

### Karakteristik & Kebutuhan Antarmuka Pengguna:
1. **Aplikasi Tertutup Berbasis Autentikasi (No SEO Requirement)**:
   Seluruh fungsionalitas aplikasi berada di balik gerbang autentikasi pengguna (*Protected Routes* dengan JWT split-token). Berbeda dengan situs portal berita atau e-commerce publik, platform ini **tidak memerlukan Search Engine Optimization (SEO)** atau kemampuan *web crawler indexing*.
2. **Interaktivitas Sisi Klien Sangat Dominan (Heavy Client-Side State)**:
   - Pengunggahan berkas besar dengan pemecahan chunk biner dan drag-and-drop di browser.
   - Perenderan grafis langsung ke GPU kanvas 2D/WebGL ([`ManhattanPlot.jsx`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/frontend/src/components/ManhattanPlot.jsx)) yang membutuhkan akses objek `window` dan DOM kanvas secara lokal.
   - Polling status berulang ke API backend ([`useJobPolling.js`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/frontend/src/hooks/useJobPolling.js)).
3. **Pemisahan Peran Bersih (*Decoupled API Architecture*)**:
   Backend platform dibangun sepenuhnya menggunakan **FastAPI (Python)**. Frontend harus bertindak sebagai klien mandiri tanpa memaksakan adanya lapisan server tambahan yang redundan.
4. **Efisiensi Pengoperasian Produksi & Lingkungan Pengembangan**:
   - Di produksi: Aset antarmuka harus dapat disajikan secara instan dan efisien menggunakan server statis ringan (Nginx atau direktori statis FastAPI) tanpa menyita memori server komputasi.
   - Di pengembangan: Waktu startup harus instan ($< 500\text{ ms}$) dengan siklus *Hot Module Replacement* (HMR) yang responsif.

---

## Keputusan
Kami memutuskan untuk menggunakan **Vite** yang dipadukan dengan **React 18 Single-Page Application (SPA)** dan **React Router v6** sebagai standar arsitektur antarmuka web platform.

### Detail Arsitektur & Pola Implementasi:
1. **Native ESM & esbuild di Lingkungan Pengembangan**:
   - Konfigurasi [`frontend/vite.config.js`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/frontend/vite.config.js) memanfaatkan native ES Modules browser dan kompilator bahasa Go (`esbuild`) untuk pre-bundling dependensi. Server pengembangan menyala instan dalam waktu $< 250\text{ ms}$, dan perubahan kode komponen ter-update via HMR dalam waktu $< 50\text{ ms}$.
2. **Reverse Proxy Terintegrasi Anti-CORS**:
   - `vite.config.js` mengonfigurasi blok `server.proxy` untuk merutekan seluruh panggilan `/api` dan `/ws` langsung ke backend FastAPI (`http://localhost:8000`), menyederhanakan workflow pengembang tanpa perlu mengonfigurasi header CORS khusus di lingkungan lokal.
3. **Produksi Tanpa Runtime Node.js (*Pure Static Output*)**:
   - Perintah `vite build` menggunakan Rollup untuk menghasilkan bundel aset statis murni (`dist/index.html`, berkas `.js` dan `.css` terkompresi dengan *content hashing*).
   - Di lingkungan server produksi (Docker/Kubernetes), aset statis ini disajikan langsung melalui **Nginx** berbobot sangat ringan ($< 15\text{ MB}$ RAM) atau langsung dimount ke FastAPI via `app.mount("/static", StaticFiles(...))`.
4. **Client-Side Routing & State Isolation**:
   - Navigasi halaman antar-modul (Login, Register, Workspace Explorer, Pipeline Upload, Results Dashboard) dikelola sepenuhnya di browser menggunakan `react-router-dom`, meminimalkan *round-trip* jaringan ke server.

---

## Alternatif yang Dipertimbangkan

### 1. Vite + React 18 SPA (Stack Terpilih)
- **Kelebihan**:
  - **Cold Start & HMR Sangat Cepat**: Server dev siap dalam hitungan ratusan milidetik; HMR bekerja presisi tanpa me-reload seluruh halaman.
  - **Zero Node.js Server in Production**: Hasil kompilasi berupa aset statis murni. Menghilangkan kebutuhan menjalankan daemon server Node.js persisten di lingkungan produksi kluster.
  - **Pemisahan Tanggung Jawab Bersih**: Frontend adalah SPA murni yang terpisah secara fisik dari logika backend Python. Tidak ada kerancuan antara kode server Node.js dan backend FastAPI.
  - **Penghematan Resource Server Maksimal**: Memori server produksi terfokus $100\%$ untuk worker komputasi Celery, database PostgreSQL, dan broker Redis, tanpa tersita oleh server render Node.js.
  - **Kompatibilitas Penuh dengan Library Client-Only**: Komponen visualisasi grafis Canvas dan WebGL dapat diimpor langsung tanpa masalah *SSR Hydration Mismatch* atau pengecekan `typeof window !== "undefined"`.
- **Kekurangan**:
  - Tidak memiliki kapabilitas Server-Side Rendering (SSR) bawaan — namun SSR tidak diperlukan untuk aplikasi dashboard privat.
- **Kesimpulan**: Kombinasi paling ideal antara kecepatan pengembangan, stabilitas produksi, dan kesederhanaan operasional infrastruktur.

---

### 2. Next.js (App Router / Pages Router dengan SSR)
- **Deskripsi**: Framework full-stack React berbasis Server-Side Rendering (SSR) dan Server Components yang berjalan di atas runtime Node.js.
- **Kelebihan**:
  - Halaman di-render di server Node.js sebelum dikirim ke browser; optimal untuk SEO publik.
  - Ekosistem kaya dengan optimasi font, gambar, dan route handlers.
- **Kekurangan**:
  - **Duplikasi Backend Server (*Dual-Server Architecture Anti-Pattern*)**: Mewajibkan berjalannya proses server Node.js secara persisten di produksi di samping server FastAPI/Uvicorn. Ini menggandakan jumlah service container di Docker Compose, menggandakan konsumsi memori server ($> 150 - 300\text{ MB}$ RAM hanya untuk Node.js idle), serta mempersulit manajemen reverse proxy dan load balancing.
  - **Keuntungan Utama (SEO) Tidak Terpakai**: Seluruh halaman platform berada di balik proteksi login autentikasi; optimasi mesin pencari sama sekali tidak relevan.
  - **Komplikasi Autentikasi Split-Token di Server Render**: SSR Next.js harus merender komponen sebelum cookie atau token JWT di-resolve di client, memicu kompleksitas sinkronisasi state autentikasi (*hydration mismatch*, perlunya middleware edge kustom untuk membaca cookie).
  - **Masalah Kompatibilitas Visualisasi Canvas**: Library grafis berbasis browser (`canvas`, `WebGL`) harus dibungkus dengan `dynamic(() => import(...), { ssr: false })` agar server Node.js tidak mengalami *window is not defined error*.
- **Alasan Ditolak**: Menggandakan kompleksitas deployment dan konsumsi memori dengan menambahkan server runtime Node.js kedua di produksi, tanpa memberikan keuntungan fungsional bagi dashboard analitik privat.

---

### 3. Remix / React Router v7 Full-stack
- **Deskripsi**: Framework SSR modern yang mengedepankan web standards dan data loader di server.
- **Kelebihan**:
  - Penanganan transisi form dan validasi rute yang terintegrasi.
- **Kekurangan**:
  - Memiliki kelemahan yang sama dengan Next.js: membutuhkan server runtime Node.js / Deno terdedikasi di lingkungan produksi.
  - Mengharuskan penulisan data loader yang sekadar menjadi jembatan perantara (*proxy passthrough*) ke API FastAPI, menciptakan latensi ekstra.
- **Alasan Ditolak**: Menciptakan lapisan perantara yang tidak perlu antara browser dan backend FastAPI.

---

### 4. Create React App (CRA) / Webpack Tradisional
- **Deskripsi**: Perkakas bawaan klasik React yang menggunakan Webpack untuk pembundelan seluruh aset sebelum server menyala.
- **Kelebihan**:
  - Menghasilkan SPA statis murni tanpa server Node.js di produksi.
- **Kekurangan**:
  - **Kinerja Pengembangan Lambat**: Webpack harus memproses seluruh modul ke memori; startup server lokal memakan waktu $20 - 45\text{ detik}$, dan HMR terasa lambat pada aplikasi besar.
  - **Proyek Telah Ditinggalkan (*Deprecated*)**: Tim resmi React telah menghentikan pemeliharaan Create React App dan secara eksplisit merekomendasikan migrasi ke Vite.
  - Kustomisasi konfigurasi bundler rumit dan rapuh (memerlukan perintah `eject` atau pustaka `craco`).
- **Alasan Ditolak**: Perangkat usang dengan kecepatan pengembangan yang jauh tertinggal dibandingkan ekosistem modern Vite.

---

## Konsekuensi

### Konsekuensi Positif:
1. **Efisiensi Infrastruktur Maksimal**: Lingkungan produksi tidak memerlukan container Node.js. Berkas hasil build di-serve sebagai aset statis oleh Nginx dengan footprint memori sangat rendah ($< 15\text{ MB}$) dan throughput tinggi.
2. **Pengalaman Pengembang Sangat Responsif**: Waktu startup server lokal $< 250\text{ ms}$ dan HMR instan secara drastis meningkatkan produktivitas tim pengembang frontend.
3. **Bebas Masalah Hydration Grafis**: Seluruh komponen visualisasi Canvas/WebGL dieksekusi murni di sisi browser tanpa risiko galat ketidaksesuaian render server-klien (*hydration mismatch*).
4. **Pipeline CI/CD Cepat**: Waktu build produksi pada pipeline CI hanya memakan waktu belasan detik menggunakan Rollup dan esbuild.

### Konsekuensi Negatif & Mitigasi:
1. **Perutean Fallback di Server Statis (SPA Routing)**:
   - Karena perutean dikelola oleh React Router di browser, memuat URL rute langsung (seperti `/jobs/analysis-123`) di server Nginx dapat memicu galat *HTTP 404* jika berkas fisik tidak ada.
   - *Mitigasi*: Konfigurasi Nginx diatur dengan direktif fallback standar: `try_files $uri $uri/ /index.html;`, memastikan semua rute SPA dialihkan kembali ke `index.html` agar React Router menangani navigasi secara mulus.
2. **Ukuran Bundel Awal (Initial Bundle Size)**:
   - Pengguna harus mengunduh bundel JavaScript sebelum aplikasi dapat berinteraksi.
   - *Mitigasi*: Vite dikonfigurasi dengan *code-splitting* otomatis per halaman/rute menggunakan `React.lazy()` dan *dynamic import*, serta mengaktifkan kompresi Gzip/Brotli pada web server.
