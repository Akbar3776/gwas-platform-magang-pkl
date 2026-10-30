import React, { useEffect, useState } from "react";
import { uploadPhenotype, deletePhenotype, fetchInspection } from "../api/jobsApi";

export function PhenotypeUploader({
  datasetId,
  initialPhenotypeFilename,
  initialColumns = [],
  initialNumericTraits = [],
  onInspectionReady,
  onPhenotypeCleared,
}) {
  const [isUploading, setIsUploading] = useState(false);
  const [isInspecting, setIsInspecting] = useState(false);
  const [error, setError] = useState(null);

  const [filename, setFilename] = useState(initialPhenotypeFilename || null);
  const [columns, setColumns] = useState(initialColumns);
  const [numericTraits, setNumericTraits] = useState(initialNumericTraits);
  const [previewRows, setPreviewRows] = useState([]);

  const [selectedIdCol, setSelectedIdCol] = useState("IID");
  const [selectedTrait, setSelectedTrait] = useState("");

  // Sync initial props
  useEffect(() => {
    if (initialPhenotypeFilename) {
      setFilename(initialPhenotypeFilename);
      setColumns(initialColumns);
      setNumericTraits(initialNumericTraits);
      if (initialColumns && initialColumns.length > 0) {
        const autoId = initialColumns.find((c) => c.toUpperCase() === "IID") || initialColumns[0] || "IID";
        setSelectedIdCol(autoId);
      }
      if (initialNumericTraits && initialNumericTraits.length > 0) {
        setSelectedTrait((curr) => curr || initialNumericTraits[0]);
      }
    } else {
      setFilename(null);
      setColumns([]);
      setNumericTraits([]);
      setSelectedTrait("");
    }
  }, [initialPhenotypeFilename, initialColumns, initialNumericTraits]);

  // Run inspection whenever trait or ID col changes
  useEffect(() => {
    if (!datasetId || !filename || !selectedTrait) return;
    let isCancelled = false;

    async function runCheck() {
      setIsInspecting(true);
      setError(null);
      try {
        const report = await fetchInspection(datasetId, {
          trait: selectedTrait,
          idColumn: selectedIdCol,
        });
        if (!isCancelled && onInspectionReady) {
          onInspectionReady(report, selectedTrait, selectedIdCol);
        }
      } catch (err) {
        if (!isCancelled) {
          setError(err.response?.data?.detail || err.message || "Gagal melakukan inspeksi data fenotipe.");
        }
      } finally {
        if (!isCancelled) setIsInspecting(false);
      }
    }

    runCheck();
    return () => {
      isCancelled = true;
    };
  }, [datasetId, filename, selectedTrait, selectedIdCol, onInspectionReady]);

  const handleUpload = async (e) => {
    const selectedFile = e.target.files?.[0];
    if (!selectedFile || !datasetId) return;

    setIsUploading(true);
    setError(null);
    try {
      const res = await uploadPhenotype(datasetId, selectedFile);
      setFilename(res.filename);
      setColumns(res.columns || []);
      setNumericTraits(res.numeric_columns || []);
      setPreviewRows(res.preview || []);

      // Auto-select ID column if IID / ID present
      const autoId = res.columns.find((c) => c.toUpperCase() === "IID") || res.columns[0] || "IID";
      setSelectedIdCol(autoId);

      // Auto-select first candidate numeric trait
      const autoTrait = res.numeric_columns?.[0] || (res.columns.length >= 3 ? res.columns[2] : "");
      setSelectedTrait(autoTrait);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || "Gagal mengunggah file fenotipe.");
    } finally {
      setIsUploading(false);
    }
  };

  const handleDelete = async () => {
    if (!datasetId) return;
    if (!window.confirm("Hapus file fenotipe yang terpasang pada dataset ini?")) return;

    try {
      await deletePhenotype(datasetId);
      setFilename(null);
      setColumns([]);
      setNumericTraits([]);
      setPreviewRows([]);
      setSelectedTrait("");
      if (onPhenotypeCleared) onPhenotypeCleared();
    } catch (err) {
      setError(err.response?.data?.detail || err.message || "Gagal menghapus file fenotipe.");
    }
  };

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {!filename ? (
        <div
          style={{
            border: "2px dashed #cbd5e1",
            borderRadius: 8,
            padding: "20px 16px",
            textAlign: "center",
            background: "#f8fafc",
          }}
        >
          <input
            type="file"
            id="pheno-file-input"
            accept=".txt,.tsv,.csv,.pheno"
            onChange={handleUpload}
            disabled={isUploading || !datasetId}
            style={{ display: "none" }}
          />
          <label
            htmlFor="pheno-file-input"
            style={{
              display: "inline-block",
              padding: "8px 16px",
              background: datasetId ? "#2563eb" : "#94a3b8",
              color: "#fff",
              borderRadius: 6,
              fontSize: 13,
              fontWeight: 600,
              cursor: datasetId ? "pointer" : "not-allowed",
            }}
          >
            {isUploading ? "Mengunggah fenotipe..." : "Pilih File Fenotipe (.txt / .tsv / .csv)"}
          </label>
          <p style={{ margin: "8px 0 0", fontSize: 12, color: "#64748b" }}>
            Format tabel dengan baris header. Kolom minimal: ID individu (IID) dan nilai fenotipe kuantitatif.
          </p>
        </div>
      ) : (
        <div style={{ padding: 14, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div>
              <span style={{ fontSize: 12, fontWeight: 700, color: "#166534", background: "#dcfce7", padding: "2px 8px", borderRadius: 4 }}>
                ✓ Fenotipe Terunggah
              </span>
              <strong style={{ marginLeft: 8, fontSize: 13, color: "#0f172a" }}>{filename}</strong>
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
              Hapus File
            </button>
          </div>

          {/* Selectors for ID column & Trait column */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
            <label style={{ display: "grid", gap: 4, fontSize: 12, color: "#334155" }}>
              <span style={{ fontWeight: 700 }}>Kolom ID Sampel:</span>
              <select
                value={selectedIdCol}
                onChange={(e) => setSelectedIdCol(e.target.value)}
                style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #cbd5e1", background: "#fff", fontSize: 13 }}
              >
                {columns.map((col) => (
                  <option key={col} value={col}>
                    {col} {col.toUpperCase() === "IID" ? "(Direkomendasikan)" : ""}
                  </option>
                ))}
              </select>
            </label>

            <label style={{ display: "grid", gap: 4, fontSize: 12, color: "#334155" }}>
              <span style={{ fontWeight: 700 }}>Kolom Trait Target (GWAS):</span>
              <select
                value={selectedTrait}
                onChange={(e) => setSelectedTrait(e.target.value)}
                style={{ padding: "7px 10px", borderRadius: 6, border: "1px solid #cbd5e1", background: "#fff", fontSize: 13 }}
              >
                {numericTraits.length > 0 ? (
                  numericTraits.map((t) => (
                    <option key={t} value={t}>
                      {t} (Numerik)
                    </option>
                  ))
                ) : (
                  columns.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))
                )}
              </select>
            </label>
          </div>

          {isInspecting && (
            <div style={{ marginTop: 8, fontSize: 12, color: "#2563eb" }}>
              Memeriksa konsistensi sampel dengan VCF...
            </div>
          )}

          {/* Preview rows if available */}
          {previewRows.length > 0 && (
            <details style={{ marginTop: 10, fontSize: 12 }}>
              <summary style={{ cursor: "pointer", color: "#475569", fontWeight: 600 }}>
                Lihat 5 baris pertama data fenotipe
              </summary>
              <div style={{ overflowX: "auto", marginTop: 6 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, background: "#fff" }}>
                  <thead>
                    <tr style={{ background: "#f1f5f9" }}>
                      {columns.map((col) => (
                        <th key={col} style={{ border: "1px solid #e2e8f0", padding: "4px 8px", textAlign: "left" }}>
                          {col}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {previewRows.map((row, idx) => (
                      <tr key={idx}>
                        {columns.map((col) => (
                          <td key={col} style={{ border: "1px solid #e2e8f0", padding: "3px 8px" }}>
                            {row[col] ?? "-"}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </div>
      )}

      {error && (
        <div style={{ padding: 10, borderRadius: 6, background: "#fef2f2", color: "#991b1b", fontSize: 12 }}>
          {error}
        </div>
      )}
    </div>
  );
}
