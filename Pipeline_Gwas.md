# Peta Alur Kerja

1. **Fase 1**: Inspect data dan cek konsistensi antar file
2. **Fase 2**: QC (quality control) VCF dan fenotipe
3. **Fase 3**: Koreksi struktur populasi (PCA / kinship)
4. **Fase 4**: Menjalankan GWAS
5. **Fase 5**: Memilih SNP signifikan dan menganotasinya ke gen
6. **Fase 6**: Mengambil sekuens flanking dan mengecek variasi di sekitarnya
7. **Fase 7**: Mendesain primer PCR dengan Primer3
8. **Fase 8**: Validasi spesifisitas primer dengan BLAST/Oligoanalyzer

# Prinsip pipeline

- **Inspect → Validate → Run.** Isi file dicek dulu sebelum mulai

---

# Fase 1: Inspect data dan cek konsistensi antar file

**1.1 Periksa file input**

1. Cek file Genotipe (genotipe.vcf.gz)

```jsx
# Pastikan nama sampel terbaca dengan benar
bcftools query -l genotipe.vcf.gz | head

# Hitung jumlah sampel
bcftools query -l genotipe.vcf.gz | wc -l

# Daftar nama sampel 
bcftools query -l genotipe.vcf.gz > sampel_vcf.txt
```

1. Periksa file fenotipe (`fenotipe.txt`)

```jsx
# Periksa struktur dan nama kolom
head -5 fenotipe.txt

# Hitung jumlah sampel
tail -n +2 fenotipe.txt | wc -l
```

- Periksa nilai fenotipe
- Periksa ID duplikat

**1.2 Menyesuaikan ID genotipe dan fenotipe**

```jsx
# 1. Baca ID sampel dari VCF
ids_vcf <- trimws(readLines("sampel_vcf.txt"))

# 2. Baca file fenotipe
fen <- read.table(
  "fenotipe.txt",
  header = TRUE,
  stringsAsFactors = FALSE,
  colClasses = "character",
  check.names = FALSE
)

# 3. Periksa struktur file fenotipe
print(head(fen))
print(names(fen))

# 4. Pastikan kolom identitas tersedia
stopifnot(all(c("FID", "IID") %in% names(fen)))

# 5. Rapikan spasi pada ID
fen$FID <- trimws(fen$FID)
fen$IID <- trimws(fen$IID)

# 6. Periksa ID kosong dan duplikat
cat("ID VCF kosong:",
    sum(is.na(ids_vcf) | ids_vcf == ""), "\n")

cat("IID fenotipe kosong:",
    sum(is.na(fen$IID) | fen$IID == ""), "\n")

cat("IID fenotipe duplikat:",
    sum(duplicated(fen$IID)), "\n")

cat("ID VCF duplikat:",
    sum(duplicated(ids_vcf)), "\n")

# 7. Periksa nilai fenotipe
# Asumsi kolom fenotipe adalah kolom ketiga
# Ambil kolom fenotipe
nilai_raw <- trimws(fen[[3]])

# Identifikasi nilai hilang
missing <- is.na(nilai_raw) |
  nilai_raw %in% c("", ".", "NA", "-9")

# Konversi ke numerik
nilai <- suppressWarnings(as.numeric(nilai_raw))

# Tandai nilai fenotipe yang valid
ada_nilai <- !missing & !is.na(nilai)

# 8. Periksa irisan ID berdasarkan IID
ids_fen_valid <- fen$IID[ada_nilai]

irisan <- intersect(ids_vcf, ids_fen_valid)

hanya_vcf <- setdiff(ids_vcf, fen$IID)
hanya_fen <- setdiff(fen$IID, ids_vcf)

# 9. Ringkasan hasil
cat("Jumlah sampel VCF:", length(ids_vcf), "\n")
cat("Jumlah sampel fenotipe:", nrow(fen), "\n")
cat("Jumlah baris dengan fenotipe valid:", sum(ada_nilai), "\n")
cat("Jumlah ID yang cocok dan memiliki fenotipe valid:",
    length(irisan), "\n")
cat("ID hanya di VCF:", length(hanya_vcf), "\n")
cat("ID hanya di fenotipe:", length(hanya_fen), "\n")

# 10. Simpan hasil
writeLines(irisan, "keep_ids.txt")
writeLines(hanya_vcf, "id_hanya_vcf.txt")
writeLines(hanya_fen, "id_hanya_fenotipe.txt")
```

**1.3 Cek Distribusi Fenotipe**

Gunakan file fenotipe asli, lalu ambil hanya individu yang ID-nya cocok dengan genotipe dan memiliki nilai fenotipe valid.

