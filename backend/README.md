# Backend GWAS Platform

Panduan ini menjelaskan instalasi dan setup backend GWAS Platform untuk local development dan Docker Compose.

## Prasyarat

- Python 3.10 atau lebih baru
- Docker Desktop dengan Docker Compose
- Git
- PostgreSQL dan Redis lokal hanya diperlukan jika backend dijalankan di luar Docker
- Linux tools PLINK dan GEMMA disediakan oleh `backend/Dockerfile`. Pada local Python, keduanya harus tersedia di `PATH` jika task bioinformatika dijalankan.

> Untuk Windows, local development API dapat dijalankan dengan Python/venv, tetapi task yang memakai `preexec_fn` dan tool Linux lebih konsisten dijalankan melalui Docker.

## Struktur Konfigurasi

Backend membaca `.env` dari current working directory melalui `pydantic-settings`. Docker Compose juga membaca file yang sama melalui `env_file: ../backend/.env`.

Buat file `backend/.env` dengan menyalin `backend/.env.example`. Jangan commit
file `.env` asli karena berisi secret.

```powershell
Copy-Item .env.example .env
```

Template tersebut berisi nilai development untuk Docker Compose dan komentar
tentang perbedaan hostname Docker (`db`, `redis`) dengan local development
(`localhost`).

Contoh isi inti environment:

```dotenv
# Database
# Local Python: gunakan localhost.
# Docker Compose: compose akan override nilai ini menjadi hostname service `db`.
DATABASE_URL=postgresql+psycopg2://gwas_user:gwas_password@localhost:5432/gwas_db

# Celery dan Redis
# Local Python: gunakan localhost.
# Docker Compose: compose akan override hostname menjadi `redis`.
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# Storage lokal atau mount Docker
STORAGE_ROOT=./storage

# JWT
# JWT_SECRET_KEY dipakai langsung oleh app.core.security.
# SECRET_KEY dipertahankan untuk konfigurasi Settings yang tersedia saat ini.
JWT_SECRET_KEY=ganti-dengan-secret-random-minimal-32-karakter
SECRET_KEY=ganti-dengan-secret-random-minimal-32-karakter
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Environment aplikasi
ENV=development

# Opsional untuk task imputasi
IMPUTATION_CONTAINER_MEM_LIMIT_MB=8192
BEAGLE_JAR_PATH=/opt/tools/beagle.jar
```

Generate secret yang lebih kuat, misalnya dengan Python:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Pada production, gunakan secret manager atau environment injection, bukan file `.env` yang disimpan di repository.

## Opsi A: Local Development dengan Python

Jalankan dari folder `backend`.

### 1. Buat virtual environment

PowerShell:

```powershell
cd D:\Games\Code\GWAS_Study_ML\gwas-platform\backend
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Jika PowerShell memblokir aktivasi script, gunakan Python tanpa aktivasi:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Linux/macOS:

```bash
cd gwas-platform/backend
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Fitur export ZIP dan report PDF juga memakai dependency tambahan di
`app/requirements-addition.txt`. Pasang dependency tersebut jika endpoint
export/report akan digunakan:

```powershell
python -m pip install -r app/requirements-addition.txt
```

`backend/Dockerfile` sekarang juga memasang file tambahan tersebut saat build:

```dockerfile
RUN pip install --no-cache-dir -r app/requirements-addition.txt
```

Perintah itu dijalankan sebelum source aplikasi disalin. Setelah mengubah
dependency, rebuild image dengan:

```powershell
docker compose build --no-cache backend celery_worker
```

### 2. Jalankan dependensi PostgreSQL dan Redis

Jika tidak memakai Docker, pastikan service berikut hidup:

- PostgreSQL pada `localhost:5432`, database `gwas_db`, user `gwas_user`.
- Redis pada `localhost:6379`.

Atau jalankan hanya dependency dari Compose:

```powershell
cd ..\infra
docker compose up -d db redis
```

### 3. Jalankan FastAPI

