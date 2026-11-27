import { useEffect, useRef, useState } from "react";
import { formatApiError } from "../api/formatApiError";
import { deleteOrtholog, uploadOrtholog } from "../api/jobsApi";
import { UploadProgressBar } from "./UploadProgressBar";

/**
 * Dropzone unggah hasil BLAST outfmt6 (.blast/.tsv/.txt/.tab/.out/.m8/.gz) opsional untuk kolom ortolog.
 */
export function OrthologUploader({
  datasetId,
  initialOrthologFilename = null,
  initialOrthologGenes = null,
  onOrthologChanged,
}) {
  const inputRef = useRef(null);
  const lastDatasetIdRef = useRef(datasetId);
  const onChangedRef = useRef(onOrthologChanged);
  onChangedRef.current = onOrthologChanged;

  const [meta, setMeta] = useState(() =>
    initialOrthologFilename
      ? { filename: initialOrthologFilename, n_queries: initialOrthologGenes ?? null }
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
        initialOrthologFilename
          ? { filename: initialOrthologFilename, n_queries: initialOrthologGenes ?? null }
          : null
      );
      return;
    }
    if (initialOrthologFilename && !meta) {
      setMeta({ filename: initialOrthologFilename, n_queries: initialOrthologGenes ?? null });
    }
  }, [datasetId, initialOrthologFilename, initialOrthologGenes, meta]);

  async function handleFile(file) {
    if (!file || !datasetId || busy) return;
    setBusy(true);
    setPercent(0);
    setErrorMessage(null);
    try {
      const res = await uploadOrtholog(datasetId, file, (pct) => setPercent(pct));
      const nextMeta = {
        filename: res?.filename || file.name,
        n_queries: res?.n_queries ?? null,
        n_ties: res?.n_ties ?? 0,
        n_rows: res?.n_rows ?? null,
        n_invalid_lines: res?.n_invalid_lines ?? 0,
      };
      setMeta(nextMeta);
      onChangedRef.current?.(nextMeta);
    } catch (err) {
      setErrorMessage(formatApiError(err, "Gagal mengunggah file ortolog BLAST."));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!datasetId || busy) return;
    setBusy(true);
    setErrorMessage(null);
    try {
      if (typeof deleteOrtholog === "function") {
        await deleteOrtholog(datasetId);
      }
      setMeta(null);
      onChangedRef.current?.(null);
    } catch (err) {
      setErrorMessage(formatApiError(err, "Gagal menghapus indeks ortolog dataset."));
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
            File Ortolog BLAST (Opsional)
          </strong>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: "#64748b" }}>
            BLAST outfmt 6 (12 kolom standar, misalnya terhadap Arabidopsis `.blast`, `.tsv`, `.txt`, `.tab`, `.out`, `.m8`, atau `.gz`). Server hanya menyimpan indeks best hit per gen.
          </p>
        </div>

        <input
          ref={inputRef}
          id="ortholog-file-input"
          className="sr-only"
          type="file"
          accept=".gz,.blast,.tsv,.txt,.tab,.out,.m8"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            handleFile(file);
          }}
        />

        <label
          htmlFor="ortholog-file-input"
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
          {meta ? "Ganti File Ortolog BLAST" : "Pilih File Ortolog BLAST"}
        </label>

        {busy && <UploadProgressBar percent={percent} label="Mengunggah & mengindeks BLAST best hit..." />}

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
              {typeof meta.n_queries === "number"
                ? ` · ${meta.n_queries.toLocaleString("id-ID")} gen terpetakan`
                : ""}
              {typeof meta.n_ties === "number" && meta.n_ties > 0
                ? ` (${meta.n_ties.toLocaleString("id-ID")} lokus setara)`
                : ""}
              {typeof meta.n_invalid_lines === "number" && meta.n_invalid_lines > 0 ? (
                <span style={{ display: "block", marginTop: 2, color: "#92400e" }}>
                  {meta.n_invalid_lines.toLocaleString("id-ID")} baris dilewati karena format tidak valid
                </span>
              ) : null}
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
          <div
            role="alert"
            style={{
              marginTop: 10,
              padding: "8px 10px",
              borderRadius: 6,
              background: "#fef2f2",
              border: "1px solid #fecaca",
              color: "#991b1b",
              fontSize: 12,
            }}
          >
            {errorMessage}
          </div>
        )}
      </div>
    </div>
  );
}
