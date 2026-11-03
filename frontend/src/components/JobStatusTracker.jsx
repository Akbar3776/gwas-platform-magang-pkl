import { useEffect, useState } from "react";
import { cancelJob, retryJob } from "../api/jobsApi";
import { useJobPolling } from "../hooks/useJobPolling";
import { JobProcessLogs } from "./JobProcessLogs";

const STEPS = [
  { key: "INSPECTION", logKey: "inspection", label: "Inspeksi Data", detail: "Rekonsiliasi sampel & fenotipe" },
  { key: "NORMALIZATION", logKey: "normalization", label: "Normalisasi VCF", detail: "Sorting & normalisasi bcftools" },
  { key: "QC", logKey: "qc", label: "Quality Control", detail: "Filter varian & sampel PLINK" },
  { key: "IMPUTATION", logKey: "imputation", label: "Imputasi Genotipe", detail: "Beagle 5.4 (Opsional)" },
  { key: "STRUCTURE", logKey: "structure", label: "Struktur & LD", detail: "PCA, Kinship & LD Decay" },
  { key: "GWAS", logKey: "gwas", label: "Analisis GWAS", detail: "Uji asosiasi GLM / LMM" },
  { key: "MULTILOCUS", logKey: "multilocus", label: "Multi-Locus GWAS", detail: "FarmCPU / BLINK / MLMM (Opsional)" },
  { key: "POSTGWAS", logKey: "postgwas", label: "Pasca-GWAS", detail: "Ambang batas & Clumping" },
  { key: "VISUALIZATION", logKey: "visualization", label: "Visualisasi Plot", detail: "Cache Manhattan & QQ Plot" },
  { key: "ANNOTATION", logKey: "annotation", label: "Anotasi Gen GFF3", detail: "Pemetaan gen & daftar GO" },
];

const STATUS_LABELS = {
  PENDING: "Menyiapkan analisis",
  RUNNING_INSPECTION: "Tahap 1/10: Inspeksi Fenotipe & Sampel sedang berjalan",
  RUNNING_NORMALIZATION: "Tahap 2/10: Normalisasi VCF sedang berjalan",
  RUNNING_QC: "Quality Control sedang berjalan",
  RUNNING_IMPUTATION: "Tahap 4/10: Imputasi sedang berjalan",
  RUNNING_STRUCTURE: "Tahap 5/10: Analisis Struktur Populasi & LD sedang berjalan",
  RUNNING_GWAS: "Tahap 6/10: Analisis GWAS sedang berjalan",
  RUNNING_MULTILOCUS: "Tahap 7/10: Multi-Locus GWAS sedang berjalan",
  RUNNING_POSTGWAS: "Tahap 8/10: Pasca-GWAS & Clumping sedang berjalan",
  RUNNING_PARSING: "Tahap 9/10: Menyiapkan visualisasi hasil",
  RUNNING_ANNOTATION: "Tahap 10/10: Anotasi Gen Kandidat GFF3 sedang berjalan",
  RUNNING: "Analisis sedang berjalan",
  COMPLETED: "Analisis selesai (10/10 tahap)",
  SUCCESS: "Analisis selesai (10/10 tahap)",
  FAILED: "Analisis berhenti karena masalah",
  CANCELLED: "Analisis dibatalkan",
};

function normalize(value) {
  return String(value || "").toUpperCase();
}

function currentStep(statusObj) {
  const step = normalize(statusObj?.current_step);
  const overall = normalize(statusObj?.overall_status);
  if (step === "INSPECTION" || overall === "RUNNING_INSPECTION") return "INSPECTION";
  if (step === "NORMALIZATION" || overall === "RUNNING_NORMALIZATION") return "NORMALIZATION";
  if (step === "QC" || overall === "RUNNING_QC") return "QC";
  if (step === "IMPUTATION" || overall === "RUNNING_IMPUTATION") return "IMPUTATION";
  if (step === "STRUCTURE" || overall === "RUNNING_STRUCTURE") return "STRUCTURE";
  if (step === "GWAS" || overall === "RUNNING_GWAS") return "GWAS";
  if (step === "MULTILOCUS" || overall === "RUNNING_MULTILOCUS") return "MULTILOCUS";
  if (step === "POSTGWAS" || overall === "RUNNING_POSTGWAS") return "POSTGWAS";
  if (step === "VISUALIZATION" || overall === "RUNNING_PARSING") return "VISUALIZATION";
  if (step === "ANNOTATION" || overall === "RUNNING_ANNOTATION" || overall === "COMPLETED" || overall === "SUCCESS") {
    return "ANNOTATION";
  }
  return "INSPECTION";
}

