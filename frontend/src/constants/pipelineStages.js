/**
 * Definisi tunggal 10 tahap pipeline GWAS beserta label status dan utilitas formatBytes.
 */

export const PIPELINE_STAGES = [
  {
    order: 1,
    stepKey: "INSPECTION",
    logKey: "inspection",
    runningStatus: "RUNNING_INSPECTION",
    label: "Inspeksi Data",
    detail: "Rekonsiliasi sampel & fenotipe",
    runningText: "Tahap 1/10: Inspeksi Fenotipe & Sampel sedang berjalan",
    logLabel: "Inspeksi Fenotipe & Sampel",
    tool: "Python / Stats",
    badge: { bg: "#f0fdf4", color: "#15803d", label: "1. Inspeksi Data" },
    tabLabel: "1. Inspeksi Data",
    explorerLabel: "1. Inspeksi Data",
  },
  {
    order: 2,
    stepKey: "NORMALIZATION",
    logKey: "normalization",
    runningStatus: "RUNNING_NORMALIZATION",
    label: "Normalisasi VCF",
    detail: "Fase 0 (QC Overflow) & bcftools",
    runningText: "Tahap 2/10: Normalisasi VCF sedang berjalan",
    logLabel: "Normalisasi VCF",
    tool: "bcftools",
    badge: { bg: "#dcfce7", color: "#166534", label: "2. Normalisasi VCF" },
    tabLabel: "2. Normalisasi VCF",
    explorerLabel: "2. Normalisasi VCF",
  },
  {
    order: 3,
    stepKey: "QC",
    logKey: "qc",
    runningStatus: "RUNNING_QC",
    label: "Quality Control",
    detail: "Filter varian & sampel PLINK",
    runningText: "Quality Control sedang berjalan",
    logLabel: "Quality Control",
    tool: "PLINK 2 / 1.9",
    badge: { bg: "#dbeafe", color: "#1e40af", label: "3. Quality Control" },
    tabLabel: "3. QC (PLINK)",
    explorerLabel: "3. Quality Control",
  },
  {
    order: 4,
    stepKey: "IMPUTATION",
    logKey: "imputation",
    runningStatus: "RUNNING_IMPUTATION",
    label: "Imputasi Genotipe",
    detail: "Beagle 5.4 (Opsional)",
    runningText: "Tahap 4/10: Imputasi sedang berjalan",
    logLabel: "Imputasi Genotipe",
    tool: "Beagle 5.4",
    badge: { bg: "#fef3c7", color: "#92400e", label: "4. Imputasi" },
    tabLabel: "4. Imputasi",
    explorerLabel: "4. Imputasi",
  },
  {
    order: 5,
    stepKey: "STRUCTURE",
    logKey: "structure",
    runningStatus: "RUNNING_STRUCTURE",
    label: "Struktur & LD",
    detail: "PCA, Kinship & LD Decay",
    runningText: "Tahap 5/10: Analisis Struktur Populasi & LD sedang berjalan",
    logLabel: "Struktur Populasi & LD",
    tool: "PCA / Centered Kinship",
    badge: { bg: "#ede9fe", color: "#5b21b6", label: "5. Struktur & LD" },
    tabLabel: "5. Struktur & LD",
    explorerLabel: "5. Struktur & LD",
  },
  {
    order: 6,
    stepKey: "GWAS",
    logKey: "gwas",
    runningStatus: "RUNNING_GWAS",
    label: "Analisis GWAS",
    detail: "Uji asosiasi GLM / LMM",
    runningText: "Tahap 6/10: Analisis GWAS sedang berjalan",
    logLabel: "Analisis Asosiasi GWAS",
    tool: "GEMMA / GLM",
    badge: { bg: "#fae8ff", color: "#86198f", label: "6. Analisis GWAS" },
    tabLabel: "6. GWAS (GEMMA)",
    explorerLabel: "6. Analisis GWAS",
  },
  {
    order: 7,
    stepKey: "MULTILOCUS",
    logKey: "multilocus",
    runningStatus: "RUNNING_MULTILOCUS",
    label: "Multi-Locus GWAS",
    detail: "FarmCPU / BLINK / MLMM (Opsional)",
    runningText: "Tahap 7/10: Multi-Locus GWAS sedang berjalan",
    logLabel: "Multi-Locus GWAS",
    tool: "FarmCPU / BLINK / MLMM",
    badge: { bg: "#fce7f3", color: "#9d174d", label: "7. Multi-Locus" },
    tabLabel: "7. Multi-Locus",
    explorerLabel: "7. Multi-Locus",
  },
  {
    order: 8,
    stepKey: "POSTGWAS",
    logKey: "postgwas",
    runningStatus: "RUNNING_POSTGWAS",
    label: "Pasca-GWAS",
    detail: "Ambang batas & Clumping",
    runningText: "Tahap 8/10: Pasca-GWAS & Clumping sedang berjalan",
    logLabel: "Pasca-GWAS & Clumping",
    tool: "Dynamic Thresholds",
    badge: { bg: "#ffedd5", color: "#9a3412", label: "8. Pasca-GWAS" },
    tabLabel: "8. Pasca-GWAS",
    explorerLabel: "8. Pasca-GWAS",
  },
  {
    order: 9,
    stepKey: "VISUALIZATION",
    logKey: "visualization",
    runningStatus: "RUNNING_PARSING",
    label: "Visualisasi Plot",
    detail: "Cache Manhattan & QQ Plot",
    runningText: "Tahap 9/10: Menyiapkan visualisasi hasil",
    logLabel: "Visualisasi & Cache Plot",
    tool: "Parquet Engine",
    badge: { bg: "#cffafe", color: "#155e75", label: "9. Visualisasi" },
    tabLabel: "9. Visualisasi",
    explorerLabel: "9. Visualisasi",
  },
  {
    order: 10,
    stepKey: "ANNOTATION",
    logKey: "annotation",
    runningStatus: "RUNNING_ANNOTATION",
    label: "Anotasi Gen GFF3",
    detail: "Pemetaan gen & daftar GO",
    runningText: "Tahap 10/10: Anotasi Gen Kandidat GFF3 sedang berjalan",
    logLabel: "Anotasi Gen Kandidat",
    tool: "GFF3 Parser",
    badge: { bg: "#ecfccb", color: "#3f6212", label: "10. Anotasi Gen" },
    tabLabel: "10. Anotasi GFF3",
    explorerLabel: "10. Anotasi Gen",
  },
];

