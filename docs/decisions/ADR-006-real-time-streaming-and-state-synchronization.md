# ADR-006: Arsitektur Streaming Real-time & Sinkronisasi Status Job (Adaptive REST Polling + SSE vs. WebSockets & gRPC)

## Status
Accepted

## Tanggal
2026-11-02

## Konteks
Aplikasi **GWAS Platform** mengelola analisis bioinformatika genomik berdurasi panjang (*long-running asynchronous jobs*) yang dieksekusi melalui 10 tahapan sekuensial. Selama komputasi berlangsung di background worker (Celery/Redis), antarmuka web pengguna (*frontend React*) harus menyajikan:
1. **Status Kemajuan Pipeline (Job Progress Tracking)**: Pembaruan aktif indikator tahapan (Tahap 1 hingga 10: *Inspection*, *QC*, *Imputation*, *Structure*, *GWAS*, *Post-GWAS*, *Visualization*, hingga *Annotation*).
2. **Streaming Log Eksekusi Terminal**: Log stdout/stderr dari biner bioinformatika (PLINK, GEMMA, Beagle, bcftools) yang dialirkan secara bertahap ke panel log antarmuka pengguna.
3. **Penyelarasan State Terminal**: Deteksi instan saat status pekerjaan berubah menjadi status akhir (*terminal states*): `COMPLETED`, `SUCCESS`, `FAILED`, atau `CANCELLED`.
4. **Tindakan Intervensi Pengguna**: Kemampuan pengguna membatalkan job yang sedang berjalan (*Cancel Job*) atau mengulang kembali job yang gagal (*Retry Job*).

### Karakteristik & Kendala Arsitektur:
- **Asimetri Komunikasi (One-Way Dominated)**: Aliran data didominasi oleh transmisi searah dari server ke client (*server-to-client telemetry*). Permintaan dari client ke server hanya terjadi sesekali saat memicu pembatalan atau pergantian tab log.
- **Durasi Eksekusi & Toleransi Latensi**: Pekerjaan komputasi GWAS berlangsung antara beberapa menit hingga beberapa jam. Latensi pembaruan status antara $1$ hingga $3\text{ detik}$ sangat dapat diterima dan tidak mengurangi kualitas pengalaman pengguna (*user experience*).
- **Keamanan Otentikasi Terpadu**: Komunikasi harus divalidasi menggunakan token JWT (*Split-Token Architecture* pada ADR-002) melalui mekanisme proteksi CSRF dan otentikasi standar tanpa memerlukan framing handshake kustom.
- **Ketahanan Jaringan & Multi-Device**: Pengguna sering kali menutup tab, me-refresh halaman, berpindah jaringan (misal Wi-Fi kampus/kantor terputus sesaat), atau membuka link job dari komputer lain. Sistem harus dapat langsung menyajikan status terkini secara deterministik tanpa kehilangan sinkronisasi.
- **Topologi Infrastruktur (Reverse Proxy & Cloudflare)**: Lalu lintas HTTP melewati reverse proxy (Nginx, Traefik, AWS ALB) yang umumnya memiliki *idle connection timeout* ketat ($60\text{ detik}$) untuk koneksi persisten.

---

## Keputusan
Kami memutuskan untuk mengadopsi arsitektur **Adaptive REST Polling** yang dipadukan dengan **Chunked Log Tail Retrieval / Server-Sent Events (SSE)** sebagai strategi utama sinkronisasi status dan streaming log:

### Detail Implementasi:
1. **Adaptive Status Polling Hook ([`useJobPolling.js`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/frontend/src/hooks/useJobPolling.js))**:
   - Frontend mengonsumsi endpoint [`GET /jobs/{job_id}/status`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/api/routes/jobs.py) dengan interval adaptif ($2.5 - 3\text{ detik}$) saat pekerjaan berstatus aktif (`RUNNING_*` atau `PENDING`).
   - Mengimplementasikan deteksi status terminal otomatis (`TERMINAL_STATUSES = ["COMPLETED", "SUCCESS", "FAILED", "CANCELLED"]`). Saat status terminal terdeteksi, interval timer dibersihkan seketika dan polling **berhenti secara permanen**, membebaskan resource browser dan jaringan.
   - Toleransi kegagalan transien: Jika terjadi *network hiccup* sesaat, polling tidak langsung mematikan tracker, melainkan mencoba kembali pada interval berikutnya tanpa mengunci antarmuka.