function stepState(stepKey, logKey, activeKey, status, stepLogs) {
  const logEntry = Array.isArray(stepLogs) ? stepLogs.find((item) => item.step === logKey) : null;
  if (logEntry?.status === "skipped") return "skipped";
  if (logEntry?.status === "failed") return "failed";
  if ((status === "FAILED" || status === "CANCELLED") && stepKey === activeKey) return "failed";
  if (status === "COMPLETED" || status === "SUCCESS") return "done";
  if (logEntry?.status === "done") return "done";
  const activeIndex = STEPS.findIndex((step) => step.key === activeKey);
  const stepIndex = STEPS.findIndex((step) => step.key === stepKey);
  if (stepIndex < activeIndex) return "done";
  if (stepIndex === activeIndex) return "active";
  return "pending";
}

const COLORS = {
  done: { accent: "#15803d", background: "#f0fdf4", border: "#bbf7d0" },
  active: { accent: "#1d4ed8", background: "#eff6ff", border: "#93c5fd" },
  failed: { accent: "#b91c1c", background: "#fef2f2", border: "#fecaca" },
  skipped: { accent: "#64748b", background: "#f1f5f9", border: "#cbd5e1" },
  pending: { accent: "#94a3b8", background: "#f8fafc", border: "#e2e8f0" },
};

