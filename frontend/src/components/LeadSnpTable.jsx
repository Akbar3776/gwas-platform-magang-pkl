import React, { useState } from "react";

export function LeadSnpTable({ leadSnps = [] }) {
  const [filterText, setFilterText] = useState("");
  const [expandedSnp, setExpandedSnp] = useState(null);

  const filtered = leadSnps.filter((s) => {
    const q = filterText.toLowerCase();
    return (
      s.snp?.toLowerCase().includes(q) ||
      String(s.chr).toLowerCase().includes(q) ||
      String(s.pos).includes(q)
    );
  });

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div>
          <h4 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "#0f172a" }}>
            Tabel Lead SNPs Independen ({leadSnps.length} Lokus Ditemukan)
          </h4>
          <p style={{ margin: "2px 0 0", fontSize: 12, color: "#64748b" }}>
            Hasil pengelompokan varian (clumping) berdasarkan Linkage Disequilibrium.
          </p>
        </div>
        <input
          type="text"
          placeholder="Cari SNP, Chr, atau Pos..."
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
          style={{
            padding: "6px 12px",
            fontSize: 12,
            border: "1px solid #cbd5e1",
            borderRadius: 6,
            width: 200,
          }}
        />
      </div>

      {filtered.length === 0 ? (
        <div style={{ padding: 20, textAlign: "center", background: "#f8fafc", borderRadius: 8, color: "#64748b", fontSize: 13 }}>
          {leadSnps.length === 0
            ? "Tidak ada SNP yang lolos ambang batas signifikansi untuk clumping."
            : "Tidak ada SNP yang cocok dengan pencarian."}
        </div>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
            <thead>
              <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0", color: "#475569" }}>
                <th style={{ padding: "8px 12px" }}>Lead SNP</th>
                <th style={{ padding: "8px 12px" }}>Chr</th>
                <th style={{ padding: "8px 12px" }}>Posisi (bp)</th>
                <th style={{ padding: "8px 12px" }}>Alel</th>
                <th style={{ padding: "8px 12px" }}>P-value</th>
                <th style={{ padding: "8px 12px" }}>Beta (SE)</th>
                <th style={{ padding: "8px 12px" }}>Varian Ter-clump</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s, idx) => {
                const isExpanded = expandedSnp === s.snp;
                return (
                  <React.Fragment key={s.snp || idx}>
                    <tr
                      style={{
                        borderBottom: "1px solid #f1f5f9",
                        background: idx % 2 === 0 ? "#fff" : "#fafafa",
                      }}
                    >
                      <td style={{ padding: "8px 12px", fontWeight: 700, color: "#1e40af" }}>
                        {s.snp}
                      </td>
                      <td style={{ padding: "8px 12px" }}>{s.chr}</td>
                      <td style={{ padding: "8px 12px" }}>{(s.pos || 0).toLocaleString("id-ID")}</td>
                      <td style={{ padding: "8px 12px" }}>
                        <span style={{ fontWeight: 600 }}>{s.a1}</span>/{s.a0}
                      </td>
                      <td style={{ padding: "8px 12px", fontWeight: 700, color: "#dc2626" }}>
                        {s.p ? s.p.toExponential(3) : "-"}
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        {s.beta !== undefined ? s.beta.toFixed(3) : "-"} (±{s.se !== undefined ? s.se.toFixed(3) : "-"})
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        {s.n_clumped > 0 ? (
                          <button
                            type="button"
                            onClick={() => setExpandedSnp(isExpanded ? null : s.snp)}
                            style={{
                              background: "#e0e7ff",
                              color: "#3730a3",
                              border: "none",
                              padding: "2px 8px",
                              borderRadius: 4,
                              fontSize: 11,
                              cursor: "pointer",
                              fontWeight: 600,
                            }}
                          >
                            {s.n_clumped} varian {isExpanded ? "▲" : "▼"}
                          </button>
                        ) : (
                          <span style={{ color: "#94a3b8" }}>0 varian</span>
                        )}
                      </td>
                    </tr>
                    {isExpanded && s.clumped_snps && (
                      <tr style={{ background: "#f8fafc" }}>
                        <td colSpan={7} style={{ padding: "8px 16px", fontSize: 11, color: "#475569" }}>
                          <strong>Varian dalam jendela LD:</strong> {s.clumped_snps.join(", ")}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
