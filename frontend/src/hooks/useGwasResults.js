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
    if (!jobId) return;

    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setIsLoading(true);
    setError(null);

    fetchManhattanData(jobId, controller.signal)
      .then((result) => {
        setData(result);
      })
      .catch((err) => {
        if (err && err.name === "AbortError") return; // dibatalkan, bukan error nyata
        setError(err instanceof ApiError ? err.message : "Terjadi kesalahan tak terduga saat mengambil data.");
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, [jobId]);

  useEffect(() => {
    load();
    return () => {
      if (abortControllerRef.current) abortControllerRef.current.abort();
    };
  }, [load]);

  return { data, isLoading, error, refetch: load };
}