```jsx
# Baca data fenotipe
fen <- fread(
  "fenotipe.txt",
  na.strings = c("", ".", "NA")
)

# Baca daftar ID yang cocok dari tahap 1.1
keep_ids <- readLines("keep_ids.txt")

# Sesuaikan nama kolom dengan file asli
fen$IID <- trimws(fen$IID)

# Ambil individu dengan ID yang cocok
fen_analisis <- fen[IID %in% keep_ids]

# Konversi kolom fenotipe menjadi numerik
fen_analisis[[3]] <- suppressWarnings(
  as.numeric(fen_analisis[[3]])
)

# Hapus nilai fenotipe yang tidak valid
nilai <- fen_analisis[[3]]
fen_analisis <- fen_analisis[
  !is.na(nilai) & nilai != -9
]

# Periksa jumlah sampel akhir
cat("Jumlah sampel untuk eksplorasi fenotipe:",
    nrow(fen_analisis), "\n")
    
# Menghitung statistik deskriptif
nilai <- fen_analisis[[3]]

statistik <- data.table(
  N = sum(!is.na(nilai)),
  Mean = mean(nilai, na.rm = TRUE),
  Median = median(nilai, na.rm = TRUE),
  SD = sd(nilai, na.rm = TRUE),
  Minimum = min(nilai, na.rm = TRUE),
  Q1 = quantile(nilai, 0.25, na.rm = TRUE),
  Q3 = quantile(nilai, 0.75, na.rm = TRUE),
  Maximum = max(nilai, na.rm = TRUE)
)

print(statistik)

# Visualisasi Histogram
hist(
  nilai,
  main = "Distribusi Fenotipe",
  xlab = "Nilai Fenotipe",
  ylab = "Frekuensi",
  col = "lightblue",
  border = "white"
)

# Boxplot
boxplot(
  nilai,
  main = "Boxplot Fenotipe",
  ylab = "Nilai Fenotipe",
  col = "lightgreen"
)
```

---

# **Fase 2**: QC (quality control) VCF dan fenotipe

### 2.1 Parameter QC untuk Organisme Diploid

- **Inbred/Selfing//Klonal/Vegetatif**
    
    Pada populasi tanaman yang menyerbuk sendiri (*inbred* seperti *Arabidopsis*) maupun tanaman klonal/vegetatif (seperti bawang putih, kentang, atau ubi kayu), tingkat homozigositasnya sangat tinggi secara alami, atau heterozigositasnya terkunci tanpa segregasi Mendel.  **JANGAN pakai `--hwe` di PLINK2.** Mengaktifkan HWE akan membuang hampir seluruh genom secara salah.
    
    ```jsx
    # QC 1
    	./plink2 --vcf genotipe.vcf.gz --keep keep_ids.txt --snps-only just-acgt --max-alleles 2 --set-all-var-ids @:# --allow-extra-chr --pheno-name NAMA_TRAIT --maf 0.05 --geno 0.05 --make-bed  --memory 6000 --out qc_step1
    
    # QC lanjutan
    ./plink2 --bfile qc_step1 --allow-extra-chr --mind 0.05 --maf 0.05 --make-bed --out data_qc
    ```
    
- **Kawin silang/Outcross**
    
    ```jsx
    HWE
    ```
    

## **Catatan:**

- Cek hasil QC
    
    Bandingkan jumlah SNP & sampel sebelum dan sesudah. Kalau SNP yang tersisa turun drastis (>50%), curiga ada filter yang salah (biasanya HWE).
    
    ```jsx
    # Ringkasan sebelum QC
    ./plink2 --vcf genotipe.vcf.gz --allow-extra-chr --freq --missing --out qc/before_qc
    
    # Ringkasan setelah QC
    ./plink2 --bfile data_qc --allow-extra-chr --freq --missing --out qc/after_qc
    
    wc -l qc/data_qc.bim   # Menghitung jumlah SNP yang tersisa
    wc -l qc/data_qc.fam   # Menghitung jumlah sampel yang tersisa
    ```
    
- SNP banyak hilang?
    
    **Imputasi Data dengan Tools**: **Beagle 5.5**
    
    Jika ada terlalu banyak SNP yang hilang namun tetap ingin dipertahankan, proses imputasi data  perlu dilakukan sebelum mengulangi langkah QC ini.
    
    ```jsx
    # 1. Convert QC'd BED file to VCF for Beagle input 
    plink2 --bfile data_qc --recode vcf-fid --out snps_qc 
    # 2. Run Beagle Imputation 
    java -Xmx16g -jar beagle.jar gt=snps_qc.vcf out=snps_imputed 
    # 3. Convert imputed VCF back to PLINK BED format 
    plink2 --vcf snps_imputed.vcf.gz --make-bed --out org_snps_imputed
    ```
    
    **Kalau tidak mau? Boleh lanjut ke LD Pruning (Fase 3)**
    

