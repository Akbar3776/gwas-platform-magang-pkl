import { useCallback, useEffect, useState } from "react";
import { downloadJobFile, fetchJobFiles, previewJobFile } from "../api/jobsApi";

const STAGE_BADGES = {
  input: { bg: "#e0e7ff", color: "#3730a3", label: "Input Dataset" },
  inspection: { bg: "#f0fdf4", color: "#15803d", label: "1. Inspeksi Data" },
  normalization: { bg: "#dcfce7", color: "#166534", label: "2. Normalisasi VCF" },
  qc: { bg: "#dbeafe", color: "#1e40af", label: "3. Quality Control" },
  imputation: { bg: "#fef3c7", color: "#92400e", label: "4. Imputasi" },
  structure: { bg: "#ede9fe", color: "#5b21b6", label: "5. Struktur & LD" },
  gwas: { bg: "#fae8ff", color: "#86198f", label: "6. Analisis GWAS" },
  multilocus: { bg: "#fce7f3", color: "#9d174d", label: "7. Multi-Locus" },
  postgwas: { bg: "#ffedd5", color: "#9a3412", label: "8. Pasca-GWAS" },
  visualization: { bg: "#cffafe", color: "#155e75", label: "9. Visualisasi" },
  annotation: { bg: "#ecfccb", color: "#3f6212", label: "10. Anotasi Gen" },
};

const STAGE_TABS = [
  { key: "all", label: "Semua File" },
  { key: "input", label: "Input Dataset" },
  { key: "inspection", label: "1. Inspeksi Data" },
  { key: "normalization", label: "2. Normalisasi VCF" },
  { key: "qc", label: "3. QC (PLINK)" },
  { key: "imputation", label: "4. Imputasi" },
  { key: "structure", label: "5. Struktur & LD" },
  { key: "gwas", label: "6. GWAS (GEMMA)" },
  { key: "multilocus", label: "7. Multi-Locus" },
  { key: "postgwas", label: "8. Pasca-GWAS" },
  { key: "visualization", label: "9. Visualisasi" },
  { key: "annotation", label: "10. Anotasi GFF3" },
];

