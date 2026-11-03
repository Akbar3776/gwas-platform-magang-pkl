# ADR-007: Validasi Skema Data & Serialisasi API (Pydantic v2 Rust Core vs. Marshmallow & Cerberus)

## Status
Accepted

## Tanggal
2026-11-02

## Konteks
Platform **GWAS Web Platform** menerima konfigurasi analisis genomik yang sangat kompleks dari antarmuka pengguna web. Parameter komputasi ini menentukan jalannya 10 tahapan pipeline pada engine bioinformatika (PLINK, GEMMA, Beagle, bcftools) dan harus memenuhi batasan biologis serta numerik yang sangat ketat:
- **Batasan Numerik Kritis**:
  - Frekuensi alel minor: $0 \le \text{MAF} \le 0.5$ (`Field(ge=0, le=0.5)`).
  - Ambang batas kehilangan data: $0 \le \text{Geno} \le 1$ dan $0 \le \text{Mind} \le 1$.
  - Koefisien determinasi LD Pruning: $0 < r^2 < 1$ (`Field(gt=0, lt=1)`).
  - Alokasi memori JVM Beagle: Pola regex `-Xmx\d+[mMgG]` (misal `-Xmx4g`, `-Xmx16g`).
- **Validasi Silang Antar-Kolom (*Cross-Field Invariants*)**:
  - Spesies tanaman menyerbuk sendiri / klonal (*Inbred*): Filter Hardy-Weinberg Equilibrium (HWE) harus dinonaktifkan (`hwe = None`), karena asumsi perkawinan acak tidak berlaku.
  - Spesies kawin silang (*Outcross*): Filter HWE wajib aktif (default $10^{-6}$).
  - Mode Imputasi: Jika mode `reference` dipilih, path berkas referensi `reference_vcf_path` wajib diisi.

### Kebutuhan & Kendala Arsitektur:
1. **Pencegahan Galat Dini (*Fail-Fast Execution*)**:
   Kesalahan input konfigurasi harus ditolak di pintu gerbang API HTTP ($< 10\text{ ms}$) dengan pesan diagnostik terstruktur, sebelum pipeline Celery diluncurkan. Kesalahan parameter yang lolos ke worker akan mengakibatkan pemborosan resource komputasi dan crash proses bioinformatika setelah berjalan berjam-jam.
2. **Kinerja Serialisasi Throughput Tinggi**:
   Endpoint visualisasi (seperti `/results/{job_id}/manhattan-data` dan `/results/{job_id}/lead-snps`) mengembalikan ribuan hingga puluhan ribu baris data koordinat genomik. Waktu serialisasi dari objek Python ke JSON response harus berjalan secepat mungkin tanpa membebani event loop async FastAPI.
3. **Type Safety & Dokumentasi API Otomatis**:
   Sistem memerlukan kontrak antarmuka (*interface contract*) bertipe statis yang terintegrasi langsung dengan dokumentasi interaktif **OpenAPI (Swagger UI & ReDoc)** tanpa penulisan spesifikasi JSON/YAML manual.

---

## Keputusan
Kami memutuskan untuk menggunakan **Pydantic v2** dengan engine berbasis **Rust Core (`pydantic-core`)** sebagai standar tunggal pemodelan skema data, validasi input, serialisasi response API, dan konfigurasi lingkungan sistem.

### Detail Arsitektur & Pola Implementasi:
1. **Pemodelan Skema Deklaratif Bertipe Kuat ([`app.schemas.pipeline`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/schemas/pipeline.py))**:
   - Seluruh kontrak parameter panggung didefinisikan sebagai turunan `pydantic.BaseModel` dengan anotasi tipe data standar Python (`Literal`, `Enum`, `Field`).
   - Setiap batasan numerik didefinisikan secara langsung pada `Field` (`ge`, `le`, `gt`, `lt`, `pattern`).
2. **Validator Multi-Level Terstruktur**:
   - `@model_validator(mode="before")`: Digunakan untuk inferensi otomatis cerdas (misalnya, jika parameter `hwe` disediakan tetapi `population_type` tidak dispesifikasikan, sistem otomatis menyimpulkan `population_type = "outcross"`).
   - `@model_validator(mode="after")`: Memastikan konsistensi invariant logika bisnis (misal memastikan `hwe = None` untuk populasi inbred, dan memvalidasi keberadaan `reference_vcf_path` saat mode imputasi referensi aktif).
