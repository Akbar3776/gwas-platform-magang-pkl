import React, { useState } from "react";
import { useGwasResults } from "../hooks/useGwasResults";
import ManhattanPlot from "./ManhattanPlot";
import QQPlot from "./QQPlot";

const TABS = [
  { key: "manhattan", label: "Manhattan Plot" },
  { key: "qq", label: "QQ Plot" },
];

/**
 * Panel utama visualisasi hasil GWAS. Menangani seluruh siklus state data:
 * loading -> error (dengan tombol "Coba Lagi") -> kosong -> siap render.
 *
 * Styling (className) sengaja dibuat minimal & tanpa CSS-in-JS agar mudah
 * disesuaikan dengan design system proyek Anda sendiri.
 *
 * @param {{ jobId: string }} props
 */
export default function GwasResultsPanel({ jobId }) {
  const { data, isLoading, error, refetch } = useGwasResults(jobId);
  const [activeTab, setActiveTab] = useState("manhattan");

  if (isLoading) {
    return (
      <div className="gwas-results-state" role="status" aria-live="polite">
        <div className="gwas-spinner" />
        <p>Memuat hasil analisis GWAS...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="gwas-results-state gwas-results-error" role="alert">
        <p>Gagal memuat hasil: {error}</p>
        <button type="button" onClick={refetch}>
          Coba Lagi
        </button>
      </div>
    );
  }

  if (!data || data.total_points === 0) {
    return (
      <div className="gwas-results-state">
        <p>Tidak ada data SNP yang bisa ditampilkan untuk job ini.</p>
      </div>
    );
  }

  return (
    <div className="gwas-results-panel">
      <div className="gwas-results-header">
        <h2>Hasil Analisis GWAS</h2>
        <span className="gwas-results-count">
          {data.total_points.toLocaleString("id-ID")} SNP ditampilkan
        </span>
      </div>

      <div className="gwas-results-tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            role="tab"
            type="button"
            aria-selected={activeTab === tab.key}
            className={activeTab === tab.key ? "active" : ""}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="gwas-results-body">
        {activeTab === "manhattan" ? <ManhattanPlot data={data} /> : <QQPlot data={data} />}
      </div>
    </div>
  );
}
