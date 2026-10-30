import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { triggerFullPipeline } from "../api/jobsApi";
import { JobFilesPanel } from "../components/JobFilesPanel";
import { JobStatusTracker } from "../components/JobStatusTracker";
import { VcfUploader } from "../components/VcfUploader";
import { WorkspaceExplorer } from "../components/WorkspaceExplorer";
import { PhenotypeUploader } from "../components/PhenotypeUploader";
import { AnnotationUploader } from "../components/AnnotationUploader";
import { DataInspectionPanel } from "../components/DataInspectionPanel";

// Path reference panel adalah path internal worker Docker, bukan path komputer user.
const REFERENCE_PANELS = [
  {
    value: "/data/reference-panels/1000g_phase3_grch38/reference.vcf.gz",
    label: "1000 Genomes Phase 3 - GRCh38",
  },
  {
    value: "/data/reference-panels/1000g_phase3_grch37/reference.vcf.gz",
    label: "1000 Genomes Phase 3 - GRCh37",
  },
];

const INITIAL_FORM = {
  // QC
  population_type: "inbred",
  maf: "0.05",
  geno: "0.05",
  mind: "0.05",
  hwe: "0.000001",
  snps_only: true,
  set_missing_var_ids: true,
  // Imputasi
  imputation_mode: "none",
  reference_vcf_path: REFERENCE_PANELS[0].value,
  nthreads: "2",
  beagle_max_memory: "-Xmx8g",
  // Struktur Populasi & LD Decay
  prune_window: "50",
  prune_step: "5",
  prune_r2: "0.2",
  n_pcs: "10",
  ld_decay_window_kb: "1000",
  ld_decay_r2_cutoff: "0.2",
  // GWAS & Covariates
  model: "auto",
  n_pc_covariates: "3",
  enable_multilocus: false,
  // Threshold & Clumping
  threshold_method: "bonferroni",
  threshold_alpha: "0.05",
  threshold_lod: "5.6",
  clump_r2: "0.2",
  clump_p2: "0.0001",
  // Anotasi
  annotation_enabled: true,
};

function Field({ label, hint, children }) {
  return (
    <label style={{ display: "grid", gap: 6, color: "#334155", fontSize: 13 }}>
      <span style={{ fontWeight: 700 }}>{label}</span>
      {children}
      {hint && <span style={{ color: "#64748b", fontSize: 11 }}>{hint}</span>}
    </label>
  );
}

const inputStyle = {
  width: "100%",
  boxSizing: "border-box",
  border: "1px solid #cbd5e1",
  borderRadius: 7,
  padding: "9px 10px",
  color: "#0f172a",
  background: "#fff",
};