export function JobStatusTracker({ jobId, onComplete, onCancelled, onRetried }) {
  const [pollTrigger, setPollTrigger] = useState(0);
  const { jobStatus, status, isPolling, pollError } = useJobPolling(jobId, {
    intervalMs: 2500,
    pollTrigger,
  });
  const [selectedStepTab, setSelectedStepTab] = useState("all");
  const [isCancelling, setIsCancelling] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [retryError, setRetryError] = useState(null);
  const [localCancelled, setLocalCancelled] = useState(false);

  const effectiveStatus = localCancelled ? "CANCELLED" : status;
  const isTerminal = ["COMPLETED", "SUCCESS", "FAILED", "CANCELLED"].includes(effectiveStatus);
  const isStopped = effectiveStatus === "FAILED" || effectiveStatus === "CANCELLED";

  useEffect(() => {
    setLocalCancelled(false);
    setRetryError(null);
  }, [jobId]);

  useEffect(() => {
    if ((effectiveStatus === "COMPLETED" || effectiveStatus === "SUCCESS") && onComplete) {
      onComplete();
    }
  }, [effectiveStatus, onComplete]);

  const handleCancelJob = async () => {
    if (!jobId || isCancelling) return;
    setIsCancelling(true);
    try {
      await cancelJob(jobId);
      setLocalCancelled(true);
      if (onCancelled) {
        onCancelled(jobId);
      }
    } catch {
      // Abaikan jika sudah selesai
    } finally {
      setIsCancelling(false);
    }
  };

  const handleRetryJob = async (fromScratch = false) => {
    if (!jobId || isRetrying) return;
    setIsRetrying(true);
    setRetryError(null);
    try {
      const res = await retryJob(jobId, { fromScratch });
      setLocalCancelled(false);
      if (res?.resumed_from_step) {
        setSelectedStepTab(res.resumed_from_step);
      }
      setPollTrigger((n) => n + 1);
      if (onRetried) {
        onRetried(jobId, res);
      }
    } catch (err) {
      setRetryError(
        err.response?.data?.detail || err.message || "Gagal menjalankan ulang job."
      );
    } finally {
      setIsRetrying(false);
    }
  };

  const activeKey = currentStep(jobStatus);
  const headline = STATUS_LABELS[effectiveStatus] || "Memuat status analisis";
  const failureMessage = jobStatus?.error_log || jobStatus?.error_message;

  return (
    <section
      aria-live="polite"
      style={{ border: "1px solid #e2e8f0", borderRadius: 12, padding: 20, background: "#fff" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
        <div>
          <p style={{ margin: 0, color: "#64748b", fontSize: 12 }}>STATUS ANALISIS</p>
          <h3 style={{ margin: "4px 0 0", color: isStopped ? "#991b1b" : "#0f172a" }}>
            {headline}
          </h3>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {isPolling && !localCancelled && (
            <span style={{ color: "#2563eb", fontSize: 12 }}>Memperbarui otomatis</span>
          )}
          {!isTerminal && (
            <button
              type="button"
              disabled={isCancelling}
              onClick={handleCancelJob}
              style={{
                padding: "6px 12px",
                fontSize: 12,
                fontWeight: 700,
                borderRadius: 6,
                border: "1px solid #fca5a5",
                background: "#fef2f2",
                color: "#b91c1c",
                cursor: "pointer",
              }}
            >
              {isCancelling ? "Menghentikan..." : "Hentikan Job"}
            </button>
          )}
          {isStopped && (
            <>
              <button
                type="button"
                disabled={isRetrying}
                onClick={() => handleRetryJob(false)}
                title="Lanjutkan dari tahap terakhir yang berhenti tanpa mengulang tahap yang sudah selesai"
                style={{
                  padding: "7px 14px",
                  fontSize: 12,
                  fontWeight: 700,
                  borderRadius: 6,
                  border: "none",
                  background: "#2563eb",
                  color: "#fff",
                  cursor: isRetrying ? "not-allowed" : "pointer",
                }}
              >
                {isRetrying ? "Menjalankan Ulang..." : "↻ Lanjutkan Job (Retry)"}
              </button>
              <button
                type="button"
                disabled={isRetrying}
                onClick={() => handleRetryJob(true)}
                title="Bersihkan hasil sementara dan ulangi seluruh analisis dari Tahap 1"
                style={{
                  padding: "7px 12px",
                  fontSize: 12,
                  fontWeight: 600,
                  borderRadius: 6,
                  border: "1px solid #cbd5e1",
                  background: "#f8fafc",
                  color: "#334155",
                  cursor: isRetrying ? "not-allowed" : "pointer",
                }}
              >
                Ulangi dari Tahap 1
              </button>
            </>
          )}
        </div>
      </div>

      {/* Context Banner: Genotipe, Fenotipe, Trait & Model */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 12,
          padding: "12px 14px",
          background: "#f8fafc",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          marginTop: 14,
        }}
      >
        <div>
          <span style={{ fontSize: 11, color: "#64748b", fontWeight: 700, letterSpacing: "0.03em" }}>
            DATASET GENOTIPE (VCF)
          </span>
          <p style={{ margin: "2px 0 0", fontSize: 13, fontWeight: 700, color: "#0f172a", fontFamily: "monospace" }}>
            {jobStatus?.dataset_filename || "Memuat VCF..."}
          </p>
        </div>
        <div>
          <span style={{ fontSize: 11, color: "#64748b", fontWeight: 700, letterSpacing: "0.03em" }}>
            STATUS FENOTIPE
          </span>
          <p style={{ margin: "2px 0 0", fontSize: 13, fontWeight: 700, color: jobStatus?.phenotype_filename ? "#15803d" : "#b45309" }}>
            {jobStatus?.phenotype_filename ? `✓ ${jobStatus.phenotype_filename}` : "⚠️ Tidak terpasang"}
          </p>
        </div>
        <div>
          <span style={{ fontSize: 11, color: "#64748b", fontWeight: 700, letterSpacing: "0.03em" }}>
            TRAIT TARGET GWAS
          </span>
          <p style={{ margin: "2px 0 0", fontSize: 13, fontWeight: 700, color: "#2563eb" }}>
            {jobStatus?.trait ? `${jobStatus.trait} (Kuantitatif)` : "Auto / Default"}
          </p>
        </div>
        <div>
          <span style={{ fontSize: 11, color: "#64748b", fontWeight: 700, letterSpacing: "0.03em" }}>
            MODEL STATISTIK
          </span>
          <p style={{ margin: "2px 0 0", fontSize: 13, fontWeight: 700, color: "#0f172a" }}>
            {String(jobStatus?.model || "Auto").toUpperCase()}
          </p>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
          gap: 10,
          marginTop: 16,
        }}
      >
        {STEPS.map((step, index) => {
          const state = stepState(step.key, step.logKey, activeKey, effectiveStatus, jobStatus?.step_logs);
          const colors = COLORS[state] || COLORS.pending;
          const isSelected = selectedStepTab === step.logKey;
          return (
            <div
              key={step.key}
              onClick={() => setSelectedStepTab(step.logKey)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  setSelectedStepTab(step.logKey);
                }
              }}
              title={`Klik untuk melihat log Tahap ${index + 1}: ${step.label}`}
              style={{
                position: "relative",
                minWidth: 0,
                minHeight: 116,
                cursor: "pointer",
                padding: "10px 8px",
                borderRadius: 10,
                border: isSelected ? `2px solid ${colors.accent}` : `1px solid ${colors.border}`,
                background: isSelected ? "#fff" : colors.background,
                boxShadow: isSelected ? "0 2px 6px rgba(37,99,235,0.15)" : "none",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                alignItems: "center",
                textAlign: "center",
                transition: "all 0.15s ease",
              }}
            >
              <div>
                <div
                  style={{
                    width: 28,
                    height: 28,
                    borderRadius: "50%",
                    display: "grid",
                    placeItems: "center",
                    margin: "0 auto",
                    background: "#fff",
                    border: `2px solid ${colors.accent}`,
                    color: colors.accent,
                    fontWeight: 700,
                    fontSize: 12,
                  }}
                  aria-label={`${step.label}: ${state}`}
                >
                  {state === "done" ? "✓" : state === "failed" ? "!" : state === "skipped" ? "–" : index + 1}
                </div>
                <div style={{ marginTop: 6, fontSize: 10, fontWeight: 700, color: "#64748b", letterSpacing: "0.04em" }}>
                  TAHAP {index + 1}
                </div>
                <strong style={{ display: "block", marginTop: 2, color: colors.accent, fontSize: 12, lineHeight: 1.25 }}>
                  {step.label}
                </strong>
              </div>
              <span style={{ marginTop: 4, color: "#64748b", fontSize: 11, lineHeight: 1.2 }}>
                {step.detail}
              </span>
            </div>
          );
        })}
      </div>

      {effectiveStatus === "FAILED" && (
        <div style={{ marginTop: 20, padding: 12, borderRadius: 8, background: "#fef2f2", color: "#991b1b", fontSize: 13 }}>
          {failureMessage || "Server tidak dapat menyelesaikan analisis. Periksa parameter atau file input."}
        </div>
      )}
      {effectiveStatus === "CANCELLED" && (
        <div style={{ marginTop: 20, padding: 12, borderRadius: 8, background: "#fffbeb", color: "#92400e", fontSize: 13 }}>
          {failureMessage || "Eksekusi job telah dihentikan oleh pengguna."}
        </div>
      )}
      {retryError && (
        <div style={{ marginTop: 12, padding: 10, borderRadius: 8, background: "#fef2f2", color: "#991b1b", fontSize: 12 }}>
          {retryError}
        </div>
      )}
      {pollError && effectiveStatus !== "FAILED" && effectiveStatus !== "CANCELLED" && (
        <p style={{ margin: "16px 0 0", color: "#a16207", fontSize: 12 }}>
          Koneksi sempat terganggu. Sistem akan mencoba mengambil status lagi.
        </p>
      )}

      <JobProcessLogs
        jobId={jobId}
        stepLogs={jobStatus?.step_logs || []}
        elapsedSeconds={jobStatus?.elapsed_seconds}
        selectedStep={selectedStepTab}
        onSelectStep={setSelectedStepTab}
      />
    </section>
  );
}
export default JobStatusTracker;
