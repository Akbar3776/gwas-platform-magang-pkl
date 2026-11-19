# Catatan Integrasi: Auth & Data Isolation

Dokumen ini merangkum pola integrasi autentikasi, CORS, dan isolasi kepemilikan data aktual yang terpasang pada `backend/` dan `frontend/`.

## 1. Registrasi Router & Lifespan (`app/main.py`)

Seluruh router FastAPI didaftarkan di [`app/main.py`](app/main.py) di bawah prefix `/api/v1` (`auth`, `upload`, `jobs`, `pipeline`, `capabilities`, `results`, `export`, `report`, `reference_panels`), bersama endpoint sinkron `GET /health` yang memverifikasi konektivitas database (`HTTP 200` atau `HTTP 503`).

## 2. CORS dengan Kredensial Cookie Refresh

Karena refresh token dikirim melalui HttpOnly cookie (`gwas_refresh_token`), `CORSMiddleware` di [`app/main.py`](app/main.py) menyetel `allow_credentials=True` dengan daftar origin eksplisit dari `settings.CORS_ORIGINS` (tanpa wildcard `*`).

## 3. Isolasi Data & Ketergantungan Keamanan (`app/core/security.py`)

Seluruh endpoint terlindungi menegakkan autentikasi dan isolasi berbasis pemilik:

- **`get_current_user`**: Memvalidasi Bearer JWT access token via `PyJWT` dan memastikan akun aktif (`User.is_active == True`).
- **`require_admin` & `is_admin`**: Membatasi aksi administrasi dan izin baca lintas-pengguna hanya untuk peran `admin`.
- **`require_job_owner`**: Dipakai oleh endpoint di [`app/api/routes/jobs.py`](app/api/routes/jobs.py), [`app/api/routes/export.py`](app/api/routes/export.py), dan [`app/api/routes/report.py`](app/api/routes/report.py); mengembalikan `HTTP 404` apabila job tidak ada, sudah soft-deleted (`deleted_at IS NOT NULL`), atau bukan milik pengguna non-admin.
- **`_owned_dataset`**: Dipakai di [`app/api/routes/upload.py`](app/api/routes/upload.py) untuk memastikan hanya pemilik (atau admin) yang dapat mengunggah fenotipe/GFF3/GO/FASTA atau membaca dataset yang masih aktif.

## 4. Konfigurasi Keamanan Produksi

- Saat `ENV` bernilai selain `development` atau `test` (mis. `ENV=production`), `resolve_jwt_secret()` di [`app/core/security.py`](app/core/security.py) mewajibkan `JWT_SECRET_KEY` berupa string acak non-placeholder sepanjang $\ge 32$ karakter.
- Pada mode non-`development`, atribut cookie refresh otomatis mengaktifkan `Secure=True`.

## 5. Integrasi Frontend (`App.jsx`, `AuthContext.jsx`, `httpClient.js`, `apiClient.js`)

- Rute terproteksi (`/upload`, `/jobs/:jobId`, `/jobs/:jobId/results`) dimuat secara *lazy* via `React.lazy` + `Suspense` di dalam `<ProtectedRoute />` pada `frontend/src/App.jsx`, dengan halaman publik `/login`, `/register`, serta fallback 404 (`NotFoundPage.jsx`).
- Seluruh panggilan HTTP autentik (`apiFetch` di `apiClient.js` dan `httpClient` Axios di `httpClient.js`) berbagi satu fungsi mutex `refreshAccessToken` sehingga lonjakan respons `401` paralel hanya memicu tepat satu permintaan `POST /api/v1/auth/refresh`.
