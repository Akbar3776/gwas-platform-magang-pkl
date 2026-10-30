import React, { useState } from "react";

export function PeakComparisonTable({ comparison }) {
  const [filterType, setFilterType] = useState("all");

  if (!comparison) return null;

  const {
    consistent = [],
    single_only = [],
    multi_only = [],
    summary = {},
  } = comparison;

  const allRows = [
    ...consistent.map((r) => ({ ...r, category: "consistent" })),
    ...multi_only.map((r) => ({ ...r, category: "multi_only" })),
    ...single_only.map((r) => ({ ...r, category: "single_only" })),
  ];

  const displayedRows = allRows.filter((r) => {
    if (filterType === "all") return true;
    return r.category === filterType;
  });

  return (
    <div style={{ display: "grid", gap: 14 }}>
      {/* Summary Stat Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
        <div
          onClick={() => setFilterType("consistent")}
          style={{
            padding: 12,
            background: "#f0fdf4",
            borderRadius: 8,
            border: `2px solid ${filterType === "consistent" ? "#16a34a" : "#bbf7d0"}`,
            cursor: "pointer",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 700, color: "#166534", textTransform: "uppercase" }}>
            Konsisten (Terkonfirmasi)
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#15803d", marginTop: 2 }}>
            {summary.n_consistent || 0}
          </div>
        </div>

        <div
          onClick={() => setFilterType("multi_only")}
          style={{
            padding: 12,
            background: "#eff6ff",
            borderRadius: 8,
            border: `2px solid ${filterType === "multi_only" ? "#2563eb" : "#bfdbfe"}`,
            cursor: "pointer",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 700, color: "#1e40af", textTransform: "uppercase" }}>
            Hanya Multi-Locus
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#1d4ed8", marginTop: 2 }}>
            {summary.n_multi_only || 0}
          </div>
        </div>

        <div
          onClick={() => setFilterType("single_only")}
          style={{
            padding: 12,
            background: "#fffbeb",
            borderRadius: 8,
            border: `2px solid ${filterType === "single_only" ? "#d97706" : "#fef3c7"}`,
            cursor: "pointer",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 700, color: "#92400e", textTransform: "uppercase" }}>
            Hanya Single-Locus
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#b45309", marginTop: 2 }}>
            {summary.n_single_only || 0}
          </div>
        </div>
      </div>

      {/* Filter Reset */}
      {filterType !== "all" && (
        <div>
          <button
            type="button"
            onClick={() => setFilterType("all")}
            style={{
              padding: "4px 10px",
              fontSize: 11,
              borderRadius: 4,
              border: "1px solid #cbd5e1",
              background: "#fff",
              cursor: "pointer",
            }}
          >
            Tampilkan Semua Kategori ({allRows.length})
          </button>
        </div>
      )}

      {/* Table */}
      <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 8 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
          <thead>
            <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0", color: "#475569" }}>
              <th style={{ padding: "8px 12px" }}>SNP</th>
              <th style={{ padding: "8px 12px" }}>Chr</th>
              <th style={{ padding: "8px 12px" }}>Posisi (bp)</th>
              <th style={{ padding: "8px 12px" }}>P Single-Locus</th>
              <th style={{ padding: "8px 12px" }}>P Multi-Locus</th>
              <th style={{ padding: "8px 12px" }}>Status Verifikasi</th>
              <th style={{ padding: "8px 12px" }}>Interpretasi</th>
            </tr>
          </thead>
          <tbody>
            {displayedRows.map((r, idx) => {
              let badgeBg = "#f0fdf4";
              let badgeColor = "#15803d";
              if (r.category === "multi_only") {
                badgeBg = "#eff6ff";
                badgeColor = "#1d4ed8";
              } else if (r.category === "single_only") {
                badgeBg = "#fffbeb";
                badgeColor = "#b45309";
              }

              return (
                <tr
                  key={r.snp || idx}
                  style={{
                    borderBottom: "1px solid #f1f5f9",
                    background: idx % 2 === 0 ? "#fff" : "#fafafa",
                  }}
                >
                  <td style={{ padding: "8px 12px", fontWeight: 700, color: "#0f172a" }}>
                    {r.snp}
                  </td>
                  <td style={{ padding: "8px 12px" }}>{r.chr}</td>
                  <td style={{ padding: "8px 12px" }}>{(r.pos || 0).toLocaleString("id-ID")}</td>
                  <td style={{ padding: "8px 12px" }}>
                    {r.p_single !== null && r.p_single !== undefined
                      ? Number(r.p_single).toExponential(3)
                      : "-"}
                  </td>
                  <td style={{ padding: "8px 12px" }}>
                    {r.p_multi !== null && r.p_multi !== undefined
                      ? Number(r.p_multi).toExponential(3)
                      : "-"}
                  </td>
                  <td style={{ padding: "8px 12px" }}>
                    <span
                      style={{
                        padding: "2px 8px",
                        borderRadius: 4,
                        fontSize: 11,
                        fontWeight: 700,
                        background: badgeBg,
                        color: badgeColor,
                      }}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td style={{ padding: "8px 12px", color: "#64748b" }}>{r.keterangan}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
