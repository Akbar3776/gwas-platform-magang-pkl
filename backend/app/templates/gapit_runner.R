#!/usr/bin/env Rscript
# ==============================================================================
# GAPIT3 Runner for Multi-Locus GWAS: FarmCPU, BLINK, and MLMM
# ==============================================================================

args <- commandArgs(trailingOnly = TRUE)

parse_args <- function(args) {
  params <- list(
    y = NULL,
    gd = NULL,
    gm = NULL,
    cv = NULL,
    models = "FarmCPU,BLINK,MLMM",
    out = "multilocus/"
  )
  i <- 1
  while (i <= length(args)) {
    key <- args[i]
    if (startsWith(key, "--")) {
      name <- substring(key, 3)
      if (i + 1 <= length(args) && !startsWith(args[i + 1], "--")) {
        params[[name]] <- args[i + 1]
        i <- i + 2
        next
      }
    }
    i <- i + 1
  }
  return(params)
}

opt <- parse_args(args)

if (is.null(opt$y) || !file.exists(opt$y)) {
  stop(paste("Berkas fenotipe tidak ditemukan:", opt$y))
}

dir.create(opt$out, showWarnings = FALSE, recursive = TRUE)
setwd(opt$out)

cat(sprintf("=== Menjalankan GAPIT Multi-Locus GWAS ===\n"))
cat(sprintf("Model target   : %s\n", opt$models))
cat(sprintf("Direktori output: %s\n", opt$out))

# Verifikasi dependensi GAPIT
has_gapit <- suppressWarnings(require("GAPIT", quietly = TRUE))
if (!has_gapit) {
  if (file.exists("gapit_functions.txt")) {
    source("gapit_functions.txt")
  } else {
    stop("Pustaka GAPIT tidak terpasang di lingkungan R ini. Silakan pasang via devtools::install_github('jiabowang/GAPIT3').")
  }
}

# 1. Muat Fenotipe
myY <- read.table(opt$y, header = TRUE, stringsAsFactors = FALSE)
trait_name <- colnames(myY)[2]
cat(sprintf("Trait terdeteksi : %s (%d sampel)\n", trait_name, nrow(myY)))

# 2. Muat Genotipe (GD & GM) bila tersedia
myGD <- NULL
myGM <- NULL
if (!is.null(opt$gd) && file.exists(opt$gd)) {
  myGD <- read.table(opt$gd, header = TRUE, stringsAsFactors = FALSE)
}
if (!is.null(opt$gm) && file.exists(opt$gm)) {
  myGM <- read.table(opt$gm, header = TRUE, stringsAsFactors = FALSE)
}

# 3. Muat Kovariat bila ada
myCV <- NULL
if (!is.null(opt$cv) && file.exists(opt$cv)) {
  myCV <- read.table(opt$cv, header = TRUE, stringsAsFactors = FALSE)
}

models_to_run <- unlist(strsplit(opt$models, ","))

for (m in models_to_run) {
  model_clean <- trimws(m)
  if (model_clean == "") next
  cat(sprintf("\n[GAPIT] Memulai analisis model: %s ...\n", model_clean))

  tryCatch({
    GAPIT(
      Y = myY,
      GD = myGD,
      GM = myGM,
      CV = myCV,
      model = model_clean,
      memo = model_clean
    )
    cat(sprintf("[GAPIT] Model %s selesai dieksekusi.\n", model_clean))
  }, error = function(e) {
    cat(sprintf("[GAPIT ERROR] Gagal menjalankan model %s: %s\n", model_clean, e$message))
  })
}

cat("\n=== Eksekusi GAPIT Selesai ===\n")
