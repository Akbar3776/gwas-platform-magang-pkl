# ADR-010: Migrasi Alur Kerja GWAS 2026-11-21 (Split Pseudo-Kromosom, LD Decay Non-Prune, Region Kandidat, dan Marker KASP)

## Status
Accepted

## Tanggal
2026-11-21

## Konteks
Pada genom berukuran raksasa seperti bawang putih (*Allium sativum*, genom haploid ~16 Gb dengan `chr7` sepanjang `2.161.681.585` bp > batas integer bertanda 32-bit `2^31 - 1 = 2.147.483.647` bp), pipeline GWAS awal menyaring habis seluruh varian di atas `2.100.000.000` bp (`5.613` varian pada dataset uji nyata) agar tool eksternal (`bcftools`/`tbi`, `plink`, `Beagle`, `GEMMA`) tidak mengalami *overflow* 32-bit. Akibatnya, wilayah ujung kromosom `chr7` tidak pernah ikut diuji asosiasi maupun dianotasi. Selain itu, alur perhitungan LD decay pada struktur populasi sebelumnya dijalankan di atas dataset hasil *LD pruning*, desain primer pasca-GWAS hanya menghasilkan primer PCR konvensional tanpa dukungan marker kompetitif **KASP**, dan ringkasan progres tahap tidak tersimpan seragam di `/jobs/{id}/status`.

Migrasi alur kerja tanggal 2026-11-21 menyelaraskan implementasi platform dengan praktik terbaru tanpa merusak kompatibilitas artefak job lama yang telah selesai.

---

## Keputusan

### 1. Pemecahan Kromosom Overflow Eksplisit (`<chrom>_p<k>` + Peta JSON) vs. Encoding Numerik
- Kromosom yang memiliki varian `POS > CHROM_SPLIT_THRESHOLD_BP` dipecah pada tahap normalisasi menjadi kontig semu `<chrom>_p0`, `<chrom>_p1`, `<chrom>_p2`, ... berdasarkan indeks `part = (pos - 1) // unit_bp` dan `pos_baru = pos - part * unit_bp` ([`chromosome_service.py`](../../backend/app/services/chromosome_service.py)).
- Varian dengan `POS <= 0` (`INVALID_POS`) dicatat di Fase 0 dan disaring pada tahap normalisasi, tanpa menghapus varian `POS > 2.147.483.647` bp.
- Pemetaan invers lengkap disimpan atomik pada `normalization/chrom_split_map.json` dan dipulihkan di hilir (GWAS normalizer, Post-GWAS clumping, Parquet/Manhattan, anotasi GFF3, region kandidat, dan desain primer/KASP) lewat `restore_coord()`.
- Job lama tanpa `chrom_split_map.json` tetap diperlakukan sebagai pemetaan identitas oleh `load_split_map()`.

### 2. Ambang Pemecahan `2.147.483.647` bp (`unit = 1 Gb`) & Jaring Pengaman Beagle (`2.100.000.000` bp)
- Parameter default ditetapkan melalui Pydantic Settings ([`config.py`](../../backend/app/core/config.py)):
  - `CHROM_SPLIT_THRESHOLD_BP = 2_147_483_647`
  - `CHROM_SPLIT_UNIT_BP = 1_000_000_000`
- Karena `unit_bp = 1.000.000.000` bp, setiap fragmen `<chrom>_p<k>` memiliki panjang maksimal `1.000.000.000` bp — jauh di bawah batas `2.100.000.000` bp (*sliding window* 40 cM Beagle 5.4).
- Jaring pengaman `MAX_32BIT_POS = 2_100_000_000` tetap dipertahankan di [`imputation_tasks.py`](../../backend/app/tasks/imputation_tasks.py) (`_prepare_beagle_safe_vcf`) khusus untuk mengawal kontig yang tidak dipecah bila ada varian pada rentang `2.100.000.001–2.147.483.647` bp yang bisa memicu `java.lang.IllegalArgumentException` pada estimasi batas *window* Beagle.