3. **Integrasi Ekosistem FastAPI Bawaan**:
   - Handler route FastAPI mendeklarasikan skema Pydantic sebagai tipe argumen request body (`cfg: QCPipelineConfig`) dan response model (`response_model=JobStatusResponse`).
   - FastAPI menangani validasi, deserialisasi, injeksi dependency, dan pembuatan skema OpenAPI 3.1 secara native tanpa lapisan middleware tambahan.
4. **Validasi Konfigurasi Sistem Startup ([`app.core.config.Settings`](file:///d:/Games/Code/GWAS_Study_ML/gwas-platform/backend/app/core/config.py))**:
   - Menggunakan pustaka `pydantic-settings` (`BaseSettings`) untuk memvalidasi seluruh environment variables (`DATABASE_URL`, `CELERY_BROKER_URL`, `STORAGE_ROOT`, `JWT_SECRET_KEY`) saat aplikasi pertama kali boot. Jika variabel penting tidak valid, server gagal menyala (*fail-fast at startup*).

---

## Alternatif yang Dipertimbangkan

### 1. Pydantic v2 (Stack Terpilih)
- **Kelebihan**:
  - **Performa Ekstrem (Rust Core Engine)**: Berkat `pydantic-core` yang ditulis dalam bahasa Rust, validasi dan serialisasi JSON berjalan $5\times - 50\times$ lebih cepat dibandingkan Pydantic v1 atau Marshmallow murni Python.
  - **Integrasi Native 100% dengan FastAPI**: Menjadi fondasi dasar ekosistem FastAPI; validasi HTTP 422 terstruktur (`RequestValidationError`) dan skema OpenAPI 3.1 dibuat secara otomatis.
  - **Dukungan Penuh Python Type Hinting (PEP 484/586)**: Menyajikan *auto-completion*, linting, dan *type-checking* sempurna di IDE dan linter tanpa deklarasi field terpisah.
  - **Penanganan Galat Standar Terperinci**: Mengembalikan format galat JSON standar yang menyebutkan lokasi field (`loc`), pesan galat (`msg`), dan tipe aturan yang dilanggar (`type`).
  - **Manajemen Konfigurasi Terpadu**: Satu paradigma skema yang sama digunakan untuk request body, response serialization, dan environment settings.
- **Kekurangan**:
  - Memerlukan biner Rust terkompilasi (telah disediakan dalam bentuk pre-built wheel di PyPI untuk seluruh OS/arsitektur utama).
  - Terdapat perbedaan konvensi validator dibanding versi warisan Pydantic v1.
- **Kesimpulan**: Standar de facto industri modern Python yang memberikan kinerja, keamanan tipe data, dan integrasi OpenAPI terbaik.

---

### 2. Marshmallow
- **Deskripsi**: Pustaka skema dan serialisasi Python klasik yang populer di ekosistem Flask, Pyramid, dan SQLAlchemy.
- **Kelebihan**:
  - Pustaka murni Python yang sangat matang dengan ekosistem plugin luas (`marshmallow-sqlalchemy`).
  - Mendukung nesting skema dan post-dump hooks.
- **Kekurangan**:
  - **Kinerja Jauh Lebih Lambat**: Seluruh logika konversi tipe dan iterasi dieksekusi di interpreter Python murni ($5\times - 10\times$ lebih lambat dibandingkan Rust-backed Pydantic v2).
  - **Duplikasi Definisi Skema (*Dual-Model Anti-Pattern*)**: Mengharuskan pengembang mendefinisikan field menggunakan objek kelas tersendiri (`fields.Float(required=True, validate=validate.Range(min=0, max=0.5))`), terpisah dari anotasi tipe Python murni, menyebabkan kode berulang dan hilangnya autocompletion IDE.
  - **Integrasi FastAPI Tidak Bawaan**: Membutuhkan wrapper pihak ketiga untuk menjembatani Marshmallow ke FastAPI, mengorbankan dukungan native OpenAPI.
- **Alasan Ditolak**: Kecepatan serialisasi lambat, ketiadaan integrasi native dengan FastAPI, dan sintaks deklarasi skema yang redundan.

---

### 3. Cerberus
- **Deskripsi**: Pustaka validasi data Python berbasis kamus dictionary deklaratif (`{"maf": {"type": "float", "min": 0, "max": 0.5}}`).
- **Kelebihan**:
  - Skema dapat disimpan dan dimuat langsung dari format JSON atau berkas YAML.
  - Ringan tanpa ketergantungan pada ekstensi C/Rust.
- **Kekurangan**:
  - **Ketiadaan Objek Bertipe (*No Class Representation*)**: Output dari validasi tetap berupa dictionary `dict[str, Any]` mentah, bukan objek Python dengan atribut bertipe (`config.maf`). IDE tidak dapat memberikan autocompletion atau deteksi salah ketik atribut (*no compile-time safety*).
  - **Hanya Memvalidasi, Tidak Melakukan Serialisasi**: Cerberus tidak menangani konversi tipe (*type coercion*) atau serialisasi objek model ORM ke JSON response.
  - **Ketiadaan Dukungan OpenAPI**: Tidak dapat menghasilkan skema Swagger/OpenAPI otomatis untuk antarmuka web.
- **Alasan Ditolak**: Berorientasi dictionary mentah yang menghilangkan seluruh keuntungan type safety, autocompletion IDE, dan dokumentasi API interaktif.

---

### 4. Pure Python Dataclasses + Validasi Manual di Route
- **Deskripsi**: Menggunakan `@dataclass` bawaan Python dan menulis validasi defensif manual menggunakan blok `if/else` di setiap fungsi controller route.
- **Kelebihan**:
  - Nol dependensi eksternal (*Python standard library only*).
- **Kekurangan**:
  - **Boilerplate Berlebihan & Rapuh**: Controller route akan dipenuhi ratusan baris validasi manual (`if maf < 0 or maf > 0.5: raise HTTPException(...)`).
  - **Inkonsistensi Format Pesan Galat**: Pesan galat menjadi heterogen dan sulit dikonsumsi secara andal oleh antarmuka frontend React.
  - Risiko tinggi kelalaian pemeriksaan parameter (*human error*) saat skema bertambah kompleks.
- **Alasan Ditolak**: Menciptakan duplikasi kode masif, membebani pemeliharaan jangka panjang, dan rentan terhadap celah validasi.

---

## Konsekuensi

### Konsekuensi Positif:
1. **Pencegahan Kegagalan Komputasi Sejak Dini**: Parameter bioinformatika yang tidak valid (misal alokasi memori Beagle salah atau $r^2$ di luar batas) ditolak seketika pada pintu masuk HTTP, melindungi server dari eksekusi task Celery yang ditakdirkan gagal.
2. **Dokumentasi API Selalu Sinkron**: Dokumentasi interaktif Swagger UI (`/docs`) dan ReDoc (`/redoc`) dihasilkan secara otomatis dan 100% akurat sesuai kode sumber tanpa memerlukan pemeliharaan manual berkas OpenAPI.
3. **Throughput Serialisasi Tinggi**: Serialisasi ribuan koordinat SNP pada respons visualisasi diproses oleh core Rust berkecepatan tinggi, meminimalkan latensi respons API.
4. **Pengalaman Pengembang Unggul**: Type hint yang ketat memungkinkan refactoring aman, penangkapan kesalahan sebelum runtime, dan autocompletion cerdas di lingkungan pengembangan.

### Konsekuensi Negatif & Mitigasi:
1. **Waktu Booting Awal & Ukuran Package Biner**:
   - Pustaka Pydantic v2 menyertakan ekstensi biner Rust terkompilasi (`pydantic_core`).
   - *Mitigasi*: PyPI menyediakan binary wheel resmi untuk seluruh arsitektur utama (Linux x86_64, aarch64, Windows, macOS), sehingga instalasi via `pip` tidak memerlukan compiler Rust lokal.
2. **Aturan Migrasi Sintaks Validator**:
   - Pengembang harus memahami perbedaan sintaks v2 (`@model_validator` menggantikan `@root_validator`).
   - *Mitigasi*: Repositori telah menggunakan konvensi Pydantic v2 secara konsisten di seluruh modul `app/schemas`, didukung oleh linter ruff/biome.
