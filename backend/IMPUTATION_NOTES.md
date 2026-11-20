# Catatan Konfigurasi: Worker Imputasi (Memory & Queue)

## 1. Alokasi Memori & Queue Imputasi Beagle

Imputasi genotipe dengan **Beagle 5.4 (JVM)** mengonsumsi RAM jauh lebih besar dibanding tahap normalisasi VCF atau QC PLINK. Di `infra/docker-compose.yml` dan `infra/docker-compose.prod.yml`, service `celery_worker` mendengarkan antrean `-Q celery,normalization,qc,imputation,gwas` dengan batas container `4G` dan konfigurasi default:

- `BEAGLE_MAX_MEMORY=` dikosongkan secara bawaan agar JVM memakai `-XX:MaxRAMPercentage=75.0` dan `-XX:InitialRAMPercentage=10.0`; JVM membaca limit cgroup container sendiri.
- Override eksplisit `-XmxN` dipotong maksimal 75% limit cgroup yang terdeteksi, dengan peringatan di log. `RLIMIT_AS` sengaja tidak dipakai untuk Beagle karena membatasi virtual address space JVM, bukan RSS.

Uji pada container 4G dengan input job nyata menunjukkan perbedaan berikut:

| Kasus | Hasil |
| --- | --- |
| A: `-Xmx8192m` + RLIMIT_AS 10650 MB | Crash di Window 1 karena native malloc |
| B: `-Xmx3072m` + RLIMIT_AS 3993 MB | JVM gagal inisialisasi |
| C: `-Xmx3072m` tanpa RLIMIT_AS | Beagle berjalan normal melewati 29 window tanpa crash |

Apabila menjalankan worker terdedikasi hanya untuk queue `imputation` dengan kapasitas container lebih besar (mis. `8G`), pastikan `--concurrency=1` agar seluruh jatah `-Xmx` aman dipakai satu proses Java tanpa rebutan antar-task di cgroup yang sama:

```yaml
services:
  worker-imputation:
    build: ./backend
    command: celery -A app.tasks.celery_app worker --loglevel=info -Q imputation --concurrency=1
    environment:
      - BEAGLE_MAX_MEMORY=
      - BEAGLE_JAR_PATH=/opt/tools/beagle.jar
      - CELERY_BROKER_URL=redis://redis:6379/0
      - STORAGE_ROOT=/data/storage
    mem_limit: 8g
    memswap_limit: 8g
    volumes:
      - reference-panels:/data/reference-panels:ro
      - vcf_storage:/data/storage
    depends_on:
      - redis
      - db
```

## 2. Kenapa 75% Heap, Bukan 100%

`HEAP_FRACTION_OF_CONTAINER_LIMIT = 0.75` di [`app/core/resource_limits.py`](app/core/resource_limits.py) menyisakan ~25% memori container untuk:

- Metaspace dan JIT code cache JVM.
- Thread stack Java sesuai jumlah `nthreads`.
- Buffer native I/O saat membaca/menulis VCF terkompresi BGZF.
- Overhead proses pendamping seperti `plink2` saat konversi & QC ulang pasca-imputasi (`_prepare_beagle_safe_vcf` juga terlebih dahulu menyaring varian dengan `POS > 2.10 Gb` serta scaffold ber-marker tunggal `< 2` posisi).

Menyetel `-Xmx` sama dengan batas RAM container (`mem_limit`) dapat memicu **OOM-kill** kernel pada proses worker tanpa jejak exception Python yang jelas.