---

# **Fase 3**: Koreksi struktur populasi (PCA / kinship)

Mengoreksi stratifikasi atau struktur populasi agar tidak terjadi *false positive* **saat uji GWAS.**

### 3.1 LD Pruning

Sebelum menghitung PCA dan Kinship, SNP yang berada dalam kondisi *Linkage Disequilibrium* (LD) yang tinggi harus di-*pruning* (dipangkas sementara). Hal ini dilakukan supaya SNP yang saling terkait tidak mendominasi dan mengaburkan sumbu variansi populasi yang sebenarnya.  

```jsx
# Melakukan LD pruning (SNP yang lolos akan masuk ke file .prune.in)
plink2 --bfile data_qc --allow-extra-chr --indep-pairwise 50 5 0.2 --out pca/ld_pruned
```

### 3.2 Menghitung PCA dan Matriks Kinship

Setelah mendapatkan daftar SNP yang independen (dari *LD pruning*), kita gunakan daftar tersebut untuk menghitung PCA dan matriks kekerabatan (*Kinship*).

```jsx
# Menghitung PCA (menghasilkan 10 PC utama)
plink2 --bfile qc/data_qc --allow-extra-chr --extract pca/ld_pruned.prune.in \
  --pca 10 --out pca/pca_results

# Menghitung matriks Kinship (berbentuk bujur sangkar / square matrix untuk model LMM)
gemma -bfile snps_imputed -gk 1 -out kinship
```

### 3.3 Visualisasi dan Evaluasi

#### 3.3.1 Visualisasi PCA

PCA dapat divisualisasikan melalui R dengan package `ggplot2` dapat membuat script sendiri atau dengan:

```r
library(ggplot2)
library(data.table)
library(dplyr)

# PEMBUATAN PLOT PCA

eigenval <- read.table("NB_khusus_pca.eigenval", header = FALSE)

pve <- eigenval$V1 / sum(eigenval$V1 * 100)
pc1_var <- round(pve[1], 2)
pc2_var <- round(pve[2], 2)

pca_data <- read.table("NB_khusus_pca.eigenvec", header = TRUE, stringsAsFactors = FALSE, comment.char = "")
colnames(pca_data)[1] <- "FID"

set.seed(42)
pca_data$Cluster <- as.factor(kmeans(pca_data[, c("PC1", "PC2")], centers = 3)$cluster)

ggplot(pca_data, aes(x = PC1, y = PC2, color = Cluster)) +
  geom_point(size = 3.5, alpha = 0.85) +
  scale_color_manual(values = c("#0072B2", "#E69F00", "#009E73", "#D55E00"),
                     labels = c("Subpopulation 1", "Subpopulation 2", 
                                "Subpopulation 3", "Subpopulation 4")) +
  labs(title = "Genomic Data: PCA",
       x = "PC1",
       y = "PC2",
       color = "Genetic Group") +
  theme_minimal() +
  theme(
    plot.title = element_text(size = 16, face = "bold", hjust = 0.5),
    plot.subtitle = element_text(size = 12, hjust = 0.5, color = "gray40"),
    axis.title = element_text(size = 13, face = "bold"),
    axis.text = element_text(size = 11),
    legend.position = "none",
    legend.title = element_text(face = "bold", size = 12),
    legend.text = element_text(size = 11),
    panel.grid.minor = element_blank(), 
    panel.border = element_rect(color = "black", fill = NA, size = 0.5)
  )
```

#### 3.3.2 Scree

```r
placeholder
```

#### 3.3.4 Kinship matrix heatmap in R using pheatmap package

```r
library(pheatmap) 

# 1. Import kinship matrixnya 
K <- as.matrix(read.table("output/rice_gwas_kinship.cK.txt")) 
fam <- read.table("rice_gwas_imputed.fam")

rownames(K) <- fam$V2
colnames(K) <- fam$V2

# 2. Buat warna yang bagus
colors <- colorRampPalette(c("#0d233a", "#ffffff", "#b2182b"))(100)

# 3. Plotting dan saving heatmap
pheatmap(K, 
color = colors, 
clustering_method = "ward.D2", 
show_rownames = FALSE, 
show_colnames = FALSE, 
main = "Genomic Relationship Matrix (Kinship - K)", 
filename = "kinship_heatmap.png", 
width = 7, 
height = 7)
```

## Catatan:

