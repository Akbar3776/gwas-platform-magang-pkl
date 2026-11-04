import React, { useEffect, useState } from "react";
import { uploadAnnotation, deleteAnnotation } from "../api/jobsApi";

export function AnnotationUploader({
  datasetId,
  initialAnnotationFilename,
  onAnnotationChanged,
}) {
  const [filename, setFilename] = useState(initialAnnotationFilename || null);
  const [geneCount, setGeneCount] = useState(null);
  const [seqids, setSeqids] = useState([]);
  const [isUploading, setIsUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState(null);

  useEffect(() => {
    setFilename(initialAnnotationFilename || null);
    setGeneCount(null);
    setSeqids([]);
    if (onAnnotationChanged) {
      onAnnotationChanged(initialAnnotationFilename ? { filename: initialAnnotationFilename } : null);
    }
  }, [datasetId, initialAnnotationFilename]);

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file || !datasetId) return;

    setIsUploading(true);
    setProgress(0);
    setError(null);
    try {
      const res = await uploadAnnotation(datasetId, file, (p) => setProgress(p));
      setFilename(res.filename);
      setGeneCount(res.n_genes);
      setSeqids(res.seqids || []);
      if (onAnnotationChanged) onAnnotationChanged(res);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || "Gagal mengunggah file anotasi GFF3.");
    } finally {
      setIsUploading(false);
    }
  };

  const handleDelete = async () => {
    if (!datasetId) return;
    if (!window.confirm("Hapus file anotasi GFF3 dari dataset ini?")) return;

    try {
      await deleteAnnotation(datasetId);
      setFilename(null);
      setGeneCount(null);
      setSeqids([]);
      if (onAnnotationChanged) onAnnotationChanged(null);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || "Gagal menghapus file anotasi.");
    }
  };

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {!filename ? (
        <div
          style={{
            border: "1px dashed #cbd5e1",
            borderRadius: 8,
            padding: "14px 16px",
            background: "#f8fafc",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: 10,
          }}
        >
          <div>
            <strong style={{ fontSize: 13, color: "#334155" }}>File Anotasi Genom GFF3 (Opsional)</strong>
            <p style={{ margin: "2px 0 0", fontSize: 11, color: "#64748b" }}>
              Diperlukan untuk memetakan Lead SNP ke gen terdekat dan klasifikasi Genik/Near-gen (Fase 5).
            </p>
          </div>
          <div>
            <input
              type="file"
              id="gff-file-input"
              accept=".gff3,.gff,.gff3.gz"
              onChange={handleUpload}
              disabled={isUploading || !datasetId}
              style={{ display: "none" }}
            />
            <label
              htmlFor="gff-file-input"
              style={{
                display: "inline-block",
                padding: "6px 14px",
                background: datasetId ? "#475569" : "#94a3b8",
                color: "#fff",
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 600,
                cursor: datasetId ? "pointer" : "not-allowed",
              }}
            >
              {isUploading ? `Mengunggah (${progress}%)...` : "Pilih File GFF3"}
            </label>
          </div>
        </div>
      ) : (
        <div
          style={{
            padding: 12,
            background: "#f8fafc",
            borderRadius: 8,
            border: "1px solid #e2e8f0",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 10,
          }}
        >
          <div>
            <span style={{ fontSize: 11, fontWeight: 700, color: "#1e40af", background: "#dbeafe", padding: "2px 6px", borderRadius: 4 }}>
              GFF3 Terunggah
            </span>
            <strong style={{ marginLeft: 8, fontSize: 13, color: "#0f172a" }}>{filename}</strong>
            {geneCount !== null && (
              <span style={{ marginLeft: 8, fontSize: 12, color: "#64748b" }}>
                ({geneCount.toLocaleString("id-ID")} fitur gen terdeteksi{seqids.length > 0 ? ` pada ${seqids.length} kromosom` : ""})
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={handleDelete}
            style={{
              padding: "4px 8px",
              fontSize: 12,
              borderRadius: 4,
              border: "1px solid #fca5a5",
              background: "#fef2f2",
              color: "#b91c1c",
              cursor: "pointer",
            }}
          >
            Hapus GFF3
          </button>
        </div>
      )}

      {error && (
        <div style={{ padding: 8, borderRadius: 6, background: "#fef2f2", color: "#991b1b", fontSize: 12 }}>
          {error}
        </div>
      )}
    </div>
  );
}
