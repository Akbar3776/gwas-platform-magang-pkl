# ADR-012: Upload `.gz` Aman, Normalisasi Ontologi GO Lokal, Fallback Isoform, Background Konsisten Kontig, & Indeks Best-Hit Ortolog BLAST

## Status
Accepted

## Tanggal
2026-11-26

## Konteks
Integrasi data anotasi fungsional nyata pada bawang putih (*Allium sativum*, `Asa.GO.gz`, `Garlic.gff3.gz`, `go-basic.obo`, dan `Asa_Ath.blast.gz`) memunculkan **21 temuan** (F1–F21):

1. **Ketidakkompatibilitas & keamanan unggahan GO (F1–F6, F16, F18):** Ekstensi `.GO.gz` (mis. `Asa.GO.gz`) ditolak allow-list; resolver path GO terduplikasi tanpa `go_annotation.txt.gz`; berkas lama terhapus ketika validasi unggahan baru gagal; tidak ada batas ukuran dekompresi, jumlah baris, panjang baris, validasi ID gen, maupun rasio baris valid; ekspor CSV backend belum menetralkan formula injection; serta endpoint upload belum memblokir job aktif (409).
2. **Ketidakcocokan ID & hilangnya konteks ontologi (F7–F10, F17, F19):** Pemetaan GO memakai ID transkrip (`*.1`), sementara UI mengunci `feature_type="gene"` tanpa fallback isoform sehingga kecocokan kandidat menjadi `0%` (`unavailable`). Selain itu, file 2 kolom tanpa ontologi lokal membuat semua term berdomain `unknown` (yang sebelumnya tidak dirender di UI) serta membiarkan `46` `alt_id` (`2.525` baris) dan `102` term obsolete (`4.545` baris) tanpa normalisasi.
3. **Inkonsistensi universe background & cache hasil job lama (F11–F13, F20–F21):** Universe background sebelumnya menggabungkan seluruh gen termasuk `2.958` gen pada kontig non-utama (`scaffold*`), tidak konsisten dengan penyaringan kontig per job (ADR-011). Di sisi lain, job `COMPLETED` tidak pernah menyegarkan hasil GO ketika pemetaan GO atau ortolog baru diunggah, dan `GET /go-enrichment` berpotensi menghitung enrichment dua kali dalam satu request.
4. **Kolom Ortolog selalu kosong (F14–F15):** Kolom Ortolog di tabel GO selalu bernilai `"-"`, sementara header `Asa_Ath.blast.gz` memiliki label kolom ke-3 keliru (`alignment_length` padahal isinya `%identity`) pada format outfmt6 12 kolom standar.

---

## Keputusan

Keputusan arsitektur **D1–D18** dan seluruh jawaban default pertanyaan terbuka **Q-G1–Q-G8** telah disetujui pemilik repositori pada 2026-11-26:

### 1. Unggahan Aman, Batas Sumber Daya, & CSV Netral (D1, D7, D13, D15, D16, Q-G2)
- Utilitas bersama `upload_validation.py` melakukan *streaming copy* berbatas ukuran (`copy_upload_bounded`), inspeksi *magic bytes* (`sniff_compression`: menolak ZIP/bzip2/xz/zstd & konten biner ber-NUL), serta pembacaan biner terbatas (`open_lines_bounded`: batas byte dekompresi, jumlah baris termasuk kosong/komentar, panjang baris `UPLOAD_TEXT_MAX_LINE_BYTES = 65536`, dan error `CORRUPT_GZIP` terstruktur).
- Nama file `.gz` apa pun diterima bila berformat GO (`.tsv`, `.txt`, `.gaf`, `.tab`, `.go`, `.gz`) atau BLAST outfmt6 (`.blast`, `.tsv`, `.txt`, `.tab`, `.out`, `.m8`, `.gz`) dan lolos validasi struktur serta rasio baris valid minimal (`GO_MAPPING_MIN_VALID_RATIO = 0.5`).
- Penulisan ke storage bersifat atomik melalui file `.part` sementara + `os.replace`, lalu pembersihan varian kanonik/lama. Unggahan mentah BLAST **tidak** disimpan; hanya indeks kanonik terurut `ortholog_besthit.tsv.gz` yang disimpan (**D10**).
- Error unggahan memakai struktur stabil `detail={"code": <KODE>, "message": <pesan Indonesia>}` tanpa membocorkan traceback atau path disk.
- Ekspor CSV backend (`Tabel_GO_Kandidat.csv` dan `Hasil_GO_Enrichment.csv`) menggunakan `csv_safe_cell()` yang identik dengan `frontend/src/utils/csv.js` (prefix `'` untuk awalan `= + - @ \t \r` selain angka bertanda murni).

### 2. Fallback Isoform & Normalisasi Ontologi OBO Lokal (D2, D3, D6, D8, D11, D12, D17, Q-G1, Q-G3)
- Utilitas bersama `gene_id_utils.py` menyediakan `GENE_ID_RE` dan `strip_isoform_suffix()` (menghapus tepat satu sufiks `\.\d+$`) tanpa impor melingkar antara `go_enrichment_service.py` dan `ortholog_service.py`.
- `GoAnnotationIndex` memprioritaskan kecocokan **exact**, kemudian **isoform fallback** satu level (`X.1` ↔ `X`), dengan prioritas sumber GO: `closest_gene`/`genes_in_window` + `Ontology_term` GFF lebih dulu, lalu file pemetaan.
- `go_ontology_service.py` memuat file OBO lokal (`GO_OBO_PATH`, berbatas `GO_OBO_MAX_BYTES = 209715200` dan `GO_OBO_MAX_LINES = 2000000`) dengan cache `(resolved_path, mtime_ns, size)`:
  - `alt_id` → GO ID primer;
  - `is_obsolete` dengan tepat satu `replaced_by` → *remap* berbatas maks 10 lompatan;
  - `is_obsolete` lain (`0` atau `>1` `replaced_by`; tanpa `consider`) → dibuang;
  - GO ID tak dikenal → dipertahankan dengan domain `"unknown"` (**Q-G3**) dan ditampilkan pada kolom UI *Domain tidak diketahui*.
