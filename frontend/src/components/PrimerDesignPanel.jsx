import React, { useState, useMemo, useEffect } from "react";
import { Pagination } from "./Pagination";

function renderHighlightedFlanking(seqStr) {
  if (!seqStr) return "-";
  const match = seqStr.match(/^([A-Za-z]+)\[([A-Za-z/]+)\]([A-Za-z]+)$/);
  if (!match) return seqStr;
  return (
    <span>
      <span>{match[1]}</span>
      <span
        style={{
          background: "#fef08a",
          color: "#b91c1c",
          fontWeight: 800,
          padding: "1px 4px",
          borderRadius: 3,
          marginInline: 2,
          border: "1px solid #facc15",
        }}
      >
        [{match[2]}]
      </span>
      <span>{match[3]}</span>
    </span>
  );
}

export function PrimerDesignPanel({ primersData = null, jobId = null }) {
  const [searchText, setSearchText] = useState("");
  const [viewMode, setViewMode] = useState("both"); // "both" | "primer3" | "table"
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(5);

  useEffect(() => {
    setCurrentPage(1);
  }, [searchText, viewMode]);

  const markers = useMemo(() => primersData?.markers || [], [primersData]);

  const filteredMarkers = useMemo(() => {
    const q = searchText.toLowerCase().trim();
    if (!q) return markers;
    return markers.filter((m) => {
      const idMatch = String(m.marker_id || "").toLowerCase().includes(q);
      const snpMatch = String(m.snp || "").toLowerCase().includes(q);
      const chrMatch = String(m.chr || "").toLowerCase().includes(q);
      const geneMatch = String(m.nearest_gene || "").toLowerCase().includes(q);
      return idMatch || snpMatch || chrMatch || geneMatch;
    });
  }, [markers, searchText]);

  const paginatedMarkers = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredMarkers.slice(start, start + pageSize);
  }, [filteredMarkers, currentPage, pageSize]);

  const handleDownloadPrimer3Txt = () => {
    if (!markers.length) return;
    const blocks = markers.map(
      (m) => `### ${m.rank}. ${m.marker_id}\n\n> ${m.flanking_sequence}\n\n\`\`\`\n${m.primer3_output}\n\`\`\`\n`
    );
    const blob = new Blob([blocks.join("\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `Flanking_dan_Primer3_${jobId || "gwas"}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleDownloadPrimerCsv = () => {
    if (!markers.length) return;
    const headers = [
      "Rank",
      "Marker_ID",
      "SNP",
      "Chr",
      "Pos",
      "Nearest_Gene",
      "Pair_Index",
      "Left_Start",
      "Left_Len",
      "Left_Tm",
      "Left_GC",
      "Left_Seq",
      "Right_Start",
      "Right_Len",
      "Right_Tm",
      "Right_GC",
      "Right_Seq",
      "Product_Size_bp",
      "Flanking_201bp",
    ];
    const rows = [];
    for (const m of markers) {
      if (!m.primer_pairs || m.primer_pairs.length === 0) {
        rows.push(
          [
            m.rank,
            m.marker_id,
            m.snp,
            m.chr,
            m.pos,
            m.nearest_gene || "-",
            0,
            "-",
            "-",
            "-",
            "-",
            "no primers found",
            "-",
            "-",
            "-",
            "-",
            "no primers found",
            "-",
            m.flanking_sequence,
          ]
            .map((v) => `"${v}"`)
            .join(",")
        );
      } else {
        m.primer_pairs.forEach((p, idx) => {
          rows.push(
            [
              m.rank,
              m.marker_id,
              m.snp,
              m.chr,
              m.pos,
              m.nearest_gene || "-",
              idx + 1,
              p.left.start,
              p.left.len,
              p.left.tm,
              p.left.gc,
              p.left.seq,
              p.right.start,
              p.right.len,
              p.right.tm,
              p.right.gc,
              p.right.seq,
              p.product_size,
              m.flanking_sequence,
            ]
              .map((v) => `"${v}"`)
              .join(",")
          );
        });
      }
    }
    const blob = new Blob([[headers.join(","), ...rows].join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `Desain_Primer_Kandidat_${jobId || "gwas"}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  if (!primersData || !markers.length) {
    return (
      <div
        style={{
          padding: 24,
          borderRadius: 8,
          background: "#f8fafc",
          border: "1px dashed #cbd5e1",
          textAlign: "center",
          color: "#64748b",
        }}
      >
        <div style={{ fontSize: 24, marginBottom: 8 }}>🧪</div>
        <div style={{ fontWeight: 600, color: "#334155", marginBottom: 4 }}>
          Sekuens Flanking &amp; Desain Primer PCR (Primer3) Belum Tersedia
        </div>
        <p style={{ margin: 0, fontSize: 13, maxWidth: 500, marginInline: "auto" }}>
          Tidak ditemukan Lead SNP kandidat pada job ini untuk ekstraksi sekuens flanking 201 bp dan desain primer.
        </p>
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {/* Header & Tombol Ekspor */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h4 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#0f172a" }}>
            Ekstraksi Sekuens Flanking (201 bp) &amp; Desain Primer PCR (Primer3) — Fase 5.4 / Fase 6–8
          </h4>
          <p style={{ margin: "3px 0 0", fontSize: 12, color: "#64748b" }}>
            Sekuens 100 bp upstream + `[Alel Target]` + 100 bp downstream beserta pasangan `LEFT PRIMER` &amp; `RIGHT PRIMER`.
          </p>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            onClick={handleDownloadPrimer3Txt}
            style={{
              background: "#2563eb",
              color: "#fff",
              border: "none",
              padding: "7px 14px",
              borderRadius: 6,
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            📥 Unduh Laporan Primer3 (.txt)
          </button>
          <button
            type="button"
            onClick={handleDownloadPrimerCsv}
            style={{
              background: "#059669",
              color: "#fff",
              border: "none",
              padding: "7px 14px",
              borderRadius: 6,
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            📥 Unduh Tabel Primer (.csv)
          </button>
        </div>
      </div>

      {/* KPI Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
        <div style={{ padding: "10px 14px", background: "#f8fafc", borderRadius: 6, border: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", fontWeight: 600 }}>Total Lokus Target</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#0f172a" }}>{primersData.total_markers || markers.length}</div>
        </div>
        <div style={{ padding: "10px 14px", background: "#f0fdf4", borderRadius: 6, border: "1px solid #bbf7d0" }}>
          <div style={{ fontSize: 11, color: "#166534", textTransform: "uppercase", fontWeight: 600 }}>Primer Berhasil Didesain</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#15803d" }}>
            {primersData.markers_with_primers ?? markers.filter((m) => m.primer_pairs_count > 0).length} Lokus
          </div>
        </div>
        <div style={{ padding: "10px 14px", background: "#eff6ff", borderRadius: 6, border: "1px solid #bfdbfe" }}>
          <div style={{ fontSize: 11, color: "#1e40af", textTransform: "uppercase", fontWeight: 600 }}>Ukuran Sekuens Flanking</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#2563eb" }}>201 bp (±100 bp)</div>
        </div>
        <div style={{ padding: "10px 14px", background: "#fffbeb", borderRadius: 6, border: "1px solid #fde68a" }}>
          <div style={{ fontSize: 11, color: "#92400e", textTransform: "uppercase", fontWeight: 600 }}>Parameter GC% &amp; Tm</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#b45309", marginTop: 2 }}>GC 35–50% · Tm ~54–56°C</div>
        </div>
      </div>

      {/* Filter & Mode Tampilan */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div style={{ display: "flex", gap: 6 }}>
          {[
            { id: "both", label: "Blok Primer3 + Tabel" },
            { id: "primer3", label: "Format Blok Primer3" },
            { id: "table", label: "Tabel Oligo" },
          ].map((opt) => (
            <button
              key={opt.id}
              type="button"
              onClick={() => setViewMode(opt.id)}
              style={{
                padding: "4px 10px",
                fontSize: 11,
                fontWeight: viewMode === opt.id ? 700 : 500,
                borderRadius: 5,
                border: "1px solid",
                borderColor: viewMode === opt.id ? "#2563eb" : "#cbd5e1",
                background: viewMode === opt.id ? "#2563eb" : "#fff",
                color: viewMode === opt.id ? "#fff" : "#475569",
                cursor: "pointer",
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <input
          type="text"
          placeholder="Cari ID Penanda, SNP, Chr, atau Gen..."
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          style={{
            padding: "6px 12px",
            fontSize: 12,
            border: "1px solid #cbd5e1",
            borderRadius: 6,
            width: 240,
          }}
        />
      </div>

      {/* Daftar Sekuens Flanking & Primer per SNP */}
      <div style={{ display: "grid", gap: 14 }}>
        {paginatedMarkers.map((item) => (
          <div
            key={item.marker_id || item.rank}
            style={{
              background: "#fff",
              border: "1px solid #cbd5e1",
              borderRadius: 8,
              padding: 14,
              display: "grid",
              gap: 10,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span style={{ fontWeight: 800, fontSize: 14, color: "#0f172a" }}>
                  ### {item.rank}. {item.marker_id}
                </span>
                <span
                  style={{
                    fontSize: 11,
                    padding: "2px 8px",
                    borderRadius: 12,
                    background: "#f1f5f9",
                    color: "#334155",
                    fontWeight: 600,
                  }}
                >
                  SNP: {item.snp} (Chr {item.chr}:{Number(item.pos || 0).toLocaleString("id-ID")})
                </span>
                {item.nearest_gene && item.nearest_gene !== "-" && (
                  <span
                    style={{
                      fontSize: 11,
                      padding: "2px 8px",
                      borderRadius: 12,
                      background: "#dcfce7",
                      color: "#166534",
                      fontWeight: 600,
                    }}
                  >
                    Gen: {item.nearest_gene}
                  </span>
                )}
              </div>

              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  padding: "3px 8px",
                  borderRadius: 4,
                  background: item.primer_pairs_count > 0 ? "#eff6ff" : "#fef2f2",
                  color: item.primer_pairs_count > 0 ? "#1d4ed8" : "#b91c1c",
                }}
              >
                {item.primer_pairs_count > 0 ? `${item.primer_pairs_count} Pasang Primer` : "no primers found"}
              </span>
            </div>

            {/* Sekuens Flanking 201 bp */}
            <div
              style={{
                background: "#f8fafc",
                borderLeft: "4px solid #2563eb",
                padding: "8px 12px",
                borderRadius: "0 6px 6px 0",
                fontFamily: "monospace",
                fontSize: 11.5,
                lineHeight: 1.55,
                wordBreak: "break-all",
                color: "#1e293b",
              }}
            >
              &gt; {renderHighlightedFlanking(item.flanking_sequence)}
            </div>

            {/* Format Blok Teks Primer3 (sesuai HASIL OUTPUT.md) */}
            {(viewMode === "both" || viewMode === "primer3") && (
              <pre
                style={{
                  margin: 0,
                  padding: "10px 12px",
                  background: "#0f172a",
                  color: "#e2e8f0",
                  borderRadius: 6,
                  fontSize: 11,
                  lineHeight: 1.45,
                  overflowX: "auto",
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                }}
              >
                {item.primer3_output || "no primers found"}
              </pre>
            )}

            {/* Tabel Oligo */}
            {(viewMode === "both" || viewMode === "table") && item.primer_pairs && item.primer_pairs.length > 0 && (
              <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 6 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5, textAlign: "left" }}>
                  <thead>
                    <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0", color: "#475569" }}>
                      <th style={{ padding: "6px 10px" }}>Pasangan</th>
                      <th style={{ padding: "6px 10px" }}>OLIGO</th>
                      <th style={{ padding: "6px 10px" }}>start</th>
                      <th style={{ padding: "6px 10px" }}>len</th>
                      <th style={{ padding: "6px 10px" }}>tm (°C)</th>
                      <th style={{ padding: "6px 10px" }}>gc%</th>
                      <th style={{ padding: "6px 10px" }}>any_th</th>
                      <th style={{ padding: "6px 10px" }}>3&apos;_th</th>
                      <th style={{ padding: "6px 10px" }}>hairpin</th>
                      <th style={{ padding: "6px 10px" }}>seq (5&apos; → 3&apos;)</th>
                      <th style={{ padding: "6px 10px" }}>PRODUCT SIZE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {item.primer_pairs.map((pair, pIdx) => (
                      <React.Fragment key={pIdx}>
                        <tr style={{ borderBottom: "1px solid #f1f5f9", background: pIdx % 2 === 0 ? "#fff" : "#fafafa" }}>
                          <td rowSpan={2} style={{ padding: "6px 10px", fontWeight: 700, color: "#1e40af" }}>
                            #{pIdx + 1}
                          </td>
                          <td style={{ padding: "6px 10px", fontWeight: 600, color: "#0f766e" }}>LEFT PRIMER</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{pair.left.start}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{pair.left.len}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{Number(pair.left.tm).toFixed(2)}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{Number(pair.left.gc).toFixed(2)}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{Number(pair.left.any_th).toFixed(2)}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{Number(pair.left.three_th).toFixed(2)}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{Number(pair.left.hairpin).toFixed(2)}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace", fontWeight: 700, color: "#0f172a" }}>
                            {pair.left.seq}
                          </td>
                          <td rowSpan={2} style={{ padding: "6px 10px", fontFamily: "monospace", fontWeight: 700, color: "#1d4ed8" }}>
                            {pair.product_size} bp
                          </td>
                        </tr>
                        <tr style={{ borderBottom: "1px solid #e2e8f0", background: pIdx % 2 === 0 ? "#fff" : "#fafafa" }}>
                          <td style={{ padding: "6px 10px", fontWeight: 600, color: "#b45309" }}>RIGHT PRIMER</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{pair.right.start}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{pair.right.len}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{Number(pair.right.tm).toFixed(2)}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{Number(pair.right.gc).toFixed(2)}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{Number(pair.right.any_th).toFixed(2)}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{Number(pair.right.three_th).toFixed(2)}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace" }}>{Number(pair.right.hairpin).toFixed(2)}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace", fontWeight: 700, color: "#0f172a" }}>
                            {pair.right.seq}
                          </td>
                        </tr>
                      </React.Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}
      </div>

      <Pagination
        currentPage={currentPage}
        totalItems={filteredMarkers.length}
        pageSize={pageSize}
        onPageChange={setCurrentPage}
        onPageSizeChange={setPageSize}
        pageSizeOptions={[5, 10, 20, 50]}
        itemLabel="Lokus Target Marker"
      />
    </div>
  );
}
