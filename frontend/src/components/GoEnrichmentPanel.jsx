import React, { useEffect, useRef, useState, useMemo } from "react";
import { Pagination } from "./Pagination";

function interpolateColor(t) {
  // Gradien p.adjust: t=0 (p.adjust paling kecil/signifikan) -> merah (#e25856), t=1 -> biru (#2c7bb6)
  const clamped = Math.max(0, Math.min(1, t));
  const r = Math.round(226 + (44 - 226) * clamped);
  const g = Math.round(88 + (123 - 88) * clamped);
  const b = Math.round(86 + (182 - 86) * clamped);
  return `rgb(${r}, ${g}, ${b})`;
}

export function GoEnrichmentPanel({ goData = null, jobId = null }) {
  const canvasRef = useRef(null);
  const [ontologyFilter, setOntologyFilter] = useState("BP");
  const [hoveredTerm, setHoveredTerm] = useState(null);
  const [goPage, setGoPage] = useState(1);
  const [goPageSize, setGoPageSize] = useState(10);

  const goTable = useMemo(() => goData?.go_table || [], [goData]);
  const allTerms = useMemo(() => goData?.enrichment_terms || [], [goData]);

  const paginatedGoRows = useMemo(() => {
    const start = (goPage - 1) * goPageSize;
    return goTable.slice(start, start + goPageSize);
  }, [goTable, goPage, goPageSize]);

  const displayedTerms = useMemo(() => {
    const filtered =
      ontologyFilter === "ALL"
        ? allTerms
        : allTerms.filter((t) => (t.ontology || "BP") === ontologyFilter);
    return (filtered.length > 0 ? filtered : allTerms).slice(0, 14);
  }, [allTerms, ontologyFilter]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || displayedTerms.length === 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    const padLeft = 285;
    const padRight = 135;
    const padTop = 44;
    const padBottom = 52;
    const plotW = Math.max(180, width - padLeft - padRight);
    const plotH = Math.max(160, height - padTop - padBottom);

    // Judul Plot
    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 15px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(
      `GO Enrichment: ${goData?.trait || "Candidate Genes"} (${ontologyFilter === "ALL" ? "All Domains" : ontologyFilter})`,
      padLeft,
      26
    );

    // Kerangka kotak plot
    ctx.strokeStyle = "#cbd5e1";
    ctx.lineWidth = 1;
    ctx.strokeRect(padLeft, padTop, plotW, plotH);

    const ratios = displayedTerms.map((t) => Number(t.gene_ratio || 0));
    const minRatio = Math.max(0, Math.min(...ratios) * 0.8);
    const maxRatio = Math.max(minRatio + 0.05, Math.max(...ratios) * 1.12);

    const counts = displayedTerms.map((t) => Number(t.count || 1));
    const minCount = Math.min(...counts);
    const maxCount = Math.max(minCount + 1, ...counts);

    const logPs = displayedTerms.map((t) => -Math.log10(Math.max(Number(t.p_adjust || 0.05), 1e-60)));
    const minLogP = Math.min(...logPs);
    const maxLogP = Math.max(minLogP + 0.5, ...logPs);

    // Grid vertikal (GeneRatio)
    const nTicksX = 4;
    ctx.font = "11px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    for (let i = 0; i <= nTicksX; i += 1) {
      const val = minRatio + ((maxRatio - minRatio) * i) / nTicksX;
      const x = padLeft + ((val - minRatio) / (maxRatio - minRatio)) * plotW;
      ctx.strokeStyle = "#f1f5f9";
      ctx.beginPath();
      ctx.moveTo(x, padTop);
      ctx.lineTo(x, padTop + plotH);
      ctx.stroke();

      ctx.fillStyle = "#334155";
      ctx.fillText(val.toFixed(2), x, padTop + plotH + 18);
    }

    // Label sumbu X
    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 12px Inter, system-ui, sans-serif";
    ctx.fillText("GeneRatio", padLeft + plotW / 2, height - 12);

    // Grid horizontal & titik dotplot
    const nRows = displayedTerms.length;
    const rowStep = plotH / Math.max(nRows, 1);

    displayedTerms.forEach((term, idx) => {
      const cy = padTop + rowStep * (idx + 0.5);
      const ratio = Number(term.gene_ratio || 0);
      const cx = padLeft + ((ratio - minRatio) / (maxRatio - minRatio)) * plotW;

      ctx.strokeStyle = "#f1f5f9";
      ctx.beginPath();
      ctx.moveTo(padLeft, cy);
      ctx.lineTo(padLeft + plotW, cy);
      ctx.stroke();

      // Label GO Term di sumbu Y
      ctx.fillStyle = "#0f172a";
      ctx.font = "11.5px Inter, system-ui, sans-serif";
      ctx.textAlign = "right";
      const labelText =
        term.description && term.description.length > 38
          ? `${term.description.slice(0, 36)}…`
          : term.description || term.go_id;
      ctx.fillText(labelText, padLeft - 10, cy + 4);

      // Radius berdasarkan Count (5 px s.d. 13 px)
      const countFrac = (Number(term.count || 1) - minCount) / Math.max(maxCount - minCount, 1);
      const radius = 5.5 + countFrac * 7.5;

      // Warna berdasarkan -log10(p.adjust) (0 = merah/paling signifikan, 1 = biru)
      const logP = -Math.log10(Math.max(Number(term.p_adjust || 0.05), 1e-60));
      const colorT = 1 - (logP - minLogP) / Math.max(maxLogP - minLogP, 0.5);

      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fillStyle = interpolateColor(colorT);
      ctx.fill();
      ctx.strokeStyle = "#1e293b";
      ctx.lineWidth = 1.1;
      ctx.stroke();
    });

    // Legenda kanan: p.adjust color bar + Count size
    const legX = padLeft + plotW + 22;
    const legY = padTop + 18;
    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 11px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("p.adjust", legX, legY);

    const barH = 72;
    const barW = 16;
    for (let py = 0; py < barH; py += 1) {
      ctx.fillStyle = interpolateColor(py / barH);
      ctx.fillRect(legX, legY + 8 + py, barW, 1.5);
    }
    ctx.fillStyle = "#475569";
    ctx.font = "10px Inter, system-ui, sans-serif";
    const minPAdj = Math.min(...displayedTerms.map((t) => Number(t.p_adjust || 0.05)));
    const maxPAdj = Math.max(...displayedTerms.map((t) => Number(t.p_adjust || 0.05)));
    ctx.fillText(minPAdj.toExponential(1), legX + barW + 6, legY + 18);
    ctx.fillText(maxPAdj.toExponential(1), legX + barW + 6, legY + 8 + barH);

    // Legenda Count
    const countLegY = legY + barH + 34;
    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 11px Inter, system-ui, sans-serif";
    ctx.fillText("Count", legX, countLegY);

    const sampleCounts = Array.from(new Set([minCount, Math.round((minCount + maxCount) / 2), maxCount]));
    sampleCounts.forEach((sc, sIdx) => {
      const sy = countLegY + 18 + sIdx * 24;
      const frac = (sc - minCount) / Math.max(maxCount - minCount, 1);
      const sr = 5.5 + frac * 7.5;
      ctx.beginPath();
      ctx.arc(legX + 10, sy, sr, 0, Math.PI * 2);
      ctx.strokeStyle = "#334155";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = "#334155";
      ctx.font = "10px Inter, system-ui, sans-serif";
      ctx.fillText(String(sc), legX + 28, sy + 3.5);
    });
  }, [displayedTerms, goData?.trait, ontologyFilter]);

  const handleDownloadEnrichmentCsv = () => {
    if (!allTerms.length) return;
    const headers = ["ID", "Description", "Ontology", "GeneRatio", "BgRatio", "pvalue", "p.adjust", "Count", "geneID"];
    const lines = allTerms.map((t) =>
      [
        t.go_id || "",
        (t.description || "").replace(/"/g, '""'),
        t.ontology || "BP",
        t.gene_ratio_str || "",
        t.bg_ratio || "",
        t.pvalue ?? "",
        t.p_adjust ?? "",
        t.count ?? "",
        t.gene_id_str || (t.genes || []).join("/"),
      ]
        .map((v) => `"${v}"`)
        .join(",")
    );
    const blob = new Blob([[headers.join(","), ...lines].join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `Hasil_GO_Enrichment_${jobId || "gwas"}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleDownloadGoTableCsv = () => {
    if (!goTable.length) return;
    const headers = ["Gene_ID", "Ortholog", "Biological_Process", "Cellular_Component", "Molecular_Function", "Source"];
    const lines = goTable.map((r) =>
      [
        r.gene_id || "",
        (r.ortholog || "").replace(/"/g, '""'),
        (r.biological_process || "").replace(/"/g, '""'),
        (r.cellular_component || "").replace(/"/g, '""'),
        (r.molecular_function || "").replace(/"/g, '""'),
        r.source_url || "",
      ]
        .map((v) => `"${v}"`)
        .join(",")
    );
    const blob = new Blob([[headers.join(","), ...lines].join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `Tabel_GO_Kandidat_${jobId || "gwas"}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  if (!goData || (!goTable.length && !allTerms.length)) {
    return null;
  }

  return (
    <div style={{ display: "grid", gap: 18, marginTop: 12 }}>
      {/* Bagian 1: Dotplot GO Enrichment (Fase 5.3) */}
      <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0", display: "grid", gap: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
          <div>
            <h4 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "#0f172a" }}>
              Visualisasi Dotplot Gene Ontology (GO) Enrichment — Fase 5.3
            </h4>
            <p style={{ margin: "2px 0 0", fontSize: 12, color: "#64748b" }}>
              Pengayaan istilah fungsional GO berdasarkan `GeneRatio`, `Count`, dan nilai `p.adjust` (Benjamini-Hochberg).
            </p>
          </div>

          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            {[
              { code: "BP", label: "Biological Process (BP)" },
              { code: "CC", label: "Cellular Component (CC)" },
              { code: "MF", label: "Molecular Function (MF)" },
              { code: "ALL", label: "Semua Domain" },
            ].map((opt) => (
              <button
                key={opt.code}
                type="button"
                onClick={() => setOntologyFilter(opt.code)}
                style={{
                  padding: "4px 10px",
                  fontSize: 11,
                  fontWeight: ontologyFilter === opt.code ? 700 : 500,
                  borderRadius: 5,
                  border: "1px solid",
                  borderColor: ontologyFilter === opt.code ? "#2563eb" : "#cbd5e1",
                  background: ontologyFilter === opt.code ? "#2563eb" : "#f8fafc",
                  color: ontologyFilter === opt.code ? "#fff" : "#334155",
                  cursor: "pointer",
                }}
              >
                {opt.label}
              </button>
            ))}
            <button
              type="button"
              onClick={handleDownloadEnrichmentCsv}
              style={{
                padding: "5px 12px",
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 5,
                border: "none",
                background: "#0284c7",
                color: "#fff",
                cursor: "pointer",
              }}
            >
              📥 Unduh Hasil_GO_Enrichment.csv
            </button>
          </div>
        </div>

        <div style={{ overflowX: "auto", border: "1px solid #f1f5f9", borderRadius: 6, background: "#fff" }}>
          <canvas
            ref={canvasRef}
            width={880}
            height={Math.max(320, displayedTerms.length * 30 + 100)}
            style={{ display: "block", maxWidth: "100%", height: "auto" }}
          />
        </div>
      </div>

      {/* Bagian 2: Tabel GO per Gen Kandidat (Biological Process, Cellular Component, Molecular Function, Source) */}
      {goTable.length > 0 && (
        <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0", display: "grid", gap: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
            <div>
              <h4 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "#0f172a" }}>
                Tabel Gene Ontology (GO) &amp; Pemetaan Ortolog Gen Kandidat ({goTable.length} Gen)
              </h4>
              <p style={{ margin: "2px 0 0", fontSize: 12, color: "#64748b" }}>
                Klasifikasi fungsi molekuler, komponen seluler, dan proses biologis beserta referensi TAIR / QuickGO / UniProt.
              </p>
            </div>
            <button
              type="button"
              onClick={handleDownloadGoTableCsv}
              style={{
                padding: "6px 12px",
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 6,
                border: "none",
                background: "#059669",
                color: "#fff",
                cursor: "pointer",
              }}
            >
              📥 Unduh Tabel_GO_Kandidat.csv
            </button>
          </div>

          <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 8 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
              <thead>
                <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0", color: "#475569" }}>
                  <th style={{ padding: "8px 12px" }}>Lokus / Gen</th>
                  <th style={{ padding: "8px 12px" }}>Ortolog / Homolog</th>
                  <th style={{ padding: "8px 12px" }}>Biological Process</th>
                  <th style={{ padding: "8px 12px" }}>Cellular Component</th>
                  <th style={{ padding: "8px 12px" }}>Molecular Function</th>
                  <th style={{ padding: "8px 12px" }}>Source</th>
                </tr>
              </thead>
              <tbody>
                {paginatedGoRows.map((row, idx) => (
                  <tr
                    key={row.gene_id || idx}
                    onMouseEnter={() => setHoveredTerm(row.gene_id)}
                    onMouseLeave={() => setHoveredTerm(null)}
                    style={{
                      borderBottom: "1px solid #f1f5f9",
                      background: hoveredTerm === row.gene_id ? "#eff6ff" : idx % 2 === 0 ? "#fff" : "#fafafa",
                    }}
                  >
                    <td style={{ padding: "8px 12px", fontWeight: 700, color: "#1e40af" }}>{row.gene_id}</td>
                    <td style={{ padding: "8px 12px", color: "#334155", fontWeight: 500 }}>{row.ortholog || "-"}</td>
                    <td style={{ padding: "8px 12px", color: "#0f172a" }}>{row.biological_process || "-"}</td>
                    <td style={{ padding: "8px 12px", color: "#334155" }}>{row.cellular_component || "-"}</td>
                    <td style={{ padding: "8px 12px", color: "#334155" }}>{row.molecular_function || "-"}</td>
                    <td style={{ padding: "8px 12px" }}>
                      {row.source_url ? (
                        <a
                          href={row.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ color: "#2563eb", textDecoration: "underline", fontWeight: 600, fontSize: 11 }}
                        >
                          {row.source_label || "Referensi GO"}
                        </a>
                      ) : (
                        "-"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination
              currentPage={goPage}
              totalItems={goTable.length}
              pageSize={goPageSize}
              onPageChange={setGoPage}
              onPageSizeChange={setGoPageSize}
              pageSizeOptions={[5, 10, 20, 50]}
              itemLabel="Gen GO"
            />
          </div>
        </div>
      )}
    </div>
  );
}
