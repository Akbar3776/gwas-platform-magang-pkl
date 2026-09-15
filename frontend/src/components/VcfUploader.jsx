import { useCallback, useRef, useState } from "react";
import { useChunkUpload, UPLOAD_STAGE } from "../hooks/useChunkUpload";
import { UploadProgressBar } from "./UploadProgressBar";

const STAGE_LABELS = {
  [UPLOAD_STAGE.UPLOADING]: "Mengunggah file...",
  [UPLOAD_STAGE.PROCESSING]: "Menyiapkan data di server...",
  [UPLOAD_STAGE.READY]: "File siap dianalisis.",
  [UPLOAD_STAGE.ERROR]: "Terjadi kesalahan.",
};

/**
 * Komponen upload file VCF dengan drag-and-drop.
 *
 * Semua logic chunking/upload/assembly-wait ditangani oleh `useChunkUpload`
 * — komponen ini murni bertanggung jawab atas tampilan (drop zone, progress
 * bar, pesan status) sesuai prinsip clean code / pemisahan concern.
 *
 * @param {{ onDatasetReady: (fileId: string) => void }} props
 *   `onDatasetReady` dipanggil sekali saat file selesai diunggah DAN
 *   selesai dirakit di server (siap dipakai untuk trigger job QC).
 */
export function VcfUploader({ onDatasetReady }) {
  const { stage, progress, etaLabel, errorMessage, startUpload, cancelUpload, reset } = useChunkUpload();
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const fileInputRef = useRef(null);

  const handleFile = useCallback(
    async (file) => {
      if (!file) return;
      try {
        const readyFileId = await startUpload(file);
        if (readyFileId) {
          onDatasetReady?.(readyFileId);
        }
      } catch {
        // Pesan error sudah ditangani & ditampilkan lewat state `errorMessage`
        // dari hook — tidak perlu penanganan tambahan di sini.
      }
    },
    [startUpload, onDatasetReady]
  );

  const handleDrop = useCallback(
    (event) => {
      event.preventDefault();
      setIsDraggingOver(false);
      const file = event.dataTransfer.files?.[0];
      handleFile(file);
    },
    [handleFile]
  );

  const handleFileInputChange = useCallback(
    (event) => {
      const file = event.target.files?.[0];
      handleFile(file);
    },
    [handleFile]
  );

  const isBusy = stage === UPLOAD_STAGE.UPLOADING || stage === UPLOAD_STAGE.PROCESSING;

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDraggingOver(true);
        }}
        onDragLeave={() => setIsDraggingOver(false)}
        onDrop={handleDrop}
        onClick={() => !isBusy && fileInputRef.current?.click()}
        style={{
          border: `2px dashed ${isDraggingOver ? "#2563eb" : "#d1d5db"}`,
          borderRadius: 12,
          padding: 32,
          textAlign: "center",
          cursor: isBusy ? "not-allowed" : "pointer",
          backgroundColor: isDraggingOver ? "#eff6ff" : "#fafafa",
          transition: "all 0.15s ease",
        }}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".vcf,.vcf.gz"
          onChange={handleFileInputChange}
          disabled={isBusy}
          style={{ display: "none" }}
        />

        {stage === UPLOAD_STAGE.IDLE && (
          <p style={{ color: "#374151" }}>
            Seret file VCF ke sini, atau klik untuk memilih file dari komputer Anda.
          </p>
        )}

        {isBusy && (
          <>
            <p style={{ color: "#374151", marginBottom: 4 }}>{STAGE_LABELS[stage]}</p>
            {stage === UPLOAD_STAGE.UPLOADING && (
              <>
                <UploadProgressBar percent={progress} label={etaLabel ? `Perkiraan selesai: ${etaLabel}` : undefined} />
                <button type="button" onClick={(event) => { event.stopPropagation(); cancelUpload(); }} style={{ marginTop: 8, border: "1px solid #cbd5e1", borderRadius: 6, padding: "6px 10px", background: "#fff", cursor: "pointer" }}>
                  Batalkan upload
                </button>
              </>
            )}
            {stage === UPLOAD_STAGE.PROCESSING && (
              <p style={{ fontSize: 12, color: "#6b7280" }}>
                Mohon tunggu, tahap ini biasanya hanya berlangsung sebentar.
              </p>
            )}
          </>
        )}

        {stage === UPLOAD_STAGE.READY && (
          <p style={{ color: "#16a34a", fontWeight: 600 }}>
            ✓ {STAGE_LABELS[UPLOAD_STAGE.READY]}
          </p>
        )}

        {stage === UPLOAD_STAGE.ERROR && (
          <div>
            <p style={{ color: "#dc2626", marginBottom: 8 }}>{errorMessage}</p>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                reset();
              }}
              style={{
                padding: "6px 12px",
                borderRadius: 6,
                border: "1px solid #dc2626",
                color: "#dc2626",
                backgroundColor: "white",
                cursor: "pointer",
              }}
            >
              Coba Lagi
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
