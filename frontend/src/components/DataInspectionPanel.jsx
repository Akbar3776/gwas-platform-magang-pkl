import React from "react";
import { HistogramChart } from "./HistogramChart";
import { BoxPlotChart } from "./BoxPlotChart";
import { downloadInspectionFile, downloadJobFile } from "../api/jobsApi";

function formatStat(val) {
  if (val === null || val === undefined) return "-";
  const num = Number(val);
  return isNaN(num) ? String(val) : num.toFixed(3);
}

export function DataInspectionPanel({ report, fileId, jobId }) {
  if (!report || report.status === "not_available" || report.status === "skipped") {
    return (
      <div style={{ padding: 16, background: "#f8fafc", borderRadius: 8, color: "#64748b", fontSize: 13 }}>
        Data inspeksi fenotipe tidak tersedia atau tahap inspeksi dilewati untuk job ini.
      </div>
    );
  }

  const {
    trait = "-",
    id_column = "-",
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
    try {
      if (fileId) {
        await downloadInspectionFile(fileId, filename);
      } else if (jobId) {
        await downloadJobFile(jobId, filename, filename);
      }
    } catch (err) {
      alert(`Gagal mengunduh file ${filename}: ${err.message}`);
    }
  };

  const hasStats = stats && typeof stats === "object" && Number(stats.N) > 0;
  const hasHist = histogram && Array.isArray(histogram.edges) && histogram.edges.length > 1;
  const hasBox = boxplot && typeof boxplot === "object" && (boxplot.median !== undefined || boxplot.q1 !== undefined);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {/* Blocking Errors */}
      {Array.isArray(blocking_errors) && blocking_errors.length > 0 && (
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
      {((Array.isArray(warnings) && warnings.length > 0) || (Array.isArray(pheno_duplicate_ids) && pheno_duplicate_ids.length > 0)) && (
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
            {Array.isArray(warnings) && warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
            {Array.isArray(pheno_duplicate_ids) && pheno_duplicate_ids.length > 0 && (
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
          <div style={{ fontSize: 18, fontWeight: 800, color: "#0f172a", marginTop: 2 }}>{n_vcf_samples.toLocaleString("id-ID")}</div>
        </div>
        <div style={{ padding: 10, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Baris Fenotipe</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#0f172a", marginTop: 2 }}>{n_pheno_rows.toLocaleString("id-ID")}</div>
        </div>
        <div style={{ padding: 10, background: "#f0fdf4", borderRadius: 8, border: "1px solid #bbf7d0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#166534", fontWeight: 700, textTransform: "uppercase" }}>Cocok & Valid</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#15803d", marginTop: 2 }}>{n_matched.toLocaleString("id-ID")}</div>
        </div>
        <div style={{ padding: 10, background: "#fef2f2", borderRadius: 8, border: "1px solid #fecaca", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#991b1b", fontWeight: 700, textTransform: "uppercase" }}>Missing / Non-Num</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#b91c1c", marginTop: 2 }}>{(missing_count + non_numeric_count).toLocaleString("id-ID")}</div>
        </div>
        <div style={{ padding: 10, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Hanya di VCF</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#334155", marginTop: 2 }}>{only_in_vcf_count.toLocaleString("id-ID")}</div>
        </div>
        <div style={{ padding: 10, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Hanya di Fenotipe</div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#334155", marginTop: 2 }}>{only_in_pheno_count.toLocaleString("id-ID")}</div>
        </div>
      </div>

      {/* Descriptive Statistics Table */}
      {hasStats && (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: 14 }}>
          <h4 style={{ margin: "0 0 10px", fontSize: 13, fontWeight: 700, color: "#0f172a" }}>
            Statistik Deskriptif Trait: <span style={{ color: "#2563eb" }}>{trait}</span> (Kolom ID: {id_column})
          </h4>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(85px, 1fr))", gap: 8, textAlign: "center" }}>
            <div style={{ padding: "8px 4px", background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
              <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700 }}>N Valid</div>
              <div style={{ fontWeight: 800, fontSize: 14, color: "#0f172a", marginTop: 2 }}>{stats.N}</div>
            </div>
            <div style={{ padding: "8px 4px", background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
              <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700 }}>Mean</div>
              <div style={{ fontWeight: 700, fontSize: 13, color: "#0f172a", marginTop: 2 }}>{formatStat(stats.mean)}</div>
            </div>
            <div style={{ padding: "8px 4px", background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
              <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700 }}>Median</div>
              <div style={{ fontWeight: 700, fontSize: 13, color: "#0f172a", marginTop: 2 }}>{formatStat(stats.median)}</div>
            </div>
            <div style={{ padding: "8px 4px", background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
              <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700 }}>SD</div>
              <div style={{ fontWeight: 700, fontSize: 13, color: "#0f172a", marginTop: 2 }}>{formatStat(stats.sd)}</div>
            </div>
            <div style={{ padding: "8px 4px", background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
              <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700 }}>Min</div>
              <div style={{ fontWeight: 700, fontSize: 13, color: "#0f172a", marginTop: 2 }}>{formatStat(stats.min)}</div>
            </div>
            <div style={{ padding: "8px 4px", background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
              <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700 }}>Q1</div>
              <div style={{ fontWeight: 700, fontSize: 13, color: "#0f172a", marginTop: 2 }}>{formatStat(stats.q1)}</div>
            </div>
            <div style={{ padding: "8px 4px", background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
              <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700 }}>Q3</div>
              <div style={{ fontWeight: 700, fontSize: 13, color: "#0f172a", marginTop: 2 }}>{formatStat(stats.q3)}</div>
            </div>
            <div style={{ padding: "8px 4px", background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
              <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700 }}>Max</div>
              <div style={{ fontWeight: 700, fontSize: 13, color: "#0f172a", marginTop: 2 }}>{formatStat(stats.max)}</div>
            </div>
            <div style={{ padding: "8px 4px", background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
              <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700 }}>Skewness</div>
              <div style={{ fontWeight: 700, fontSize: 13, color: "#0f172a", marginTop: 2 }}>{formatStat(stats.skewness)}</div>
            </div>
          </div>
        </div>
      )}

      {/* Chart Row */}
      {(hasHist || hasBox) && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 16 }}>
          {hasHist && (
            <div style={{ background: "#fff", padding: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}>
              <HistogramChart
                edges={histogram.edges}
                counts={histogram.counts}
                title={`Distribusi Frekuensi Trait (${trait})`}
                width={420}
                height={220}
              />
            </div>
          )}
          {hasBox && (
            <div style={{ background: "#fff", padding: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}>
              <BoxPlotChart
                data={boxplot}
                title={`Boxplot & Outlier (${trait})`}
                width={380}
                height={220}
              />
            </div>
          )}
        </div>
      )}

      {/* Download Action Buttons */}
      {(fileId || jobId) && (
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, paddingTop: 4 }}>
          <span style={{ fontSize: 12, color: "#64748b", fontWeight: 600 }}>Unduh File Rekonsiliasi ID:</span>
          <button
            type="button"
            onClick={() => handleDownload("keep_ids.txt")}
            style={{
              padding: "5px 12px",
              fontSize: 12,
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              background: "#fff",
              cursor: "pointer",
              fontWeight: 600,
              color: "#334155",
            }}
          >
            📥 keep_ids.txt
          </button>
          <button
            type="button"
            onClick={() => handleDownload("pheno_plink.txt")}
            style={{
              padding: "5px 12px",
              fontSize: 12,
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              background: "#fff",
              cursor: "pointer",
              fontWeight: 600,
              color: "#334155",
            }}
          >
            📥 pheno_plink.txt
          </button>
        </div>
      )}
    </div>
  );
}