- Cek hasil PCA dan Kinship
    
    **A. Pengambilan Keputusan Berdasarkan PCA**
    
    | Hasil analisis | Interpretasi | Keputusan |
    | --- | --- | --- |
    | Scatter plot menunjukkan cluster yang jelas | Mengindikasikan adanya struktur populasi | Pertimbangkan PC sebagai kovariat GWAS untuk mengontrol struktur populasi. |
    | Terdapat satu atau beberapa titik yang terpisah jauh dari kelompok utama | Dapat mengindikasikan outlier, perbedaan genetik yang nyata, atau masalah data | Periksa identitas sampel dan kualitas genotipe sebelum menentukan apakah sampel perlu dikeluarkan. |
    | Scatter plot tidak menunjukkan cluster yang jelas | Struktur populasi belum terlihat jelas pada PC yang divisualisasikan | Evaluasi bersama scree plot dan informasi biologis; jangan langsung menyimpulkan bahwa struktur populasi tidak ada. |
    | Scree plot menunjukkan penurunan variansi yang kemudian melandai | PC awal menjelaskan variasi genetik lebih besar daripada PC berikutnya | Gunakan pola ini sebagai salah satu pertimbangan jumlah PC, lalu evaluasi kecukupannya dalam model GWAS. |
    
    B. Pengambilan Keputusan Berdasarkan Kinship
    
    | Hasil analisis kinship | Interpretasi | Keputusan untuk GWAS |
    | --- | --- | --- |
    | Terlihat kelompok sampel dengan nilai kinship relatif tinggi | Mengindikasikan adanya kelompok sampel yang berkerabat lebih dekat | Pertimbangkan penggunaan mixed model dengan matriks kekerabatan yang kompatibel untuk mengontrol efek kekerabatan. |
    | Nilai kinship antarsampel umumnya rendah dan tidak menunjukkan pola kekerabatan yang kuat | Bukti kekerabatan dekat mungkin terbatas pada dataset | Evaluasi kebutuhan model kekerabatan bersama struktur populasi dan desain penelitian. |
    | Hanya terdapat beberapa pasangan sampel dengan kinship tinggi | Kekerabatan dekat mungkin terkonsentrasi pada pasangan atau kelompok tertentu | Periksa pasangan sampel tersebut dan tentukan apakah kekerabatan perlu dimodelkan dalam GWAS. |
    | Teridentifikasi pasangan sampel dengan kinship sangat tinggi, mendekati duplikat genetik | Dapat mengindikasikan sampel duplikat atau individu yang sangat berkerabat | Periksa identitas sampel dan data genotipe sebelum memutuskan penanganannya. |
    | Matriks kinship tidak menunjukkan pola yang mudah diinterpretasikan | Pola kekerabatan belum dapat disimpulkan hanya dari visualisasi | Periksa distribusi koefisien kinship, identitas sampel, dan karakteristik dataset sebelum memilih model. |
    
    Catatan: nilai kinship harus ditafsirkan sesuai estimator yang digunakan. Tabel ini menggunakan matriks KING dari PLINK 2; nilai KING tidak boleh langsung dianggap setara dengan nilai dari semua metode estimasi kekerabatan lainnya.
    
    C. Keputusan Akhir Pemilihan Model GWAS
    
    | Hasil PCA | Hasil kinship | Pendekatan yang dipertimbangkan |
    | --- | --- | --- |
    | Struktur populasi terdeteksi | Kekerabatan antarsampel juga terindikasi | Pertimbangkan mixed model yang mengakomodasi matriks kekerabatan dan PC sebagai kovariat, jika didukung perangkat lunak. |
    | Struktur populasi terdeteksi | Bukti kekerabatan dekat terbatas | Pertimbangkan model dengan PC sebagai kovariat. |
    | Struktur populasi tidak terlihat jelas | Kekerabatan antarsampel terindikasi | Pertimbangkan mixed model dengan matriks kekerabatan yang sesuai. |
    | Struktur populasi dan kekerabatan dekat tidak terindikasi kuat | Keduanya belum menunjukkan kebutuhan yang jelas | Evaluasi model yang lebih sederhana sebagai pembanding; jangan mengasumsikan bahwa kontrol keduanya tidak diperlukan tanpa pemeriksaan lanjutan. |

**Lanjut Fase 4**

---

# **Fase 4**: Menjalankan GWAS dan Evaluasinya

