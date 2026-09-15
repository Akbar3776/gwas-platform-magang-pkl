import React, { useEffect, useRef, useState } from "react";

/**
 * ResultDashboard.jsx
 *
 * Menampilkan ringkasan statistik hasil satu job GWAS, dengan dua aksi unduh:
 * - "Download Full Results (ZIP)"   -> GET /results/{jobId}/export
 * - "Download Summary Report (PDF)" -> GET /results/{jobId}/report
 *
 * Penanganan download yang lama:
 * - Tombol berubah jadi state loading (spinner + label berubah), bukan sekadar
 *   disabled diam-diam, agar peneliti non-technical yakin prosesnya berjalan.
 * - Elapsed-time counter ditampilkan setelah beberapa detik supaya wajar jika
 *   ZIP besar makan waktu, dan tidak terlihat seperti aplikasi hang.
 * - AbortController memungkinkan user membatalkan unduhan yang berjalan.
 * - Pesan error dibedakan: 409 (job belum selesai) vs kegagalan jaringan lain,
 *   ditulis dalam bahasa awam, bukan raw error message.
 */

const API_BASE = "/api/v1"; // sesuaikan dengan base URL backend FastAPI kamu

function formatElapsed(seconds) {
  if (seconds < 60) return `${seconds} detik`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

function DownloadButton({ label, loadingLabel, onDownload, variant }) {
  const [status, setStatus] = useState("idle"); // idle | loading | error
  const [errorMessage, setErrorMessage] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const abortControllerRef = useRef(null);
  const timerRef = useRef(null);

  useEffect(() => {
    return () => {
      // Bersihkan timer & batalkan request jika komponen unmount di tengah download.
      if (timerRef.current) clearInterval(timerRef.current);
      if (abortControllerRef.current) abortControllerRef.current.abort();
    };
  }, []);

  const startElapsedTimer = () => {
    setElapsed(0);
    timerRef.current = setInterval(() => {
      setElapsed((prev) => prev + 1);
    }, 1000);
  };

  const stopElapsedTimer = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  const handleClick = async () => {
    setStatus("loading");
    setErrorMessage("");
    startElapsedTimer();

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      await onDownload(controller.signal);
      setStatus("idle");
    } catch (err) {
      if (err.name === "AbortError") {
        setStatus("idle"); // dibatalkan user, bukan error sesungguhnya
      } else {
        setStatus("error");
        setErrorMessage(err.message || "Terjadi kesalahan saat mengunduh.");
      }
    } finally {
      stopElapsedTimer();
      abortControllerRef.current = null;
    }
  };

  const handleCancel = () => {
    if (abortControllerRef.current) abortControllerRef.current.abort();
  };

  return (
    <div className="flex flex-col gap-1">
      <button
        onClick={handleClick}
        disabled={status === "loading"}
        className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${
          variant === "primary"
            ? "bg-slate-900 text-white hover:bg-slate-800 disabled:bg-slate-400"
            : "border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-60"
        }`}
      >
        {status === "loading" ? (
          <>
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
            {loadingLabel}
            {elapsed >= 3 && (
              <span className="text-xs opacity-80">({formatElapsed(elapsed)})</span>
            )}
          </>
        ) : (
          label
        )}
      </button>

      {status === "loading" && elapsed >= 5 && (
        <button
          onClick={handleCancel}
          className="self-start text-xs text-slate-500 underline hover:text-slate-700"
        >
          Batalkan
        </button>
      )}

      {status === "error" && (
        <p className="text-xs text-red-600">{errorMessage}</p>
      )}
    </div>
  );
}

async function triggerBrowserDownload(url, fallbackFilename, signal) {
  const response = await fetch(url, { signal, credentials: "include" });

  if (!response.ok) {
    if (response.status === 409) {
      throw new Error("Hasil belum siap — job masih diproses. Coba lagi sebentar.");
    }
    if (response.status === 403) {
      throw new Error("Anda tidak memiliki akses ke hasil ini.");
    }
    if (response.status === 404) {
      throw new Error("File hasil tidak ditemukan di server.");
    }
    throw new Error("Gagal mengunduh file. Silakan coba lagi.");
  }

  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : fallbackFilename;

  const blob = await response.blob();
  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);
}

export default function ResultDashboard({ jobId }) {
  const [summary, setSummary] = useState(null);
  const [loadingSummary, setLoadingSummary] = useState(true);
  const [summaryError, setSummaryError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function fetchSummary() {
      try {
        const res = await fetch(`${API_BASE}/jobs/${jobId}/results`, {
          credentials: "include",
        });
        if (!res.ok) throw new Error("Gagal memuat ringkasan hasil.");
        const data = await res.json();
        if (!cancelled) setSummary(data);
      } catch (err) {
        if (!cancelled) setSummaryError(err.message);
      } finally {
        if (!cancelled) setLoadingSummary(false);
      }
    }

    fetchSummary();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  return (
    <div className="mx-auto max-w-3xl rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">Hasil Analisis GWAS</h2>
      <p className="mb-4 text-sm text-slate-500">Job ID: {jobId}</p>

      {loadingSummary && (
        <p className="text-sm text-slate-500">Memuat ringkasan hasil…</p>
      )}

      {summaryError && (
        <p className="text-sm text-red-600">{summaryError}</p>
      )}

      {summary && (
        <div className="mb-6 grid grid-cols-3 gap-3">
          <StatBox label="Total SNP" value={summary.total_snps} />
          <StatBox label="SNP Signifikan" value={summary.significant_snps} />
          <StatBox label="Lambda GC" value={summary.lambda_gc} />
        </div>
      )}

      <div className="flex flex-col gap-3 sm:flex-row">
        <DownloadButton
          label="Download Full Results (ZIP)"
          loadingLabel="Menyiapkan ZIP…"
          variant="primary"
          onDownload={(signal) =>
            triggerBrowserDownload(
              `${API_BASE}/results/${jobId}/export`,
              `gwas_results_${jobId}.zip`,
              signal
            )
          }
        />
        <DownloadButton
          label="Download Summary Report (PDF)"
          loadingLabel="Membuat laporan…"
          variant="secondary"
          onDownload={(signal) =>
            triggerBrowserDownload(
              `${API_BASE}/results/${jobId}/report`,
              `gwas_summary_report_${jobId}.pdf`,
              signal
            )
          }
        />
      </div>

      <p className="mt-3 text-xs text-slate-400">
        Untuk dataset besar, penyiapan file ZIP bisa memakan waktu beberapa
        menit — biarkan tab ini terbuka sampai unduhan dimulai.
      </p>
    </div>
  );
}

function StatBox({ label, value }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3 text-center">
      <div className="text-xl font-bold text-slate-900">
        {value !== undefined && value !== null ? value : "-"}
      </div>
      <div className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
        {label}
      </div>
    </div>
  );
}
