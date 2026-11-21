# ADR-011: Pembuangan Otomatis Scaffold / Kontig Non-Utama pada Normalisasi & Perbaikan `started_at` Stage Summary

## Status
Accepted

## Tanggal
2026-11-22

## Konteks
Pada dataset bawang putih nyata (*Allium sativum*, job pembanding `36ac3ef3-602a-41a8-a8f1-1b6a359b6645` vs. job pasca-perbaikan `38c1a66e-173e-4f68-9073-1ea5edea586f`), file VCF berukuran ~8,9 GB memuat **2.036.116 varian** yang terdistribusi pada **10.813 kontig**:

| Kelompok | Kontig | SNP | Porsi |
| --- | ---: | ---: | ---: |
| Kromosom utama `chr1`–`chr8` | 8 | 1.863.771 | 91,5% |
| Kontig non-utama (`scaffold<N>`) | 10.805 | 172.345 | 8,5% |
| └ *scaffold* ber-marker tunggal (`TOTAL_SNP = 1`) | 498 | 498 | — |
| **Total VCF** | **10.813** | **2.036.116** | **100%** |

Membiarkan 10.805 kontig *scaffold* ikut melewati seluruh pipeline menimbulkan beberapa masalah praktis:
1. **Beban tahap & artefak hilir:** Beagle 5.4 sebelumnya membuang `981` varian dari kontig bermarker tunggal pasca-QC agar terhindar dari `MarkerMap.meanSingleBaseGenDist`, sementara `172` ribu varian *scaffold* sisanya tetap diproses oleh QC, struktur (`n_variants_pruned` naik menjadi `94.230`), GEMMA (`1.009.509` SNP), Post-GWAS (`5` dari `14` sinyal sugestif di `36ac3ef3` berasal dari *scaffold* tanpa pemetaan gen GFF3 kromosom utama), dan anotasi.
2. **Pembengkakan JSON/UI Fase 0:** `inspection_report.json` memuat `10.813` baris kontig di `chromosome_summary.chromosomes`, memaksa komponen [`DataInspectionPanel.jsx`](../../frontend/src/components/DataInspectionPanel.jsx) merender ribuan baris tabel DOM.
3. **Bug pencatatan waktu tahap (D38):** Pada temuan review `W27`, `stage_summary.json` di 10 tahap selalu menulis `started_at == finished_at` (`duration_s = 0.0`) karena task tahap belum meneruskan waktu mulai eksekusi ke `write_stage_summary()`.

---

## Keputusan

### 1. Regex Blacklist `NON_PRIMARY_CONTIG_REGEX` sebagai Sumber Kebenaran Tunggal
- Konfigurasi [`Settings`](../../backend/app/core/config.py) menambahkan variabel lingkungan `NON_PRIMARY_CONTIG_REGEX` dengan default *blacklist*:
  `(?i)^(scaffold|contig|ctg|unplaced|chrun)|_(random|unplaced|alt)$`
  yang divalidasi saat startup (`re.compile`). Mengosongkan nilai (`""`) menonaktifkan penyaringan.
- Klasifikasi dipusatkan di fungsi tunggal `is_non_primary_contig()` pada [`chromosome_service.py`](../../backend/app/services/chromosome_service.py).
- Pola ini secara eksplisit mempertahankan penamaan kromosom lintas spesies (`chr1`–`chr8`, `1`, `Chr01`, `Gm01`, `LG3`, `A01`, `chrX`), fragmen split (`chr7_p0`–`chr7_p2`), serta kontig organel (`chrM`, `chrC`, `Pt`, `MT` — tidak dibuang secara default kecuali ditambahkan pengguna ke regex).

### 2. Pembuangan *Streaming* Varian & Header `##contig` di Normalisasi (Pas dengan Split >32-bit)
- Fungsi `split_overflow_chromosomes()` di [`chromosome_service.py`](../../backend/app/services/chromosome_service.py) membuang record varian dan baris header `##contig=<...>` untuk kontig non-utama dalam pass *streaming* yang sama dengan pemecahan kromosom overflow.
- Pemeriksaan non-utama dijalankan **sebelum** validasi karakter kontig dan filter `POS <= 0`.
- **Guard anti salah konfigurasi:** bila seluruh varian VCF ikut terbuang (`kept_variants == 0` saat `non_primary_removed_variants > 0`), normalisasi gagal segera dengan pesan `ValueError("Semua varian berada di kontig non-utama; periksa NON_PRIMARY_CONTIG_REGEX")` tanpa meninggalkan keluaran setengah jadi.
- Baik jalur utama [`normalization_tasks.py`](../../backend/app/tasks/normalization_tasks.py) maupun jalur *standalone* [`qc_tasks.py`](../../backend/app/tasks/qc_tasks.py) meneruskan pola ini dan mencatat `non_primary_removed_contigs`, `non_primary_removed_variants`, serta `contig_filter_pattern` di `qc_overflow_stats.json`, `stage_summary.json`, `qc_comparison.json`, log `[Fase 0 - Buang Scaffold]`, dan `job.parameters["contig_filter"]`.