- Berdasarkan cara menguji SNP
    - **Single-locus GWAS**
        
        Satu SNP per pengujian. Menguji setiap SNP satu per satu terhadap fenotipe. Misalnya SNP 1 diuji, lalu SNP 2, kemudian SNP 3, dan seterusnya. Rentan *spurious association* akibat *LD ghosting*. Contoh: PLINK 2 `--glm`, GEMMA LMM, atau pendekatan single-marker lain.
        
    - **Multilocus GWAS**
        
        Banyak SNP dalam model. Metode tertentu memasukkan beberapa SNP sebagai kofaktor atau komponen model untuk membantu mengidentifikasi asosiasi yang mungkin terlewat atau kurang terpisah pada analisis single-locus. Contoh: FarmCPU dan BLINK yang tersedia melalui GAPIT.
        
    
    Catatan: single-locus tidak selalu berarti model sederhana tanpa koreksi kekerabatan. Single-locus juga bisa menggunakan mixed model.
    
- Berdasarkan cara mengontrol kekerabatan
    - **Model umum (GLM)**
        
        Menguji hubungan SNP dan fenotipe, dengan kovariat seperti PC jika diperlukan.
        
    - **Mixed model (LMM)**
        
        Menguji hubungan SNP dan fenotipe sambil memodelkan struktur kekerabatan melalui matriks hubungan genetik, serta dapat memasukkan PC sebagai kovariat.
        

### 4A. Jalur **Single-Locus** General (GLM)

Hanya ketika jika tidak ada kekerabatan kuat antar sampel (misalnya pada populasi kawin acak tanpa struktur famili yang jelas).

- **Model:** General Linear Model (GLM).
- **Kovariat:** Hanya menggunakan PC (tanpa matriks *kinship*).

```bash
plink2 --bfile input_file --pheno pheno.txt --covar hasil_pca.eigenvec --glm hide-covar --allow-extra-chr --out gwas_glm

```

### 4B. Jalur **Single-Locus** Mixed Model (LMM)

Ketika data memiliki **kekerabatan kuat** (klonal, *inbred*, atau ada struktur famili).

- **Model:** Mixed Model (LMM).
- **Kovariat:** Wajib menggunakan PC ditambah dengan matriks *Kinship*.
- **Tools:**  Gunakan GEMMA (`--lmm`), GCTA (`mlma`), atau GAPIT (model MLM).

```jsx
gemma -bfile input_file_imputed -k output/rice_kinship.cK.txt -pheno pheno_formatted.txt -cov top_PCs.eigenvec -lmm 4 -out gwas_lmm_results
```

#### **Evaluasi Single-Locus (4A & 4B):** hitung nilai Lambda dan *****QQ-plot*

```r
# Definisikan p_value
p_vals <- gwas$p_wald 
p_vals <- p_vals[!is.na(p_vals) & p_vals > 0] 

# 2. Calculate Lambda 
chisq <- qchisq(1 - p_vals, df = 1) 
lambda <- median(chisq) / qchisq(0.5, df = 1) 

cat("Genomic Inflation Factor (Lambda):", round(lambda, 4), "\n") 

# 3. Prepare Expected vs. Observed -log10(P) values 
n <- length(p_vals) 
observed <- -log10(sort(p_vals)) 
expected <- -log10(ppoints(n))

library(CMplot) 
# Read GWAS summary statistics (columns: SNP, Chromosome, Position, P-value) 

gwas <- read.table("output/rice_gwas_lmm_results.assoc.txt", header = TRUE) 

# Generate Q-Q Plot
CMplot(gwas[, c("rs", "chr", "ps", "p_wald")], 
	plot.type = "q",
	box = TRUE, 
	multracks = FALSE, 
	threshold = 0.05 / nrow(gwas), # Bonferroni line threshold.
	col = "red", 
	file = "png", 
	file.name = "gwas_cmplot", 
	dpi = 300)
```

### 4C. Jalur **Multi-Locus** (FarmCPU & BLINK di GAPIT3)

Direkomendasikan sebagai **hasil utama** untuk data *inbred/klonal*, serta sensitif untuk menangkap *minor-effect loci* yang tertutup di model *Single-Locus*.

```jsx

```

### 4D. Penentuan LD Decay

LD Decay (peluruhan *Linkage Disequilibrium*) adalah sifat populasi dan genom yang harus dihitung satu kali dari data QC (`bfile`). Hasil dari LD Decay ini akan menentukan dua hal:

1. Batas jarak fisik (… kb) untuk parameter *clumping* di hasil *Single-Locus*.   
2. Jarak rentang interval (… kb) untuk mencari gen kandidat di Fase 5 (berlaku untuk hasil *Single-Locus* maupun *Multi-Locus*).

```jsx
./plink2 \
  --bfile qc/data_qc \
  --r2-unphased \
  --ld-window 99999 \
  --ld-window-kb 1000 \
  --ld-window-r2 0 \
  --out gwas/ld_decay_allchr
```

