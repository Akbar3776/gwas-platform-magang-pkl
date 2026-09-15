# ==============================================================================
# GWAS Pipeline - Backend Dockerfile
# Base: python:3.10-slim
# Menyertakan dependensi OS untuk build tools bioinformatika (PLINK, GEMMA)
# ==============================================================================

FROM python:3.10-slim

LABEL maintainer="GWAS Platform Team"
LABEL description="FastAPI + Celery backend, membungkus PLINK & GEMMA sebagai subprocess"

# ------------------------------------------------------------------------------
# 1. Dependensi OS level
#    - build-essential : kompilasi package Python yang butuh C extension
#    - wget, unzip      : mengunduh & mengekstrak binary PLINK/GEMMA
#    - libgomp1         : runtime OpenMP, dibutuhkan GEMMA & PLINK multi-thread
#    - zlib1g-dev        : dependensi umum untuk tools bioinformatika (bcftools/htslib)
#    - ca-certificates  : agar wget bisa mengakses HTTPS dengan aman
# ------------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    unzip \
    libgomp1 \
    zlib1g-dev \
    bcftools \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------------------
# 2. Instalasi PLINK 1.9 (binary statis resmi)
#    Diinstal ke /usr/local/bin agar tersedia sebagai command global `plink`
#    Catatan: sesuaikan URL versi PLINK sesuai kebutuhan (PLINK 1.9 vs PLINK 2.0)
# ------------------------------------------------------------------------------
ARG PLINK_VERSION=20260913
RUN wget -q "https://s3.amazonaws.com/plink1-assets/plink_linux_x86_64_20260913.zip" \
    -O /tmp/plink.zip \
    && unzip -o /tmp/plink.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/plink \
    && rm -f /tmp/plink.zip /usr/local/bin/LICENSE /usr/local/bin/*.txt

# ------------------------------------------------------------------------------
# 3. Instalasi GEMMA (binary rilis resmi dari GitHub)
#    Sesuaikan GEMMA_VERSION dengan rilis terbaru saat build image.
# ------------------------------------------------------------------------------
ARG GEMMA_VERSION=0.98.5
RUN wget -q "https://github.com/genetics-statistics/GEMMA/releases/download/v0.98.5/gemma-0.98.5-linux-static-AMD64.gz" \
    -O /tmp/gemma.gz \
    && gunzip -c /tmp/gemma.gz > /usr/local/bin/gemma \
    && chmod +x /usr/local/bin/gemma \
    && rm -f /tmp/gemma.gz

# ------------------------------------------------------------------------------
# 4. (Opsional, disiapkan untuk tahap QC/normalisasi lanjutan)
#    bcftools/vcftools bisa ditambahkan di sini dengan pola wget+build serupa
#    bila dibutuhkan di luar apt repository slim.
# ------------------------------------------------------------------------------

# Catatan: WeasyPrint butuh library sistem (Pango, Cairo, GDK-PixBuf).
# Untuk image Docker berbasis Debian/Ubuntu, tambahkan di Dockerfile backend:
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libcairo2 \
    libffi-dev shared-mime-info \
    && rm -rf /var/lib/apt/lists/*


# ------------------------------------------------------------------------------
# 5. Setup direktori kerja & dependensi Python
# ------------------------------------------------------------------------------
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Dependency tambahan untuk export ZIP dan report PDF.
COPY app/requirements-addition.txt /tmp/requirements-addition.txt
RUN pip install --no-cache-dir -r /tmp/requirements-addition.txt \
    && rm -f /tmp/requirements-addition.txt

COPY ./app ./app

# ------------------------------------------------------------------------------
# 6. Direktori staging untuk file VCF (di-mount sebagai volume oleh docker-compose)
# ------------------------------------------------------------------------------
RUN mkdir -p /data/storage

# ------------------------------------------------------------------------------
# 7. Non-root user demi keamanan container
# ------------------------------------------------------------------------------
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app /data/storage
USER appuser

EXPOSE 8000

# Default command untuk service backend (di-override oleh celery_worker di compose)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