2. **Granular Log Tail Retrieval ([`GET /jobs/{job_id}/logs`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/api/routes/jobs.py))**:
   - Server membaca potongan log terakhir (*tail lines*) dari berkas log panggung secara efisien menggunakan helper `_read_log_tail` dengan batas baris aman (`max_lines=2000`).
   - Menyediakan endpoint log terstruktur per-tahap yang dapat difilter oleh tab antarmuka pengguna ([`JobProcessLogs.jsx`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/frontend/src/components/JobProcessLogs.jsx)).
3. **Endpoint Kontrol Idempoten**:
   - Aksi intervensi pengguna dieksekusi melalui REST verbs standar yang dilindungi dependensi otentikasi: `POST /jobs/{job_id}/cancel` dan `POST /jobs/{job_id}/retry`.

---

## Alternatif yang Dipertimbangkan

### 1. Adaptive REST Polling + Log Tail / SSE (Stack Terpilih)
- **Kelebihan**:
  - **Stateless & Resilient**: Setiap request HTTP bersifat mandiri (*stateless*). Jika koneksi client terputus dan tersambung kembali, polling berikutnya langsung merefleksikan kondisi terkini dari database tanpa memerlukan protokol rekoneksi khusus.
  - **Penyelarasan Instan Pasca-Refresh**: Saat pengguna membuka ulang halaman atau membuka tautan dari perangkat lain, panggilan pertama langsung memuat seluruh status dan log komprehensif.
  - **Transparan Terhadap Proxy & CDN**: Beroperasi di atas protokol HTTP/1.1 dan HTTP/2 standar. Tidak ada risiko koneksi diputus sepihak oleh proxy timeout, Cloudflare WebSocket drops, atau firewall institusi.
  - **Otentikasi Standar Tanpa Celah**: Menggunakan middleware FastAPI `Depends(get_current_user)` yang memverifikasi header `Authorization: Bearer <token>` dan cookie secara native.
  - **Beban Server Sangat Ringan**: Endpoint `GET /status` hanya melakukan query terindeks primary key pada satu baris tabel `AnalysisJob`, menghasilkan latensi eksekusi database $< 3\text{ ms}$.
- **Kekurangan**:
  - Terdapat latensi pembaruan maksimum sebesar interval polling ($2.5\text{ detik}$) — namun tidak berdampak negatif pada analisis bioinformatika yang memakan waktu puluhan menit.
  - Sedikit overhead header HTTP pada setiap interval polling.
- **Kesimpulan**: Solusi paling tangguh, sederhana, aman, dan minim beban operasional untuk sistem manajemen analisis komputasi sains.

---

### 2. Full-Duplex WebSockets (`ws://` / `wss://`)
- **Deskripsi**: Membuka koneksi socket TCP dua arah persisten antara browser dan FastAPI server (`@router.websocket("/ws/jobs/{id}")`).
- **Kelebihan**:
  - Latensi transmisi mendekati nol milidetik (*sub-millisecond streaming*).
  - Overhead header minimum setelah initial HTTP upgrade handshake berhasil.
  - Kemampuan pengiriman pesan dua arah secara simultan.
- **Kekurangan**:
  - **Kompleksitas Otentikasi pada Browser**: API `new WebSocket(url)` native di browser **tidak mendukung penambahan custom headers** (`Authorization: Bearer ...`). Token terpaksa dikirim via *query parameter* (rentan bocor di access log server/proxy) atau memerlukan *handshake authentication protocol* kustom di pesan frame pertama.
  - **Stateful Connection Management & Horizontal Scaling**: Server harus mempertahankan state ribuan koneksi socket di memori. Dalam arsitektur multi-instance web di balik load balancer, diperlukan infrastruktur *Redis Pub/Sub channel backplane* tambahan agar event dari worker dapat diarahkan ke instance server yang tepat tempat soket pengguna terhubung.
  - **Masalah Idle Timeout Reverse Proxy**: Proxy dan load balancer (Nginx / ALB) akan memutuskan koneksi WebSocket secara sepihak jika tidak ada lalu lintas data selama 60 detik, mewajibkan implementasi mekanisme *heartbeat ping/pong* berkala.
  - **Logika Rekoneksi Rumit di Frontend**: Frontend harus mengelola *exponential backoff retry*, buffer pesan yang tertunda saat koneksi drop, dan deteksi *zombie connections*.
  - **Kebutuhan Tidak Seimbang**: Sifat interaksi pengguna dengan platform adalah *telemetry monitoring* searah; kemampuan bidirectional full-duplex tidak memberikan keuntungan esensial.
