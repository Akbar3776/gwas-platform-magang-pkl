import { useCallback, useEffect, useState } from "react";
import { fetchJobLogs } from "../api/jobsApi";

const DEFAULT_STEPS = [
  { step: "inspection", label: "Inspeksi Fenotipe & Sampel", tool: "Python / Stats", status: "pending", line_count: 0, log_text: "" },
  { step: "normalization", label: "Normalisasi VCF", tool: "bcftools", status: "pending", line_count: 0, log_text: "" },
  { step: "qc", label: "Quality Control", tool: "PLINK 2 / 1.9", status: "pending", line_count: 0, log_text: "" },
  { step: "imputation", label: "Imputasi Genotipe", tool: "Beagle 5.4", status: "pending", line_count: 0, log_text: "" },
  { step: "structure", label: "Struktur Populasi & LD", tool: "PCA / Centered Kinship", status: "pending", line_count: 0, log_text: "" },
  { step: "gwas", label: "Analisis Asosiasi GWAS", tool: "GEMMA / GLM", status: "pending", line_count: 0, log_text: "" },
  { step: "multilocus", label: "Multi-Locus GWAS", tool: "FarmCPU / BLINK / MLMM", status: "pending", line_count: 0, log_text: "" },
  { step: "postgwas", label: "Pasca-GWAS & Clumping", tool: "Dynamic Thresholds", status: "pending", line_count: 0, log_text: "" },
  { step: "visualization", label: "Visualisasi & Cache Plot", tool: "Parquet Engine", status: "pending", line_count: 0, log_text: "" },
  { step: "annotation", label: "Anotasi Gen Kandidat", tool: "GFF3 Parser", status: "pending", line_count: 0, log_text: "" },
];

const STATUS_BADGE = {
  done: { text: "Selesai", bg: "#dcfce7", color: "#166534", dot: "#22c55e" },
  active: { text: "Sedang Berjalan", bg: "#dbeafe", color: "#1d4ed8", dot: "#3b82f6" },
  failed: { text: "Gagal", bg: "#fee2e2", color: "#991b1b", dot: "#ef4444" },
  cancelled: { text: "Dihentikan", bg: "#fef3c7", color: "#92400e", dot: "#f59e0b" },
  skipped: { text: "Dilewati", bg: "#f1f5f9", color: "#64748b", dot: "#94a3b8" },
  pending: { text: "Menunggu", bg: "#f8fafc", color: "#64748b", dot: "#cbd5e1" },
};