### 3. Ringkasan Fase 0 Ringkas di JSON/UI, Audit Lengkap di TXT
- Pada [`inspection_tasks.py`](../../backend/app/tasks/inspection_tasks.py), berkas audit [`chromosome_characteristics_summary.txt`](../../backend/app/services/chromosome_service.py) tetap ditulis lengkap seluruh kontig (termasuk `10.805` *scaffold* dan `NEEDS_SPLIT`) sebagai jejak audit sesuai format dokumen.
- Payload JSON `inspection_report.json` hanya menyertakan kromosom utama pada `chromosome_summary.chromosomes` dan merangkum kontig non-utama ke blok agregat `excluded_contigs` (`pattern`, `contigs`, `variants`, `single_marker_contigs`, maks `20` `examples`). Pada dataset bawang putih nyata (`38c1a66e`), ukuran `inspection_report.json` menyusut menjadi **`7.525` bytes** (jauh di bawah batas uji `< 50 KB` untuk `10.000` *scaffold* sintetis).
- UI [`DataInspectionPanel.jsx`](../../frontend/src/components/DataInspectionPanel.jsx) dan [`QcComparisonPanel.jsx`](../../frontend/src/components/QcComparisonPanel.jsx) menampilkan ringkasan agregat *scaffold* dengan label teks eksplisit tanpa toggle konfigurasi UI, serta tetap merender payload job historis dengan utuh.

### 4. Kompatibilitas Resume Job Lama
- Pada [`pipeline_orchestrator.py`](../../backend/app/tasks/pipeline_orchestrator.py), `discover_resume_state()` mewajibkan keberadaan penanda `contig_filter_pattern` di `normalization/qc_overflow_stats.json` bila job menyimpan `contig_filter` di `job.parameters`.
- Job lama sebelum perubahan ini tetap dilanjutkan tanpa dipaksa normalisasi ulang, sambil mencatat log `[INFO] Job lama: scaffold tidak disaring; jalankan ulang dari awal (from_scratch) untuk menerapkan`.

### 5. Retensi Filter Marker Tunggal Beagle sebagai Jaring Pengaman Generik
- Pemfilter `_prepare_beagle_safe_vcf()` di [`imputation_tasks.py`](../../backend/app/tasks/imputation_tasks.py) tetap dipertahankan sebagai jaring pengaman generik (bersama `MAX_32BIT_POS = 2.100.000.000` bp) untuk menghadapi VCF khusus atau job lama yang di-resume; pada run bawang putih nyata pasca-pembuangan *scaffold*, jumlah yang disaring tercatat `singles_removed = 0`.

### 6. Perbaikan Pencatatan `started_at` dan `duration_s` pada 10 Tahap
- Seluruh 10 task tahap pipeline (`STAGE_SEQUENCE`) kini mencatat `stage_started_at = datetime.now(timezone.utc)` di awal eksekusi dan meneruskannya ke `write_stage_summary()` ([`_common.py`](../../backend/app/tasks/_common.py)).
- Pemanggilan `write_stage_summary()` tanpa `started_at` tetap kompatibel tetapi mencatat `[WARN]` ke log worker.

---

## Alternatif yang Ditolak
- **Regex *whitelist* kromosom utama (mis. `^chr[0-9]+$`):** Ditolak karena berisiko membuang seluruh kromosom asli pada spesies dengan konvensi nama lain (`1..N`, `Gm01`, `LG3`, `A01`, `chrX`).
- **Memaksa normalisasi ulang otomatis saat *resume* job lama:** Ditolak agar artefak job historis tetap stabil dan *resume* hanya mengulang tahap yang gagal; pengguna tetap bisa menjalankan `from_scratch` bila ingin menerapkan filter *scaffold*.
- **Menghapus baris *scaffold* dari `chromosome_characteristics_summary.txt`:** Ditolak agar file TSV Fase 0 tetap menjadi jejak audit lengkap sebelum penyaringan, sementara JSON/UI dibuat ringkas.
- **Menambahkan *toggle* UI on/off untuk membuang *scaffold*:** Ditolak sesuai keputusan pemilik proyek untuk menjaga UI bebas opsi ambigu; lanjutan teknis tetap dapat disesuaikan via variabel `NON_PRIMARY_CONTIG_REGEX`.
- **Menghapus penyaring marker tunggal `_prepare_beagle_safe_vcf()`:** Ditolak karena mempertahankan *defense-in-depth* tidak memakan biaya ketika kontig utama valid dan tetap melindungi imputasi Beagle untuk input non-standar atau job lama.

