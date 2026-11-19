import { useEffect, useRef, useState } from "react";
import { deleteGoAnnotation, uploadGoAnnotation } from "../api/jobsApi";
import { UploadProgressBar } from "./UploadProgressBar";

/**
 * Dropzone unggah file pemetaan Gene Ontology (.tsv/.txt/.gaf/.gaf.gz) opsional.
 */
export function GoMappingUploader({
  datasetId,
  initialGoAnnotationFilename = null,
  initialGoAnnotationGenes = null,
  onGoAnnotationChanged,
}) {
  const inputRef = useRef(null);
  const lastDatasetIdRef = useRef(datasetId);
  const onChangedRef = useRef(onGoAnnotationChanged);
  onChangedRef.current = onGoAnnotationChanged;

  const [meta, setMeta] = useState(() =>
    initialGoAnnotationFilename
      ? { filename: initialGoAnnotationFilename, n_genes: initialGoAnnotationGenes ?? null }
      : null
  );
  const [percent, setPercent] = useState(0);
  const [busy, setBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  useEffect(() => {
    if (lastDatasetIdRef.current !== datasetId) {
      lastDatasetIdRef.current = datasetId;
      setPercent(0);
      setErrorMessage(null);
      setMeta(
        initialGoAnnotationFilename
          ? { filename: initialGoAnnotationFilename, n_genes: initialGoAnnotationGenes ?? null }
          : null
      );
      return;
    }
    if (initialGoAnnotationFilename && !meta) {
      setMeta({ filename: initialGoAnnotationFilename, n_genes: initialGoAnnotationGenes ?? null });
    }
  }, [datasetId, initialGoAnnotationFilename, initialGoAnnotationGenes, meta]);

  async function handleFile(file) {
    if (!file || !datasetId || busy) return;
    setBusy(true);
    setPercent(0);
    setErrorMessage(null);
    try {
      const res = await uploadGoAnnotation(datasetId, file, (pct) => setPercent(pct));
      const nextMeta = {
        filename: res?.filename || file.name,
        n_genes: res?.n_genes ?? null,
        n_mappings: res?.n_mappings ?? null,
        format: res?.format ?? null,
      };
      setMeta(nextMeta);
      onChangedRef.current?.(nextMeta);
    } catch (err) {
      setErrorMessage(err?.response?.data?.detail || err?.message || "Gagal mengunggah file pemetaan GO.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!datasetId || busy) return;
    setBusy(true);
    setErrorMessage(null);
    try {
      if (typeof deleteGoAnnotation === "function") {
        await deleteGoAnnotation(datasetId);
      }
      setMeta(null);
      onChangedRef.current?.(null);
    } catch (err) {
      setErrorMessage(err?.response?.data?.detail || err?.message || "Gagal menghapus file pemetaan GO.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div
        style={{
          border: "2px dashed #cbd5e1",
          borderRadius: 8,
          padding: 16,
          background: "#f8fafc",
        }}
      >
        <div style={{ marginBottom: 8 }}>
          <strong style={{ fontSize: 14, color: "#0f172a" }}>
            File Pemetaan GO (Opsional)
          </strong>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: "#64748b" }}>
            TSV (`gene_id\tGO:0006357[\tBP\tname]`) atau GAF 2.x (`.tsv`, `.txt`, `.gaf`, `.gaf.gz`) jika GFF3 belum memiliki atribut `Ontology_term`.
          </p>
        </div>

        <input
          ref={inputRef}
          id="go-mapping-file-input"
          className="sr-only"
          type="file"
          accept=".tsv,.txt,.gaf,.gaf.gz"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            handleFile(file);
          }}
        />

        <label
          htmlFor="go-mapping-file-input"
          role="button"
          tabIndex={busy ? -1 : 0}
          aria-disabled={busy}
          onKeyDown={(e) => {
            if (!busy && (e.key === "Enter" || e.key === " ")) {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          style={{
            display: "inline-block",
            padding: "7px 12px",
            borderRadius: 6,
            background: busy ? "#94a3b8" : "#0f172a",
            color: "#ffffff",
            fontSize: 12,
            fontWeight: 600,
            cursor: busy ? "not-allowed" : "pointer",
          }}
        >
          {meta ? "Ganti File Pemetaan GO" : "Pilih File Pemetaan GO"}
        </label>

        {busy && <UploadProgressBar percent={percent} label="Mengunggah & memvalidasi pemetaan GO..." />}

        {meta && (
          <div
            style={{
              marginTop: 10,
              padding: "8px 10px",
              borderRadius: 6,
              background: "#ecfdf5",
              border: "1px solid #a7f3d0",
              color: "#065f46",
              fontSize: 12,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: 8,
              flexWrap: "wrap",
            }}
          >
            <span>
              <strong>{meta.filename}</strong>
              {typeof meta.n_genes === "number" ? ` · ${meta.n_genes} gen terdeteksi` : ""}
              {typeof meta.n_mappings === "number" ? ` (${meta.n_mappings} entri GO)` : ""}
            </span>
            <button
              type="button"
              onClick={handleDelete}
              disabled={busy}
              style={{
                padding: "3px 8px",
                borderRadius: 5,
                border: "1px solid #fca5a5",
                background: "#ffffff",
                color: "#991b1b",
                fontSize: 11,
                fontWeight: 600,
                cursor: busy ? "not-allowed" : "pointer",
              }}
            >
              Hapus
            </button>
          </div>
        )}

        {errorMessage && (
          <p style={{ margin: "8px 0 0", fontSize: 12, color: "#b91c1c" }} role="alert">
            {errorMessage}
          </p>
        )}
      </div>
    </div>
  );
}
