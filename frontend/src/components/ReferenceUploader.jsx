import { useEffect, useRef, useState } from "react";
import { formatApiError } from "../api/formatApiError";
import { deleteReference } from "../api/jobsApi";
import { UPLOAD_STAGE, useChunkUpload } from "../hooks/useChunkUpload";
import { UploadProgressBar } from "./UploadProgressBar";

/**
 * Dropzone chunked upload untuk FASTA referensi genom (untuk ekstraksi flanking & Primer3).
 */
export function ReferenceUploader({
  datasetId,
  initialReferenceFilename = null,
  initialReferenceContigs = null,
  onReferenceChanged,
}) {
  const inputRef = useRef(null);
  const lastDatasetIdRef = useRef(datasetId);
  const onChangedRef = useRef(onReferenceChanged);
  onChangedRef.current = onReferenceChanged;

  const [meta, setMeta] = useState(() =>
    initialReferenceFilename
      ? { filename: initialReferenceFilename, n_contigs: initialReferenceContigs ?? null }
      : null
  );
  const [localError, setLocalError] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const {
    stage,
    progress,
    etaLabel,
    uploadResult,
    errorMessage,
    startUpload,
    cancelUpload,
    reset,
  } = useChunkUpload({
    endpoints: {
      initUrl: `/upload/${datasetId}/reference/init`,
      chunkUrl: `/upload/${datasetId}/reference/chunk`,
      finalizeUrl: `/upload/${datasetId}/reference/finalize`,
      statusUrl: `/upload/${datasetId}/reference/status`,
    },
  });

  useEffect(() => {
    if (lastDatasetIdRef.current !== datasetId) {
      lastDatasetIdRef.current = datasetId;
      reset();
      setLocalError(null);
      setMeta(
        initialReferenceFilename
          ? { filename: initialReferenceFilename, n_contigs: initialReferenceContigs ?? null }
          : null
      );
      return;
    }
    if (initialReferenceFilename && !meta) {
      setMeta({ filename: initialReferenceFilename, n_contigs: initialReferenceContigs ?? null });
    }
  }, [datasetId, initialReferenceContigs, initialReferenceFilename, meta, reset]);

  useEffect(() => {
    if (stage !== UPLOAD_STAGE.READY || !uploadResult) return;
    const nextMeta = {
      filename: uploadResult.filename || "reference.fasta",
      n_contigs: uploadResult.n_contigs ?? null,
    };
    setMeta(nextMeta);
    onChangedRef.current?.(nextMeta);
  }, [stage, uploadResult]);

  const isBusy = stage === UPLOAD_STAGE.UPLOADING || stage === UPLOAD_STAGE.PROCESSING || deleting;

  async function handleFile(file) {
    if (!file || !datasetId || isBusy) return;
    setLocalError(null);
    try {
      await startUpload(file);
    } catch (err) {
      setLocalError(formatApiError(err, "Gagal mengunggah FASTA referensi."));
    }
  }

  async function handleDelete() {
    if (!datasetId || isBusy) return;
    setDeleting(true);
    setLocalError(null);
    try {
      if (typeof deleteReference === "function") {
        await deleteReference(datasetId);
      }
      reset();
      setMeta(null);
      onChangedRef.current?.(null);
    } catch (err) {
      setLocalError(formatApiError(err, "Gagal menghapus FASTA referensi."));
    } finally {
      setDeleting(false);
    }
  }

  const displayError = localError || errorMessage;
  const stageLabel =
    stage === UPLOAD_STAGE.PROCESSING
      ? "Mendekompresi & membangun indeks .fai di server..."
      : etaLabel
        ? `Mengunggah FASTA referensi (${etaLabel})...`
        : "Mengunggah FASTA referensi...";

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
            FASTA Referensi Genom (Opsional)
          </strong>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: "#64748b" }}>
            Untuk ekstraksi sekuens flanking 201 bp &amp; desain primer KASP/CAPS (`.fa`, `.fasta`, `.fna`, `.gz`).
          </p>
        </div>

        <input
          ref={inputRef}
          id="reference-fasta-input"
          className="sr-only"
          type="file"
          accept=".fa,.fasta,.fna,.fa.gz,.fasta.gz,.fna.gz"
          disabled={isBusy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            handleFile(file);
          }}
        />

        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <label
            htmlFor="reference-fasta-input"
            role="button"
            tabIndex={isBusy ? -1 : 0}
            aria-disabled={isBusy}
            onKeyDown={(e) => {
              if (!isBusy && (e.key === "Enter" || e.key === " ")) {
                e.preventDefault();
                inputRef.current?.click();
              }
            }}
            style={{
              display: "inline-block",
              padding: "7px 12px",
              borderRadius: 6,
              background: isBusy ? "#94a3b8" : "#0f172a",
              color: "#ffffff",
              fontSize: 12,
              fontWeight: 600,
              cursor: isBusy ? "not-allowed" : "pointer",
            }}
          >
            {meta ? "Ganti FASTA Referensi" : "Pilih File FASTA Referensi"}
          </label>

          {isBusy && !deleting && (
            <button
              type="button"
              onClick={cancelUpload}
              style={{
                padding: "6px 10px",
                borderRadius: 6,
                border: "1px solid #cbd5e1",
                background: "#ffffff",
                color: "#334155",
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              Batalkan
            </button>
          )}
        </div>

        {(stage === UPLOAD_STAGE.UPLOADING || stage === UPLOAD_STAGE.PROCESSING) && (
          <UploadProgressBar percent={progress} label={stageLabel} />
        )}

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
              {typeof meta.n_contigs === "number"
                ? ` · ${meta.n_contigs} kromosom / contig terindeks`
                : ""}
            </span>
            <button
              type="button"
              onClick={handleDelete}
              disabled={isBusy}
              style={{
                padding: "3px 8px",
                borderRadius: 5,
                border: "1px solid #fca5a5",
                background: "#ffffff",
                color: "#991b1b",
                fontSize: 11,
                fontWeight: 600,
                cursor: isBusy ? "not-allowed" : "pointer",
              }}
            >
              Hapus
            </button>
          </div>
        )}

        {displayError && (
          <p style={{ margin: "8px 0 0", fontSize: 12, color: "#b91c1c" }} role="alert">
            {displayError}
          </p>
        )}
      </div>
    </div>
  );
}