Dari `backend` dengan `.venv` aktif:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Endpoint pemeriksaan:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
```

Karena router menggunakan prefix `/api/v1`, endpoint aplikasi berada di bawah
`/api/v1`, misalnya `/api/v1/auth/login` dan `/api/v1/jobs/qc`.

### 4. Jalankan Celery worker secara lokal

Dari `backend` dengan PostgreSQL dan Redis aktif:

```powershell
celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2
```

Task imputasi/GWAS juga memerlukan binary dan file tool bioinformatika yang sesuai. Jika hanya menguji API upload atau autentikasi, worker dapat ditunda.

## Opsi B: Docker Compose

Compose berada di `infra/docker-compose.yml`, sehingga perintah dijalankan dari folder `infra`.

### 1. Siapkan environment

Pastikan file berikut ada:

```text
backend/.env
backend/Dockerfile
infra/docker-compose.yml
```

### 2. Build image backend

```powershell
cd D:\Games\Code\GWAS_Study_ML\gwas-platform\infra
docker compose build backend celery_worker
```

Build menginstal PLINK, GEMMA, dependency Python, dan library sistem untuk WeasyPrint. Proses pertama dapat cukup lama.

### 3. Jalankan semua service

```powershell
docker compose up -d
```

Service yang dijalankan:

| Service | Fungsi | Port host |
|---|---|---:|
| `backend` | FastAPI + Uvicorn reload | `8000` |
| `db` | PostgreSQL metadata | `5432` |
| `redis` | Broker/result backend Celery | `6379` |
| `celery_worker` | Task QC, imputasi, GWAS | tidak diekspos |

Periksa status dan log:

```powershell
docker compose ps
docker compose logs -f backend
docker compose logs -f celery_worker
```

Uji API:

```powershell
Invoke-WebRequest http://localhost:8000/health
```

### 4. Hentikan service

```powershell
docker compose down
```

Perintah ini menghentikan container tetapi mempertahankan named volume. Untuk menghapus data PostgreSQL, Redis, dan storage Docker secara permanen:

```powershell
docker compose down -v
```

Gunakan `down -v` hanya jika memang siap kehilangan database dan file upload lokal.

## Database dan Alembic

### Kondisi repository saat ini

Folder `backend/alembic/` saat ini masih kosong dan belum terdapat `alembic.ini`, `alembic/env.py`, atau revision file. Karena itu, `alembic upgrade head` belum dapat dijalankan langsung sebelum Alembic diinisialisasi.

Model SQLAlchemy utama berada di:

```text
backend/app/db/engine/models.py
```

Model tersebut mencakup `User`, `Dataset`, `AnalysisJob`, `Result`, dan `ReferencePanel`.

### Inisialisasi Alembic pertama kali

Jalankan dari folder `backend` dengan virtual environment aktif:

```powershell
cd D:\Games\Code\GWAS_Study_ML\gwas-platform\backend
alembic init alembic
```

Jika folder `alembic` sudah dibuat tetapi kosong, pastikan perintah tidak menimpa file yang berisi perubahan. Buat `alembic.ini` dan struktur Alembic secara manual atau pindahkan folder kosong terlebih dahulu.

Edit `alembic/env.py` agar metadata model terdaftar. Bagian pentingnya harus memuat:

```python
from app.db.base import Base
from app.db.engine import models  # noqa: F401

