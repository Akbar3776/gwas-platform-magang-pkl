import { useCallback, useEffect, useRef, useState } from "react";
import { fetchManhattanData, ApiError } from "../api/gwasApi";

/**
 * Hook pengambilan data hasil GWAS dengan manajemen state standar:
 * isLoading, data, error - plus fungsi `refetch` untuk tombol "Coba Lagi".
 *
 * Request otomatis dibatalkan (AbortController) ketika jobId berubah atau
 * komponen unmount, untuk mencegah race condition / state update pada
 * komponen yang sudah tidak ter-mount.
 *
 * @param {string} jobId
 */
export function useGwasResults(jobId) {
  const [data, setData] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const abortControllerRef = useRef(null);

  const load = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    if (!jobId) {
      setData(null);
      setError(null);
      setIsLoading(false);
      return;
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setData(null);
    setIsLoading(true);
    setError(null);

    fetchManhattanData(jobId, controller.signal)
      .then((result) => {
        if (abortControllerRef.current !== controller) return;
        setData(result);
      })
      .catch((err) => {
        if (abortControllerRef.current !== controller) return;
        if (err && err.name === "AbortError") return; // dibatalkan, bukan error nyata
        setError(err instanceof ApiError ? err.message : "Terjadi kesalahan tak terduga saat mengambil data.");
      })
      .finally(() => {
        if (abortControllerRef.current !== controller) return;
        setIsLoading(false);
      });
  }, [jobId]);

  useEffect(() => {
    load();
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
    };
  }, [load]);

  return { data, isLoading, error, refetch: load };
}