- **Pemasangan OBO Docker (`Q-G1`):** `backend/Dockerfile` mengunduh rilis `2026-08-05` (`releases/2026-07-26`), memverifikasi SHA-256 (`b08d45b2...a3bcc`), dan menetapkan `ENV GO_OBO_PATH=/opt/go/go-basic.obo`. Pada `.env.example`, `GO_OBO_PATH` dibiarkan terkomentar agar `env_file` Docker Compose tidak menimpa variabel `ENV` dari image.

### 3. Background Universe Konsisten Kontig & Lazy Refresh Non-Destruktif (D4, D9, Q-G4, Q-G8)
- Universe background dihitung di ruang kunci GFF ber-GO (`feature_type` milik job) yang kontignya lolos penyaringan `is_non_primary_contig(seqid, pattern)` (tanpa hardcode `Asa0`/`scaffold`):
  - job dengan `job.parameters["contig_filter"]["pattern"]` memakai pola job tersebut (`pattern_source="job"`);
  - job pra-Phase 5 tanpa key `contig_filter` memakai `""` tanpa filter kontig (`legacy_none`, **Q-G8**);
  - pemanggilan tanpa konteks job (skrip harness) jatuh ke `settings.NON_PRIMARY_CONTIG_REGEX` (`pattern_source="settings"`).
- `results.py` menghitung `compute_go_fingerprint(...)` yang identik dengan `run_go_enrichment_analysis` (versi algoritma `GO_ALGO_VERSION = 2`, sha mapping, sha GFF, versi+sha OBO, sha indeks ortolog, `feature_type`, `contig_pattern`, dan `ALLOW_SYNTHETIC_ANNOTATION`).
- Saat `GET /go-enrichment` atau `GET /candidate-genes`, `refresh_go_if_stale()` melakukan penyegaran *lazy* non-destruktif (`Q-G4`) bila fingerprint berbeda (menyimpan salinan `go_enrichment.previous.json`, menjaga payload lama jika GFF tidak tersedia, dan menghindari komputasi ganda per request).

### 4. Indeks Best-Hit Ortolog BLAST & Integrasi UI (D5, D10, D14, D18, Q-G5, Q-G6, Q-G7)
- `ortholog_service.py` mem-parsing BLAST outfmt6 secara posisional (melewati hanya baris pertama non-komentar dengan kolom ke-3 non-numerik), lalu memilih *best hit* per `qseqid` berurutan: **bitscore maks → e-value min → pident maks → `subject_locus` leksikografis terkecil → `sseqid` leksikografis terkecil**.
- Endpoint `POST`/`DELETE /api/v1/upload/{file_id}/ortholog` menyimpan indeks `ortholog_besthit.tsv.gz`, dan `GET /api/v1/upload/datasets` menyertakan `has_ortholog`, `ortholog_filename`, dan `ortholog_n_genes`.
- Kolom UI bertajuk **"Ortolog / Homolog (BLAST best hit)"** menampilkan `subject_locus` dengan `title` detail (`identitas ...% · e-value ... · bitscore ...` serta `(+N lokus setara)` bila ada tie) tanpa tautan eksternal (`Q-G6`) maupun pratinjau % saat upload (`Q-G7`). Analisis pengayaan di semesta Arabidopsis tetap ditunda (`Q-G5`).

---

## Verifikasi Data Nyata (Checkpoints B–D)

| Komponen / Metrik | Hasil Terverifikasi | Catatan |
| --- | --- | --- |
| Ontologi OBO (`go-basic.obo`) | `releases/2026-07-26` (`48.340` term, `3.646` `alt_id`, `10.248` obsolete) | Tersemat di `/opt/go/go-basic.obo` |
| Normalisasi `Asa.GO.gz` (OBO) | `28.317` gen / `75.526` pasangan mentah → `28.304` gen / `71.613` pasangan (`2.525` via `alt_id`, `911` remap, `3.634` obsolete dibuang, `0` `unknown`) | `n_invalid_lines = 0` |
| Background Universe (`Garlic.gff3.gz`) | `57.561` fitur `gene`, `28.304` ber-GO; universe `gff_primary` = **`25.346`** (`2.958` non-utama dibuang) | Mode `gff_all` = `28.304` |
| Indeks BLAST `Asa_Ath.blast.gz` | `2.343.951` baris valid → **`47.736`** query unik, **`2.350`** *ties*, `0` invalid (`10,15–10,52 s` < `60 s`) | `Asa7G00205 → AT3G05510`, `Asa1G03447 → AT3G11440` |

---

## Konsekuensi
- **Positif:** File `.GO.gz` dan `.blast.gz` mentah dapat langsung diunggah dengan aman terhadap gzip bomb, format rusak, maupun CSV formula injection; job `COMPLETED` otomatis terbarui non-destruktif saat anotasi/ortolog/OBO berubah; dan hasil GO memakai background yang adil terhadap penyaringan kontig masing-masing job.
- **Di luar cakupan (dicatat untuk iterasi mendatang):** Pemindaian awal unggahan GFF (`upload.py`) tetap berhenti di baris `500.001`; endpoint `GET` hasil masih menulis file cache di storage sesuai pola historis; dan enrichment GO berbasis ortolog di semesta *Arabidopsis* (`Pipeline_Gwas.md`) tetap ditunda sesuai `Q-G5`.
