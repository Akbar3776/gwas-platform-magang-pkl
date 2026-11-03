import React, { useState, useMemo } from "react";
import { downloadJobFile } from "../api/jobsApi";

function formatBeta(b) {
  if (b === null || b === undefined) return "-";
  const num = Number(b);
  return isNaN(num) ? String(b) : num.toFixed(3);
}

function formatSe(se) {
  if (se === null || se === undefined) return "";
  const num = Number(se);
  return isNaN(num) ? "" : ` (±${num.toFixed(3)})`;
}

function formatP(p) {
  if (p === null || p === undefined) return "-";
  const num = Number(p);
  return isNaN(num) ? String(p) : num.toExponential(3);
}

function formatPos(pos) {
  if (pos === null || pos === undefined) return "-";
  const num = Number(pos);
  return isNaN(num) ? String(pos) : num.toLocaleString("id-ID");
}

export function LeadSnpTable({
  leadSnps = [],
  activeThreshold = null,
  activeThresholdMethod = "Bonferroni",
  jobId = null,
}) {
  const [filterText, setFilterText] = useState("");
  const [onlySignificant, setOnlySignificant] = useState(false);
  const [expandedSnp, setExpandedSnp] = useState(null);
  const [sortField, setSortField] = useState("p");
  const [sortAsc, setSortAsc] = useState(true);
  const [isDownloading, setIsDownloading] = useState(false);

  const safeLeadSnps = useMemo(() => {
    let rawList = leadSnps;
    if (leadSnps && typeof leadSnps === "object" && !Array.isArray(leadSnps)) {
      if (Array.isArray(leadSnps.snps)) {
        rawList = leadSnps.snps;
      } else {
        return [];
      }
    }
    if (!Array.isArray(rawList)) return [];
    return rawList.filter((s) => s && typeof s === "object");
  }, [leadSnps]);

  const numPassThreshold = useMemo(() => {
    if (!activeThreshold || typeof activeThreshold !== "number") return safeLeadSnps.length;
    return safeLeadSnps.filter((s) => {
      const pNum = Number(s.p);
      return !isNaN(pNum) && pNum <= activeThreshold;
    }).length;
  }, [safeLeadSnps, activeThreshold]);

  const handleSort = (field) => {
    if (sortField === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortField(field);
      setSortAsc(field === "p" ? true : false);
    }
  };

  const filtered = useMemo(() => {
    const q = filterText.toLowerCase().trim();
    return safeLeadSnps.filter((s) => {
      const pNum = Number(s.p);
      if (onlySignificant && activeThreshold && !isNaN(pNum)) {
        if (pNum > activeThreshold) return false;
      }
      if (!q) return true;
      const snpStr = String(s.snp || "").toLowerCase();
      const chrStr = String(s.chr || "").toLowerCase();
      const posStr = String(s.pos || "");
      return snpStr.includes(q) || chrStr.includes(q) || posStr.includes(q);
    });
  }, [safeLeadSnps, filterText, onlySignificant, activeThreshold]);

  const sortedRows = useMemo(() => {
    return [...filtered].sort((a, b) => {
      let valA = a[sortField];
      let valB = b[sortField];
      if (sortField === "p" || sortField === "pos" || sortField === "beta" || sortField === "se") {
        valA = Number(valA) || 0;
        valB = Number(valB) || 0;
        return sortAsc ? valA - valB : valB - valA;
      }
      if (sortField === "chr") {
        const numA = Number(valA);
        const numB = Number(valB);
        if (!isNaN(numA) && !isNaN(numB)) return sortAsc ? numA - numB : numB - numA;
        return sortAsc
          ? String(valA || "").localeCompare(String(valB || ""))
          : String(valB || "").localeCompare(String(valA || ""));
      }
      return sortAsc
        ? String(valA || "").localeCompare(String(valB || ""))
        : String(valB || "").localeCompare(String(valA || ""));
    });
  }, [filtered, sortField, sortAsc]);

  const handleDownloadTsv = async () => {
    if (!jobId) return;
    setIsDownloading(true);
    try {
      await downloadJobFile(jobId, "lead_snps.tsv", "lead_snps.tsv");
    } catch (err) {
      alert(`Gagal mengunduh file lead_snps.tsv: ${err.message}`);
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <div style={{ display: "grid", gap: 14 }}>
      {/* Header & Controls */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h4 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "#0f172a" }}>
            Tabel Lead SNPs Independen ({safeLeadSnps.length} Lokus Ditemukan)
          </h4>
          <p style={{ margin: "3px 0 0", fontSize: 12, color: "#64748b" }}>
            Hasil pengelompokan varian (*clumping*) berdasarkan Linkage Disequilibrium.
            {activeThreshold && typeof activeThreshold === "number" && (
              <span style={{ marginLeft: 6, fontWeight: 600, color: "#1d4ed8" }}>
                ({numPassThreshold} lokus signifikan pada ambang {activeThresholdMethod} ≤ {activeThreshold.toExponential(2)})
              </span>
            )}
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          {jobId && safeLeadSnps.length > 0 && (
            <button
              type="button"
              onClick={handleDownloadTsv}
              disabled={isDownloading}
              style={{
                padding: "5px 12px",
                fontSize: 12,
                borderRadius: 6,
                border: "1px solid #cbd5e1",
                background: "#fff",
                cursor: isDownloading ? "not-allowed" : "pointer",
                fontWeight: 600,
                color: "#1e293b",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              📥 {isDownloading ? "Mengunduh..." : "Unduh TSV"}
            </button>
          )}

          {activeThreshold && (
            <label
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                fontSize: 12,
                cursor: "pointer",
                background: onlySignificant ? "#eff6ff" : "#f8fafc",
                border: `1px solid ${onlySignificant ? "#93c5fd" : "#cbd5e1"}`,
                padding: "5px 10px",
                borderRadius: 6,
                color: onlySignificant ? "#1e40af" : "#475569",
                fontWeight: onlySignificant ? 700 : 500,
              }}
            >
              <input
                type="checkbox"
                checked={onlySignificant}
                onChange={(e) => setOnlySignificant(e.target.checked)}
                style={{ cursor: "pointer" }}
              />
              Hanya Signifikan ({numPassThreshold})
            </label>
          )}

          <input
            type="text"
            placeholder="Cari SNP, Chr, Pos..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            style={{
              padding: "6px 12px",
              fontSize: 12,
              border: "1px solid #cbd5e1",
              borderRadius: 6,
              width: 180,
            }}
          />
        </div>
      </div>

      {/* Empty States */}
      {sortedRows.length === 0 ? (
        <div style={{ padding: 24, textAlign: "center", background: "#f8fafc", borderRadius: 8, color: "#64748b", fontSize: 13 }}>
          {safeLeadSnps.length === 0 ? (
            <div>
              <div style={{ fontSize: 20, marginBottom: 6 }}>🎯</div>
              <strong>Tidak ada Lead SNPs terdeteksi.</strong>
              <p style={{ margin: "4px 0 0", fontSize: 12 }}>
                Tidak ada varian yang mencapai ambang batas signifikansi clumping pada parameter yang digunakan.
              </p>
            </div>
          ) : (
            <div>
              Tidak ada SNP yang cocok dengan filter atau kata kunci pencarian.
              <button
                type="button"
                onClick={() => {
                  setFilterText("");
                  setOnlySignificant(false);
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
                <th
                  onClick={() => handleSort("snp")}
                  style={{ padding: "8px 12px", cursor: "pointer", userSelect: "none" }}
                >
                  Lead SNP {sortField === "snp" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th
                  onClick={() => handleSort("chr")}
                  style={{ padding: "8px 12px", cursor: "pointer", userSelect: "none" }}
                >
                  Chr {sortField === "chr" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th
                  onClick={() => handleSort("pos")}
                  style={{ padding: "8px 12px", cursor: "pointer", userSelect: "none" }}
                >
                  Posisi (bp) {sortField === "pos" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th style={{ padding: "8px 12px" }}>Alel</th>
                <th
                  onClick={() => handleSort("p")}
                  style={{ padding: "8px 12px", cursor: "pointer", userSelect: "none" }}
                >
                  P-value {sortField === "p" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th
                  onClick={() => handleSort("beta")}
                  style={{ padding: "8px 12px", cursor: "pointer", userSelect: "none" }}
                >
                  Beta (SE) {sortField === "beta" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th style={{ padding: "8px 12px" }}>Status Ambang</th>
                <th style={{ padding: "8px 12px" }}>Varian Ter-clump</th>
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((s, idx) => {
                const isExpanded = expandedSnp === s.snp;
                const pNum = Number(s.p);
                const isValidP = !isNaN(pNum) && pNum > 0;
                const isSig = activeThreshold && isValidP && pNum <= activeThreshold;

                let clumpedList = [];
                if (Array.isArray(s.clumped_snps)) {
                  clumpedList = s.clumped_snps.filter((c) => c && c !== "." && c !== s.snp);
                } else if (typeof s.clumped_snps === "string" && s.clumped_snps.trim() && s.clumped_snps !== ".") {
                  clumpedList = s.clumped_snps
                    .split(",")
                    .map((c) => c.trim())
                    .filter((c) => c && c !== "." && c !== s.snp);
                }
                const nClumped = typeof s.n_clumped === "number" ? s.n_clumped : clumpedList.length;

                const a1 = s.a1 || s.alt || s.A1 || "-";
                const a0 = s.a0 || s.ref || s.A2 || s.A0 || "-";
                const alleleStr = a1 === "-" && a0 === "-" ? "-" : `${a1}/${a0}`;

                return (
                  <React.Fragment key={s.snp || idx}>
                    <tr
                      style={{
                        borderBottom: "1px solid #f1f5f9",
                        background: idx % 2 === 0 ? "#fff" : "#fafafa",
                      }}
                    >
                      <td style={{ padding: "8px 12px", fontWeight: 700, color: "#1e40af" }}>
                        {s.snp || "-"}
                      </td>
                      <td style={{ padding: "8px 12px" }}>{s.chr ?? "-"}</td>
                      <td style={{ padding: "8px 12px" }}>{formatPos(s.pos)}</td>
                      <td style={{ padding: "8px 12px" }}>
                        <span style={{ fontWeight: 600 }}>{alleleStr}</span>
                      </td>
                      <td style={{ padding: "8px 12px", fontWeight: 700, color: isSig ? "#dc2626" : "#475569" }}>
                        {formatP(s.p)}
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        {formatBeta(s.beta)}{formatSe(s.se)}
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        {activeThreshold ? (
                          <span
                            style={{
                              padding: "2px 8px",
                              borderRadius: 4,
                              fontSize: 10,
                              fontWeight: 700,
                              background: isSig ? "#fef2f2" : "#f1f5f9",
                              color: isSig ? "#b91c1c" : "#64748b",
                            }}
                          >
                            {isSig ? `Signifikan (≤ ${activeThresholdMethod})` : "Di bawah ambang"}
                          </span>
                        ) : (
                          <span style={{ color: "#94a3b8" }}>-</span>
                        )}
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        {nClumped > 0 ? (
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
                            {nClumped} varian {isExpanded ? "▲" : "▼"}
                          </button>
                        ) : (
                          <span style={{ color: "#94a3b8" }}>0 varian</span>
                        )}
                      </td>
                    </tr>
                    {isExpanded && clumpedList.length > 0 && (
                      <tr style={{ background: "#f8fafc" }}>
                        <td colSpan={8} style={{ padding: "8px 16px", fontSize: 11, color: "#475569" }}>
                          <strong>Varian dalam jendela LD ({clumpedList.length}):</strong> {clumpedList.join(", ")}
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
