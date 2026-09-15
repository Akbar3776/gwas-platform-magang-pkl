# Catatan Konfigurasi: Worker Imputasi (Memory & Queue)

## 1. Kenapa perlu worker/queue khusus untuk imputasi

Imputasi (Beagle) jauh lebih rakus RAM dibanding tahap normalisasi/QC. Sesuai
`design.md` bagian 1.4, worker Celery sudah dipisah per-queue
(`normalization`, `qc`, `imputation`, `gwas`). Pastikan worker untuk queue
`imputation` dijalankan sebagai **service Docker terpisah** dengan:

- `mem_limit` eksplisit yang **sama persis** dengan nilai yang kamu pakai
  untuk `IMPUTATION_CONTAINER_MEM_LIMIT_MB` (lihat `core/resource_limits.py`).
- `concurrency` Celery rendah (mis. `--concurrency=2`) — kalau satu container
  dibatasi 8GB dan menjalankan 4 task Beagle bersamaan, masing-masing task
  hanya "melihat" limit 8GB dari sisi hitungan `-Xmx`, padahal total RAM
  container yang tersedia harus dibagi 4. **Jumlah task paralel per worker
  container HARUS diperhitungkan saat menentukan `container_mem_limit_mb`
  per task**, atau turunkan concurrency ke 1 task per container untuk queue
  ini agar penghitungan tetap sederhana dan aman.

## 2. Contoh docker-compose.yml

```yaml
services:
  worker-imputation:
    build: ./backend
    command: celery -A worker worker --loglevel=info -Q imputation --concurrency=1
    environment:
      - IMPUTATION_CONTAINER_MEM_LIMIT_MB=8192
      - BEAGLE_JAR_PATH=/opt/tools/beagle.jar
      - CELERY_BROKER_URL=redis://redis:6379/0
    mem_limit: 8g          # HARUS SINKRON dengan IMPUTATION_CONTAINER_MEM_LIMIT_MB di atas
    memswap_limit: 8g      # matikan swap agar OOM terjadi cepat & jelas, bukan thrashing
    volumes:
      - reference-panels:/data/reference-panels:ro   # read-only, panel tidak pernah ditulis worker
      - storage:/storage
    depends_on:
      - redis
      - postgres

  worker-gwas:
    build: ./backend
    command: celery -A worker worker --loglevel=info -Q gwas --concurrency=2
    mem_limit: 4g
    # ...

volumes:
  reference-panels:
  storage:
```

Kalau nanti pindah ke Kubernetes, padanannya adalah resource `limits.memory`
di pod spec worker imputasi, dengan nilai yang sama.

## 3. Kenapa 75% heap, bukan 100%

`HEAP_FRACTION_OF_CONTAINER_LIMIT = 0.75` di `core/resource_limits.py` sengaja
menyisakan ~25% dari `mem_limit` container untuk:

- Metaspace & JIT code cache JVM
- Thread stack (tiap thread Java default ~1MB, Beagle multi-threaded via `nthreads`)
- Native buffer I/O saat membaca/menulis VCF terkompresi
- Sedikit ruang untuk `bcftools`/proses lain yang mungkin berjalan di
  container yang sama

Kalau `-Xmx` diset sama persis dengan `mem_limit` Docker, JVM yang sehat
sekalipun (heap belum penuh) bisa tetap kena **OOM-killed oleh kernel**
karena RSS total proses (heap + overhead di atas) melebihi cgroup limit.
Gejalanya: task Celery "hilang" tanpa exception Python yang jelas (worker
process langsung mati), yang jauh lebih sulit di-debug dibanding exception
`RuntimeError` biasa dari return code non-nol.

## 4. Alternatif: `-XX:MaxRAMPercentage` (adaptif)

Pendekatan di kode ini (`-Xmx<angka statis>`) dipilih karena eksplisit dan
mudah diaudit — cocok untuk single-tenant imputation queue dengan `mem_limit`
tetap. Alternatif yang lebih adaptif untuk lingkungan dengan `mem_limit`
yang bisa berubah-ubah (mis. autoscaling K8s) adalah membiarkan JVM modern
(JDK 10+) membaca limit cgroup-nya sendiri lewat:

```
java -XX:MaxRAMPercentage=75.0 -XX:+UseContainerSupport -jar beagle.jar ...
```

Ini menghilangkan kebutuhan menghitung `heap_mb` manual di Python — JVM akan
otomatis menyesuaikan ke `mem_limit` container yang sedang berjalan. Trade-off:
sedikit kurang eksplisit di level kode aplikasi (nilai heap tidak terlihat
langsung dari argumen), jadi tetap pertahankan Lapis 2 (`RLIMIT_AS`) sebagai
jaring pengaman kalaupun beralih ke pendekatan ini.
