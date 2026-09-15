import { useCallback, useRef, useState } from "react";
import { httpClient } from "../api/httpClient";

const CHUNK_SIZE = 10 * 1024 * 1024;
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 800;
const STATUS_POLL_MS = 2000;

export const UPLOAD_STAGE = {
  IDLE: "idle",
  UPLOADING: "uploading",
  PROCESSING: "processing",
  READY: "ready",
  ERROR: "error",
};

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatEta(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  if (seconds < 60) return `${Math.ceil(seconds)} detik`;
  return `${Math.ceil(seconds / 60)} menit`;
}

async function postChunkWithRetry(formData) {
  let lastError;
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt += 1) {
    try {
      return await httpClient.post("/upload/chunk", formData);
    } catch (error) {
      lastError = error;
      if (attempt < MAX_RETRIES) await wait(RETRY_DELAY_MS * attempt);
    }
  }
  throw lastError;
}

export function useChunkUpload() {
  const [stage, setStage] = useState(UPLOAD_STAGE.IDLE);
  const [progress, setProgress] = useState(0);
  const [uploadedBytes, setUploadedBytes] = useState(0);
  const [etaSeconds, setEtaSeconds] = useState(null);
  const [fileId, setFileId] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);
  const cancelledRef = useRef(false);
  const startedAtRef = useRef(null);

  const reset = useCallback(() => {
    cancelledRef.current = false;
    setStage(UPLOAD_STAGE.IDLE);
    setProgress(0);
    setUploadedBytes(0);
    setEtaSeconds(null);
    setFileId(null);
    setErrorMessage(null);
  }, []);

  const cancelUpload = useCallback(() => {
    cancelledRef.current = true;
  }, []);

  const waitUntilReady = useCallback(async (id) => {
    while (!cancelledRef.current) {
      const { data } = await httpClient.get(`/upload/${id}/status`);
      const status = String(data.upload_status).toUpperCase();
      if (status === "UPLOADED" || status === "READY") return data;
      if (status === "FAILED") throw new Error("Server gagal merakit file upload.");
      await wait(STATUS_POLL_MS);
    }
    return null;
  }, []);

  const startUpload = useCallback(async (file) => {
    reset();
    cancelledRef.current = false;
    startedAtRef.current = performance.now();
    setStage(UPLOAD_STAGE.UPLOADING);

    try {
      const { data: init } = await httpClient.post("/upload/init", {
        original_filename: file.name,
        total_size_bytes: file.size,
      });
      const id = init.file_id;
      setFileId(id);
      const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

      for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
        if (cancelledRef.current) return null;
        const start = chunkIndex * CHUNK_SIZE;
        const end = Math.min(start + CHUNK_SIZE, file.size);
        const formData = new FormData();
        formData.append("file_id", id);
        formData.append("chunk_index", String(chunkIndex));
        formData.append("total_chunks", String(totalChunks));
        formData.append("chunk", file.slice(start, end), file.name);
        await postChunkWithRetry(formData);

        const bytes = end;
        const elapsed = (performance.now() - startedAtRef.current) / 1000;
        const speed = bytes / Math.max(elapsed, 0.1);
        setUploadedBytes(bytes);
        setProgress(Math.min(100, Math.round((bytes / file.size) * 100)));
        setEtaSeconds((file.size - bytes) / speed);
      }

      const finalizeData = new FormData();
      finalizeData.append("file_id", id);
      finalizeData.append("total_chunks", String(totalChunks));
      setStage(UPLOAD_STAGE.PROCESSING);
      await httpClient.post("/upload/finalize", finalizeData);
      await waitUntilReady(id);
      if (cancelledRef.current) return null;
      setProgress(100);
      setEtaSeconds(null);
      setStage(UPLOAD_STAGE.READY);
      return id;
    } catch (error) {
      if (cancelledRef.current) return null;
      setErrorMessage(error.response?.data?.detail || error.message || "Upload gagal.");
      setStage(UPLOAD_STAGE.ERROR);
      throw error;
    }
  }, [reset, waitUntilReady]);

  return {
    stage,
    progress,
    uploadedBytes,
    etaSeconds,
    etaLabel: formatEta(etaSeconds),
    fileId,
    errorMessage,
    startUpload,
    cancelUpload,
    reset,
  };
}
