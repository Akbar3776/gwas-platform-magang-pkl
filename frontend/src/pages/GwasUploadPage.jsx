import { cloneElement, isValidElement, useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { formatApiError } from "../api/formatApiError";
import { fetchCapabilities, fetchReferencePanels, triggerFullPipeline } from "../api/jobsApi";
import { JobFilesPanel } from "../components/JobFilesPanel";
import { JobStatusTracker } from "../components/JobStatusTracker";
import { VcfUploader } from "../components/VcfUploader";
import { WorkspaceExplorer } from "../components/WorkspaceExplorer";
import { PhenotypeUploader } from "../components/PhenotypeUploader";
import { AnnotationUploader } from "../components/AnnotationUploader";
import { GoMappingUploader } from "../components/GoMappingUploader";
import { ReferenceUploader } from "../components/ReferenceUploader";
import { DataInspectionPanel } from "../components/DataInspectionPanel";

const EMPTY_ARRAY = [];
const TERMINAL_JOB_STATUSES = new Set(["COMPLETED", "SUCCESS", "FAILED", "CANCELLED"]);

const INITIAL_FORM = {
  // QC
  population_type: "inbred",
  maf: "0.05",
  enable_geno: true,
  geno: "0.05",
  mind: "0.1",
  hwe: "0.000001",
  snps_only: true,
  set_missing_var_ids: true,
  // Imputasi
  imputation_mode: "none",
  reference_panel_id: "",
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
  multilocus_methods: ["FarmCPU", "BLINK", "MLMM"],
  // Threshold & Clumping
  threshold_method: "bonferroni",
  threshold_alpha: "0.05",
  threshold_lod: "5.6",
  clump_r2: "0.2",
  clump_p2: "0.0001",
  // Anotasi
  annotation_enabled: true,
};

function Field({ id, label, headerExtra, hint, children }) {
  const autoId = useId();
  const childId = isValidElement(children) ? children.props.id : undefined;
  const controlId = id || childId || autoId;
  const control =
    isValidElement(children) && !childId
      ? cloneElement(children, { id: controlId })
      : children;

  return (
    <div style={{ display: "grid", gap: 6, color: "#334155", fontSize: 13 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <label htmlFor={controlId} style={{ fontWeight: 700 }}>
          {label}
        </label>
        {headerExtra}
      </div>
      {control}
      {hint && <span style={{ color: "#64748b", fontSize: 11 }}>{hint}</span>}
    </div>
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
  const [polledJobStatus, setPolledJobStatus] = useState("");
  const [autoRedirectOnComplete, setAutoRedirectOnComplete] = useState(true);
  const [refreshCount, setRefreshCount] = useState(0);
  const [form, setForm] = useState(INITIAL_FORM);
  const [referencePanels, setReferencePanels] = useState([]);
  const [capabilities, setCapabilities] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // Phenotype & Inspection states
  const [inspectionReport, setInspectionReport] = useState(null);
  const [selectedTrait, setSelectedTrait] = useState("");
  const [selectedIdCol, setSelectedIdCol] = useState("IID");
  const [hasAnnotation, setHasAnnotation] = useState(false);

  const hasRedirectedRef = useRef(false);

  useEffect(() => {
    let active = true;
    fetchReferencePanels()
      .then((panels) => {
        if (!active) return;
        const list = Array.isArray(panels) ? panels : [];
        setReferencePanels(list);
        if (list.length > 0) {
          setForm((current) =>
            current.reference_panel_id ? current : { ...current, reference_panel_id: list[0].id }
          );
        }
      })
      .catch(() => {
        if (active) setReferencePanels([]);
      });
    if (typeof fetchCapabilities === "function") {
      fetchCapabilities()
        .then((caps) => {
          if (active && caps && typeof caps === "object") {
            setCapabilities(caps);
          }
        })
        .catch(() => {
          if (active) setCapabilities(null);
        });
    }
    return () => {
      active = false;
    };
  }, []);

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
    setHasAnnotation(Boolean(meta?.has_annotation || meta?.annotation_filename));
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
    setHasAnnotation(Boolean(selectedMeta?.has_annotation || selectedMeta?.annotation_filename));
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
    setPolledJobStatus("");
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

  const handleAnnotationChanged = useCallback((meta) => {
    setHasAnnotation(Boolean(meta));
    setRefreshCount((c) => c + 1);
  }, []);

  const handleReferenceChanged = useCallback((meta) => {
    setDatasetMeta((prev) =>
      prev
        ? {
            ...prev,
            has_reference: Boolean(meta),
            reference_filename: meta?.filename || null,
            reference_n_contigs: meta?.n_contigs ?? null,
          }
        : prev
    );
    setRefreshCount((c) => c + 1);
  }, []);

  const handleGoAnnotationChanged = useCallback((meta) => {
    setDatasetMeta((prev) =>
      prev
        ? {
            ...prev,
            has_go_annotation: Boolean(meta),
            go_annotation_filename: meta?.filename || null,
            go_annotation_n_genes: meta?.n_genes ?? null,
          }
        : prev
    );
    setRefreshCount((c) => c + 1);
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
          geno: form.enable_geno ? Number(form.geno) : null,
          mind: Number(form.mind),
          hwe: form.population_type === "outcross" ? Number(form.hwe) : null,
          snps_only: Boolean(form.snps_only),
          max_alleles: 2,
          set_missing_var_ids: Boolean(form.set_missing_var_ids),
        },
        imputation: {
          mode: form.imputation_mode,
          reference_panel_id: form.imputation_mode === "reference" ? form.reference_panel_id || null : null,
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
          multilocus_methods: form.multilocus_methods || ["FarmCPU", "BLINK", "MLMM"],
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
          enabled: Boolean(form.annotation_enabled),
          feature_type: "gene",
        },
      };

      const response = await triggerFullPipeline(datasetId, payload);
      hasRedirectedRef.current = false;
      setAutoRedirectOnComplete(true);
      setPolledJobStatus("PENDING");
      setJobId(response.job_id);
      setRefreshCount((c) => c + 1);
    } catch (requestError) {
      setError(
        formatApiError(
          requestError,
          "Pipeline tidak dapat dimulai. Periksa konfigurasi Anda."
        )
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

      {/* Saat Job Berjalan / Terpilih: Tampilkan Tracker di paling atas dengan layout bersih */}
      {jobId ? (
        <section style={{ display: "grid", gap: 20 }}>
          <div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 12,
                flexWrap: "wrap",
                gap: 10,
              }}
            >
              <div>
                <span style={{ fontSize: 12, fontWeight: 700, color: "#2563eb", textTransform: "uppercase" }}>
                  Status Komputasi GWAS
                </span>
                <h2 style={{ margin: "2px 0 0", fontSize: 20 }}>Progress & Eksekusi Pipeline</h2>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  type="button"
                  onClick={() => setJobId(null)}
                  style={{
                    padding: "7px 14px",
                    fontSize: 13,
                    fontWeight: 600,
                    borderRadius: 7,
                    border: "1px solid #cbd5e1",
                    background: "#fff",
                    color: "#334155",
                    cursor: "pointer",
                  }}
                >
                  ← Konfigurasi Job / Dataset Lain
                </button>
                <button
                  type="button"
                  onClick={() => navigate(`/jobs/${jobId}/results`)}
                  style={{
                    padding: "7px 14px",
                    fontSize: 13,
                    background: "#10b981",
                    color: "#fff",
                    borderRadius: 7,
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
              onStatusChange={setPolledJobStatus}
            />
            <JobFilesPanel
              jobId={jobId}
              isRunning={!TERMINAL_JOB_STATUSES.has(String(polledJobStatus || "").toUpperCase())}
            />
          </div>

          {/* Workspace Explorer di bagian bawah untuk referensi riwayat job */}
          <div style={{ marginTop: 10 }}>
            <h3 style={{ fontSize: 15, margin: "0 0 10px", color: "#64748b" }}>Riwayat Workspace & Job Lainnya</h3>
            <WorkspaceExplorer
              activeDatasetId={datasetId}
              activeJobId={jobId}
              onSelectDataset={handleSelectExistingDataset}
              onDeleteDataset={handleDeleteDataset}
              onSelectJob={handleSelectExistingJob}
              onDeleteJob={handleDeleteJob}
              refreshTrigger={refreshCount}
            />
          </div>
        </section>
      ) : (
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
          {datasetId && (
            <div style={{ display: "grid", gap: 18, padding: 18, border: "1px solid #e2e8f0", borderRadius: 12, background: "#fff" }}>
              <h2 style={{ margin: 0, fontSize: 17 }}>2. Fenotipe & Anotasi Genom</h2>
              <p style={{ margin: 0, color: "#64748b", fontSize: 13 }}>
                Unggah file fenotipe (.txt/.tsv/.csv) untuk analisis asosiasi, serta file opsional GFF3 (anotasi gen), pemetaan GO (.tsv/.gaf), dan FASTA referensi genom (flanking &amp; Primer3).
              </p>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 }}>
                <PhenotypeUploader
                  datasetId={datasetId}
                  initialPhenotypeFilename={datasetMeta?.phenotype_filename}
                  initialColumns={datasetMeta?.phenotype_columns || EMPTY_ARRAY}
                  initialNumericTraits={datasetMeta?.numeric_traits || EMPTY_ARRAY}
                  onInspectionReady={handleInspectionReady}
                  onPhenotypeCleared={handlePhenotypeCleared}
                />
                <AnnotationUploader
                  datasetId={datasetId}
                  initialAnnotationFilename={datasetMeta?.annotation_filename}
                  onAnnotationChanged={handleAnnotationChanged}
                />
                <ReferenceUploader
                  datasetId={datasetId}
                  initialReferenceFilename={datasetMeta?.reference_filename}
                  initialReferenceContigs={datasetMeta?.reference_n_contigs}
                  onReferenceChanged={handleReferenceChanged}
                />
                <GoMappingUploader
                  datasetId={datasetId}
                  initialGoAnnotationFilename={datasetMeta?.go_annotation_filename}
                  initialGoAnnotationGenes={datasetMeta?.go_annotation_n_genes}
                  onGoAnnotationChanged={handleGoAnnotationChanged}
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
                <Field
                  label="GENO"
                  headerExtra={
                    <label style={{ display: "inline-flex", alignItems: "center", gap: 5, cursor: "pointer", fontWeight: 500, fontSize: 12, color: form.enable_geno ? "#1e293b" : "#64748b" }}>
                      <input
                        type="checkbox"
                        name="enable_geno"
                        checked={form.enable_geno}
                        onChange={updateField}
                      />
                      <span>Aktifkan</span>
                    </label>
                  }
                  hint={form.enable_geno ? "Batas missingness varian SNP (default 0.05)" : "Filter missingness varian SNP dinonaktifkan (opsional)"}
                >
                  <input
                    name="geno"
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    value={form.geno}
                    onChange={updateField}
                    disabled={!form.enable_geno}
                    style={{
                      ...inputStyle,
                      backgroundColor: form.enable_geno ? "#fff" : "#f1f5f9",
                      color: form.enable_geno ? "#0f172a" : "#94a3b8",
                      cursor: form.enable_geno ? "text" : "not-allowed",
                    }}
                    required={form.enable_geno}
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
              {capabilities?.beagle === false && (
                <div style={{ fontSize: 12, color: "#92400e", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 6, padding: "8px 10px" }}>
                  Imputasi Beagle tidak tersedia karena Java atau <code>beagle.jar</code> belum terpasang di server.
                </div>
              )}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14 }}>
                <Field label="Strategi Imputasi" hint="Gunakan 'none' bila tanpa panel referensi teruji">
                  <select
                    name="imputation_mode"
                    value={form.imputation_mode}
                    onChange={updateField}
                    style={inputStyle}
                  >
                    <option value="none">Lewati Imputasi (Gunakan Genotipe Asli)</option>
                    <option value="reference" disabled={capabilities?.beagle === false}>
                      {capabilities?.beagle === false
                        ? "Dengan Panel Referensi (Tidak tersedia: Beagle/Java tidak terpasang)"
                        : "Dengan Panel Referensi (Reference-guided)"}
                    </option>
                    <option value="reference_free" disabled={capabilities?.beagle === false}>
                      {capabilities?.beagle === false
                        ? "Tanpa Referensi (Tidak tersedia: Beagle/Java tidak terpasang)"
                        : "Tanpa Referensi (Reference-free Beagle)"}
                    </option>
                  </select>
                </Field>
                {form.imputation_mode === "reference" && (
                  <Field label="Panel Referensi VCF">
                    <select
                      name="reference_panel_id"
                      value={form.reference_panel_id}
                      onChange={updateField}
                      style={inputStyle}
                      required
                    >
                      {referencePanels.length === 0 ? (
                        <option value="">Panel referensi tidak tersedia</option>
                      ) : (
                        referencePanels.map((panel) => (
                          <option key={panel.id} value={panel.id}>
                            {`${panel.name} - ${panel.genome_build}`}
                          </option>
                        ))
                      )}
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
                    <option value="farmcpu" disabled={capabilities?.multilocus_available === false}>
                      {capabilities?.multilocus_available === false
                        ? "FarmCPU (Tidak tersedia di server)"
                        : "FarmCPU (Fixed and Random Model Iterative)"}
                    </option>
                    <option value="blink" disabled={capabilities?.multilocus_available === false}>
                      {capabilities?.multilocus_available === false
                        ? "BLINK (Tidak tersedia di server)"
                        : "BLINK (Bayesian Information & LD Pruned Keyway)"}
                    </option>
                    <option value="mlmm" disabled={capabilities?.multilocus_available === false}>
                      {capabilities?.multilocus_available === false
                        ? "MLMM (Tidak tersedia di server)"
                        : "MLMM (Multiple Locus Mixed Model Stepwise)"}
                    </option>
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
              <div style={{ display: "grid", gap: 8, marginTop: 4 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: capabilities?.multilocus_available === false ? "#94a3b8" : "#334155", cursor: capabilities?.multilocus_available === false ? "not-allowed" : "pointer" }}>
                  <input
                    type="checkbox"
                    name="enable_multilocus"
                    checked={capabilities?.multilocus_available === false ? false : form.enable_multilocus}
                    disabled={capabilities?.multilocus_available === false}
                    onChange={updateField}
                  />
                  <span style={{ fontWeight: 600 }}>
                    Jalankan juga Multi-Locus GWAS (FarmCPU, BLINK & MLMM)
                  </span>
                </label>
                {capabilities?.multilocus_available === false && (
                  <div style={{ fontSize: 12, color: "#92400e", marginLeft: 24 }}>
                    Multi-Locus GWAS tidak tersedia pada konfigurasi server saat ini.
                  </div>
                )}
                {capabilities?.multilocus_available && capabilities?.rscript === false && (
                  <div style={{ fontSize: 11, color: "#475569", marginLeft: 24 }}>
                    Rscript/GAPIT tidak terpasang; Multi-Locus berjalan memakai engine Python Native.
                  </div>
                )}
                {form.enable_multilocus && (
                  <div style={{ display: "flex", gap: 16, marginLeft: 24, flexWrap: "wrap", fontSize: 12, color: "#475569" }}>
                    {["FarmCPU", "BLINK", "MLMM"].map((m) => {
                      const currentMethods = form.multilocus_methods || ["FarmCPU", "BLINK", "MLMM"];
                      const isChecked = currentMethods.includes(m);
                      const isOnlyOne = isChecked && currentMethods.length === 1;
                      return (
                        <label
                          key={m}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                            cursor: isOnlyOne ? "not-allowed" : "pointer",
                            opacity: isOnlyOne ? 0.65 : 1,
                          }}
                          title={isOnlyOne ? "Minimal satu metode harus dipilih" : undefined}
                        >
                          <input
                            type="checkbox"
                            checked={isChecked}
                            disabled={isOnlyOne}
                            onChange={(e) => {
                              const prev = form.multilocus_methods || ["FarmCPU", "BLINK", "MLMM"];
                              if (e.target.checked) {
                                setForm((f) => ({ ...f, multilocus_methods: [...prev, m] }));
                              } else if (prev.length > 1) {
                                setForm((f) => ({ ...f, multilocus_methods: prev.filter((item) => item !== m) }));
                              }
                            }}
                          />
                          <span>{m}</span>
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>
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
                      min="0.0001"
                      max="0.2"
                      step="any"
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
              <div
                style={{
                  padding: "10px 14px",
                  background: "#fef2f2",
                  border: "1px solid #fecaca",
                  borderRadius: 8,
                  color: "#b91c1c",
                  fontSize: 13,
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <span style={{ fontSize: 16 }}>⚠️</span>
                <span>
                  {!inspectionReport
                    ? "File fenotipe belum diunggah atau diinspeksi. Silakan unggah file fenotipe pada Tahap 2 di atas terlebih dahulu."
                    : !selectedTrait
                    ? "Silakan pilih salah satu trait fenotipe numerik target pada Tahap 2 di atas."
                    : `Terdapat error pada fenotipe: ${inspectionReport.blocking_errors.join(", ")}`}
                </span>
              </div>
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
      </section>
    )}
  </main>
);
}
