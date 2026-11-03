import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  cancelJob,
  deleteJob,
  deleteUploadedDataset,
  downloadUploadedDataset,
  fetchUploadedDatasets,
  fetchUserJobs,
  previewUploadedDataset,
  retryJob,
} from "../api/jobsApi";

const JOB_STATUS_BADGE = {
  completed: { text: "Selesai (10/10)", bg: "#dcfce7", color: "#166534" },
  success: { text: "Selesai (10/10)", bg: "#dcfce7", color: "#166534" },
  failed: { text: "Gagal", bg: "#fee2e2", color: "#991b1b" },
  cancelled: { text: "Dihentikan", bg: "#fef3c7", color: "#92400e" },
  pending: { text: "Menunggu", bg: "#f1f5f9", color: "#475569" },
  running: { text: "Sedang Berjalan", bg: "#dbeafe", color: "#1d4ed8" },
  running_inspection: { text: "1. Inspeksi Data", bg: "#dbeafe", color: "#1d4ed8" },
  running_normalization: { text: "2. Normalisasi VCF", bg: "#dbeafe", color: "#1d4ed8" },
  running_qc: { text: "3. Quality Control", bg: "#dbeafe", color: "#1d4ed8" },
  running_imputation: { text: "4. Imputasi", bg: "#dbeafe", color: "#1d4ed8" },
  running_structure: { text: "5. Struktur & LD", bg: "#dbeafe", color: "#1d4ed8" },
  running_gwas: { text: "6. Analisis GWAS", bg: "#dbeafe", color: "#1d4ed8" },
  running_multilocus: { text: "7. Multi-Locus", bg: "#dbeafe", color: "#1d4ed8" },
  running_postgwas: { text: "8. Pasca-GWAS", bg: "#dbeafe", color: "#1d4ed8" },
  running_parsing: { text: "9. Visualisasi", bg: "#dbeafe", color: "#1d4ed8" },
  running_annotation: { text: "10. Anotasi Gen", bg: "#dbeafe", color: "#1d4ed8" },
};

