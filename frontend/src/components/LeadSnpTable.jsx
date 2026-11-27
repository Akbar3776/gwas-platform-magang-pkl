import React, { useState, useMemo, useEffect } from "react";
import { downloadJobFileByName } from "../utils/jobFiles";
import { Pagination } from "./Pagination";

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

function pValue(s) {
  const num = Number(s?.p);
  return Number.isFinite(num) ? num : null;
}

export function LeadSnpTable({
  leadSnps = [],
  suggestiveSnps = [],
  activeThreshold = null,
  activeThresholdMethod = "Bonferroni",
  pipelineThreshold = null,
  isLoading = false,
  jobId = null,
}) {
  const [filterText, setFilterText] = useState("");
  const [expandedSnp, setExpandedSnp] = useState(null);
  const [sortField, setSortField] = useState("p");
  const [sortAsc, setSortAsc] = useState(true);
  const [isDownloading, setIsDownloading] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  const threshold =
    typeof activeThreshold === "number" && Number.isFinite(activeThreshold) && activeThreshold > 0
      ? activeThreshold
      : null;
  const thresholdText = threshold ? `${activeThresholdMethod} ≤ ${threshold.toExponential(2)}` : null;
  const differsFromPipeline =
    threshold !== null &&
    typeof pipelineThreshold === "number" &&
    pipelineThreshold > 0 &&
    Math.abs(threshold - pipelineThreshold) > 1e-6 * Math.max(threshold, pipelineThreshold);

  useEffect(() => {
    setCurrentPage(1);
  }, [filterText, sortField, sortAsc, threshold]);

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

  const safeSuggestiveSnps = useMemo(() => {
    if (Array.isArray(suggestiveSnps) && suggestiveSnps.length > 0) {
      return suggestiveSnps.filter((s) => s && typeof s === "object");
    }
    if (leadSnps && typeof leadSnps === "object" && !Array.isArray(leadSnps)) {
      const alt = Array.isArray(leadSnps.suggestive) ? leadSnps.suggestive : leadSnps.sugestif;
      if (Array.isArray(alt)) return alt.filter((s) => s && typeof s === "object");
    }
    return [];
  }, [suggestiveSnps, leadSnps]);

  // Tabel utama hanya memuat SNP yang lolos ambang aktif; sisanya (mis. data lama yang
  // belum dibagi ulang backend) dipindah ke bagian peak sugestif.
  const significantSnps = useMemo(() => {
    if (threshold === null) return safeLeadSnps;
    return safeLeadSnps.filter((s) => {
      const p = pValue(s);
      return p !== null && p <= threshold;
    });
  }, [safeLeadSnps, threshold]);

  const belowThresholdSnps = useMemo(() => {
    const demoted =
      threshold === null
        ? []
        : safeLeadSnps.filter((s) => {
            const p = pValue(s);
            return p === null || p > threshold;
          });
    return [...demoted, ...safeSuggestiveSnps].sort((a, b) => (pValue(a) ?? 1) - (pValue(b) ?? 1));
  }, [safeLeadSnps, safeSuggestiveSnps, threshold]);

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
    if (!q) return significantSnps;
    return significantSnps.filter((s) => {
      const snpStr = String(s.snp || "").toLowerCase();
      const chrStr = String(s.chr || "").toLowerCase();
      const posStr = String(s.pos || "");
      return snpStr.includes(q) || chrStr.includes(q) || posStr.includes(q);
    });
  }, [significantSnps, filterText]);

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

  const paginatedRows = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return sortedRows.slice(start, start + pageSize);
  }, [sortedRows, currentPage, pageSize]);

  const handleDownloadTsv = async () => {
    if (!jobId) return;
    setIsDownloading(true);
    try {
      await downloadJobFileByName(jobId, "lead_snps.tsv", "postgwas");
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
            Tabel Lead SNPs Independen ({isLoading ? "…" : significantSnps.length} Lokus Teridentifikasi)
          </h4>
          <p style={{ margin: "3px 0 0", fontSize: 12, color: "#64748b" }}>
            Hasil pengelompokan varian (*clumping*) berdasarkan Linkage Disequilibrium.
            {thresholdText && (
              <span style={{ marginLeft: 6, fontWeight: 600, color: "#15803d" }}>
                (ambang aktif {thresholdText})
              </span>
            )}
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          {jobId && significantSnps.length + belowThresholdSnps.length > 0 && (
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
              📥 {isDownloading ? "Mengunduh..." : differsFromPipeline ? "Unduh TSV (ambang pipeline)" : "Unduh TSV"}
            </button>
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

      {differsFromPipeline && (
        <div
          role="note"
          style={{
            padding: "8px 12px",
            background: "#eff6ff",
            borderRadius: 6,
            color: "#1e3a8a",
            fontSize: 12,
            border: "1px solid #bfdbfe",
          }}
        >
          ℹ️ Ambang aktif ({thresholdText}) berbeda dari ambang pipeline (≤ {pipelineThreshold.toExponential(2)}).
          Lead SNP pipeline dipertahankan; puncak tambahan dihitung ulang dengan clumping jendela posisi.
          Anotasi gen, GO, primer, dan file TSV unduhan tetap memakai Lead SNP pipeline.
        </div>
      )}

      {/* Empty States */}
      {isLoading ? (
        <div role="status" style={{ padding: 24, textAlign: "center", background: "#f8fafc", borderRadius: 8, color: "#64748b", fontSize: 13 }}>
          Memuat Lead SNPs{thresholdText ? ` untuk ambang ${thresholdText}` : ""}...
        </div>
      ) : sortedRows.length === 0 ? (
        <div style={{ padding: 24, textAlign: "center", background: "#f8fafc", borderRadius: 8, color: "#64748b", fontSize: 13 }}>
          {significantSnps.length === 0 ? (
            <div>
              <div style={{ fontSize: 20, marginBottom: 6 }}>🎯</div>
              <strong>
                Tidak ada Lead SNPs terdeteksi{thresholdText ? ` pada ambang ${thresholdText}` : ""}.
              </strong>
              <p style={{ margin: "4px 0 0", fontSize: 12 }}>
                {belowThresholdSnps.length > 0
                  ? `Lihat ${belowThresholdSnps.length} peak sugestif / kandidat eksploratif di bawah, atau pilih ambang yang lebih longgar.`
                  : "Tidak ada varian yang mencapai kriteria clumping pada dataset ini."}
              </p>
            </div>
          ) : (
            <div>
              Tidak ada SNP yang cocok dengan kata kunci pencarian.
              <button
                type="button"
                onClick={() => setFilterText("")}
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
                {[
                  { key: "snp", label: "Lead SNP", sortable: true },
                  { key: "chr", label: "Chr", sortable: true },
                  { key: "pos", label: "Posisi (bp)", sortable: true },
                  { key: "allele", label: "Alel", sortable: false },
                  { key: "p", label: "P-value", sortable: true },
                  { key: "beta", label: "Beta (SE)", sortable: true },
                  { key: "clumped", label: "Varian Ter-clump", sortable: false },
                ].map((col) =>
                  col.sortable ? (
                    <th
                      key={col.key}
                      aria-sort={sortField === col.key ? (sortAsc ? "ascending" : "descending") : "none"}
                      style={{ padding: "8px 12px" }}
                    >
                      <button
                        type="button"
                        onClick={() => handleSort(col.key)}
                        style={{
                          background: "none",
                          border: "none",
                          padding: 0,
                          margin: 0,
                          font: "inherit",
                          color: "inherit",
                          fontWeight: "inherit",
                          cursor: "pointer",
                          userSelect: "none",
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 4,
                        }}
                      >
                        {col.label}
                        {sortField === col.key ? (sortAsc ? " ▲" : " ▼") : ""}
                      </button>
                    </th>
                  ) : (
                    <th key={col.key} style={{ padding: "8px 12px" }}>
                      {col.label}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody>
              {paginatedRows.map((s, idx) => {
                const isExpanded = expandedSnp === s.snp;

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
                      <td style={{ padding: "8px 12px", fontWeight: 700, color: threshold ? "#dc2626" : "#475569" }}>
                        {formatP(s.p)}
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        {formatBeta(s.beta)}{formatSe(s.se)}
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
                        <td colSpan={7} style={{ padding: "8px 16px", fontSize: 11, color: "#475569" }}>
                          <strong>Varian dalam jendela LD ({clumpedList.length}):</strong> {clumpedList.join(", ")}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
          <Pagination
            currentPage={currentPage}
            totalItems={sortedRows.length}
            pageSize={pageSize}
            onPageChange={setCurrentPage}
            onPageSizeChange={setPageSize}
            pageSizeOptions={[10, 25, 50, 100]}
            itemLabel="Lead SNPs"
          />
        </div>
      )}

      {!isLoading && belowThresholdSnps.length > 0 && (
        <div
          style={{
            marginTop: 8,
            padding: 14,
            borderRadius: 8,
            border: "1px solid #fde68a",
            background: "#fffbeb",
            display: "grid",
            gap: 10,
          }}
        >
          <div>
            <h5 style={{ margin: 0, fontSize: 13.5, fontWeight: 700, color: "#92400e" }}>
              Peak Sugestif / Kandidat Eksploratif ({belowThresholdSnps.length})
            </h5>
            <p style={{ margin: "3px 0 0", fontSize: 12, color: "#78350f" }}>
              Puncak independen yang tidak lolos ambang aktif{thresholdText ? ` (${thresholdText})` : ""}; tidak digolongkan sebagai Lead SNP signifikan.
            </p>
          </div>
          <div style={{ overflowX: "auto", border: "1px solid #fcd34d", borderRadius: 6, background: "#fff" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
              <thead>
                <tr style={{ background: "#fef3c7", borderBottom: "1px solid #fde68a", color: "#78350f" }}>
                  <th style={{ padding: "8px 12px" }}>SNP</th>
                  <th style={{ padding: "8px 12px" }}>Chr</th>
                  <th style={{ padding: "8px 12px" }}>Posisi (bp)</th>
                  <th style={{ padding: "8px 12px" }}>Alel</th>
                  <th style={{ padding: "8px 12px" }}>P-value</th>
                  <th style={{ padding: "8px 12px" }}>Beta (SE)</th>
                </tr>
              </thead>
              <tbody>
                {belowThresholdSnps.map((s, idx) => {
                  const a1 = s.a1 || s.alt || "-";
                  const a0 = s.a0 || s.ref || "-";
                  const alleleStr = a1 === "-" && a0 === "-" ? "-" : `${a1}/${a0}`;
                  return (
                    <tr key={s.snp || idx} style={{ borderBottom: "1px solid #fef3c7" }}>
                      <td style={{ padding: "8px 12px", fontWeight: 700, color: "#92400e" }}>{s.snp || "-"}</td>
                      <td style={{ padding: "8px 12px" }}>{s.chr ?? "-"}</td>
                      <td style={{ padding: "8px 12px" }}>{formatPos(s.pos)}</td>
                      <td style={{ padding: "8px 12px" }}>{alleleStr}</td>
                      <td style={{ padding: "8px 12px", fontWeight: 600 }}>{formatP(s.p)}</td>
                      <td style={{ padding: "8px 12px" }}>{formatBeta(s.beta)}{formatSe(s.se)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
