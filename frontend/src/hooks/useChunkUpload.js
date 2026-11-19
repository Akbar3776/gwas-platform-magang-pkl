import { useCallback, useEffect, useRef, useState } from "react";
import { httpClient } from "../api/httpClient";
import { createIncrementalSha256, readBlobAsUint8Array } from "../utils/sha256";

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

function isAborted(token, mountedRef) {
  return Boolean(
    !token ||
      token.cancelled ||
      token.controller?.signal?.aborted ||
      (mountedRef && !mountedRef.current)
  );
}

function wait(ms, signal) {
  if (signal?.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const onAbort = () => {
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener?.("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener?.("abort", onAbort, { once: true });
  });
}

function formatEta(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  if (seconds < 60) return `${Math.ceil(seconds)} detik`;
  return `${Math.ceil(seconds / 60)} menit`;
}

async function postChunkWithRetry(formData, token, mountedRef, chunkUrl = "/upload/chunk") {
  let lastError;
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt += 1) {
    if (isAborted(token, mountedRef)) return null;
    try {
      return await httpClient.post(chunkUrl, formData, {
        signal: token.controller.signal,
      });
    } catch (error) {
      if (isAborted(token, mountedRef)) return null;
      lastError = error;
      if (attempt < MAX_RETRIES) {
        await wait(RETRY_DELAY_MS * attempt, token.controller.signal);
      }
    }
  }
  throw lastError;
}

export function useChunkUpload(options = {}) {
  const {
    initUrl = "/upload/init",
    chunkUrl = "/upload/chunk",
    finalizeUrl = "/upload/finalize",
    statusUrl = null,
  } = options?.endpoints || {};

  const [stage, setStage] = useState(UPLOAD_STAGE.IDLE);
  const [progress, setProgress] = useState(0);
  const [uploadedBytes, setUploadedBytes] = useState(0);
  const [etaSeconds, setEtaSeconds] = useState(null);
  const [fileId, setFileId] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);
  const activeTokenRef = useRef(null);
  const mountedRef = useRef(true);
  const startedAtRef = useRef(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const activeToken = activeTokenRef.current;
      if (activeToken) {
        activeToken.cancelled = true;
        activeToken.controller.abort();
        activeTokenRef.current = null;
      }
    };
  }, []);

  const clearUiState = useCallback(() => {
    if (!mountedRef.current) return;
    setStage(UPLOAD_STAGE.IDLE);
    setProgress(0);
    setUploadedBytes(0);
    setEtaSeconds(null);
    setFileId(null);
    setUploadResult(null);
    setErrorMessage(null);
  }, []);

  const cancelUpload = useCallback(() => {
    const activeToken = activeTokenRef.current;
    if (activeToken) {
      activeToken.cancelled = true;
      activeToken.controller.abort();
      activeTokenRef.current = null;
    }
    clearUiState();
  }, [clearUiState]);

  const reset = useCallback(() => {
    cancelUpload();
  }, [cancelUpload]);

  const waitUntilReady = useCallback(
    async (id, token) => {
      const resolvedStatusUrl = statusUrl
        ? typeof statusUrl === "function"
          ? statusUrl(id)
          : statusUrl
        : `/upload/${id}/status`;
      while (!isAborted(token, mountedRef)) {
        const { data } = await httpClient.get(resolvedStatusUrl, {
          signal: token.controller.signal,
        });
        if (isAborted(token, mountedRef)) return null;
        const status = String(data.upload_status).toUpperCase();
        if (status === "UPLOADED" || status === "READY") return data;
        if (status === "FAILED") {
          throw new Error(data.error_message || "Server gagal merakit file upload.");
        }
        await wait(STATUS_POLL_MS, token.controller.signal);
      }
      return null;
    },
    [statusUrl]
  );

  const startUpload = useCallback(
    async (file) => {
      if (!file) return null;
      if (activeTokenRef.current && !activeTokenRef.current.cancelled) {
        return null;
      }

      const token = { cancelled: false, controller: new AbortController() };
      activeTokenRef.current = token;
      clearUiState();
      startedAtRef.current = performance.now();
      if (mountedRef.current) {
        setStage(UPLOAD_STAGE.UPLOADING);
      }

      try {
        const { data: init } = await httpClient.post(
          initUrl,
          {
            original_filename: file.name,
            total_size_bytes: file.size,
          },
          { signal: token.controller.signal }
        );
        if (isAborted(token, mountedRef) || activeTokenRef.current !== token) {
          return null;
        }
        const id = init.file_id;
        setFileId(id);
        const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
        const hasher = createIncrementalSha256();

        for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
          if (isAborted(token, mountedRef) || activeTokenRef.current !== token) {
            return null;
          }
          const start = chunkIndex * CHUNK_SIZE;
          const end = Math.min(start + CHUNK_SIZE, file.size);
          const chunkBlob = file.slice(start, end);
          const chunkBytes = await readBlobAsUint8Array(chunkBlob);
          if (isAborted(token, mountedRef) || activeTokenRef.current !== token) {
            return null;
          }
          hasher.update(chunkBytes);

          const formData = new FormData();
          formData.append("file_id", id);
          formData.append("chunk_index", String(chunkIndex));
          formData.append("total_chunks", String(totalChunks));
          formData.append("chunk", chunkBlob, file.name);
          const res = await postChunkWithRetry(formData, token, mountedRef, chunkUrl);
          if (res === null || isAborted(token, mountedRef) || activeTokenRef.current !== token) {
            return null;
          }

          const bytes = end;
          const elapsed = (performance.now() - startedAtRef.current) / 1000;
          const speed = bytes / Math.max(elapsed, 0.1);
          setUploadedBytes(bytes);
          setProgress(Math.min(100, Math.round((bytes / file.size) * 100)));
          setEtaSeconds((file.size - bytes) / speed);
        }

        const expectedSha256 = hasher.digest();
        const finalizeData = new FormData();
        finalizeData.append("file_id", id);
        finalizeData.append("total_chunks", String(totalChunks));
        finalizeData.append("expected_sha256", expectedSha256);
        setStage(UPLOAD_STAGE.PROCESSING);
        await httpClient.post(finalizeUrl, finalizeData, {
          signal: token.controller.signal,
        });
        if (isAborted(token, mountedRef) || activeTokenRef.current !== token) {
          return null;
        }
        const readyData = await waitUntilReady(id, token);
        if (isAborted(token, mountedRef) || activeTokenRef.current !== token) {
          return null;
        }
        setUploadResult(readyData);
        setProgress(100);
        setEtaSeconds(null);
        setStage(UPLOAD_STAGE.READY);
        return id;
      } catch (error) {
        if (isAborted(token, mountedRef) || activeTokenRef.current !== token) {
          return null;
        }
        setErrorMessage(error.response?.data?.detail || error.message || "Upload gagal.");
        setStage(UPLOAD_STAGE.ERROR);
        throw error;
      } finally {
        if (activeTokenRef.current === token) {
          activeTokenRef.current = null;
        }
      }
    },
    [chunkUrl, clearUiState, finalizeUrl, initUrl, waitUntilReady]
  );

  return {
    stage,
    progress,
    uploadedBytes,
    etaSeconds,
    etaLabel: formatEta(etaSeconds),
    fileId,
    uploadResult,
    errorMessage,
    startUpload,
    cancelUpload,
    reset,
  };
}