---

## Verifikasi Job Nyata (`36ac3ef3` Lama vs. `38c1a66e` Baru, Bawang Putih)

| Aspek | Job Lama (`36ac3ef3…`) | Job Baru (`38c1a66e…`) | Keterangan |
| --- | --- | --- | --- |
| Status & Durasi 10 Tahap | `completed`, seluruh 10 `duration_s = 0.0` | `completed`, seluruh 10 `duration_s > 0` (total `~3.971 s`) | `15,52 s` inspeksi, `367,03 s` normalisasi, `51,00 s` QC, `3.118,96 s` Beagle, `43,93 s` struktur, `16,15 s` GEMMA, `335,37 s` GAPIT, `8,19 s` postgwas, `1,86 s` vis, `13,28 s` anotasi |
| Kontig non-utama dibuang (Normalisasi) | `0` kontig / `0` varian | `10.805` kontig / `172.345` varian | `normalization/normalized.vcf.gz` membawa `10` header `##contig` (`chr1..6, chr7_p0..p2, chr8`), **0 `scaffold`** |
| Keluar Normalisasi / Masuk QC | `2.036.116` varian | `1.863.771` varian (`overflow_removed = 0`) | `chr7` tetap terpecah `p0/p1/p2` |
| Lolos QC (sampel / SNP) | `149` / `1.013.158` (`50,24%` loss) | `149` / `927.518` (`50,23%` loss) | `qc/plink_qc.bim` mempertahankan `231` varian `chr7 > 2,147 Gb`, **0 `scaffold`** |
| Beagle `singles_removed` | `981` | **`0`** | Jaring pengaman tetap ada (`beagle_guard_removed = 0`) |
| Struktur (`n_variants_pruned` / LD decay) | `94.230` / `100 kb` | `73.704` / `100 kb` | Keduanya memakai blok kontigu non-prune `20.000` SNP |
| GEMMA (`total_snps` / `λGC`) | `1.009.509` / `1,225` (postgwas `1,2337`) | `925.267` / `0,693` (postgwas `0,6731`) | `0` *scaffold* di `association.assoc.txt` & `gwas_results.normalized.tsv`; `231` varian `chr7 > 2,147 Gb` tetap teruji |
| Lead SNP Bonferroni & Multi-locus GAPIT | `3:1223403176` (`p = 4,33e-8`); GAPIT `7` lead (`BLINK chr7_p0:313980654`, `3:1223403176`, dll.) | `3:1223403176` (`p = 4,19e-8`); GAPIT `7` lead identik (`FarmCPU`, `BLINK`, `MLMM`) | Sinyal utama lebih kuat tanpa *scaffold* |
| Puncak Sugestif | `14` (`5` di antaranya di `scaffold*`) | `11` (**0** di `scaffold`) | Semua puncak berada di kromosom utama |
| Anotasi GFF3 / Region / KASP | `3` Near-gen, `2` Intergenik (`FASTA` belum ada saat run) | `3` Near-gen, `2` Intergenik, `5` region (`1.000.005` bp), KASP `3/5` (`completed`) | `0` `scaffold` di `candidate_regions` & `kasp_input.tsv` |

---

## Konsekuensi
- **Positif:** Dataset dengan ribuan kontig *scaffold* pendek langsung bersih sejak tahap normalisasi, memangkas ukuran `normalized.vcf.gz`, beban PCA/kinship/GWAS, artefak *inspection* JSON/UI, serta menyingkirkan hit sugestif semu dari kontig non-utama tanpa menghilangkan varian valid kromosom panjang (`chr7 > 2,147 Gb`).
- **Risiko/Catatan Operasional:** Peneliti yang secara eksplisit ingin menganalisis asosiasi pada *scaffold* dapat mengosongkan `NON_PRIMARY_CONTIG_REGEX=` di berkas `.env` sebelum menjalankan pipeline.