```jsx
# visualisasi titik potong di R

library(data.table)
library(ggplot2)

# Baca hasil dari PLINK2 (sesuaikan nama file output, biasanya .vcor atau .ld)
ld_data <- fread("gwas/ld_decay_allchr.vcor")

# Hitung jarak fisik antar SNP dalam satuan kb
ld_data[, Distance_kb := abs(POS_B - POS_A) / 1000]

# Binning: Meringkas titik menjadi rata-rata per blok 10 kb
ld_data[, Bin := cut(Distance_kb, breaks = seq(0, 1000, by = 10), labels = FALSE) * 10]
ld_summary <- ld_data[, .(Mean_R2 = mean(UNPHASED_R2, na.rm = TRUE)), by = Bin]
setorder(ld_summary, Bin)

# Mencari Titik Potong (Batas r2 < 0.2)
titik_potong <- ld_summary[Mean_R2 < 0.2, min(Bin)]
print(paste("Titik potong LD Decay jatuh pada jarak:", titik_potong, "kb"))

# Visualisasi Plot
ggplot(ld_summary, aes(x = Bin, y = Mean_R2)) +
  geom_line(color = "#2E86C1", linewidth = 1.2) +
  geom_point(color = "orange3", size = 2.5) +
  geom_hline(yintercept = 0.2, linetype = "dashed", color="red", linewidth = 1) +
  geom_vline(xintercept = titik_potong, linetype = "dashed", color = "black", linewidth = 1) +
  theme_minimal() +
  labs(title = "Kurva LD Decay", x = "Jarak Fisik antar SNP (kb)", y = "Rata-rata R2")
```

**Kalau LD decay tidak tembus 0.2** (`titik_potong` bernilai Inf, umum di klonal/inbred, genom besar, atau sampel sedikit): jangan dipaksakan ke 0.2. Pilih salah satu opsi di bawah dan laporkan apa adanya, misalnya "r2 terendah 0.35 di 1 Mb, interval ditetapkan ±100 kb …".

Titik potong akan dipakai di Fase 4F dan Fase 5.

Multi-Locus langsung ke 4E lalu ke 4FG

### 4E. Menentukan Threshold &

```jsx
# Baca hasil GWAS Single-Locus (sesuaikan nama file dengan output PLINK2)
gwas_res <- read.table("gwas/hasil_gwas.PHENO1.glm.linear", header = TRUE, comment.char = "")

# Ambil kolom P-value dan buang nilai NA
p_values <- gwas_res$P[!is.na(gwas_res$P)] 
total_snps <- length(p_values)

# --- A. Menghitung Threshold Bonferroni (Ketat) ---
# Rumus: alpha (0.05) dibagi jumlah SNP yang diuji
bonferroni_thresh <- 0.05 / total_snps
print(paste("Threshold Bonferroni:", bonferroni_thresh))

# --- B. Menghitung Threshold FDR / False Discovery Rate (Moderat) ---
# Mencari batas p-value tertinggi di mana nilai FDR (q-value) masih di bawah 0.05
fdr_adjusted <- p.adjust(p_values, method = "fdr")
fdr_thresh <- max(p_values[fdr_adjusted < 0.05], na.rm = TRUE)

if(is.infinite(fdr_thresh) | is.na(fdr_thresh)) {
  print("Threshold FDR: Tidak ada SNP yang lolos FDR < 0.05")
} else {
  print(paste("Threshold FDR:", fdr_thresh))
}

# --- C. Konversi LOD ke P-value (Khusus inbred seperti Arabidopsis) ---
# Untuk Arabidopsis, batas LOD yang disarankan adalah 5.6 hingga 6.6
lod_score <- 5.6
p_lod <- 10^(-lod_score)
print(paste("Threshold LOD", lod_score, "setara dengan p-value:", p_lod))
```

### 4F. Clumping (Khusus Single-Locus)

```jsx
./plink2 --bfile qc/data_qc --allow-extra-chr \
  --clump gwas/hasil_gwas.PHENO1.glm.linear \
  --clump-p1 2.5e-6 \
  --clump-p2 1e-4 \
  --clump-r2 0.2 \
  --clump-kb 50 \
  --out gwas/clumped
```

**Panduan Parameter:**

- `-clump-p1`: Isi dengan nilai *threshold* signifikan dari Fase 4E.
- `-clump-p2`: Batas p-value longgar, SNP di bawah nilai ini akan digabung ke *Lead SNP*.
- `-clump-kb`: Isi dengan jarak *titik potong* LD Decay dari Fase 4D (misal: 50 atau 100).