function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export function JobFilesPanel({ jobId, isRunning = false }) {
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [stageFilter, setStageFilter] = useState("all");
  const [previewItem, setPreviewItem] = useState(null);
  const [previewText, setPreviewText] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [downloadingKey, setDownloadingKey] = useState(null);

  const loadFiles = useCallback(async () => {
    if (!jobId) return;
    try {
      setLoading(true);
      const res = await fetchJobFiles(jobId);
      if (res && Array.isArray(res.files)) {
        setFiles(res.files);
      }
    } catch {
      // Abaikan jika job belum memiliki direktori
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    loadFiles();
    if (!isRunning) return undefined;
    const interval = setInterval(loadFiles, 3000);
    return () => clearInterval(interval);
  }, [loadFiles, isRunning]);

  const handlePreview = async (item) => {
    if (previewItem?.file_key === item.file_key) {
      setPreviewItem(null);
      setPreviewText("");
      return;
    }
    setPreviewItem(item);
    setPreviewLoading(true);
    setPreviewText("");
    try {
      const content = await previewJobFile(jobId, item.file_key);
      setPreviewText(content || "(File kosong)");
    } catch (err) {
      setPreviewText(
        err.response?.data?.detail || "Tidak dapat memuat pratinjau file."
      );
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleDownload = async (item) => {
    setDownloadingKey(item.file_key);
    try {
      await downloadJobFile(jobId, item.file_key, item.filename);
    } finally {
      setDownloadingKey(null);
    }
  };

  const filteredFiles =
    stageFilter === "all"
      ? files
      : files.filter((f) => f.stage === stageFilter);

  return (
    <div
      style={{
        marginTop: 16,
        border: "1px solid #cbd5e1",
        borderRadius: 10,
        background: "#fff",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "10px 14px",
          background: "#f8fafc",
          borderBottom: "1px solid #e2e8f0",
          flexWrap: "wrap",
          gap: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <strong style={{ fontSize: 13, color: "#0f172a" }}>
            File Job & Output Setiap Tahap ({files.length} file)
          </strong>
          {isRunning && (
            <span
              style={{
                fontSize: 11,
                padding: "2px 8px",
                borderRadius: 999,
                background: "#dbeafe",
                color: "#1d4ed8",
                fontWeight: 600,
              }}
            >
              Live Update
            </span>
          )}
        </div>

        <button
          type="button"
          onClick={loadFiles}
          disabled={loading}
          style={{
            padding: "4px 10px",
            fontSize: 11,
            fontWeight: 600,
            borderRadius: 6,
            border: "1px solid #cbd5e1",
            background: "#fff",
            color: "#334155",
            cursor: "pointer",
          }}
        >
          {loading ? "Memuat..." : "Segarkan Daftar File"}
        </button>
      </div>

      {/* Filter Stage */}
      <div
        style={{
          display: "flex",
          gap: 6,
          padding: "8px 12px",
          borderBottom: "1px solid #f1f5f9",
          overflowX: "auto",
          background: "#fff",
        }}
      >
        {STAGE_TABS.map((tab) => {
          const count =
            tab.key === "all"
              ? files.length
              : files.filter((f) => f.stage === tab.key).length;
          return (
            <button
              key={tab.key}
              type="button"
              onClick={() => setStageFilter(tab.key)}
              style={{
                padding: "4px 10px",
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 6,
                border:
                  stageFilter === tab.key
                    ? "1px solid #2563eb"
                    : "1px solid #e2e8f0",
                background: stageFilter === tab.key ? "#eff6ff" : "#f8fafc",
                color: stageFilter === tab.key ? "#1d4ed8" : "#475569",
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              {tab.label} ({count})
            </button>
          );
        })}
      </div>

      {/* File Table */}
      {filteredFiles.length === 0 ? (
        <div style={{ padding: 16, fontSize: 12, color: "#64748b", textAlign: "center" }}>
          Belum ada file pada kategori ini.
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
              <tr style={{ background: "#f8fafc", color: "#475569", borderBottom: "1px solid #e2e8f0" }}>
                <th style={{ padding: "8px 12px" }}>Nama File</th>
                <th style={{ padding: "8px 12px" }}>Tahap</th>
                <th style={{ padding: "8px 12px" }}>Ukuran</th>
                <th style={{ padding: "8px 12px", textAlign: "right" }}>Aksi</th>
              </tr>
            </thead>
            <tbody>
              {filteredFiles.map((item) => {
                const badge = STAGE_BADGES[item.stage] || STAGE_BADGES.input;
                const isPreviewOpen = previewItem?.file_key === item.file_key;
                return (
                  <tr
                    key={item.file_key}
                    style={{ borderBottom: "1px solid #f1f5f9" }}
                  >
                    <td style={{ padding: "8px 12px", fontWeight: 600, color: "#0f172a", fontFamily: "monospace" }}>
                      {item.filename}
                    </td>
                    <td style={{ padding: "8px 12px" }}>
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
                        {badge.label}
                      </span>
                    </td>
                    <td style={{ padding: "8px 12px", color: "#64748b" }}>
                      {formatBytes(item.size_bytes)}
                    </td>
                    <td style={{ padding: "8px 12px", textAlign: "right", whiteSpace: "nowrap" }}>
                      {item.previewable && (
                        <button
                          type="button"
                          onClick={() => handlePreview(item)}
                          style={{
                            marginRight: 6,
                            padding: "4px 8px",
                            fontSize: 11,
                            fontWeight: 600,
                            borderRadius: 6,
                            border: "1px solid #cbd5e1",
                            background: isPreviewOpen ? "#dbeafe" : "#fff",
                            color: isPreviewOpen ? "#1d4ed8" : "#334155",
                            cursor: "pointer",
                          }}
                        >
                          {isPreviewOpen ? "Tutup Pratinjau" : "Lihat Isi"}
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => handleDownload(item)}
                        disabled={downloadingKey === item.file_key}
                        style={{
                          padding: "4px 8px",
                          fontSize: 11,
                          fontWeight: 600,
                          borderRadius: 6,
                          border: "none",
                          background: "#2563eb",
                          color: "#fff",
                          cursor: "pointer",
                        }}
                      >
                        {downloadingKey === item.file_key ? "Mengunduh..." : "Unduh"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Inline File Preview */}
      {previewItem && (
        <div
          style={{
            borderTop: "1px solid #cbd5e1",
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
            <strong style={{ fontSize: 12, color: "#38bdf8", fontFamily: "monospace" }}>
              Pratinjau: {previewItem.filename} (maks. 150 baris pertama)
            </strong>
            <button
              type="button"
              onClick={() => setPreviewItem(null)}
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
              maxHeight: 260,
              overflow: "auto",
              padding: 10,
              borderRadius: 6,
              background: "#020617",
              fontSize: 11,
              lineHeight: 1.5,
              whiteSpace: "pre",
            }}
          >
            {previewLoading ? "Memuat isi file..." : previewText}
          </pre>
        </div>
      )}
    </div>
  );
}

export default JobFilesPanel;
