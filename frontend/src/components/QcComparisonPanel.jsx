import React from "react";
import { HistogramChart } from "./HistogramChart";

function formatCount(val) {
  if (val === null || val === undefined) return "0";
  const num = Number(val);
  return isNaN(num) ? String(val) : num.toLocaleString("id-ID");
}

export function QcComparisonPanel({ comparison }) {
  if (!comparison || comparison.status === "not_available" || comparison.status === "skipped") {
    return (
      <div style={{ padding: 16, background: "#f8fafc", borderRadius: 8, color: "#64748b", fontSize: 13 }}>
        Data evaluasi perbandingan QC belum tersedia untuk job ini.
      </div>
    );
  }

  const {
    before = {},
    after = {},
    removed = {},
    snp_loss_fraction = 0,
    warning = null,
  } = comparison;

  const numFraction = typeof snp_loss_fraction === "number" ? snp_loss_fraction : Number(snp_loss_fraction) || 0;
  const lossPct = (numFraction * 100).toFixed(1);

  const hasBeforeHist = before?.maf_hist?.edges?.length > 1;
  const hasAfterHist = after?.maf_hist?.edges?.length > 1;
  const hasBeforeMiss = before?.missing_hist?.edges?.length > 1;
  const hasAfterMiss = after?.missing_hist?.edges?.length > 1;

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
            {formatCount(before.n_snps)}
          </div>
        </div>
        <div style={{ padding: 12, background: "#f0fdf4", borderRadius: 8, border: "1px solid #bbf7d0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#166534", fontWeight: 700, textTransform: "uppercase" }}>SNP Lolos QC</div>
          <div style={{ fontSize: 20, fontWeight: 800, color: "#15803d", marginTop: 2 }}>
            {formatCount(after.n_snps)}
          </div>
          <div style={{ fontSize: 11, color: "#16a34a", marginTop: 2 }}>(-{lossPct}%)</div>
        </div>
        <div style={{ padding: 12, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Sampel Sebelum QC</div>
          <div style={{ fontSize: 20, fontWeight: 800, color: "#0f172a", marginTop: 2 }}>
            {formatCount(before.n_samples)}
          </div>
        </div>
        <div style={{ padding: 12, background: "#f0fdf4", borderRadius: 8, border: "1px solid #bbf7d0", textAlign: "center" }}>
          <div style={{ fontSize: 11, color: "#166534", fontWeight: 700, textTransform: "uppercase" }}>Sampel Lolos QC</div>
          <div style={{ fontSize: 20, fontWeight: 800, color: "#15803d", marginTop: 2 }}>
            {formatCount(after.n_samples)}
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
            <span style={{ fontSize: 11, color: "#64748b" }}>Overflow 32-bit (Fase 0)</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{formatCount(removed.overflow_32bit)}</div>
          </div>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>Filter MAF</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{formatCount(removed.maf)}</div>
          </div>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>Missing SNP (GENO)</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{formatCount(removed.geno)}</div>
          </div>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>Missing Sampel (MIND)</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{formatCount(removed.mind)}</div>
          </div>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>HWE (Outcross)</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{formatCount(removed.hwe)}</div>
          </div>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>Non-SNP (Indel dsb)</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{formatCount(removed.non_snp)}</div>
          </div>
          <div style={{ padding: 8, background: "#f8fafc", borderRadius: 6, border: "1px solid #f1f5f9" }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>Multi-alelik (&gt;2 alel)</span>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#b91c1c" }}>{formatCount(removed.multiallelic)}</div>
          </div>
        </div>
      </div>

      {/* Histograms: Before vs After (MAF) */}
      {(hasBeforeHist || hasAfterHist) && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 14 }}>
          {hasBeforeHist && (
            <div style={{ background: "#fff", padding: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}>
              <HistogramChart
                edges={before.maf_hist.edges}
                counts={before.maf_hist.counts}
                title="Distribusi MAF (Sebelum QC)"
                xLabel="Minor Allele Frequency"
                width={380}
                height={200}
              />
            </div>
          )}
          {hasAfterHist && (
            <div style={{ background: "#fff", padding: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}>
              <HistogramChart
                edges={after.maf_hist.edges}
                counts={after.maf_hist.counts}
                title="Distribusi MAF (Sesudah QC)"
                xLabel="Minor Allele Frequency"
                width={380}
                height={200}
              />
            </div>
          )}
        </div>
      )}

      {/* Histograms: Before vs After (Missingness Varian) */}
      {(hasBeforeMiss || hasAfterMiss) && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 14 }}>
          {hasBeforeMiss && (
            <div style={{ background: "#fff", padding: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}>
              <HistogramChart
                edges={before.missing_hist.edges}
                counts={before.missing_hist.counts}
                title="Missingness Varian (Sebelum QC)"
                xLabel="Tingkat Missing (F_MISS)"
                width={380}
                height={200}
              />
            </div>
          )}
          {hasAfterMiss && (
            <div style={{ background: "#fff", padding: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}>
              <HistogramChart
                edges={after.missing_hist.edges}
                counts={after.missing_hist.counts}
                title="Missingness Varian (Sesudah QC)"
                xLabel="Tingkat Missing (F_MISS)"
                width={380}
                height={200}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