### 4G. Manhattan Plot (Script nyambung sama 4E)

ini belom kelar

```jsx
# Single-Locus
library(CMplot)

# 1. Siapkan data output PLINK2 agar terbaca oleh CMplot
gwas_res <- read.table("gwas/hasil_gwas.PHENO1.glm.linear", header = TRUE, comment.char = "", check.names = FALSE)

# Buat dataframe baru dengan 4 kolom wajib: SNP, Chromosome, Position, P-value
plot_df <- data.frame(SNP = gwas_res$ID, 
                      Chr = gwas_res[["#CHROM"]], 
                      Pos = gwas_res$POS, 
                      P = gwas_res$P)
plot_df <- plot_df[!is.na(plot_df$P), ] # Buang data kosong (NA)

# 2. TENTUKAN THRESHOLD YANG AKAN DIPAKAI DI SINI
# Pilihan: bonferroni_thresh / fdr_thresh / p_lod
batas_signifikan <- bonferroni_thresh  

# 3. Eksekusi Gambar Manhattan Plot
CMplot(plot_df,
       type = "p", 
       plot.type = "m",
       # Kita buat dua garis batas: suggestive (biru, opsional di 1e-5) dan signifikan (merah)
       threshold = c(1e-5, batas_signifikan),  
       threshold.col = c("blue", "red"),
       amplify = TRUE,      # Perbesar titik yang melewati batas merah
       chr.den.col = c("darkblue", "darkgreen", "yellow"), # Gradasi warna kepadatan gen
       file = "png",        # Format output gambar
       file.name = "Manhattan_Single_Locus", 
       dpi = 300)
```

### 4h. Membandingkan Puncak GWAS

Mengekstrak dan membandingkan daftar *Lead SNP* dari *Single-Locus* (setelah clumping) dengan daftar dari *Multi-Locus*.

```jsx
# --- 1. Membaca Hasil Single-Locus (Setelah Clumping) ---
# Ganti nama file dengan output dari Fase 4F
single_res <- read.table("gwas/clumped.clumped", header = TRUE, stringsAsFactors = FALSE)
snp_single <- single_res$SNP  

# --- 2. Membaca Hasil Multi-Locus (GAPIT) ---
# Ganti dengan nama file .csv output dari GAPIT (FarmCPU atau BLINK)
multi_res <- read.csv("GAPIT.Association.GWAS_Results.FarmCPU.PHENO1.csv", header = TRUE, stringsAsFactors = FALSE)

# Filter SNP yang signifikan di Multi-Locus (menggunakan threshold Bonferroni otomatis)
threshold_multi <- 0.05 / nrow(multi_res)
snp_multi <- multi_res$SNP[multi_res$P.value < threshold_multi]

# --- 3. Membandingkan Puncak (Mencari Irisan) ---
konsisten <- intersect(snp_single, snp_multi)
hanya_multi <- setdiff(snp_multi, snp_single)
hanya_single <- setdiff(snp_single, snp_multi)

# --- 4. Menyusun Data Frame untuk Diekspor ---
# Jika ada SNP di kategori tersebut, masukkan ke dalam tabel
df_list <- list()

if(length(konsisten) > 0) {
  df_list[[1]] <- data.frame(SNP = konsisten, Status = "Kandidat Kuat (Konsisten)")
}
if(length(hanya_multi) > 0) {
  df_list[[2]] <- data.frame(SNP = hanya_multi, Status = "Minor-Effect (Hanya Multi-Locus)")
}
if(length(hanya_single) > 0) {
  df_list[[3]] <- data.frame(SNP = hanya_single, Status = "False Positive (Hanya Single-Locus)")
}

# Menggabungkan semua kategori menjadi satu tabel utuh
tabel_hasil <- do.call(rbind, df_list)

# --- 5. Ekspor ke File CSV ---
# Menyimpan tabel ke dalam file CSV yang siap dibuka di Excel
write.csv(tabel_hasil, "gwas/Ringkasan_Puncak_GWAS.csv", row.names = FALSE, quote = FALSE)

# Menampilkan ringkasan di layar
cat("=== RINGKASAN EVALUASI PUNCAK GWAS ===\n")
cat("KANDIDAT KUAT (Konsisten)          :", length(konsisten), "SNP\n")
cat("Potensi Minor-Effect (Hanya Multi) :", length(hanya_multi), "SNP\n")
cat("Dicurigai False Positive (Hanya Single) :", length(hanya_single), "SNP\n")
cat("--------------------------------------\n")
cat("Tabel hasil telah berhasil disimpan sebagai 'Ringkasan_Puncak_GWAS.csv' di folder gwas!\n")
```

