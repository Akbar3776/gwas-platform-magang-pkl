import React from "react";
import { HistogramChart } from "./HistogramChart";

export function QcComparisonPanel({ comparison }) {
  if (!comparison) return null;

  const {
    before = {},
    after = {},
    removed = {},
    snp_loss_fraction = 0,
    warning = null,
  } = comparison;

  const lossPct = (snp_loss_fraction * 100).toFixed(1);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {/* Warning Alert if SNP loss is high */}
      {warning && (
        <div
          role="alert"
          style={{
            padding: 12,
            borderRadius: 8,
            background: "#fffbeb",
            border: "1px solid #fef3c7",
            color: "#92400e",
            fontSize: 13,
          }}
        >
          <strong>⚠️ Peringatan Filter QC:</strong> {warning}
        </div>
      )}

      {/* Summary Cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: 12,
        }}
      >
        <div style={{ padding: 12, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>SNP Sebelum QC</div>
          <div style={{ fontSize: 20, fontWeight: 800, color: "#0f172a", marginTop: 2 }}>
            {(before.n_snps || 0).toLocaleString("id-ID")}
          </div>
        </div>
        <div style={{ padding: 12, background: "#f0fdf4", borderRadius: 8, border: "1px solid #bbf7d0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#166534", fontWeight: 700, textTransform: "uppercase" }}>SNP Lolos QC</div>
          <div style={{ fontSize: 20, fontWeight: 800, color: "#15803d", marginTop: 2 }}>
            {(after.n_snps || 0).toLocaleString("id-ID")}
          </div>
          <div style={{ fontSize: 11, color: "#16a34a", marginTop: 2 }}>(-{lossPct}%)</div>
        </div>
        <div style={{ padding: 12, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Sampel Sebelum QC</div>
          <div style={{ fontSize: 20, fontWeight: 800, color: "#0f172a", marginTop: 2 }}>
            {(before.n_samples || 0).toLocaleString("id-ID")}
          </div>
        </div>
        <div style={{ padding: 12, background: "#f0fdf4", borderRadius: 8, border: "1px solid #bbf7d0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#166534", fontWeight: 700, textTransform: "uppercase" }}>Sampel Lolos QC</div>
          <div style={{ fontSize: 20, fontWeight: 800, color: "#15803d", marginTop: 2 }}>
            {(after.n_samples || 0).toLocaleString("id-ID")}
          </div>
        </div>
      </div>

      {/* Filter Removal Breakdown */}
      <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: 14 }}>
        <h4 style={{ margin: "0 0 10px", fontSize: 13, fontWeight: 700, color: "#1e293b" }}>
          Rincian Varian & Sampel Terbuang per Kriteria QC:
        </h4>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 10 }}>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>Filter MAF</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{removed.maf || 0}</div>
          </div>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>Missing SNP (GENO)</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{removed.geno || 0}</div>
          </div>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>Missing Sampel (MIND)</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{removed.mind || 0}</div>
          </div>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>HWE (Outcross)</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{removed.hwe || 0}</div>
          </div>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>Non-SNP (Indel dsb)</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{removed.non_snp || 0}</div>
          </div>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>Multi-alelik (&gt;2 alel)</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{removed.multiallelic || 0}</div>
          </div>
        </div>
      </div>

      {/* Histograms: Before vs After */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))", gap: 14 }}>
        {before.maf_hist?.edges?.length > 1 && (
          <HistogramChart
            edges={before.maf_hist.edges}
            counts={before.maf_hist.counts}
            title="Distribusi MAF (Sebelum QC)"
            xLabel="Minor Allele Frequency"
            width={380}
            height={200}
          />
        )}
        {after.maf_hist?.edges?.length > 1 && (
          <HistogramChart
            edges={after.maf_hist.edges}
            counts={after.maf_hist.counts}
            title="Distribusi MAF (Sesudah QC)"
            xLabel="Minor Allele Frequency"
            width={380}
            height={200}
          />
        )}
      </div>
    </div>
  );
}
