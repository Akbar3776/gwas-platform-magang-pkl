import React from "react";
import { HistogramChart } from "./HistogramChart";
import { BoxPlotChart } from "./BoxPlotChart";
import { downloadInspectionFile } from "../api/jobsApi";

export function DataInspectionPanel({ report, fileId }) {
  if (!report) return null;

  const {
    trait,
    id_column,
    n_vcf_samples = 0,
    n_pheno_rows = 0,
    n_matched = 0,
    missing_count = 0,
    non_numeric_count = 0,
    only_in_vcf_count = 0,
    only_in_pheno_count = 0,
    pheno_duplicate_ids = [],
    stats = {},
    histogram = {},
    boxplot = {},
    warnings = [],
    blocking_errors = [],
  } = report;

  const handleDownload = async (filename) => {
    if (!fileId) return;
    try {
      await downloadInspectionFile(fileId, filename);
    } catch (err) {
      alert(`Gagal mengunduh file ${filename}: ${err.message}`);
    }
  };

  return (
    <div style={{ display: "grid", gap: 16, marginTop: 12 }}>
      {/* Blocking Errors */}
      {blocking_errors.length > 0 && (
        <div
          role="alert"
          style={{
            padding: 14,
            borderRadius: 8,
            background: "#fef2f2",
            border: "1px solid #fecaca",
            color: "#991b1b",
          }}
        >
          <h4 style={{ margin: "0 0 6px", fontSize: 14, fontWeight: 700 }}>
            Kesalahan Fatal (Pipeline Tidak Dapat Dimulai):
          </h4>
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: 13 }}>
            {blocking_errors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Warnings */}
      {(warnings.length > 0 || pheno_duplicate_ids.length > 0) && (
        <div
          style={{
            padding: 12,
            borderRadius: 8,
            background: "#fffbeb",
            border: "1px solid #fef3c7",
            color: "#92400e",
            fontSize: 13,
          }}
        >
          <strong style={{ display: "block", marginBottom: 4 }}>Peringatan Kualitas Data:</strong>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
            {pheno_duplicate_ids.length > 0 && (
              <li>
                Terdapat {pheno_duplicate_ids.length} ID sampel duplikat di file fenotipe:{" "}
                {pheno_duplicate_ids.slice(0, 8).join(", ")}
                {pheno_duplicate_ids.length > 8 ? "..." : ""}
              </li>
            )}
          </ul>
        </div>
      )}

      {/* Metric Cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
          gap: 10,
        }}
      >
        <div style={{ padding: 10, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Sampel VCF</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#0f172a", marginTop: 2 }}>{n_vcf_samples}</div>
        </div>
        <div style={{ padding: 10, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Baris Fenotipe</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#0f172a", marginTop: 2 }}>{n_pheno_rows}</div>
        </div>
        <div style={{ padding: 10, background: "#f0fdf4", borderRadius: 8, border: "1px solid #bbf7d0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#166534", fontWeight: 700, textTransform: "uppercase" }}>Cocok & Valid</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#15803d", marginTop: 2 }}>{n_matched}</div>
        </div>
        <div style={{ padding: 10, background: "#fef2f2", borderRadius: 8, border: "1px solid #fecaca", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#991b1b", fontWeight: 700, textTransform: "uppercase" }}>Missing / Non-Num</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#b91c1c", marginTop: 2 }}>{missing_count + non_numeric_count}</div>
        </div>
        <div style={{ padding: 10, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Hanya di VCF</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#334155", marginTop: 2 }}>{only_in_vcf_count}</div>
        </div>
        <div style={{ padding: 10, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Hanya di Fenotipe</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#334155", marginTop: 2 }}>{only_in_pheno_count}</div>
        </div>
      </div>

      {/* Descriptive Statistics Table */}
      {stats && stats.N > 0 && (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: 12 }}>
          <h4 style={{ margin: "0 0 8px", fontSize: 13, fontWeight: 700, color: "#0f172a" }}>
            Statistik Deskriptif Trait: {trait} (Kolom ID: {id_column})
          </h4>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(90px, 1fr))", gap: 8, textAlign: "center" }}>
            <div style={{ padding: "6px 4px", background: "#f8fafc", borderRadius: 6 }}>
              <div style={{ fontSize: 10, color: "#64748b" }}>N</div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{stats.N}</div>
            </div>
            <div style={{ padding: "6px 4px", background: "#f8fafc", borderRadius: 6 }}>
              <div style={{ fontSize: 10, color: "#64748b" }}>Mean</div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{stats.mean}</div>
            </div>
            <div style={{ padding: "6px 4px", background: "#f8fafc", borderRadius: 6 }}>
              <div style={{ fontSize: 10, color: "#64748b" }}>Median</div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{stats.median}</div>
            </div>
            <div style={{ padding: "6px 4px", background: "#f8fafc", borderRadius: 6 }}>
              <div style={{ fontSize: 10, color: "#64748b" }}>SD</div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{stats.sd}</div>
            </div>
            <div style={{ padding: "6px 4px", background: "#f8fafc", borderRadius: 6 }}>
              <div style={{ fontSize: 10, color: "#64748b" }}>Min</div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{stats.min}</div>
            </div>
            <div style={{ padding: "6px 4px", background: "#f8fafc", borderRadius: 6 }}>
              <div style={{ fontSize: 10, color: "#64748b" }}>Q1</div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{stats.q1}</div>
            </div>
            <div style={{ padding: "6px 4px", background: "#f8fafc", borderRadius: 6 }}>
              <div style={{ fontSize: 10, color: "#64748b" }}>Q3</div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{stats.q3}</div>
            </div>
            <div style={{ padding: "6px 4px", background: "#f8fafc", borderRadius: 6 }}>
              <div style={{ fontSize: 10, color: "#64748b" }}>Max</div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{stats.max}</div>
            </div>
            <div style={{ padding: "6px 4px", background: "#f8fafc", borderRadius: 6 }}>
              <div style={{ fontSize: 10, color: "#64748b" }}>Skewness</div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{stats.skewness}</div>
            </div>
          </div>
        </div>
      )}

      {/* Chart Row */}
      {histogram && histogram.edges && histogram.edges.length > 1 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
          <HistogramChart
            edges={histogram.edges}
            counts={histogram.counts}
            title={`Distribusi Frekuensi Trait (${trait})`}
            width={420}
            height={220}
          />
          <BoxPlotChart
            data={boxplot}
            title={`Boxplot & Outlier (${trait})`}
            width={380}
            height={220}
          />
        </div>
      )}

      {/* Download Action Buttons for Phase 1 Reconciliation Files */}
      {fileId && (
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, paddingTop: 4 }}>
          <span style={{ fontSize: 12, color: "#64748b", fontWeight: 600 }}>Unduh File Rekonsiliasi ID:</span>
          <button
            type="button"
            onClick={() => handleDownload("keep_ids.txt")}
            style={{
              padding: "4px 10px",
              fontSize: 12,
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              background: "#fff",
              color: "#1e293b",
              cursor: "pointer",
            }}
          >
            📥 keep_ids.txt ({n_matched})
          </button>
          <button
            type="button"
            onClick={() => handleDownload("id_hanya_vcf.txt")}
            style={{
              padding: "4px 10px",
              fontSize: 12,
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              background: "#fff",
              color: "#1e293b",
              cursor: "pointer",
            }}
          >
            📥 id_hanya_vcf.txt ({only_in_vcf_count})
          </button>
          <button
            type="button"
            onClick={() => handleDownload("id_hanya_fenotipe.txt")}
            style={{
              padding: "4px 10px",
              fontSize: 12,
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              background: "#fff",
              color: "#1e293b",
              cursor: "pointer",
            }}
          >
            📥 id_hanya_fenotipe.txt ({only_in_pheno_count})
          </button>
          <button
            type="button"
            onClick={() => handleDownload("sampel_vcf.txt")}
            style={{
              padding: "4px 10px",
              fontSize: 12,
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              background: "#fff",
              color: "#1e293b",
              cursor: "pointer",
            }}
          >
            📥 sampel_vcf.txt ({n_vcf_samples})
          </button>
        </div>
      )}
    </div>
  );
}
