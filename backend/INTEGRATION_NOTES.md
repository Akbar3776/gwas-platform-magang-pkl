# Catatan Integrasi: Auth & Data Isolation

## 1. Registrasi router di `main.py`

```python
from api.routers import auth, export, report, reference_panels

app.include_router(auth.router)
app.include_router(export.router)
app.include_router(report.router)
app.include_router(reference_panels.router)
```

## 2. CORS — WAJIB `allow_credentials=True` agar cookie refresh token terkirim

Karena refresh token dikirim sebagai cookie (bukan header), request
cross-origin dari frontend (mis. `localhost:5173` ke `localhost:8000` saat
development) butuh konfigurasi CORS eksplisit:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.domain-kamu.com"],  # daftar eksplisit, JANGAN "*"
    allow_credentials=True,   # wajib True, atau cookie tidak akan pernah terkirim
    allow_methods=["*"],
    allow_headers=["*"],
)
```

`allow_origins=["*"]` tidak bisa dipakai bersamaan dengan `allow_credentials=True`
(dilarang oleh spesifikasi CORS) — daftar origin harus eksplisit.

## 3. Selaraskan `export.py` dan `report.py` yang sudah dibuat sebelumnya

Dua endpoint yang dibuat di sesi sebelumnya (`GET /results/{job_id}/export`
dan `GET /results/{job_id}/report`) sudah memakai pola pengecekan kepemilikan
manual:

```python
if job.triggered_by != user.id and user.role != "admin":
    raise HTTPException(status_code=403, ...)
```

Sekarang setelah ada `get_owned_job` (lihat
`datasets_and_jobs_isolation_example.py`), sebaiknya kedua endpoint itu
disederhanakan untuk konsisten dan mengurangi duplikasi logika:

```python
@router.get("/{job_id}/export")
def export_job_results(
    job: AnalysisJob = Depends(get_owned_job),
    db: Session = Depends(get_db),
):
    # job sudah pasti milik user yang login (atau 404 kalau bukan)
    ...
```

Catatan: `get_owned_job` versi contoh mengembalikan 404 untuk non-pemilik
(bukan 403) — lihat penjelasan kenapa di docstring-nya. Kalau kamu tetap
ingin admin bisa mengakses job siapa pun (dashboard admin, dsb.), buat
varian kedua `get_owned_job_or_admin` yang mengizinkan `user.role == "admin"`
lewat, alih-alih mencampur logikanya ke dalam endpoint.

## 4. Environment variables yang perlu ditambahkan

```
JWT_SECRET_KEY=<hasil: openssl rand -hex 32>
ENV=production   # "development" hanya untuk lokal tanpa HTTPS
```

## 5. Menghubungkan AuthContext ke App React

```jsx
// App.jsx
import { AuthProvider, ProtectedRoute } from "./AuthContext";
import { BrowserRouter, Route, Routes } from "react-router-dom";

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />

          <Route element={<ProtectedRoute />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/jobs/:jobId" element={<ResultDashboard />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
```

`ResultDashboard.jsx` (dibuat di sesi sebelumnya) memakai `fetch` langsung ke
`${API_BASE}/results/{jobId}/export` dan `.../report` — ganti pemanggilan
`fetch` di sana dengan `apiFetch` dari `apiClient.js` supaya header
`Authorization` ikut terpasang otomatis dan auto-refresh berlaku juga untuk
kedua endpoint download tersebut.