export function GwasUploadPage() {
  const navigate = useNavigate();

  const [datasetId, setDatasetId] = useState(null);
  const [datasetMeta, setDatasetMeta] = useState(null);
  const [jobId, setJobId] = useState(null);
  const [autoRedirectOnComplete, setAutoRedirectOnComplete] = useState(true);
  const [refreshCount, setRefreshCount] = useState(0);
  const [form, setForm] = useState(INITIAL_FORM);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // Phenotype & Inspection states
  const [inspectionReport, setInspectionReport] = useState(null);
  const [selectedTrait, setSelectedTrait] = useState("");
  const [selectedIdCol, setSelectedIdCol] = useState("IID");
  const [hasAnnotation, setHasAnnotation] = useState(false);

  const hasRedirectedRef = useRef(false);

  const handleJobComplete = useCallback(() => {
    setRefreshCount((c) => c + 1);
    if (!autoRedirectOnComplete || hasRedirectedRef.current || !jobId) return;
    hasRedirectedRef.current = true;
    setTimeout(() => {
      navigate(`/jobs/${jobId}/results`);
    }, 1500);
  }, [navigate, jobId, autoRedirectOnComplete]);

  const handleDatasetReady = useCallback((readyDatasetId, meta) => {
    hasRedirectedRef.current = false;
    setDatasetId(readyDatasetId);
    setDatasetMeta(meta || null);
    setJobId(null);
    setError(null);
    setInspectionReport(null);
    setSelectedTrait("");
    setRefreshCount((c) => c + 1);
  }, []);

  const handleSelectExistingDataset = useCallback((selectedDatasetId, selectedMeta) => {
    hasRedirectedRef.current = false;
    setDatasetId(selectedDatasetId);
    setDatasetMeta(selectedMeta || null);
    setJobId(null);
    setError(null);
    setInspectionReport(null);
    setSelectedTrait("");
  }, []);

  const handleDeleteDataset = useCallback(
    (deletedDatasetId) => {
      if (datasetId === deletedDatasetId) {
        setDatasetId(null);
        setDatasetMeta(null);
        setJobId(null);
        setInspectionReport(null);
      }
      setRefreshCount((c) => c + 1);
    },
    [datasetId]
  );

  const handleSelectExistingJob = useCallback((selectedJobId) => {
    setAutoRedirectOnComplete(false);
    hasRedirectedRef.current = true;
    setJobId(selectedJobId);
  }, []);

  const handleDeleteJob = useCallback(
    (deletedJobId) => {
      if (jobId === deletedJobId) {
        setJobId(null);
      }
      setRefreshCount((c) => c + 1);
    },
    [jobId]
  );

  const handleInspectionReady = useCallback((report, trait, idCol) => {
    setInspectionReport(report);
    setSelectedTrait(trait);
    setSelectedIdCol(idCol);
  }, []);

  const handlePhenotypeCleared = useCallback(() => {
    setInspectionReport(null);
    setSelectedTrait("");
  }, []);

  const updateField = (event) => {
    const { name, value, type, checked } = event.target;
    setForm((current) => ({
      ...current,
      [name]: type === "checkbox" ? checked : value,
    }));
  };

  const hasBlockingErrors =
    !inspectionReport ||
    Boolean(inspectionReport?.blocking_errors && inspectionReport.blocking_errors.length > 0) ||
    !selectedTrait;

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!datasetId) return;

    if (!inspectionReport) {
      setError("Silakan unggah dan selesaikan inspeksi data fenotipe sebelum memulai analisis.");
      return;
    }

    if (inspectionReport.blocking_errors && inspectionReport.blocking_errors.length > 0) {
      setError(`Tidak dapat melanjutkan karena terdapat error pada fenotipe: ${inspectionReport.blocking_errors.join(", ")}`);
      return;
    }

    setError(null);
    setIsSubmitting(true);
    try {
      const payload = {
        phenotype: {
          trait: selectedTrait,
          id_column: selectedIdCol,
        },
        qc: {
          population_type: form.population_type,
          maf: Number(form.maf),
          geno: Number(form.geno),
          mind: Number(form.mind),
          hwe: form.population_type === "outcross" ? Number(form.hwe) : null,
          snps_only: Boolean(form.snps_only),
          max_alleles: 2,
          set_missing_var_ids: Boolean(form.set_missing_var_ids),
        },
        imputation: {
          mode: form.imputation_mode,
          reference_vcf_path: form.imputation_mode === "reference" ? form.reference_vcf_path : null,
          nthreads: Number(form.nthreads),
          beagle_max_memory: form.beagle_max_memory,
        },
        structure: {
          prune_window: Number(form.prune_window),
          prune_step: Number(form.prune_step),
          prune_r2: Number(form.prune_r2),
          n_pcs: Number(form.n_pcs),
          ld_decay_window_kb: Number(form.ld_decay_window_kb),
          ld_decay_bin_kb: 10,
          ld_decay_r2_cutoff: Number(form.ld_decay_r2_cutoff),
        },
        gwas: {
          model: form.model,
          n_pc_covariates: Number(form.n_pc_covariates),
          enable_multilocus: Boolean(form.enable_multilocus),
          multilocus_methods: ["FarmCPU", "BLINK"],
        },
        threshold: {
          method: form.threshold_method,
          alpha: Number(form.threshold_alpha),
          lod_score: Number(form.threshold_lod),
        },
        clump: {
          r2: Number(form.clump_r2),
          p2: Number(form.clump_p2),
        },
        annotation: {
          enabled: Boolean(form.annotation_enabled && hasAnnotation),
          feature_type: "gene",
        },
      };

      const response = await triggerFullPipeline(datasetId, payload);
      hasRedirectedRef.current = false;
      setAutoRedirectOnComplete(true);
      setJobId(response.job_id);
      setRefreshCount((c) => c + 1);
    } catch (requestError) {
      setError(
        requestError.response?.data?.detail ||
          requestError.message ||
          "Pipeline tidak dapat dimulai. Periksa konfigurasi Anda."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "28px 20px", color: "#0f172a" }}>
      <header style={{ marginBottom: 24 }}>
        <p style={{ margin: 0, color: "#2563eb", fontSize: 12, fontWeight: 800, letterSpacing: "0.08em" }}>
          GWAS WORKSPACE — FULL PIPELINE
        </p>
        <h1 style={{ margin: "6px 0", fontSize: 28 }}>Analisis Asosiasi Genom (GWAS)</h1>
        <p style={{ margin: 0, color: "#64748b" }}>
          Pipeline terintegrasi dari inspeksi data, QC, struktur populasi, GWAS GLM/LMM, post-GWAS, clumping, hingga anotasi gen kandidat GFF3.
        </p>
      </header>

      <section style={{ display: "grid", gap: 20 }}>
        {/* Tahap 1: Dataset Genotipe */}
        <div style={{ padding: 18, border: "1px solid #e2e8f0", borderRadius: 12, background: "#fff" }}>
          <h2 style={{ margin: "0 0 12px", fontSize: 17 }}>1. Unggah Data Genotipe Baru (VCF)</h2>
          <VcfUploader onDatasetReady={handleDatasetReady} />
          {datasetId && (
            <p style={{ margin: "12px 0 0", color: "#15803d", fontSize: 13, fontWeight: 600 }}>
              Dataset terpilih siap diproses (ID: {datasetId}).
            </p>
          )}
        </div>

        {/* Workspace Explorer */}
        <WorkspaceExplorer
          activeDatasetId={datasetId}
          activeJobId={jobId}
          onSelectDataset={handleSelectExistingDataset}
          onDeleteDataset={handleDeleteDataset}
          onSelectJob={handleSelectExistingJob}
          onDeleteJob={handleDeleteJob}
          refreshTrigger={refreshCount}
        />

        {/* Tahap 2: Fenotipe & Anotasi */}
        {datasetId && !jobId && (
          <div style={{ display: "grid", gap: 18, padding: 18, border: "1px solid #e2e8f0", borderRadius: 12, background: "#fff" }}>
            <h2 style={{ margin: 0, fontSize: 17 }}>2. Fenotipe & Anotasi Genom</h2>
            <p style={{ margin: 0, color: "#64748b", fontSize: 13 }}>
              Unggah file fenotipe (.txt/.tsv/.csv) untuk analisis asosiasi dan file anotasi referensi (.gff3 opsional) untuk pemetaan gen kandidat.
            </p>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 }}>
              <PhenotypeUploader
                datasetId={datasetId}
                initialPhenotypeFilename={datasetMeta?.phenotype_filename}
                initialColumns={datasetMeta?.phenotype_columns || []}
                initialNumericTraits={datasetMeta?.numeric_traits || []}
                onInspectionReady={handleInspectionReady}
                onPhenotypeCleared={handlePhenotypeCleared}
              />
              <AnnotationUploader
                datasetId={datasetId}
                initialAnnotationFilename={datasetMeta?.annotation_filename}
                onAnnotationChanged={(meta) => setHasAnnotation(Boolean(meta))}
              />
            </div>

            {/* Panel Hasil Inspeksi */}
            {inspectionReport && (
              <DataInspectionPanel report={inspectionReport} fileId={datasetId} />
            )}
          </div>
        )}

        {/* Tahap 3: Konfigurasi Pipeline Lengkap */}
        {datasetId && !jobId && (
          <form
            onSubmit={handleSubmit}
            style={{
              display: "grid",
              gap: 20,
              padding: 18,
              border: "1px solid #e2e8f0",
              borderRadius: 12,
              background: "#fff",
            }}
          >
            <div>
              <h2 style={{ margin: 0, fontSize: 17 }}>3. Konfigurasi Analisis Pipeline GWAS</h2>
              <p style={{ margin: "6px 0 0", color: "#64748b", fontSize: 13 }}>
                Parameter disesuaikan dengan jenis organisme (inbred vs outcross) dan kebutuhan studi.
              </p>
            </div>

            {/* Fieldset A: Organisme & Quality Control */}
            <fieldset style={{ display: "grid", gap: 14, border: "1px solid #e2e8f0", borderRadius: 8, padding: 16, margin: 0 }}>
              <legend style={{ fontWeight: 800, padding: "0 6px", color: "#1e293b" }}>
                A. Organisme & Quality Control (PLINK)
              </legend>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14 }}>
                <Field
                  label="Tipe Populasi / Organisme"
                  hint="Penting: Inbred lines TIDAK boleh difilter HWE"
                >
                  <select
                    name="population_type"
                    value={form.population_type}
                    onChange={updateField}
                    style={inputStyle}
                  >
                    <option value="inbred">Tanaman Inbred / Selfing (HWE Nonaktif)</option>
                    <option value="outcross">Kawin Silang / Outcross / Hewan (HWE Aktif)</option>
                  </select>
                </Field>
                <Field label="MAF" hint="Minor Allele Frequency threshold">
                  <input
                    name="maf"
                    type="number"
                    min="0"
                    max="0.5"
                    step="0.005"
                    value={form.maf}
                    onChange={updateField}
                    style={inputStyle}
                    required
                  />
                </Field>
                <Field label="GENO" hint="Batas missingness varian SNP">
                  <input
                    name="geno"
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    value={form.geno}
                    onChange={updateField}
                    style={inputStyle}
                    required
                  />
                </Field>
                <Field label="MIND" hint="Batas missingness sampel individu">
                  <input
                    name="mind"
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    value={form.mind}
                    onChange={updateField}
                    style={inputStyle}
                    required
                  />
                </Field>
                {form.population_type === "outcross" && (
                  <Field label="HWE (p-value)" hint="Uji kesetimbangan Hardy-Weinberg">
                    <input
                      name="hwe"
                      type="number"
                      min="0"
                      max="1"
                      step="any"
                      value={form.hwe}
                      onChange={updateField}
                      style={inputStyle}
                      required
                    />
                  </Field>
                )}
              </div>
              <div style={{ display: "flex", gap: 20, flexWrap: "wrap", fontSize: 13, color: "#334155" }}>
                <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    name="snps_only"
                    checked={form.snps_only}
                    onChange={updateField}
                  />
                  <span>Hanya SNP bi-alelik murni (--snps-only just-acgt --max-alleles 2)</span>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    name="set_missing_var_ids"
                    checked={form.set_missing_var_ids}
                    onChange={updateField}
                  />
                  <span>Format ulang ID varian menjadi CHR:POS (@:#)</span>
                </label>
              </div>
            </fieldset>

            {/* Fieldset B: Imputasi Genotipe */}
            <fieldset style={{ display: "grid", gap: 14, border: "1px solid #e2e8f0", borderRadius: 8, padding: 16, margin: 0 }}>
              <legend style={{ fontWeight: 800, padding: "0 6px", color: "#1e293b" }}>
                B. Imputasi Genotipe (Beagle 5)
              </legend>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14 }}>
                <Field label="Strategi Imputasi" hint="Gunakan 'none' bila tanpa panel referensi teruji">
                  <select
                    name="imputation_mode"
                    value={form.imputation_mode}
                    onChange={updateField}
                    style={inputStyle}
                  >
                    <option value="none">Lewati Imputasi (Gunakan Genotipe Asli)</option>
                    <option value="reference">Dengan Panel Referensi (Reference-guided)</option>
                    <option value="reference_free">Tanpa Referensi (Reference-free Beagle)</option>
                  </select>
                </Field>
                {form.imputation_mode === "reference" && (
                  <Field label="Panel Referensi VCF">
                    <select
                      name="reference_vcf_path"
                      value={form.reference_vcf_path}
                      onChange={updateField}
                      style={inputStyle}
                    >
                      {REFERENCE_PANELS.map((panel) => (
                        <option key={panel.value} value={panel.value}>
                          {panel.label}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
                {form.imputation_mode !== "none" && (
                  <>
                    <Field label="Thread Beagle" hint="Thread komputasi JVM">
                      <input
                        name="nthreads"
                        type="number"
                        min="1"
                        max="64"
                        value={form.nthreads}
                        onChange={updateField}
                        style={inputStyle}
                        required
                      />
                    </Field>
                    <Field label="Batas Memori JVM" hint="Contoh: -Xmx8g">
                      <input
                        name="beagle_max_memory"
                        pattern="^-Xmx\d+[mMgG]$"
                        value={form.beagle_max_memory}
                        onChange={updateField}
                        style={inputStyle}
                        required
                      />
                    </Field>
                  </>
                )}
              </div>
            </fieldset>

            {/* Fieldset C: Struktur Populasi & LD Decay */}
            <fieldset style={{ display: "grid", gap: 14, border: "1px solid #e2e8f0", borderRadius: 8, padding: 16, margin: 0 }}>
              <legend style={{ fontWeight: 800, padding: "0 6px", color: "#1e293b" }}>
                C. Struktur Populasi, Kinship & LD Decay
              </legend>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 14 }}>
                <Field label="LD Pruning r²" hint="Threshold linkage disequilibrium">
                  <input
                    name="prune_r2"
                    type="number"
                    min="0.05"
                    max="0.9"
                    step="0.05"
                    value={form.prune_r2}
                    onChange={updateField}
                    style={inputStyle}
                    required
                  />
                </Field>
                <Field label="Jumlah PC (PCA)" hint="Komponen utama populasi (2-20)">
                  <input
                    name="n_pcs"
                    type="number"
                    min="2"
                    max="20"
                    value={form.n_pcs}
                    onChange={updateField}
                    style={inputStyle}
                    required
                  />
                </Field>
                <Field label="Window LD Decay (kb)" hint="Jarak maksimum evaluasi LD">
                  <input
                    name="ld_decay_window_kb"
                    type="number"
                    min="100"
                    max="5000"
                    step="100"
                    value={form.ld_decay_window_kb}
                    onChange={updateField}
                    style={inputStyle}
                    required
                  />
                </Field>
                <Field label="Cutoff LD r²" hint="Batas baseline decay (default 0.2)">
                  <input
                    name="ld_decay_r2_cutoff"
                    type="number"
                    min="0.1"
                    max="0.5"
                    step="0.05"
                    value={form.ld_decay_r2_cutoff}
                    onChange={updateField}
                    style={inputStyle}
                    required
                  />
                </Field>
              </div>
            </fieldset>

            {/* Fieldset D: Analisis GWAS & Multi-Locus */}
            <fieldset style={{ display: "grid", gap: 14, border: "1px solid #e2e8f0", borderRadius: 8, padding: 16, margin: 0 }}>
              <legend style={{ fontWeight: 800, padding: "0 6px", color: "#1e293b" }}>
                D. Analisis Asosiasi (GWAS) & Multi-Locus
              </legend>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14 }}>
                <Field label="Model Statistik" hint="Pilih Auto untuk rekomendasi otomatis">
                  <select name="model" value={form.model} onChange={updateField} style={inputStyle}>
                    <option value="auto">Auto (Rekomendasi Berdasarkan Struktur & Kinship)</option>
                    <option value="lmm">Linear Mixed Model (GEMMA Wald LMM)</option>
                    <option value="glm">Generalized Linear Model (PLINK2 Linear GLM)</option>
                  </select>
                </Field>
                <Field label="Jumlah PC Kovariat" hint="Jumlah PC yang dimasukkan ke model">
                  <input
                    name="n_pc_covariates"
                    type="number"
                    min="0"
                    max="10"
                    value={form.n_pc_covariates}
                    onChange={updateField}
                    style={inputStyle}
                    required
                  />
                </Field>
              </div>
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "#334155", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  name="enable_multilocus"
                  checked={form.enable_multilocus}
                  onChange={updateField}
                />
                <span>
                  Jalankan juga Multi-Locus GWAS (FarmCPU & BLINK via GAPIT/R, opsional jika lingkungan R tersedia)
                </span>
              </label>
            </fieldset>

            {/* Fieldset E: Post-GWAS Threshold & Clumping */}
            <fieldset style={{ display: "grid", gap: 14, border: "1px solid #e2e8f0", borderRadius: 8, padding: 16, margin: 0 }}>
              <legend style={{ fontWeight: 800, padding: "0 6px", color: "#1e293b" }}>
                E. Ambang Signifikansi & Clumping
              </legend>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14 }}>
                <Field label="Metode Threshold">
                  <select
                    name="threshold_method"
                    value={form.threshold_method}
                    onChange={updateField}
                    style={inputStyle}
                  >
                    <option value="bonferroni">Bonferroni Correction (0.05 / M)</option>
                    <option value="fdr">Benjamini-Hochberg FDR</option>
                    <option value="lod">LOD Score (p &lt; 10^-LOD)</option>
                  </select>
                </Field>
                {form.threshold_method !== "lod" ? (
                  <Field label="Tingkat Signifikansi (Alpha)">
                    <input
                      name="threshold_alpha"
                      type="number"
                      min="0.001"
                      max="0.2"
                      step="0.01"
                      value={form.threshold_alpha}
                      onChange={updateField}
                      style={inputStyle}
                      required
                    />
                  </Field>
                ) : (
                  <Field label="Ambang LOD Score">
                    <input
                      name="threshold_lod"
                      type="number"
                      min="1"
                      max="20"
                      step="0.1"
                      value={form.threshold_lod}
                      onChange={updateField}
                      style={inputStyle}
                      required
                    />
                  </Field>
                )}
                <Field label="Clumping r²" hint="Ambang LD untuk pengelompokan SNP">
                  <input
                    name="clump_r2"
                    type="number"
                    min="0.05"
                    max="0.8"
                    step="0.05"
                    value={form.clump_r2}
                    onChange={updateField}
                    style={inputStyle}
                    required
                  />
                </Field>
              </div>
            </fieldset>

            {/* Fieldset F: Anotasi Gen Kandidat */}
            <fieldset style={{ display: "grid", gap: 14, border: "1px solid #e2e8f0", borderRadius: 8, padding: 16, margin: 0 }}>
              <legend style={{ fontWeight: 800, padding: "0 6px", color: "#1e293b" }}>
                F. Anotasi Gen Kandidat
              </legend>
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "#334155", cursor: hasAnnotation ? "pointer" : "not-allowed" }}>
                <input
                  type="checkbox"
                  name="annotation_enabled"
                  checked={form.annotation_enabled && hasAnnotation}
                  onChange={updateField}
                  disabled={!hasAnnotation}
                />
                <span>
                  {hasAnnotation
                    ? "Petakan lead SNPs ke gen kandidat dalam rentang LD decay (GFF3 aktif)"
                    : "Anotasi gen nonaktif (unggah file GFF3 pada Bagian 2 untuk mengaktifkan)"}
                </span>
              </label>
            </fieldset>

            {/* Error & Warning */}
            {error && (
              <p
                role="alert"
                style={{
                  margin: 0,
                  padding: 12,
                  borderRadius: 8,
                  background: "#fef2f2",
                  color: "#991b1b",
                  fontSize: 13,
                  border: "1px solid #fecaca",
                }}
              >
                {error}
              </p>
            )}

            {hasBlockingErrors && (
              <p style={{ margin: 0, color: "#dc2626", fontSize: 13, fontWeight: 600 }}>
                ⚠️ Harap unggah file fenotipe dan selesaikan rekonsiliasi data pada Bagian 2 sebelum menjalankan pipeline.
              </p>
            )}

            <button
              type="submit"
              disabled={isSubmitting || hasBlockingErrors}
              style={{
                border: 0,
                borderRadius: 8,
                padding: "13px 20px",
                background: isSubmitting || hasBlockingErrors ? "#94a3b8" : "#1d4ed8",
                color: "#fff",
                fontWeight: 800,
                fontSize: 15,
                cursor: isSubmitting || hasBlockingErrors ? "not-allowed" : "pointer",
                boxShadow: "0 1px 2px rgba(0,0,0,0.05)",
              }}
            >
              {isSubmitting ? "Menyiapkan pipeline..." : "Mulai Analisis Pipeline GWAS Lengkap →"}
            </button>
          </form>
        )}

        {/* Status Tracker & File Explorer saat job berjalan / terpilih */}
        {jobId && (
          <div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 10,
              }}
            >
              <h2 style={{ margin: 0, fontSize: 17 }}>4. Progress, Log & File Job</h2>
              <div style={{ display: "flex", gap: 8 }}>
                {datasetId && (
                  <button
                    type="button"
                    onClick={() => setJobId(null)}
                    style={{
                      padding: "6px 12px",
                      fontSize: 12,
                      fontWeight: 600,
                      borderRadius: 6,
                      border: "1px solid #cbd5e1",
                      background: "#fff",
                      color: "#334155",
                      cursor: "pointer",
                    }}
                  >
                    Konfigurasi Job Baru
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => navigate(`/jobs/${jobId}/results`)}
                  style={{
                    padding: "6px 12px",
                    fontSize: 12,
                    background: "#10b981",
                    color: "#fff",
                    borderRadius: 6,
                    border: "none",
                    cursor: "pointer",
                    fontWeight: 700,
                  }}
                >
                  Lihat Halaman Hasil →
                </button>
              </div>
            </div>
            <JobStatusTracker
              jobId={jobId}
              onComplete={handleJobComplete}
              onCancelled={() => setRefreshCount((c) => c + 1)}
              onRetried={() => setRefreshCount((c) => c + 1)}
            />
            <JobFilesPanel jobId={jobId} isRunning={true} />
          </div>
        )}
      </section>
    </main>
  );
}