| Perbandingan | Single-Locus | Multi-Locus |
| --- | --- | --- |
| Kecepatan | Cepat | Lebih lambat |
| False positive (LD ghosting) | Lebih rentan | Lebih terkontrol |
| Minor-effect loci | Sering terlewat | Lebih sensitif |
| Threshold | Ketat (Bonferroni/FDR) | Moderat (LOD ≥ 3.0) |
| Saran | Screening awal, pembanding | Hasil utama untuk inbred/klonal |

---

## **Fase 5**: Memilih SNP signifikan dan menganotasinya ke gen

**Sebelum:** daftar lead SNP (dari clumping atau hasil Multi-Locus) + file anotasi GFF3 genom referensi.
**Output:** daftar gen kandidat + hasil GO enrichment.

## 5.1 Ambil gen di sekitar lead SNP (Yang di atas treshold)

Interval gen mengikuti LD decay.

```jsx
# --- 1. Persiapan Library ---
library(rtracklayer)
library(GenomicRanges)
library(data.table)

# --- 2. Membaca Data SNP Kandidat Kuat ---
hasil_gwas <- read.csv("gwas/Ringkasan_Puncak_GWAS.csv")
kandidat_kuat <- subset(hasil_gwas, Status == "Kandidat Kuat (Konsisten)")$SNP

# Baca posisi fisik SNP dari file .bim (hasil QC)
bim <- fread("qc/data_qc.bim")
colnames(bim) <- c("Chr", "SNP", "cM", "Pos", "A1", "A2")
info_snp <- bim[bim$SNP %in% kandidat_kuat, ]

# Definisikan koordinat SNP 
snp_ranges <- GRanges(
  seqnames = info_snp$Chr,
  ranges = IRanges(start = info_snp$Pos, end = info_snp$Pos),
  SNP_ID = info_snp$SNP
)

# --- 3. Membaca Genom Referensi (GFF3) ---
cat("Membaca GFF3...\n")
gff <- import("annotation.gff3")
genes <- gff[gff$type == "gene"]

# --- 4. Ekstraksi Gen Terdekat dan Hitung Jarak ---
# Mencari gen terdekat untuk setiap SNP
nearest_info <- distanceToNearest(snp_ranges, genes)

# Menyusun hasil ekstraksi mentah ke dalam dataframe
ekstraksi_df <- data.frame(
  SNP = snp_ranges$SNP_ID,
  Kromosom = seqnames(snp_ranges),
  Posisi_SNP = start(snp_ranges),
  Gen_Terdekat = genes$ID[subjectHits(nearest_info)],
  Jarak_bp = mcols(nearest_info)$distance
)

# Simpan hasil ekstraksi sementara
write.csv(ekstraksi_df, "gwas/Ekstraksi_Gen_Mentah.csv", row.names = FALSE)
cat("Ekstraksi selesai! Lanjut ke tahap Anotasi.\n")
```

## 5.2 Anotasi

- **Genik:** SNP berada tepat di dalam urutan gen.
- **Near-gen:** SNP berada di luar gen, namun masih masuk dalam jangkauan jarak LD Decay.
- **Intergenik:** SNP berada jauh di luar area gen dan melampaui batas LD Decay.

```jsx
# --- 1. Masukkan Nilai LD Decay ---
# Ganti dengan angka LD decay populasi Anda (dalam bp). Contoh: 50 kb = 50000.
jarak_ld <- 50000  

# --- 2. Anotasi Kategori berdasarkan Jarak ---
ekstraksi_df$Kategori_Lokasi <- ifelse(ekstraksi_df$Jarak_bp == 0, "Genik",
                                ifelse(ekstraksi_df$Jarak_bp <= jarak_ld, "Near-gen", "Intergenik"))

# --- 3. Simpan Hasil Anotasi Akhir ---
write.csv(ekstraksi_df, "gwas/Anotasi_Kategori_SNP.csv", row.names = FALSE, quote = FALSE)

# Tampilkan ringkasan klasifikasi
cat("=== RINGKASAN ANOTASI LOKASI SNP ===\n")
print(table(ekstraksi_df$Kategori_Lokasi))

# --- 4. Ekspor Daftar Gen untuk Uji Lanjutan (GO Enrichment) ---
# Hanya genik dan near-gen yang relevan secara biologi untuk dianalisis fungsinya
gen_valid <- subset(ekstraksi_df, Kategori_Lokasi %in% c("Genik", "Near-gen"))$Gen_Terdekat
write.table(unique(gen_valid), "gwas/Daftar_Gen_Untuk_GO.txt", row.names = FALSE, col.names = FALSE, quote = FALSE)
```