export const STEPS = PIPELINE_STAGES.map((stage) => ({
  key: stage.stepKey,
  logKey: stage.logKey,
  label: stage.label,
  detail: stage.detail,
}));

export const STATUS_LABELS = {
  PENDING: "Menyiapkan analisis",
  ...Object.fromEntries(PIPELINE_STAGES.map((stage) => [stage.runningStatus, stage.runningText])),
  RUNNING: "Analisis sedang berjalan",
  COMPLETED: "Analisis selesai (10/10 tahap)",
  SUCCESS: "Analisis selesai (10/10 tahap)",
  FAILED: "Analisis berhenti karena masalah",
  CANCELLED: "Analisis dibatalkan",
};

export const DEFAULT_STEPS = PIPELINE_STAGES.map((stage) => ({
  step: stage.logKey,
  label: stage.logLabel,
  tool: stage.tool,
  status: "pending",
  line_count: 0,
  log_text: "",
}));

export const STAGE_BADGES = {
  input: { bg: "#e0e7ff", color: "#3730a3", label: "Input Dataset" },
  ...Object.fromEntries(PIPELINE_STAGES.map((stage) => [stage.logKey, stage.badge])),
};

export const STAGE_TABS = [
  { key: "all", label: "Semua File" },
  { key: "input", label: "Input Dataset" },
  ...PIPELINE_STAGES.map((stage) => ({ key: stage.logKey, label: stage.tabLabel })),
];

export const JOB_STATUS_BADGE = {
  completed: { text: "Selesai (10/10)", bg: "#dcfce7", color: "#166534" },
  success: { text: "Selesai (10/10)", bg: "#dcfce7", color: "#166534" },
  failed: { text: "Gagal", bg: "#fee2e2", color: "#991b1b" },
  cancelled: { text: "Dihentikan", bg: "#fef3c7", color: "#92400e" },
  pending: { text: "Menunggu", bg: "#f1f5f9", color: "#475569" },
  running: { text: "Sedang Berjalan", bg: "#dbeafe", color: "#1d4ed8" },
  ...Object.fromEntries(
    PIPELINE_STAGES.map((stage) => [
      stage.runningStatus.toLowerCase(),
      { text: stage.explorerLabel, bg: "#dbeafe", color: "#1d4ed8" },
    ])
  ),
};

export function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
