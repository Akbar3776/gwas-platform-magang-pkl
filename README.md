 # GWAS Platform

Platform web end-to-end untuk analisis Genome-Wide Association Study (GWAS).
Aplikasi ini menyediakan alur visual bagi peneliti yang tidak perlu berpindah
antara banyak CLI: upload VCF, normalisasi, QC, imputasi, analisis GEMMA, dan
visualisasi hasil Manhattan/QQ Plot.

## Fitur Utama

- Chunked dan resumable upload untuk VCF berukuran MB hingga GB.
- Normalisasi VCF dengan `bcftools` dan output BGZF terindeks.
- QC PLINK dengan parameter MAF, GENO, MIND, dan HWE.
- Imputasi Beagle dengan batas memory JVM dan timeout.
- Analisis GWAS GEMMA dengan Linear Model atau Linear Mixed Model.
- Task asynchronous menggunakan Celery dan Redis.
- Progress tracker frontend dan polling status job.
- Export hasil ZIP dan report PDF.
- Monitoring worker/task melalui Celery Flower.

## Arsitektur Sistem

```text
Browser React/Vite
		  |
		  v
Nginx: static files + /api/v1 reverse proxy
		  |
		  v
FastAPI  ---> PostgreSQL (metadata)
	|
	+------> Redis (Celery broker/result backend)
					  |
					  v
		  Celery Worker
		  bcftools -> PLINK -> Beagle -> GEMMA
					  |
					  v
		  Shared VCF/result storage
```

Komponen utama:

| Komponen | Peran |
|---|---|
| React/Vite | Upload, konfigurasi pipeline, progress tracker, dan plot |
| Nginx | Static hosting, gzip, batas upload, reverse proxy |
| FastAPI | API, autentikasi, validasi, metadata job/dataset |
| PostgreSQL | User, dataset, job, result, dan status pipeline |
| Redis | Broker Celery dan result backend |
| Celery Worker | Eksekusi CLI bioinformatika di background |
| Flower | Dashboard observabilitas task dan worker |

## Prasyarat

- Docker Desktop dan Docker Compose.
- Minimal 4 GB RAM untuk development; imputasi memerlukan memory lebih besar
  sesuai ukuran reference panel.
- Untuk deployment production, siapkan secret JWT dan password PostgreSQL dari
  secret manager atau environment injection.

## Development dengan Docker Compose

Salin template environment:

```powershell
Copy-Item backend/.env.example backend/.env
```

Jalankan service development:

```powershell
docker compose -f infra/docker-compose.yml build
docker compose -f infra/docker-compose.yml up -d
```

Endpoint penting:

- API health: `http://localhost:8000/health`
- API docs: `http://localhost:8000/docs`
- Celery Flower: `http://localhost:5555`
- Frontend dev server: `http://localhost:5173`

Lihat log:

```powershell
docker compose -f infra/docker-compose.yml logs -f backend celery_worker flower
```

## Production Deployment

Production menggunakan multi-stage build: Node membangun React `dist/`, lalu
image Nginx menyajikan hasil build dan meneruskan `/api/` ke Gunicorn.

Set password database sebelum Compose melakukan interpolasi variable:

```powershell
$env:POSTGRES_PASSWORD = "password-kuat"
docker compose -f infra/docker-compose.prod.yml build
docker compose -f infra/docker-compose.prod.yml up -d
```

Service backend production dijalankan dengan Gunicorn dan worker ASGI:

```text
gunicorn app.main:app --bind 0.0.0.0:8000 --workers 2 --worker-class uvicorn.workers.UvicornWorker
```

Production membuka Nginx pada port `80`. Flower dipetakan ke `127.0.0.1:5555`
agar dashboard tidak terbuka langsung ke jaringan publik. Gunakan SSH tunnel,
VPN, atau reverse proxy ber-authentication untuk akses operasional.

```powershell
docker compose -f infra/docker-compose.prod.yml ps
docker compose -f infra/docker-compose.prod.yml logs -f nginx backend celery_worker flower
```

## Alur Kerja Biologi

1. **Upload VCF**: browser memecah file menjadi chunk. Backend menyimpan chunk
	ke staging dan Celery merakitnya secara streaming setelah finalize.
2. **Normalisasi**: `bcftools view` memvalidasi file, `bcftools sort` mengurutkan
	varian, lalu output disimpan sebagai VCF BGZF terindeks. Reference FASTA
	opsional dipakai untuk normalisasi allele.
3. **Quality Control**: PLINK menyaring varian dan sampel berdasarkan MAF,
	missingness, serta HWE. Output PLINK dan VCF QC diteruskan ke imputasi.
4. **Imputasi**: Beagle melengkapi genotype menggunakan reference panel. JVM
	dibatasi dengan `-Xmx` agar worker tidak menghabiskan memory container.
5. **GWAS**: GEMMA membangun kinship matrix untuk LMM atau menjalankan LM,
	kemudian menghasilkan file association.
6. **Visualisasi**: hasil association diparse dan diringkas untuk Manhattan Plot
	dan QQ Plot tanpa mengirim file mentah besar ke browser.

Jika satu tahap gagal, Celery chain berhenti. Status job disimpan sebagai
`FAILED`, sementara pesan error dan log tail tersedia untuk ditampilkan pada UI.

## Observabilitas dan Logging

Flower memantau queue, task aktif, retry, durasi, worker online, serta statistik
worker. Gunakan dashboard untuk melihat apakah task menunggu broker, berjalan,
atau gagal. Detail log tetap tersedia melalui Docker:

```powershell
docker compose -f infra/docker-compose.prod.yml logs --since=10m backend celery_worker
```

FastAPI menggunakan `backend/app/core/logging_config.py`. Logger memakai
`StreamHandler` ke stdout, sehingga Docker/Gunicorn dapat mengumpulkan log
tanpa menulis log besar ke filesystem container. Atur level dengan:

```dotenv
LOG_LEVEL=INFO
```

Untuk file VCF besar, log sebaiknya mencatat `job_id`, tahap, command yang aman
ditampilkan, durasi, exit code, dan tail error. Jangan mencatat isi VCF, token,
password, atau secret.

## Testing

Backend:

```powershell
$env:PYTHONPATH = "backend"
python -m pip install -r backend/requirements.txt -r backend/requirements-test.txt
python -m pytest backend/tests -q
```

Frontend:

```powershell
Push-Location frontend
npm ci
npm run lint
npm test
npm run build
Pop-Location
```

Test backend memock `subprocess.Popen`, Celery `.delay()`, database session,
dan dependency user. Test frontend memock hook polling sehingga tidak perlu
memanggil API.

## CI/CD

GitHub Actions di `.github/workflows/test.yml` berjalan pada Pull Request dan
push ke `main`. Pipeline menjalankan Flake8, Pytest, ESLint, Jest/RTL, dan
build Vite. Deployment image production sebaiknya dijalankan hanya setelah
workflow ini berhasil.

## Data dan Secrets

- Jangan commit `backend/.env`.
- Gunakan `backend/.env.example` sebagai template.
- Jangan menghapus named volume database/storage tanpa backup.
- Jalankan migration Alembic sebelum memakai schema baru pada environment
  existing.
