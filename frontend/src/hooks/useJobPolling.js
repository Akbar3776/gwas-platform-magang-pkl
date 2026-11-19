import { useEffect, useRef, useState } from "react";
import { formatApiError } from "../api/formatApiError";
import { fetchJobStatus } from "../api/jobsApi";

// Status yang menandakan job sudah tidak akan berubah lagi -> polling berhenti.
const TERMINAL_STATUSES = ["COMPLETED", "SUCCESS", "FAILED", "CANCELLED"];

function normalizeStatus(value) {
  return String(value || "").toUpperCase();
}

/**
 * Hook generik untuk polling status AnalysisJob (dipakai untuk QC, dan bisa
 * dipakai ulang untuk tahap Imputasi/GWAS nanti karena bentuk response
 * `GET /jobs/{job_id}/status` seragam untuk semua jenis job).
 *
 * @param {string | null} jobId - jika null/undefined, hook tidak melakukan apa pun
 * @param {{ intervalMs?: number }} options
 * @returns {{
 *   jobStatus: object | null,
 *   isPolling: boolean,
 *   pollError: string | null,
 * }}
 */
export function useJobPolling(jobId, { intervalMs = 3000, pollTrigger = 0 } = {}) {
  const [jobStatus, setJobStatus] = useState(null);
  const [isPolling, setIsPolling] = useState(false);
  const [pollError, setPollError] = useState(null);

  const timerRef = useRef(null);
  const activeTokenRef = useRef(null);

  useEffect(() => {
    setJobStatus(null);
    setPollError(null);

    if (!jobId) {
      setIsPolling(false);
      return undefined;
    }

    const token = { cancelled: false };
    activeTokenRef.current = token;
    setIsPolling(true);

    const poll = async () => {
      try {
        const data = await fetchJobStatus(jobId);
        if (token.cancelled || activeTokenRef.current !== token) return;

        setJobStatus(data);
        setPollError(null);

        if (TERMINAL_STATUSES.includes(normalizeStatus(data.overall_status))) {
          setIsPolling(false);
          return; // berhenti polling — job sudah final
        }

        timerRef.current = setTimeout(poll, intervalMs);
      } catch (err) {
        if (token.cancelled || activeTokenRef.current !== token) return;
        // Error jaringan sementara TIDAK menghentikan tracker — tetap coba
        // lagi di interval berikutnya, supaya hiccup koneksi singkat tidak
        // membuat progress tracker "macet" secara permanen di mata user.
        setPollError(formatApiError(err, "Gagal memuat status pekerjaan."));
        timerRef.current = setTimeout(poll, intervalMs);
      }
    };

    poll();

    return () => {
      token.cancelled = true;
      if (activeTokenRef.current === token) {
        activeTokenRef.current = null;
      }
      setIsPolling(false);
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [jobId, intervalMs, pollTrigger]);

  return {
    jobStatus,
    status: normalizeStatus(jobStatus?.overall_status),
    error: jobStatus?.error_message || pollError,
    isPolling,
    pollError,
  };
}