function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function formatDate(isoStr) {
  if (!isoStr) return "-";
  try {
    return new Date(isoStr).toLocaleString("id-ID", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return isoStr;
  }
}

export function WorkspaceExplorer({
  activeDatasetId,
  activeJobId,
  onSelectDataset,
  onDeleteDataset,
  onSelectJob,
  onDeleteJob,
  refreshTrigger = 0,
}) {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [previewDataset, setPreviewDataset] = useState(null);
  const [previewText, setPreviewText] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [confirmDeleteJobId, setConfirmDeleteJobId] = useState(null);
  const [deletingJobId, setDeletingJobId] = useState(null);
  const [cancellingJobId, setCancellingJobId] = useState(null);
  const [retryingJobId, setRetryingJobId] = useState(null);
  const [jobActionError, setJobActionError] = useState(null);

  const loadWorkspaceData = useCallback(async () => {
    try {
      setLoading(true);
      const [dsList, jobList] = await Promise.all([
        fetchUploadedDatasets().catch(() => []),
        fetchUserJobs().catch(() => []),
      ]);
      setDatasets(Array.isArray(dsList) ? dsList : []);
      setJobs(Array.isArray(jobList) ? jobList : []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadWorkspaceData();
  }, [loadWorkspaceData, refreshTrigger]);

  // Auto-refresh daftar job jika ada job yang sedang berjalan
  const hasRunningJobs = jobs.some(
    (j) =>
      !["completed", "success", "failed", "cancelled"].includes(
        String(j.overall_status || "").toLowerCase()
      )
  );

  useEffect(() => {
    if (!hasRunningJobs) return undefined;
    const timer = setInterval(loadWorkspaceData, 3500);
    return () => clearInterval(timer);
  }, [hasRunningJobs, loadWorkspaceData]);

  const handlePreviewDataset = async (ds) => {
    if (previewDataset?.file_id === ds.file_id) {
      setPreviewDataset(null);
      setPreviewText("");
      return;
    }
    setPreviewDataset(ds);
    setPreviewLoading(true);
    setPreviewText("");
    try {
      const text = await previewUploadedDataset(ds.file_id);
      setPreviewText(text || "(File kosong)");
    } catch (err) {
      setPreviewText(
        err.response?.data?.detail || "Tidak dapat memuat pratinjau file."
      );
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleConfirmDelete = async (ds) => {
    setDeleteError(null);
    setDeletingId(ds.file_id);
    try {
      await deleteUploadedDataset(ds.file_id);
      if (previewDataset?.file_id === ds.file_id) {
        setPreviewDataset(null);
        setPreviewText("");
      }
      setConfirmDeleteId(null);
      if (onDeleteDataset) {
        onDeleteDataset(ds.file_id);
      }
      await loadWorkspaceData();
    } catch (err) {
      setDeleteError(
        err.response?.data?.detail ||
          err.message ||
          "Gagal menghapus file dataset."
      );
    } finally {
      setDeletingId(null);
    }
  };

  const handleCancelJob = async (jobId) => {
    setJobActionError(null);
    setCancellingJobId(jobId);
    try {
      await cancelJob(jobId);
      await loadWorkspaceData();
    } catch (err) {
      setJobActionError(
        err.response?.data?.detail || err.message || "Gagal menghentikan job."
      );
    } finally {
      setCancellingJobId(null);
    }
  };

  const handleRetryJob = async (jobId, fromScratch = false) => {
    setJobActionError(null);
    setRetryingJobId(jobId);
    try {
      await retryJob(jobId, { fromScratch });
      await loadWorkspaceData();
      if (onSelectJob) {
        onSelectJob(jobId);
      }
    } catch (err) {
      setJobActionError(
        err.response?.data?.detail || err.message || "Gagal menjalankan ulang job."
      );
    } finally {
      setRetryingJobId(null);
    }
  };

  const handleConfirmDeleteJob = async (jobId) => {
    setJobActionError(null);
    setDeletingJobId(jobId);
    try {
      await deleteJob(jobId);
      setConfirmDeleteJobId(null);
      if (onDeleteJob) {
        onDeleteJob(jobId);
      }
      await loadWorkspaceData();
    } catch (err) {
      setJobActionError(
        err.response?.data?.detail || err.message || "Gagal menghapus job."
      );
    } finally {
      setDeletingJobId(null);
    }
  };

  return (
    <div style={{ display: "grid", gap: 18 }}>
      {/* Panel 1: File Dataset yang Sudah Diunggah */}
      <div
        style={{
          padding: 18,
          border: "1px solid #e2e8f0",
          borderRadius: 12,
          background: "#fff",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 12,
            flexWrap: "wrap",
            gap: 8,
          }}
        >
          <div>
            <h2 style={{ margin: 0, fontSize: 16 }}>
              File VCF yang Sudah Diunggah ({datasets.length})
            </h2>
            <p style={{ margin: "2px 0 0", fontSize: 12, color: "#64748b" }}>
              Pilih kembali file yang pernah diunggah, lihat isinya, atau unduh tanpa perlu upload ulang.
            </p>
          </div>
          <button
            type="button"
            onClick={loadWorkspaceData}
            disabled={loading}
            style={{
              padding: "5px 10px",
              fontSize: 12,
              fontWeight: 600,
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              background: "#f8fafc",
              color: "#334155",
              cursor: "pointer",
            }}
          >
            {loading ? "Memuat..." : "Segarkan"}
          </button>
        </div>

        {deleteError && (
          <div
            role="alert"
            style={{
              marginBottom: 12,
              padding: "8px 12px",
              borderRadius: 8,
              background: "#fef2f2",
              color: "#991b1b",
              fontSize: 12,
            }}
          >
            {deleteError}
          </div>
        )}

        {datasets.length === 0 ? (
          <div
            style={{
              padding: 14,
              borderRadius: 8,
              background: "#f8fafc",
              color: "#64748b",
              fontSize: 12,
              textAlign: "center",
            }}
          >
            Belum ada riwayat file VCF yang diunggah.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: 12,
                textAlign: "left",
              }}
            >
              <thead>
                <tr
                  style={{
                    background: "#f8fafc",
                    color: "#475569",
                    borderBottom: "1px solid #e2e8f0",
                  }}
                >
                  <th style={{ padding: "8px 10px" }}>Nama File</th>
                  <th style={{ padding: "8px 10px" }}>Ukuran</th>
                  <th style={{ padding: "8px 10px" }}>Status VCF</th>
                  <th style={{ padding: "8px 10px" }}>Fenotipe</th>
                  <th style={{ padding: "8px 10px" }}>Anotasi</th>
                  <th style={{ padding: "8px 10px" }}>Waktu Upload</th>
                  <th style={{ padding: "8px 10px", textAlign: "right" }}>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {datasets.map((ds) => {
                  const isSelected = activeDatasetId === ds.file_id;
                  const isPreviewOpen = previewDataset?.file_id === ds.file_id;
                  const isReady =
                    ds.upload_status === "uploaded" || ds.upload_status === "ready";
                  return (
                    <tr
                      key={ds.file_id}
                      style={{
                        borderBottom: "1px solid #f1f5f9",
                        background: isSelected ? "#eff6ff" : "transparent",
                      }}
                    >
                      <td
                        style={{
                          padding: "8px 10px",
                          fontWeight: 600,
                          color: "#0f172a",
                          fontFamily: "monospace",
                        }}
                      >
                        {ds.original_filename}
                      </td>
                      <td style={{ padding: "8px 10px", color: "#64748b" }}>
                        {formatBytes(ds.file_size_bytes)}
                      </td>
                      <td style={{ padding: "8px 10px" }}>
                        <span
                          style={{
                            padding: "2px 8px",
                            borderRadius: 999,
                            fontSize: 11,
                            fontWeight: 600,
                            background: isReady ? "#dcfce7" : "#fef3c7",
                            color: isReady ? "#166534" : "#92400e",
                          }}
                        >
                          {ds.upload_status.toUpperCase()}
                        </span>
                      </td>
                      <td style={{ padding: "8px 10px" }}>
                        {ds.has_phenotype ? (
                          <span
                            title={ds.phenotype_filename}
                            style={{
                              padding: "2px 8px",
                              borderRadius: 6,
                              fontSize: 11,
                              fontWeight: 600,
                              background: "#f0fdf4",
                              color: "#15803d",
                              border: "1px solid #bbf7d0",
                              display: "inline-block",
                              maxWidth: 130,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            ✓ {ds.phenotype_filename || "Terpasang"}
                          </span>
                        ) : (
                          <span
                            style={{
                              padding: "2px 8px",
                              borderRadius: 6,
                              fontSize: 11,
                              fontWeight: 600,
                              background: "#fffbeb",
                              color: "#b45309",
                              border: "1px solid #fde68a",
                            }}
                          >
                            Belum Ada
                          </span>
                        )}
                      </td>
                      <td style={{ padding: "8px 10px" }}>
                        {ds.has_annotation ? (
                          <span
                            title={ds.annotation_filename}
                            style={{
                              padding: "2px 8px",
                              borderRadius: 6,
                              fontSize: 11,
                              fontWeight: 600,
                              background: "#f5f3ff",
                              color: "#6d28d9",
                              border: "1px solid #ddd6fe",
                              display: "inline-block",
                              maxWidth: 110,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            ✓ GFF3
                          </span>
                        ) : (
                          <span style={{ color: "#94a3b8", fontSize: 11 }}>–</span>
                        )}
                      </td>
                      <td style={{ padding: "8px 10px", color: "#64748b" }}>
                        {formatDate(ds.uploaded_at)}
                      </td>
                      <td
                        style={{
                          padding: "8px 10px",
                          textAlign: "right",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {isReady && onSelectDataset && (
                          <button
                            type="button"
                            onClick={() => onSelectDataset(ds.file_id, ds)}
                            style={{
                              marginRight: 6,
                              padding: "4px 9px",
                              fontSize: 11,
                              fontWeight: 700,
                              borderRadius: 6,
                              border: isSelected
                                ? "1px solid #1d4ed8"
                                : "1px solid #cbd5e1",
                              background: isSelected ? "#1d4ed8" : "#fff",
                              color: isSelected ? "#fff" : "#1d4ed8",
                              cursor: "pointer",
                            }}
                          >
                            {isSelected ? "Dipilih" : "Gunakan File"}
                          </button>
                        )}
                        {ds.file_exists && (
                          <>
                            <button
                              type="button"
                              onClick={() => handlePreviewDataset(ds)}
                              style={{
                                marginRight: 6,
                                padding: "4px 8px",
                                fontSize: 11,
                                fontWeight: 600,
                                borderRadius: 6,
                                border: "1px solid #cbd5e1",
                                background: isPreviewOpen ? "#dbeafe" : "#fff",
                                color: "#334155",
                                cursor: "pointer",
                              }}
                            >
                              {isPreviewOpen ? "Tutup Isi" : "Lihat Isi"}
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                downloadUploadedDataset(
                                  ds.file_id,
                                  ds.original_filename
                                )
                              }
                              style={{
                                marginRight: 6,
                                padding: "4px 8px",
                                fontSize: 11,
                                fontWeight: 600,
                                borderRadius: 6,
                                border: "1px solid #cbd5e1",
                                background: "#f8fafc",
                                color: "#0f172a",
                                cursor: "pointer",
                              }}
                            >
                              Unduh
                            </button>
                          </>
                        )}
                        {confirmDeleteId === ds.file_id ? (
                          <span style={{ display: "inline-flex", gap: 4 }}>
                            <button
                              type="button"
                              disabled={deletingId === ds.file_id}
                              onClick={() => handleConfirmDelete(ds)}
                              style={{
                                padding: "4px 8px",
                                fontSize: 11,
                                fontWeight: 700,
                                borderRadius: 6,
                                border: "none",
                                background: "#dc2626",
                                color: "#fff",
                                cursor: "pointer",
                              }}
                            >
                              {deletingId === ds.file_id ? "Menghapus..." : "Ya, Hapus"}
                            </button>
                            <button
                              type="button"
                              disabled={deletingId === ds.file_id}
                              onClick={() => setConfirmDeleteId(null)}
                              style={{
                                padding: "4px 8px",
                                fontSize: 11,
                                fontWeight: 600,
                                borderRadius: 6,
                                border: "1px solid #cbd5e1",
                                background: "#fff",
                                color: "#475569",
                                cursor: "pointer",
                              }}
                            >
                              Batal
                            </button>
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => {
                              setDeleteError(null);
                              setConfirmDeleteId(ds.file_id);
                            }}
                            style={{
                              padding: "4px 8px",
                              fontSize: 11,
                              fontWeight: 600,
                              borderRadius: 6,
                              border: "1px solid #fecaca",
                              background: "#fef2f2",
                              color: "#b91c1c",
                              cursor: "pointer",
                            }}
                          >
                            Hapus
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {previewDataset && (
          <div
            style={{
              marginTop: 12,
              borderRadius: 8,
              background: "#0f172a",
              color: "#e2e8f0",
              padding: 12,
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 8,
              }}
            >
              <strong
                style={{ fontSize: 12, color: "#38bdf8", fontFamily: "monospace" }}
              >
                Pratinjau Dataset: {previewDataset.original_filename}
              </strong>
              <button
                type="button"
                onClick={() => setPreviewDataset(null)}
                style={{
                  padding: "2px 8px",
                  fontSize: 11,
                  borderRadius: 4,
                  border: "1px solid #475569",
                  background: "#1e293b",
                  color: "#f8fafc",
                  cursor: "pointer",
                }}
              >
                Tutup
              </button>
            </div>
            <pre
              style={{
                margin: 0,
                maxHeight: 220,
                overflow: "auto",
                padding: 10,
                borderRadius: 6,
                background: "#020617",
                fontSize: 11,
                lineHeight: 1.5,
                whiteSpace: "pre",
              }}
            >
              {previewLoading ? "Memuat isi file VCF..." : previewText}
            </pre>
          </div>
        )}
      </div>

      {/* Panel 2: Daftar Job yang Sedang Berjalan & Riwayat Job */}
      <div
        style={{
          padding: 18,
          border: "1px solid #e2e8f0",
          borderRadius: 12,
          background: "#fff",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 12,
            flexWrap: "wrap",
            gap: 8,
          }}
        >
          <div>
            <h2 style={{ margin: 0, fontSize: 16 }}>
              Daftar Job Berjalan & Riwayat Analisis ({jobs.length})
            </h2>
            <p style={{ margin: "2px 0 0", fontSize: 12, color: "#64748b" }}>
              Klik &ldquo;Pantau &amp; File&rdquo; untuk melihat progres, log, serta mengakses seluruh file pada job yang sedang berjalan maupun selesai.
            </p>
          </div>
          {hasRunningJobs && (
            <span
              style={{
                fontSize: 11,
                padding: "3px 10px",
                borderRadius: 999,
                background: "#dbeafe",
                color: "#1d4ed8",
                fontWeight: 700,
              }}
            >
              Job Aktif Sedang Diproses
            </span>
          )}
        </div>

        {jobActionError && (
          <div
            role="alert"
            style={{
              marginBottom: 12,
              padding: "8px 12px",
              borderRadius: 8,
              background: "#fef2f2",
              color: "#991b1b",
              fontSize: 12,
            }}
          >
            {jobActionError}
          </div>
        )}

        {jobs.length === 0 ? (
          <div
            style={{
              padding: 14,
              borderRadius: 8,
              background: "#f8fafc",
              color: "#64748b",
              fontSize: 12,
              textAlign: "center",
            }}
          >
            Belum ada job analisis yang dijalankan.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: 12,
                textAlign: "left",
              }}
            >
              <thead>
                <tr
                  style={{
                    background: "#f8fafc",
                    color: "#475569",
                    borderBottom: "1px solid #e2e8f0",
                  }}
                >
                  <th style={{ padding: "8px 10px" }}>Dataset</th>
                  <th style={{ padding: "8px 10px" }}>Status & Tahap</th>
                  <th style={{ padding: "8px 10px" }}>File</th>
                  <th style={{ padding: "8px 10px" }}>Durasi</th>
                  <th style={{ padding: "8px 10px" }}>Waktu</th>
                  <th style={{ padding: "8px 10px", textAlign: "right" }}>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const statusKey = String(job.overall_status || "").toLowerCase();
                  const badge =
                    JOB_STATUS_BADGE[statusKey] || JOB_STATUS_BADGE.running;
                  const isCompleted =
                    statusKey === "completed" || statusKey === "success";
                  const isTerminalJob = [
                    "completed",
                    "success",
                    "failed",
                    "cancelled",
                  ].includes(statusKey);
                  const isActiveJob = activeJobId === job.job_id;
                  return (
                    <tr
                      key={job.job_id}
                      style={{
                        borderBottom: "1px solid #f1f5f9",
                        background: isActiveJob ? "#eff6ff" : "transparent",
                      }}
                    >
                      <td
                        style={{
                          padding: "8px 10px",
                          fontWeight: 600,
                          color: "#0f172a",
                          fontFamily: "monospace",
                        }}
                      >
                        {job.dataset_filename}
                      </td>
                      <td style={{ padding: "8px 10px" }}>
                        <span
                          style={{
                            padding: "2px 8px",
                            borderRadius: 999,
                            fontSize: 11,
                            fontWeight: 600,
                            background: badge.bg,
                            color: badge.color,
                          }}
                        >
                          {badge.text}
                        </span>
                      </td>
                      <td style={{ padding: "8px 10px", color: "#475569" }}>
                        {job.file_count} file
                      </td>
                      <td style={{ padding: "8px 10px", color: "#64748b" }}>
                        {job.elapsed_seconds != null
                          ? `${job.elapsed_seconds}s`
                          : "-"}
                      </td>
                      <td style={{ padding: "8px 10px", color: "#64748b" }}>
                        {formatDate(job.created_at)}
                      </td>
                      <td
                        style={{
                          padding: "8px 10px",
                          textAlign: "right",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {onSelectJob && (
                          <button
                            type="button"
                            onClick={() => onSelectJob(job.job_id)}
                            style={{
                              marginRight: 6,
                              padding: "4px 9px",
                              fontSize: 11,
                              fontWeight: 700,
                              borderRadius: 6,
                              border: isActiveJob
                                ? "1px solid #1d4ed8"
                                : "1px solid #cbd5e1",
                              background: isActiveJob ? "#1d4ed8" : "#fff",
                              color: isActiveJob ? "#fff" : "#1d4ed8",
                              cursor: "pointer",
                            }}
                          >
                            {isActiveJob ? "Sedang Dipantau" : "Pantau & File"}
                          </button>
                        )}
                        {isCompleted && (
                          <button
                            type="button"
                            onClick={() => navigate(`/jobs/${job.job_id}/results`)}
                            style={{
                              marginRight: 6,
                              padding: "4px 9px",
                              fontSize: 11,
                              fontWeight: 700,
                              borderRadius: 6,
                              border: "none",
                              background: "#10b981",
                              color: "#fff",
                              cursor: "pointer",
                            }}
                          >
                            Buka Hasil
                          </button>
                        )}
                        {(statusKey === "failed" || statusKey === "cancelled") && (
                          <button
                            type="button"
                            disabled={retryingJobId === job.job_id}
                            onClick={() => handleRetryJob(job.job_id, false)}
                            title="Lanjutkan job dari tahap terakhir yang terhenti"
                            style={{
                              marginRight: 6,
                              padding: "4px 9px",
                              fontSize: 11,
                              fontWeight: 700,
                              borderRadius: 6,
                              border: "1px solid #2563eb",
                              background: "#2563eb",
                              color: "#fff",
                              cursor: retryingJobId === job.job_id ? "not-allowed" : "pointer",
                            }}
                          >
                            {retryingJobId === job.job_id ? "Memulai..." : "↻ Lanjutkan (Retry)"}
                          </button>
                        )}
                        {!isTerminalJob && (
                          <button
                            type="button"
                            disabled={cancellingJobId === job.job_id}
                            onClick={() => handleCancelJob(job.job_id)}
                            style={{
                              marginRight: 6,
                              padding: "4px 8px",
                              fontSize: 11,
                              fontWeight: 700,
                              borderRadius: 6,
                              border: "1px solid #fcd34d",
                              background: "#fffbeb",
                              color: "#b45309",
                              cursor: "pointer",
                            }}
                          >
                            {cancellingJobId === job.job_id
                              ? "Menghentikan..."
                              : "Hentikan"}
                          </button>
                        )}
                        {confirmDeleteJobId === job.job_id ? (
                          <span style={{ display: "inline-flex", gap: 4 }}>
                            <button
                              type="button"
                              disabled={deletingJobId === job.job_id}
                              onClick={() => handleConfirmDeleteJob(job.job_id)}
                              style={{
                                padding: "4px 8px",
                                fontSize: 11,
                                fontWeight: 700,
                                borderRadius: 6,
                                border: "none",
                                background: "#dc2626",
                                color: "#fff",
                                cursor: "pointer",
                              }}
                            >
                              {deletingJobId === job.job_id
                                ? "Menghapus..."
                                : "Ya, Hapus"}
                            </button>
                            <button
                              type="button"
                              disabled={deletingJobId === job.job_id}
                              onClick={() => setConfirmDeleteJobId(null)}
                              style={{
                                padding: "4px 8px",
                                fontSize: 11,
                                fontWeight: 600,
                                borderRadius: 6,
                                border: "1px solid #cbd5e1",
                                background: "#fff",
                                color: "#475569",
                                cursor: "pointer",
                              }}
                            >
                              Batal
                            </button>
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => {
                              setJobActionError(null);
                              setConfirmDeleteJobId(job.job_id);
                            }}
                            style={{
                              padding: "4px 8px",
                              fontSize: 11,
                              fontWeight: 600,
                              borderRadius: 6,
                              border: "1px solid #fecaca",
                              background: "#fef2f2",
                              color: "#b91c1c",
                              cursor: "pointer",
                            }}
                          >
                            Hapus
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

export default WorkspaceExplorer;