export function JobProcessLogs({
  jobId,
  stepLogs: externalStepLogs,
  selectedStep: controlledStep,
  onSelectStep,
  elapsedSeconds: externalElapsed,
  defaultExpanded = true,
}) {
  const [fetchedLogs, setFetchedLogs] = useState(null);
  const [fetchedElapsed, setFetchedElapsed] = useState(null);
  const [internalTab, setInternalTab] = useState("all");
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  const [isFullHeight, setIsFullHeight] = useState(false);
  const [copied, setCopied] = useState(false);
  const [loadingLogs, setLoadingLogs] = useState(false);

  const activeTab = controlledStep || internalTab;
  const setActiveTab = (tab) => {
    setInternalTab(tab);
    if (onSelectStep) {
      onSelectStep(tab);
    }
  };

  const loadLogsFromServer = useCallback(async () => {
    if (!jobId) return;
    try {
      setLoadingLogs(true);
      const res = await fetchJobLogs(jobId);
      if (res) {
        setFetchedLogs(res.step_logs || []);
        setFetchedElapsed(res.elapsed_seconds ?? null);
      }
    } catch {
      // Abaikan jika job sedang diinisialisasi
    } finally {
      setLoadingLogs(false);
    }
  }, [jobId]);

  useEffect(() => {
    if (externalStepLogs !== undefined) {
      return undefined;
    }
    loadLogsFromServer();
    return undefined;
  }, [externalStepLogs, loadLogsFromServer]);

  const steps =
    externalStepLogs && externalStepLogs.length > 0
      ? externalStepLogs
      : fetchedLogs && fetchedLogs.length > 0
      ? fetchedLogs
      : DEFAULT_STEPS;

  const elapsed = externalElapsed ?? fetchedElapsed;
  const visibleSteps =
    activeTab === "all" ? steps : steps.filter((s) => s.step === activeTab);

  const buildCombinedLogText = () =>
    visibleSteps
      .map((s) => {
        const stageNum = steps.findIndex((item) => item.step === s.step) + 1;
        return (
          `# [Tahap ${stageNum}] ${s.label} (${s.tool}) - ${s.status.toUpperCase()}\n` +
          (s.log_text || "(Belum ada output log pada tahap ini)")
        );
      })
      .join("\n\n");

  const handleCopy = () => {
    const content = buildCombinedLogText();
    if (navigator.clipboard) {
      navigator.clipboard.writeText(content).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1800);
      });
    }
  };

  const handleDownloadLog = () => {
    const content = buildCombinedLogText();
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `gwas_job_${jobId || "pipeline"}_${activeTab}_logs.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  };

  return (
    <div
      style={{
        marginTop: 18,
        border: "1px solid #cbd5e1",
        borderRadius: 10,
        overflow: "hidden",
        background: "#0f172a",
        color: "#e2e8f0",
      }}
    >
      {/* Header Bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "10px 14px",
          background: "#1e293b",
          borderBottom: isExpanded ? "1px solid #334155" : "none",
          flexWrap: "wrap",
          gap: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "#38bdf8",
            }}
          />
          <strong style={{ fontSize: 13, color: "#f8fafc", letterSpacing: "0.02em" }}>
            Log Proses Eksekusi Per Tahap
          </strong>
          {elapsed != null && (
            <span
              style={{
                fontSize: 11,
                padding: "2px 8px",
                borderRadius: 999,
                background: "#334155",
                color: "#bae6fd",
              }}
            >
              Durasi: {elapsed}s
            </span>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          {isExpanded && (
            <>
              {jobId && (
                <button
                  type="button"
                  onClick={loadLogsFromServer}
                  disabled={loadingLogs}
                  style={{
                    padding: "4px 10px",
                    fontSize: 11,
                    fontWeight: 600,
                    borderRadius: 6,
                    border: "1px solid #475569",
                    background: "#0f172a",
                    color: "#e2e8f0",
                    cursor: "pointer",
                  }}
                >
                  {loadingLogs ? "Memuat..." : "Segarkan Log"}
                </button>
              )}
              <button
                type="button"
                onClick={() => setIsFullHeight((prev) => !prev)}
                style={{
                  padding: "4px 10px",
                  fontSize: 11,
                  fontWeight: 600,
                  borderRadius: 6,
                  border: "1px solid #475569",
                  background: "#0f172a",
                  color: "#e2e8f0",
                  cursor: "pointer",
                }}
              >
                {isFullHeight ? "Ukuran Kompak" : "Tampilkan Penuh"}
              </button>
              <button
                type="button"
                onClick={handleCopy}
                style={{
                  padding: "4px 10px",
                  fontSize: 11,
                  fontWeight: 600,
                  borderRadius: 6,
                  border: "1px solid #475569",
                  background: "#0f172a",
                  color: "#e2e8f0",
                  cursor: "pointer",
                }}
              >
                {copied ? "Tersalin!" : "Salin Log"}
              </button>
              <button
                type="button"
                onClick={handleDownloadLog}
                style={{
                  padding: "4px 10px",
                  fontSize: 11,
                  fontWeight: 600,
                  borderRadius: 6,
                  border: "1px solid #0284c7",
                  background: "#0284c7",
                  color: "#f8fafc",
                  cursor: "pointer",
                }}
              >
                Unduh Log (.txt)
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => setIsExpanded((prev) => !prev)}
            style={{
              padding: "4px 10px",
              fontSize: 11,
              fontWeight: 600,
              borderRadius: 6,
              border: "1px solid #475569",
              background: "#334155",
              color: "#f8fafc",
              cursor: "pointer",
            }}
          >
            {isExpanded ? "Sembunyikan" : "Tampilkan Log"}
          </button>
        </div>
      </div>

      {isExpanded && (
        <>
          {/* Stage Filter Tabs */}
          <div
            style={{
              display: "flex",
              gap: 6,
              padding: "8px 12px",
              background: "#0f172a",
              borderBottom: "1px solid #1e293b",
              overflowX: "auto",
            }}
          >
            <button
              type="button"
              onClick={() => setActiveTab("all")}
              style={{
                padding: "5px 10px",
                fontSize: 12,
                fontWeight: 600,
                borderRadius: 6,
                border: activeTab === "all" ? "1px solid #38bdf8" : "1px solid #334155",
                background: activeTab === "all" ? "#0284c7" : "#1e293b",
                color: "#f8fafc",
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              Semua Tahap ({steps.length})
            </button>

            {steps.map((item, idx) => {
              const badge = STATUS_BADGE[item.status] || STATUS_BADGE.pending;
              const isSelected = activeTab === item.step;
              return (
                <button
                  key={item.step}
                  type="button"
                  onClick={() => setActiveTab(item.step)}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "5px 10px",
                    fontSize: 12,
                    fontWeight: 600,
                    borderRadius: 6,
                    border: isSelected ? "1px solid #38bdf8" : "1px solid #334155",
                    background: isSelected ? "#0284c7" : "#1e293b",
                    color: "#f8fafc",
                    cursor: "pointer",
                    whiteSpace: "nowrap",
                  }}
                >
                  <span
                    style={{
                      width: 7,
                      height: 7,
                      borderRadius: "50%",
                      background: badge.dot,
                    }}
                  />
                  <span>
                    {idx + 1}. {item.label}
                  </span>
                  {item.line_count > 0 && (
                    <span
                      style={{
                        fontSize: 10,
                        padding: "1px 5px",
                        borderRadius: 999,
                        background: "rgba(15, 23, 42, 0.55)",
                        color: "#cbd5e1",
                      }}
                    >
                      {item.line_count} baris
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* Stage Log Content */}
          <div
            style={{
              maxHeight: isFullHeight ? "none" : 520,
              overflowY: isFullHeight ? "visible" : "auto",
              padding: 12,
              display: "grid",
              gap: 12,
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
              fontSize: 12,
            }}
          >
            {visibleSteps.map((item) => {
              const badge = STATUS_BADGE[item.status] || STATUS_BADGE.pending;
              const stepIdx = steps.findIndex((s) => s.step === item.step) + 1;
              return (
                <div
                  key={item.step}
                  style={{
                    border: "1px solid #1e293b",
                    borderRadius: 8,
                    background: "#020617",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "6px 12px",
                      background: "#0f172a",
                      borderBottom: "1px solid #1e293b",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <strong style={{ color: "#f8fafc" }}>
                        Tahap {stepIdx}: {item.label}
                      </strong>
                      <span
                        style={{
                          fontSize: 11,
                          padding: "1px 6px",
                          borderRadius: 4,
                          background: "#1e293b",
                          color: "#94a3b8",
                        }}
                      >
                        {item.tool}
                      </span>
                    </div>

                    <span
                      style={{
                        fontSize: 11,
                        fontWeight: 700,
                        padding: "2px 8px",
                        borderRadius: 999,
                        background: badge.bg,
                        color: badge.color,
                      }}
                    >
                      {badge.text}
                    </span>
                  </div>

                  <pre
                    style={{
                      margin: 0,
                      padding: 12,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      color: item.log_text ? "#e2e8f0" : "#64748b",
                      lineHeight: 1.55,
                    }}
                  >
                    {item.log_text ||
                      (item.status === "skipped"
                        ? "Tahap ini tidak dijalankan untuk tipe pipeline yang dipilih."
                        : item.status === "pending"
                        ? "Menunggu tahap sebelumnya selesai..."
                        : "Memulai eksekusi proses...")}
                  </pre>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
export default JobProcessLogs;