- **Alasan Ditolak**: Kompleksitas arsitektur stateful (otentikasi, Redis pub/sub backplane, proxy timeout, dan rekoneksi client) jauh melampaui kebutuhan sistem monitoring analisis berdurasi panjang.

---

### 3. gRPC / gRPC-Web (HTTP/2 Protocol Buffers Streaming)
- **Deskripsi**: Menggunakan Remote Procedure Call berbasis Protobuf dengan HTTP/2 server streaming.
- **Kelebihan**:
  - Kontrak skema bertipe data ketat (*strongly typed contract*) melalui berkas `.proto`.
  - Serialisasi data biner efisien berkecepatan tinggi.
- **Kekurangan**:
  - **Inkompatibilitas Browser Native (Envoy Proxy Overhead)**: Browser modern tidak mendukung HTTP/2 frame biner gRPC secara langsung; memerlukan perantara *Envoy Proxy* untuk mentranslasikan request gRPC-Web ke backend gRPC.
  - Menambah perkakas eksternal dalam siklus build (kompilasi `.proto` ke TypeScript dan Python).
  - Menyulitkan proses *debugging* di tab Network browser karena payload berupa byte biner yang tidak dapat dibaca manusia secara langsung.
- **Alasan Ditolak**: Membutuhkan infrastruktur proxy tambahan (Envoy) dan beban pemeliharaan skema gRPC yang tidak sebanding dengan manfaat yang didapat.

---

### 4. Long Polling (Hanging GET / Comet)
- **Deskripsi**: Client mengirim request HTTP GET yang sengaja ditahan oleh server hingga terjadi perubahan status baru, lalu segera mengirimkan request berikutnya saat respons diterima.
- **Kelebihan**: Menggunakan protokol HTTP standar tanpa soket khusus.
- **Kekurangan**:
  - Berisiko memblokir worker thread web jika konkurensi asinkron tidak dikelola sempurna.
  - Rentan mengalami gateway timeout (*HTTP 504*) pada jaringan proxy jika komputasi berlangsung hening dalam durasi lama.
  - Pola lama yang telah usang dan sepenuhnya digantikan oleh adaptive polling dan Server-Sent Events.
- **Alasan Ditolak**: Rentan timeout pada reverse proxy dan lebih rumit dikelola dibanding adaptive polling diskrit.

---

## Konsekuensi

### Konsekuensi Positif:
1. **Keandalan Tinggi & Nol Zombie State**: Sistem kebal terhadap gangguan jaringan sesaat; client secara otomatis merekonsiliasi state saat koneksi pulih tanpa perlu logika reset socket.
2. **Kemudahan Deployment**: Backend dan frontend dapat dideploy di balik konfigurasi reverse proxy standar (Nginx, Traefik, Caddy) tanpa konfigurasi *upgrade headers* atau modul WebSocket khusus.
3. **Efisiensi Penggunaan Resource**: Polling otomatis mati permanen ketika job selesai atau gagal, memastikan tidak ada koneksi yang menggantung atau memboroskan CPU/bandwidth server saat pengguna meninggalkan tab terbuka.
4. **Keamanan Konsisten**: Seluruh validasi otentikasi dan izin akses pengguna berjalan melalui pipeline middleware JWT FastAPI yang sama seperti endpoint data lainnya.

### Konsekuensi Negatif & Mitigasi:
1. **Interval Latensi Pembaruan (2.5 Detik)**:
   - Status pembaruan memiliki penundaan rata-rata $\sim 1.25\text{ detik}$.
   - *Mitigasi*: Untuk komputasi bioinformatika berdurasi panjang (rata-rata 2 hingga 45 menit), latensi 2 detik tidak terasa oleh pengguna dan diimbangi dengan visualisasi status spinner/progress bar yang mulus di UI.
2. **Akumulasi Permintaan Polling pada Banyak Job Paralel**:
   - Jika satu pengguna memantau banyak job sekaligus, terjadi beberapa request per 2.5 detik.
   - *Mitigasi*: Endpoint `GET /status` sangat ringan (hanya membaca kolom status job dengan index primary key di memori database); backend FastAPI berbasis `async` dapat melayani ribuan request status per detik dengan konsumsi CPU $< 2\%$.