### 3. Kurva LD Decay Berbasis Blok Kontigu Non-Prune vs. `plink2 --r2-unphased` Penuh
- Penghitungan LD decay pada [`structure_tasks.py`](../../backend/app/tasks/structure_tasks.py) kini membaca berkas PLINK sebelum *LD pruning* (`qc/plink_qc` atau `imputation/imputed`), dibatasi secara deterministik pada blok kontigu hingga `ld_decay_max_snps` (default `20.000` SNP via `select_ld_decay_blocks()`).
- Nilai $r^2$ *unphased* dihitung secara vektorial di Python/NumPy (`compute_ld_decay_curve()`) tanpa membangkitkan matriks LD penuh di disk, serta mencatat field eksplisit `method` dan `source_bfile` pada `structure/ld_decay.json`.

### 4. Model Asosiasi GLM Tetap Memakai `GEMMA -lm 1` vs. `plink2 --glm`
- Mode GLM tetap dieksekusi menggunakan biner `gemma -lm 1` ([`gemma_tasks.py`](../../backend/app/tasks/gemma_tasks.py)), bukan `plink2 --glm hide-covar`.
- Output GEMMA langsung melalui pipeline normalisasi 10 kolom yang sama (`assoc_normalizer.py`) dengan LMM (`gemma -lmm 4`), menyederhanakan koreksi kovariat PC serta penanganan sampel bertanda `-9`.

### 5. Ekstraksi Region Kandidat (Fase 5.4) Tanpa Dependensi Eksternal `bedtools`
- Pembuatan `annotation/candidate_regions.csv`, `candidate_regions.bed`, dan `candidate_regions.fa` dilakukan murni dalam Python ([`flanking_service.py`](../../backend/app/services/flanking_service.py)) memanfaatkan indeks FASTA `.fai` secara *streaming* $O(1)$ RAM.
- Ukuran total ekspor FASTA dibatasi secara ketat melalui `CANDIDATE_REGION_MAX_TOTAL_BP = 50_000_000` agar antarmuka web maupun bundel ekspor `.zip` tidak membengkak pada puluhan puncak signifikan berjendela lebar.

### 6. Default Parameter QC Sampel `--mind 0.1`
- Nilai bawaan `QCPipelineConfig.mind` ([`pipeline.py`](../../backend/app/schemas/pipeline.py)) dan formulir upload frontend ([`GwasUploadPage.jsx`](../../frontend/src/pages/GwasUploadPage.jsx)) diseragamkan menjadi `0.1` (sebelumnya `0.05` di beberapa default skema/UI). Job historis tetap mempertahankan nilai `mind` yang tercatat pada `job.parameters`.

### 7. Keterbatasan Perhitungan LD Lintas Batas Split (`_p<k>`)
- Setelah kromosom dipecah di batas `unit_bp = 1.000.000.000` bp (mis. `chr7_p0`, `chr7_p1`, `chr7_p2`), operasi berbasis kontig pada PLINK (*LD pruning* `--indep-pairwise`, clumping `--clump`, dan blok kontigu LD decay) berjalan secara independen per potongan pseudo-kromosom.
- Pasangan SNP yang mengangkangi batas tepat `1.000.000.000` bp tidak dihitung *pairwise LD*-nya lintas bagian; keputusan ini diterima karena efeknya hanya terjadi pada jendela ~100 kb di sekitar titik potong `1 Gb`/`2 Gb` dari panjang kromosom `> 2,1 Gb`.

