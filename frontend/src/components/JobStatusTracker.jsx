import { useJobPolling } from "../hooks/useJobPolling";

const STEPS = [
  { key: "NORMALIZATION", label: "Normalisasi", detail: "Memeriksa dan menata VCF" },
  { key: "QC", label: "Quality Control", detail: "Memeriksa kualitas varian dan sampel" },
  { key: "IMPUTATION", label: "Imputasi", detail: "Melengkapi data genotipe" },
  { key: "GWAS", label: "Analisis GWAS", detail: "Menghitung asosiasi genetik" },
  { key: "VISUALIZATION", label: "Menyiapkan hasil", detail: "Menyiapkan data grafik" },
];

const STATUS_LABELS = {
  PENDING: "Menyiapkan analisis",
  RUNNING_QC: "Quality Control sedang berjalan",
  RUNNING_IMPUTATION: "Imputasi sedang berjalan",
  RUNNING_GWAS: "Analisis GWAS sedang berjalan",
  RUNNING: "Analisis sedang berjalan",
  COMPLETED: "Analisis selesai",
  SUCCESS: "Analisis selesai",
  FAILED: "Analisis berhenti karena masalah",
  CANCELLED: "Analisis dibatalkan",
};

function normalize(value) {
  return String(value || "").toUpperCase();
}

function currentStep(status) {
  const step = normalize(status?.current_step);
  if (step === "NORMALIZATION" || status === "RUNNING_NORMALIZATION") return "NORMALIZATION";
  if (step === "QC" || status === "RUNNING_QC") return "QC";
  if (step === "IMPUTATION" || status === "RUNNING_IMPUTATION") return "IMPUTATION";
  if (step === "GWAS" || status === "RUNNING_GWAS") return "GWAS";
  if (step === "VISUALIZATION" || status === "COMPLETED" || status === "SUCCESS") return "VISUALIZATION";
  return "QC";
}

function stepState(stepKey, activeKey, status) {
  if (status === "FAILED" && stepKey === activeKey) return "failed";
  if (status === "COMPLETED" || status === "SUCCESS") return "done";
  const activeIndex = STEPS.findIndex((step) => step.key === activeKey);
  const stepIndex = STEPS.findIndex((step) => step.key === stepKey);
  if (stepIndex < activeIndex) return "done";
  if (stepIndex === activeIndex) return "active";
  return "pending";
}

const COLORS = {
  done: { accent: "#15803d", background: "#f0fdf4" },
  active: { accent: "#1d4ed8", background: "#eff6ff" },
  failed: { accent: "#b91c1c", background: "#fef2f2" },
  pending: { accent: "#94a3b8", background: "#f8fafc" },
};

export function JobStatusTracker({ jobId }) {
  const { jobStatus, status, isPolling, pollError } = useJobPolling(jobId, { intervalMs: 4000 });
  const activeKey = currentStep(jobStatus);
  const headline = STATUS_LABELS[status] || "Memuat status analisis";
  const failureMessage = jobStatus?.error_log || jobStatus?.error_message;

  return (
    <section
      aria-live="polite"
      style={{ border: "1px solid #e2e8f0", borderRadius: 12, padding: 20, background: "#fff" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "baseline" }}>
        <div>
          <p style={{ margin: 0, color: "#64748b", fontSize: 12 }}>STATUS ANALISIS</p>
          <h3 style={{ margin: "4px 0 0", color: status === "FAILED" ? "#991b1b" : "#0f172a" }}>
            {headline}
          </h3>
        </div>
        {isPolling && <span style={{ color: "#2563eb", fontSize: 12 }}>Memperbarui otomatis</span>}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: `repeat(${STEPS.length}, minmax(0, 1fr))`, gap: 8, marginTop: 24 }}>
        {STEPS.map((step, index) => {
          const state = stepState(step.key, activeKey, status);
          const colors = COLORS[state];
          return (
            <div key={step.key} style={{ position: "relative", minWidth: 0 }}>
              {index > 0 && (
                <span
                  aria-hidden="true"
                  style={{ position: "absolute", top: 15, left: -8, right: "50%", height: 2, background: state === "done" ? "#15803d" : "#e2e8f0" }}
                />
              )}
              <div style={{ position: "relative", zIndex: 1, display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center" }}>
                <div
                  style={{ width: 30, height: 30, borderRadius: "50%", display: "grid", placeItems: "center", background: colors.background, border: `2px solid ${colors.accent}`, color: colors.accent, fontWeight: 700 }}
                  aria-label={`${step.label}: ${state}`}
                >
                  {state === "done" ? "✓" : state === "failed" ? "!" : index + 1}
                </div>
                <strong style={{ marginTop: 8, color: colors.accent, fontSize: 12 }}>{step.label}</strong>
                <span style={{ marginTop: 4, color: "#64748b", fontSize: 11 }}>{step.detail}</span>
              </div>
            </div>
          );
        })}
      </div>

      {status === "FAILED" && (
        <div style={{ marginTop: 20, padding: 12, borderRadius: 8, background: "#fef2f2", color: "#991b1b", fontSize: 13 }}>
          {failureMessage || "Server tidak dapat menyelesaikan analisis. Periksa parameter atau file input."}
        </div>
      )}
      {pollError && status !== "FAILED" && (
        <p style={{ margin: "16px 0 0", color: "#a16207", fontSize: 12 }}>
          Koneksi sempat terganggu. Sistem akan mencoba mengambil status lagi.
        </p>
      )}
    </section>
  );
}