target_metadata = Base.metadata
```

Atur URL database dari environment, bukan hardcode. Salah satu pendekatan sederhana di `env.py`:

```python
from app.core.config import settings

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
```

Pastikan `PYTHONPATH` menunjuk ke folder `backend` saat menjalankan Alembic:

```powershell
$env:PYTHONPATH = (Get-Location).Path
```

### Membuat migration awal

Untuk local PostgreSQL pada host:

```powershell
alembic revision --autogenerate -m "create initial GWAS schema"
alembic upgrade head
```

Untuk database PostgreSQL di Compose, jalankan setelah service `db` sehat. Jika Alembic belum ikut image, jalankan dari container backend setelah image memiliki konfigurasi Alembic:

```powershell
docker compose exec backend alembic upgrade head
```

Periksa revision aktif:

```powershell
alembic current
alembic history
```

Setelah perubahan model, termasuk kolom soft-delete `deleted_at`, buat migration baru:

```powershell
alembic revision --autogenerate -m "add retention soft delete fields"
alembic upgrade head
```

Jangan memakai `Base.metadata.create_all()` sebagai pengganti migration pada environment bersama atau production; gunakan Alembic agar perubahan schema terlacak.

## Troubleshooting Umum

### Port 8000, 5432, atau 6379 sudah digunakan

Cek proses/container yang memakai port:

```powershell
docker compose ps
Get-NetTCPConnection -LocalPort 8000,5432,6379 -ErrorAction SilentlyContinue
```

Pilihan solusi:

- Hentikan service lama yang memakai port tersebut.
- Ubah port host di `infra/docker-compose.yml`, misalnya `"8001:8000"` untuk backend.
- Untuk PostgreSQL/Redis, jika hanya backend container yang mengaksesnya, mapping port host dapat dihapus; service tetap dapat berkomunikasi melalui network Compose.
- Jika backend dijalankan di host, sesuaikan port pada `.env` dengan port host baru. Jangan mengubah hostname `db`/`redis` untuk koneksi antar-container.

### Backend container restart terus

```powershell
docker compose logs --tail=200 backend
docker compose logs --tail=200 celery_worker
```

Periksa khususnya `JWT_SECRET_KEY`, dependency import, URL database, dan apakah dependency PostgreSQL/Redis sudah healthy.

### PostgreSQL gagal start atau data lama tidak cocok

Named volume mempertahankan schema dan data lama. Untuk development yang boleh kehilangan data:

```powershell
docker compose down -v
docker compose up -d db redis
```

Untuk data penting, jangan menghapus volume. Perbaiki migration atau koneksi database terlebih dahulu.

### `alembic upgrade head` gagal karena file tidak ditemukan

Repository ini belum berisi konfigurasi Alembic lengkap. Pastikan `alembic.ini`, `alembic/env.py`, dan folder `alembic/versions/` sudah dibuat, `target_metadata` mengarah ke `Base.metadata`, serta `PYTHONPATH=backend`.

### Worker tidak menerima task

Pastikan Redis hidup dan URL broker sesuai konteks eksekusi:

- Dari host: `redis://localhost:6379/0`.
- Dari container Compose: `redis://redis:6379/0`.

Lihat log worker dan pastikan task terdaftar:

```powershell
docker compose logs -f celery_worker
```

### File VCF atau hasil tidak ditemukan

Backend dan worker harus memakai volume `vcf_storage` yang sama. Jangan mengganti mount storage hanya pada salah satu service. Periksa nilai `STORAGE_ROOT` dan isi volume:

```powershell
docker compose exec backend sh -lc "ls -la /data/storage"
docker compose exec celery_worker sh -lc "ls -la /data/storage"
```

## Checklist Setup Berhasil

- `backend/.env` tersedia dan secret JWT sudah diganti.
- `docker compose ps` menunjukkan `backend`, `db`, `redis`, dan `celery_worker` berjalan.
- `http://localhost:8000/health` mengembalikan status `ok`.
- `/docs` dapat dibuka.
- Alembic sudah memiliki `env.py` dan revision awal.
- `alembic upgrade head` selesai tanpa error.
- Worker Celery terhubung ke Redis.
- Volume storage yang dipakai backend dan worker sama.

## Production Deployment

Production Compose berada di `infra/docker-compose.prod.yml`. Nginx membangun
frontend Vite melalui multi-stage Dockerfile di `infra/nginx/Dockerfile`, lalu
menyajikan `dist/` sekaligus meneruskan `/api/` ke Gunicorn.

Sebelum menjalankan production stack, set password database di environment
shell dan pastikan `backend/.env` production berisi `JWT_SECRET_KEY` yang kuat:

```powershell
$env:POSTGRES_PASSWORD = "gunakan-password-kuat-di-secret-manager"
docker compose -f infra/docker-compose.prod.yml build
docker compose -f infra/docker-compose.prod.yml up -d
```

FastAPI dijalankan oleh backend container dengan:

```text
gunicorn app.main:app --bind 0.0.0.0:8000 --workers 2 --worker-class uvicorn.workers.UvicornWorker
```

Nginx menerima trafik pada port `80`, melayani React SPA, meneruskan API ke
backend internal, mengaktifkan gzip untuk JSON/asset, dan menerima request
hingga `20M` agar chunk VCF 10 MB tidak terkena HTTP 413. Backend dan Celery
worker berbagi volume `vcf_storage`; database dan Redis memiliki volume
persisten masing-masing.

Periksa service production:

```powershell
docker compose -f infra/docker-compose.prod.yml ps
docker compose -f infra/docker-compose.prod.yml logs -f nginx backend celery_worker
```