### 8. Desain Marker KASP (Port EasyKASP ke Python, Oligo QC Bertail, dan Penundaan Fase 8 BLAST)
- Algoritma desain primer KASP dari VBA *EasyKASP* (DOI [`10.1186/s12859-025-06322-x`](https://doi.org/10.1186/s12859-025-06322-x)) di-port murni ke Python pada [`kasp_service.py`](../../backend/app/services/kasp_service.py) dengan atribusi sumber di header modul; tim wajib meninjau syarat lisensi repositori hulu *EasyKASP* sebelum distribusi komersial/publik.
- Sekuens masukan (`build_kasp_input()` di `flanking_service.py`) menggunakan koordinat asli, memeriksa kecocokan basa `REF` FASTA, menyisipkan masker `N` untuk varian tetangga dari `.bim` di sekitar SNP target, memakai panjang flank default `KASP_FLANK_BP = 150` bp per sisi (batas bawah `60` bp), dan menolak indel/`ref_mismatch`.
- Evaluasi struktur sekunder (`analyze_kasp_oligos()`) dijalankan via `primer3-py` (**hairpin**, **homodimer**, **heterodimer**, dan **end-stability**) terhadap oligo **dengan tail fluoresensi FAM/HEX utuh**, serta menandai `warn` bila `Tm struktur >= Tm primer - 5 °C` atau `ΔG < -9 kcal/mol` tanpa menggagalkan status desain.
- Verifikasi spesifisitas genom via **BLAST (Fase 8)** ditunda keluar cakupan rilis ini; artefak bebas-tail `annotation/kasp_primers_no_tail.fa` langsung disediakan agar siap diumpankan ke tahap BLAST mendatang.

### 9. Pemeliharaan `Pipeline_Gwas.md` & Status Dokumen Pendahulu
- Berkas acuan ilmiah [`Pipeline_Gwas.md`](../../Pipeline_Gwas.md) **dipertahankan utuh (tidak diubah)** sebagai dokumen referensi historis pemilik repositori.
- Perilaku aktual sistem didokumentasikan secara sinkron pada [`README.md`](../../README.md), [`PROJECT_STRUCTURE.md`](../../PROJECT_STRUCTURE.md), [`docs/design.md`](../design.md), dan ADR ini, sedangkan dokumen rencana awal [`PLAN_INTEGRASI_PIPELINE_GWAS.md`](../PLAN_INTEGRASI_PIPELINE_GWAS.md) ditandai berstatus **SUPERSEDED**.

---

## Alternatif yang Ditolak
- **Membuang seluruh varian `POS > 2.100.000.000` bp (`filter_vcf_32bit_overflow`) atau memfilter lewat `awk` tersembunyi:** Ditolak karena menghilangkan informasi genetik sah di ujung kromosom panjang (`3.011` varian pasca-QC dan puncak sugestif `chr7:2.116.776.358` terbukti terdeteksi pada bawang putih setelah migrasi).
- **Encoding numerik posisi (`Pos * 1000` atau kompresi koordinat):** Ditolak karena mengubah jarak fisik pasangan basa asli yang langsung memengaruhi *clumping* (`--clump-kb`), jendela anotasi gen GFF3, serta membutuhkan skala ulang di seluruh utilitas visualisasi.
- **Menjalankan `plink2 --r2-unphased` penuh dan mengintegrasikan `bedtools`:** Ditolak karena pembengkakan disk puluhan gigabyte untuk pasangan LD seluruh genom serta ketergantungan biner eksternal baru di container.

---

## Konsekuensi
- **Positif:** Nol varian berposisi valid (`POS > 0`) hilang saat melewati normalisasi (`overflow_removed = 0`); job nyata bawang putih mempertahankan `231` varian pasca-GWAS di atas `2^31 - 1` bp hingga puncak & anotasi; panel Primer kini menyediakan ekspor PCR konvensional dan KASP bertail lengkap dengan analisis termodinamika.
- **Risiko/Catatan Operasional:** Pengguna yang tetap menginginkan penamaan kontig FASTA cocok dengan VCF (`chr1..chr8`, bukan aksesi NCBI seperti `CM031529.1`) tetap perlu menjalankan pra-pemrosesan `prepare_reference_fasta.py` sebelum mengunggah FASTA referensi; bila Fase 8 BLAST ditambahkan di masa depan, berkas input `kasp_primers_no_tail.fa` sudah siap pakai.
