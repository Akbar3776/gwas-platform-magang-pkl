import React, { useState, useMemo } from "react";

function formatP(val) {
  if (val === null || val === undefined) return "-";
  const num = Number(val);
  return isNaN(num) ? String(val) : num.toExponential(3);
}

function formatPos(pos) {
  if (pos === null || pos === undefined) return "-";
  const num = Number(pos);
  return isNaN(num) ? String(pos) : num.toLocaleString("id-ID");
}

export function PeakComparisonTable({ comparison, multilocusSummary }) {
  const [filterType, setFilterType] = useState("all");
  const [searchText, setSearchText] = useState("");

  const {
    consistent = [],
    single_only = [],
    multi_only = [],
    summary = {},
  } = comparison || {};

  const allRows = useMemo(() => {
    const list = [
      ...(Array.isArray(consistent) ? consistent : []).map((r) => ({ ...r, category: "consistent" })),
      ...(Array.isArray(multi_only) ? multi_only : []).map((r) => ({ ...r, category: "multi_only" })),
      ...(Array.isArray(single_only) ? single_only : []).map((r) => ({ ...r, category: "single_only" })),
    ];
    if (list.length === 0 && Array.isArray(multilocusSummary?.lead_snps) && multilocusSummary.lead_snps.length > 0) {
      return multilocusSummary.lead_snps.map((s) => ({
        snp: s.snp,
        chr: s.chr,
        pos: s.pos,
        p_single: null,
        p_multi: s.p,
        method: s.method || "Multi-Locus",
        status: "Hanya Multi-Locus",
        keterangan: `Puncak asosiasi teridentifikasi dari evaluasi model ${s.method || "Multi-Locus"}.`,
        category: "multi_only",
      }));
    }
    return list;
  }, [consistent, multi_only, single_only, multilocusSummary?.lead_snps]);

  const displayedRows = useMemo(() => {
    const q = searchText.toLowerCase().trim();
    return allRows.filter((r) => {
      if (filterType !== "all" && r.category !== filterType) return false;
      if (!q) return true;
      const snpStr = String(r.snp || "").toLowerCase();
      const chrStr = String(r.chr || "").toLowerCase();
      const methodStr = String(r.method || "").toLowerCase();
      const statusStr = String(r.status || "").toLowerCase();
      return snpStr.includes(q) || chrStr.includes(q) || methodStr.includes(q) || statusStr.includes(q);
    });
  }, [allRows, filterType, searchText]);

  const modelResults = multilocusSummary?.results || {};
  const hasModels = Object.keys(modelResults).length > 0;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {/* Multi-Locus Execution Models Overview (FarmCPU, BLINK, MLMM) */}
      {hasModels && (
        <div style={{ background: "#f8fafc", padding: 14, borderRadius: 8, border: "1px solid #e2e8f0" }}>
          <h4 style={{ margin: "0 0 10px", fontSize: 13, fontWeight: 700, color: "#1e293b" }}>
            Model Evaluasi Multi-Locus GWAS (Fase 4C):
          </h4>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
            {Object.entries(modelResults).map(([modelKey, info]) => {
              const isDone = info?.status === "completed";
              return (
                <div
                  key={modelKey}
                  style={{
                    padding: 12,
                    background: "#fff",
                    borderRadius: 8,
                    border: "1px solid #cbd5e1",
                    display: "grid",
                    gap: 4,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontWeight: 800, fontSize: 13, color: "#0f172a" }}>{modelKey}</span>
                    <span
                      style={{
                        padding: "2px 6px",
                        borderRadius: 4,
                        fontSize: 10,
                        fontWeight: 700,
                        background: isDone ? "#f0fdf4" : "#fef2f2",
                        color: isDone ? "#15803d" : "#b91c1c",
                      }}
                    >
                      {isDone ? "Selesai" : (info?.status || "Dilewati")}
                    </span>
                  </div>
                  <div style={{ fontSize: 11, color: "#64748b" }}>
                    Total SNP: <strong>{info?.total_snps ? Number(info.total_snps).toLocaleString("id-ID") : "-"}</strong>
                  </div>
                  <div style={{ fontSize: 11, color: "#64748b" }}>
                    P-value minimum: <strong>{formatP(info?.min_p)}</strong>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Summary Stat Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
        <div
          onClick={() => setFilterType("consistent")}
          style={{
            padding: 12,
            background: filterType === "consistent" ? "#dcfce7" : "#f0fdf4",
            borderRadius: 8,
            border: `2px solid ${filterType === "consistent" ? "#16a34a" : "#bbf7d0"}`,
            cursor: "pointer",
            textAlign: "center",
            transition: "all 0.15s ease",
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 700, color: "#166534", textTransform: "uppercase" }}>
            Konsisten (Terkonfirmasi)
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#15803d", marginTop: 2 }}>
            {summary.n_consistent || (Array.isArray(consistent) ? consistent.length : 0)}
          </div>
          <div style={{ fontSize: 10, color: "#166534" }}>Ditemukan di kedua model</div>
        </div>

        <div
          onClick={() => setFilterType("multi_only")}
          style={{
            padding: 12,
            background: filterType === "multi_only" ? "#dbeafe" : "#eff6ff",
            borderRadius: 8,
            border: `2px solid ${filterType === "multi_only" ? "#2563eb" : "#bfdbfe"}`,
            cursor: "pointer",
            textAlign: "center",
            transition: "all 0.15s ease",
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 700, color: "#1e40af", textTransform: "uppercase" }}>
            Hanya Multi-Locus
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#1d4ed8", marginTop: 2 }}>
            {summary.n_multi_only || (Array.isArray(multi_only) ? multi_only.length : 0)}
          </div>
          <div style={{ fontSize: 10, color: "#1e40af" }}>Varian efek minor / BLINK</div>
        </div>

        <div
          onClick={() => setFilterType("single_only")}
          style={{
            padding: 12,
            background: filterType === "single_only" ? "#fef3c7" : "#fffbeb",
            borderRadius: 8,
            border: `2px solid ${filterType === "single_only" ? "#d97706" : "#fef3c7"}`,
            cursor: "pointer",
            textAlign: "center",
            transition: "all 0.15s ease",
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 700, color: "#92400e", textTransform: "uppercase" }}>
            Hanya Single-Locus
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#b45309", marginTop: 2 }}>
            {summary.n_single_only || (Array.isArray(single_only) ? single_only.length : 0)}
          </div>
          <div style={{ fontSize: 10, color: "#92400e" }}>Model GEMMA / Univariat</div>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: "#475569" }}>
            Menampilkan: <strong>{displayedRows.length}</strong> dari {allRows.length} puncak asosiasi
          </span>
          {filterType !== "all" && (
            <button
              type="button"
              onClick={() => setFilterType("all")}
              style={{
                padding: "3px 8px",
                fontSize: 11,
                borderRadius: 4,
                border: "1px solid #cbd5e1",
                background: "#fff",
                cursor: "pointer",
                color: "#2563eb",
                fontWeight: 600,
              }}
            >
              Tampilkan Semua
            </button>
          )}
        </div>

        <input
          type="text"
          placeholder="Cari SNP, Chr, Metode..."
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          style={{
            padding: "6px 12px",
            fontSize: 12,
            border: "1px solid #cbd5e1",
            borderRadius: 6,
            width: 200,
          }}
        />
      </div>

      {/* Table & Empty States */}
      {displayedRows.length === 0 ? (
        <div
          style={{
            padding: 24,
            textAlign: "center",
            background: "#f8fafc",
            borderRadius: 8,
            color: "#64748b",
            fontSize: 13,
          }}
        >
          {allRows.length === 0 ? (
            <div>
              <div style={{ fontSize: 20, marginBottom: 4 }}>📊</div>
              <strong>Belum ada perbandingan puncak asosiasi yang signifikan.</strong>
              <p style={{ margin: "4px 0 0", fontSize: 12 }}>
                Semua varian berada di bawah ambang batas deteksi puncak, atau analisis perbandingan belum dijalankan.
              </p>
            </div>
          ) : (
            <div>
              Tidak ada puncak asosiasi yang sesuai dengan kategori atau pencarian saat ini.
              <button
                type="button"
                onClick={() => {
                  setFilterType("all");
                  setSearchText("");
                }}
                style={{
                  marginLeft: 8,
                  padding: "3px 8px",
                  fontSize: 12,
                  borderRadius: 4,
                  border: "1px solid #cbd5e1",
                  background: "#fff",
                  cursor: "pointer",
                  color: "#2563eb",
                  fontWeight: 600,
                }}
              >
                Reset Filter
              </button>
            </div>
          )}
        </div>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
            <thead>
              <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0", color: "#475569" }}>
                <th style={{ padding: "8px 12px" }}>SNP</th>
                <th style={{ padding: "8px 12px" }}>Chr</th>
                <th style={{ padding: "8px 12px" }}>Posisi (bp)</th>
                <th style={{ padding: "8px 12px" }}>Metode</th>
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
                      {r.snp || "-"}
                    </td>
                    <td style={{ padding: "8px 12px" }}>{r.chr ?? "-"}</td>
                    <td style={{ padding: "8px 12px" }}>{formatPos(r.pos)}</td>
                    <td style={{ padding: "8px 12px" }}>
                      <span
                        style={{
                          padding: "2px 6px",
                          borderRadius: 4,
                          fontSize: 10,
                          fontWeight: 700,
                          background: "#f1f5f9",
                          color: "#334155",
                        }}
                      >
                        {r.method || (r.category === "single_only" ? "GEMMA/LMM" : "Multi-Locus")}
                      </span>
                    </td>
                    <td style={{ padding: "8px 12px", fontFamily: "monospace" }}>
                      {formatP(r.p_single)}
                    </td>
                    <td style={{ padding: "8px 12px", fontFamily: "monospace" }}>
                      {formatP(r.p_multi)}
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
                        {r.status || (r.category === "consistent" ? "Konsisten" : r.category === "multi_only" ? "Hanya Multi-Locus" : "Hanya Single-Locus")}
                      </span>
                    </td>
                    <td style={{ padding: "8px 12px", color: "#64748b" }}>{r.keterangan || "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
