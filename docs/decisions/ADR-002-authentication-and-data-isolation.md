# ADR-002: Arsitektur Autentikasi JWT (Split-Token In-Memory & HttpOnly Cookie) dan Isolasi Data

## Status
Accepted

## Tanggal
2026-10-30

## Konteks
Platform GWAS memproses file genomik (VCF) dan fenotipe yang mencerminkan profil genetik dan klinis individu/populasi. Kebocoran token sesi pada platform ini dapat berakibat fatal (eksploitasi data genomik pengguna lain).

Kebutuhan keamanan sistem meliputi:
1. **Ketahanan terhadap Serangan XSS (*Cross-Site Scripting*)**: Jika terjadi celah injeksi skrip pada pustaka pihak ketiga di frontend, token sesi tidak boleh dapat dibaca atau diekstraksi oleh skrip berbahaya.
2. **Ketahanan terhadap Serangan CSRF (*Cross-Site Request Forgery*)**: Permintaan yang mengubah status atau memicu proses komputasi berat tidak boleh dapat dipicu dari situs pihak ketiga yang tidak berwenang.
3. **Pencabutan Sesi Seketika (*Immediate Revocation / Logout All Devices*)**: Kemampuan membatalkan seluruh sesi aktif seorang pengguna jika terdeteksi indikasi kompromi kredensial, tanpa perlu menjaga *stateful session table* yang berat di setiap panggilan API.
4. **Isolasi Data Multitenant yang Ketat**: Pengguna peran *Researcher* hanya boleh melihat, memodifikasi, dan menghapus dataset serta riwayat analisis miliknya sendiri. Peran *Admin* memiliki hak kelola sistem dan panel referensi bersama.

## Keputusan
Kami mengimplementasikan arsitektur autentikasi **Split-Token JWT**:

1. **Access Token (Umur Pendek - 180 Menit)**:
   - Dikirimkan via header HTTP standard `Authorization: Bearer <token>`.
   - **Hanya disimpan di memori runtime JavaScript (React Context state)** di frontend.
   - **DILARANG KERAS** disimpan di `localStorage` atau `sessionStorage` browser untuk memitigasi risiko pencurian token via serangan XSS.
2. **Refresh Token (Umur Panjang - 7 Hari)**:
   - Dikirimkan oleh backend sebagai **HttpOnly, SameSite=Lax, Secure Cookie** (`gwas_refresh_token`).
   - Flag `HttpOnly` memastikan cookie mustahil dibaca oleh JavaScript (`document.cookie`), sehingga kebal dari ekstraksi skrip XSS.
   - Flag `SameSite=Lax` mencegah serangan CSRF dari domain eksternal.
   - Digunakan secara eksklusif untuk memperbarui access token melalui endpoint `POST /api/v1/auth/refresh`.
3. **Mekanisme Invalidation Instan (`refresh_token_version`)**:
   - Kolom integer `refresh_token_version` ditambahkan pada tabel `users`.
   - Setiap payload refresh token memuat klaim `"token_version"`.
   - Ketika pengguna memilih *"Keluar dari Semua Perangkat"* atau admin menonaktifkan akun, nilai `refresh_token_version` di database dinaikkan ($+1$). Seluruh refresh token lama yang beredar seketika menjadi tidak valid tanpa memerlukan Redis session blacklist.
4. **Isolasi Data Tingkat Baris (*Row-Level Multi-Tenancy*)**:
   - Ditegakkan melalui dependency injection FastAPI [`get_current_user`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/core/security.py) dan [`require_admin`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/core/security.py).
   - Setiap endpoint yang mengakses `Dataset` atau `AnalysisJob` wajib menyaring data berdasarkan `owner_id == user.id` (kecuali pengguna adalah admin).

## Alternatif yang Dipertimbangkan

### 1. Pure Bearer JWT (Access Token & Refresh Token di LocalStorage)
- **Kelebihan**: Sangat mudah diimplementasikan di frontend SPA, tidak memerlukan konfigurasi cookie CORS.
- **Kekurangan**: Token yang disimpan di `localStorage` rentan dicuri jika ada celah XSS pada dependensi frontend (misal pustaka visualisasi atau parser).
- **Alasan Ditolak**: Risiko keamanan terhadap kebocoran data genetik pasien/populasi terlalu tinggi.

### 2. Sesi Stateful Penuh di Server (Database / Redis Session Store)
- **Kelebihan**: Pencabutan sesi sangat mudah; data sesi sepenuhnya berada di bawah kendali server.
- **Kekurangan**: Memerlukan panggilan I/O ke Redis atau database di setiap request API tunggal; menambah beban koneksi saat polling status job frekuensi tinggi dilakukan oleh klien.
- **Alasan Ditolak**: Mengurangi skalabilitas arsitektur API; mekanisme *token versioning* pada refresh token memberikan manfaat pencabutan sesi yang sama dengan biaya I/O yang jauh lebih hemat (hanya diakses saat refresh 3 jam sekali).

### 3. Otentikasi Pihak Ketiga (OAuth2 / OpenID Connect Google/GitHub)
- **Kelebihan**: Tidak perlu mengelola penyimpanan kata sandi lokal; delegasi keamanan ke penyedia identitas terpercaya.
- **Kekurangan**: Mengharuskan konektivitas internet aktif dan konfigurasi credentials eksternal; platform ini dirancang agar dapat di-deploy di lingkungan intranet/laboratorium bioinformatika tertutup (*air-gapped*).
- **Alasan Ditolak**: Membatasi fleksibilitas deployment offline laboratorium riset genomik.

## Konsekuensi
- **Positif**:
  - Pertahanan berlapis terhadap XSS: penyerang tidak dapat mencuri refresh token karena terlindung oleh browser (HttpOnly).
  - Skalabilitas tinggi: verifikasi access token bersifat stateless melalui verifikasi signature kriptografis kriptografi simetris HS256.
  - Pembatalan sesi global dapat dilakukan instan dengan menaikkan `refresh_token_version`.
- **Negatif / Mitigasi**:
  - Siklus hidup sesi di frontend memerlukan penanganan silent refresh di balik layar (interceptor HTTP) saat tab browser dibuka kembali atau access token kadaluwarsa. Diimplementasikan secara mulus pada `AuthContext.jsx`.
  - Pada lingkungan pengembangan lokal tanpa HTTPS, flag cookie `secure` dinonaktifkan secara otomatis (`secure = env != "development"`) agar pengembang lokal tidak mengalami masalah cookie yang ditolak oleh browser `localhost`.
