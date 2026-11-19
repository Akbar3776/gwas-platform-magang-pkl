import { useCallback, useEffect, useRef, useState } from "react";
import { formatApiError } from "../api/formatApiError";

/**
 * Hook generik untuk mengambil resource API per `jobId` dengan AbortController,
 * active-controller guard agar response basi tidak menimpa job aktif,
 * serta status `isLoading`, `error`, dan `refetch`.
 */
export function useResource(
  fetcher,
  jobId,
  { enabled = true, fallbackMessage = "Gagal memuat data." } = {}
) {
  const [data, setData] = useState(null);
  const [isLoading, setIsLoading] = useState(Boolean(enabled && jobId));
  const [error, setError] = useState(null);
  const controllerRef = useRef(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(() => {
    if (controllerRef.current) {
      controllerRef.current.abort();
      controllerRef.current = null;
    }

    if (!enabled || !jobId) {
      setData(null);
      setError(null);
      setIsLoading(false);
      return;
    }

    const controller = new AbortController();
    controllerRef.current = controller;
    setData(null);
    setError(null);
    setIsLoading(true);

    Promise.resolve(fetcherRef.current(jobId, controller.signal))
      .then((result) => {
        if (controllerRef.current !== controller) return;
        setData(result);
      })
      .catch((err) => {
        if (controllerRef.current !== controller) return;
        if (err && err.name === "AbortError") return;
        const payload = err?.response?.data ?? (err instanceof Error ? (err.detail ?? err.message) : err);
        setError(payload ? formatApiError(payload, fallbackMessage) : fallbackMessage);
      })
      .finally(() => {
        if (controllerRef.current !== controller) return;
        setIsLoading(false);
      });
  }, [enabled, fallbackMessage, jobId]);

  useEffect(() => {
    load();
    return () => {
      if (controllerRef.current) {
        controllerRef.current.abort();
        controllerRef.current = null;
      }
    };
  }, [load]);

  return { data, isLoading, error, refetch: load };
}
