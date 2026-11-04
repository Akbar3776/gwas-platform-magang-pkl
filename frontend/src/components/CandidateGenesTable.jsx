import React, { useState, useMemo } from "react";
import { GoEnrichmentPanel } from "./GoEnrichmentPanel";

export function CandidateGenesTable({ annotationData = null, goEnrichmentData = null, jobId = null }) {
  const [filterText, setFilterText] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("ALL");
  const [expandedSnp, setExpandedSnp] = useState(null);

  const mappings = useMemo(
    () => annotationData?.mappings || annotationData?.rows || [],
    [annotationData]
  );

  const uniqueGenes = useMemo(() => {
    if (Array.isArray(annotationData?.candidate_genes_list) && annotationData.candidate_genes_list.length > 0) {
      return annotationData.candidate_genes_list;
    }
    const geneSet = new Set();
    for (const m of mappings) {
      const gid = m.closest_gene?.gene_id || (m.nearest_gene !== "-" ? m.nearest_gene : null);
      if (gid && (m.category === "Genik" || m.category === "Near-gen")) {
        geneSet.add(gid);
      }
      for (const gw of m.genes_in_window || []) {
        const wid = gw.gene_id || gw.id;
        if (wid) geneSet.add(wid);
      }
    }
    return Array.from(geneSet).sort();
  }, [annotationData, mappings]);

  const windowKb = annotationData?.window_kb || Math.round((annotationData?.window_bp || 100000) / 1000);

  // Category counts
  const categoryCounts = useMemo(() => {
    const counts = { Genik: 0, "Near-gen": 0, Intergenik: 0 };
    for (const m of mappings) {
      if (counts[m.category] !== undefined) {
        counts[m.category] += 1;
      }
    }
    return counts;
  }, [mappings]);

  // Filtered rows
  const filtered = useMemo(() => {
    const q = filterText.toLowerCase().trim();
    return mappings.filter((item) => {
      if (categoryFilter !== "ALL" && item.category !== categoryFilter) {
        return false;
      }
      if (!q) return true;
      const closestId = item.closest_gene?.gene_id || (item.nearest_gene !== "-" ? item.nearest_gene : "") || "";
      const closestName = item.closest_gene?.gene_name || (item.nearest_gene_name !== "-" ? item.nearest_gene_name : "") || "";
      const desc = item.description || item.closest_gene?.description || "";
      const snpMatch = String(item.snp || "").toLowerCase().includes(q);
      const chrMatch = String(item.chr || "").toLowerCase().includes(q);
      const geneIdMatch = closestId.toLowerCase().includes(q);
      const geneNameMatch = closestName.toLowerCase().includes(q);
      const descMatch = desc.toLowerCase().includes(q);
      const inWindowMatch = item.genes_in_window?.some((g) => {
        const gid = g.gene_id || g.id || "";
        const gname = g.gene_name || g.name || "";
        return gid.toLowerCase().includes(q) || gname.toLowerCase().includes(q);
      });
      return snpMatch || chrMatch || geneIdMatch || geneNameMatch || descMatch || inWindowMatch;
    });
  }, [mappings, filterText, categoryFilter]);

  const handleDownloadGoList = () => {
    if (!uniqueGenes.length) return;
    const content = uniqueGenes.join("\n") + "\n";
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `Daftar_Gen_Untuk_GO_${jobId || "gwas"}.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const handleDownloadCsv = () => {
    if (!mappings.length) return;
    const headers = [
      "SNP",
      "Kromosom",
      "Posisi",
      "P_Value",
      "Kategori",
      "Gen_Terdekat",
      "Nama_Gen",
      "Jarak_bp",
      "Strand",
      "Deskripsi",
      "Gen_Dalam_Jendela",
    ];
    const rowsCsv = mappings.map((r) => {
      const gid = r.closest_gene?.gene_id || r.nearest_gene || "-";
      const gname = r.closest_gene?.gene_name || r.nearest_gene_name || "-";
      const dist =
        r.distance_to_closest_gene !== undefined && r.distance_to_closest_gene !== null
          ? r.distance_to_closest_gene
          : r.distance_bp ?? -1;
      const winIds = (r.genes_in_window || [])
        .map((g) => g.gene_id || g.id)
        .filter(Boolean)
        .join("; ");
      const vals = [
        r.snp || "",
        r.chr || "",
        r.pos ?? "",
        r.p ?? "",
        r.category || "",
        gid,
        gname,
        dist,
        r.strand || r.closest_gene?.strand || "-",
        (r.description || r.closest_gene?.description || "").replace(/"/g, '""'),
        winIds,
      ];
      return vals.map((v) => `"${v}"`).join(",");
    });
    const csvContent = [headers.join(","), ...rowsCsv].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `Ekstraksi_Gen_Kandidat_${jobId || "gwas"}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  if (!annotationData || annotationData.status === "skipped" || (!mappings.length && !annotationData.total_lead_snps)) {
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
        <div style={{ fontSize: 24, marginBottom: 8 }}>🧬</div>
        <div style={{ fontWeight: 600, color: "#334155", marginBottom: 4 }}>
          Anotasi Gen Kandidat GFF3 Belum Tersedia / Dilewati
        </div>
        <p style={{ margin: 0, fontSize: 13, maxWidth: 500, marginInline: "auto" }}>
          File anotasi GFF3 (.gff3 atau .gff3.gz) opsional belum diunggah saat memulai job GWAS, atau
          tidak ada Lead SNP signifikan yang ditemukan untuk dipetakan ke gen.
        </p>
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 14 }}>
      {/* Header & Metric Badges */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h4 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#0f172a" }}>
            Anotasi Gen Kandidat GFF3
          </h4>
          <p style={{ margin: "3px 0 0", fontSize: 12, color: "#64748b" }}>
            Pemetaan Lead SNPs ke fitur gen dalam jendela LD decay (±{windowKb} kb).
          </p>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {mappings.length > 0 && (
            <button
              type="button"
              onClick={handleDownloadCsv}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
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
              📥 Unduh Tabel CSV ({mappings.length} SNP)
            </button>
          )}
          {uniqueGenes.length > 0 && (
            <button
              type="button"
              onClick={handleDownloadGoList}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                background: "#10b981",
                color: "#fff",
                border: "none",
                padding: "7px 14px",
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              📥 Unduh Daftar Gen untuk GO ({uniqueGenes.length} Gen)
            </button>
          )}
        </div>
      </div>

      {/* Summary KPI Cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: 10,
        }}
      >
        <div style={{ padding: "10px 14px", background: "#f8fafc", borderRadius: 6, border: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", fontWeight: 600 }}>Total Lead SNPs</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#0f172a" }}>{annotationData.total_lead_snps || mappings.length}</div>
        </div>
        <div style={{ padding: "10px 14px", background: "#f0fdf4", borderRadius: 6, border: "1px solid #bbf7d0" }}>
          <div style={{ fontSize: 11, color: "#166534", textTransform: "uppercase", fontWeight: 600 }}>Genik (Dalam Gen)</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#15803d" }}>{categoryCounts.Genik}</div>
        </div>
        <div style={{ padding: "10px 14px", background: "#fffbeb", borderRadius: 6, border: "1px solid #fef3c7" }}>
          <div style={{ fontSize: 11, color: "#92400e", textTransform: "uppercase", fontWeight: 600 }}>Near-Gen (&le; {windowKb} kb)</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#b45309" }}>{categoryCounts["Near-gen"]}</div>
        </div>
        <div style={{ padding: "10px 14px", background: "#f1f5f9", borderRadius: 6, border: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: 11, color: "#475569", textTransform: "uppercase", fontWeight: 600 }}>Intergenik (&gt; {windowKb} kb)</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#334155" }}>{categoryCounts.Intergenik}</div>
        </div>
        <div style={{ padding: "10px 14px", background: "#eff6ff", borderRadius: 6, border: "1px solid #bfdbfe" }}>
          <div style={{ fontSize: 11, color: "#1e40af", textTransform: "uppercase", fontWeight: 600 }}>Gen Kandidat Unik</div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#2563eb" }}>{uniqueGenes.length}</div>
        </div>
      </div>

      {/* Search and Category Filter Controls */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div style={{ display: "flex", gap: 6 }}>
          {["ALL", "Genik", "Near-gen", "Intergenik"].map((cat) => (
            <button
              key={cat}
              type="button"
              onClick={() => setCategoryFilter(cat)}
              style={{
                padding: "4px 10px",
                fontSize: 12,
                borderRadius: 4,
                border: "1px solid",
                borderColor: categoryFilter === cat ? "#2563eb" : "#cbd5e1",
                background: categoryFilter === cat ? "#2563eb" : "#fff",
                color: categoryFilter === cat ? "#fff" : "#475569",
                fontWeight: categoryFilter === cat ? 600 : 400,
                cursor: "pointer",
              }}
            >
              {cat === "ALL" ? "Semua Kategori" : cat}
            </button>
          ))}
        </div>

        <input
          type="text"
          placeholder="Cari SNP, Chr, atau Gen ID..."
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
          style={{
            padding: "6px 12px",
            fontSize: 12,
            border: "1px solid #cbd5e1",
            borderRadius: 6,
            width: 240,
          }}
        />
      </div>

      {/* Table */}
      {filtered.length === 0 ? (
        <div style={{ padding: 20, textAlign: "center", background: "#f8fafc", borderRadius: 8, color: "#64748b", fontSize: 13 }}>
          Tidak ada SNP atau gen kandidat yang cocok dengan filter.
        </div>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
            <thead>
              <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0", color: "#475569" }}>
                <th style={{ padding: "8px 12px" }}>Lead SNP</th>
                <th style={{ padding: "8px 12px" }}>Chr:Pos</th>
                <th style={{ padding: "8px 12px" }}>P-value</th>
                <th style={{ padding: "8px 12px" }}>Kategori</th>
                <th style={{ padding: "8px 12px" }}>SOURCE / TYPE</th>
                <th style={{ padding: "8px 12px" }}>Gen Terdekat (ID)</th>
                <th style={{ padding: "8px 12px" }}>START – END (GFF3)</th>
                <th style={{ padding: "8px 12px" }}>Jarak (bp)</th>
                <th style={{ padding: "8px 12px" }}>Gen dalam Jendela (±{windowKb} kb)</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item, idx) => {
                const isExpanded = expandedSnp === item.snp;
                const closest =
                  item.closest_gene ||
                  (item.nearest_gene && item.nearest_gene !== "-"
                    ? {
                        gene_id: item.nearest_gene,
                        gene_name: item.nearest_gene_name || item.nearest_gene,
                        strand: item.strand || "-",
                        description: item.description || "",
                      }
                    : null);
                const distVal =
                  item.distance_to_closest_gene !== undefined && item.distance_to_closest_gene !== null
                    ? item.distance_to_closest_gene
                    : item.distance_bp !== undefined && item.distance_bp >= 0
                    ? item.distance_bp
                    : null;
                const inWindowCount = item.genes_in_window_count ?? item.genes_in_window?.length ?? 0;

                const categoryBadge = {
                  Genik: { bg: "#dcfce7", color: "#15803d", border: "#86efac" },
                  "Near-gen": { bg: "#fef3c7", color: "#b45309", border: "#fde68a" },
                  Intergenik: { bg: "#f1f5f9", color: "#475569", border: "#cbd5e1" },
                }[item.category] || { bg: "#f1f5f9", color: "#475569", border: "#cbd5e1" };

                return (
                  <React.Fragment key={item.snp || idx}>
                    <tr
                      style={{
                        borderBottom: "1px solid #f1f5f9",
                        background: idx % 2 === 0 ? "#fff" : "#fafafa",
                      }}
                    >
                      <td style={{ padding: "8px 12px", fontWeight: 700, color: "#1e40af" }}>
                        {item.snp}
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        {item.chr}:{(item.pos || 0).toLocaleString("id-ID")}
                      </td>
                      <td style={{ padding: "8px 12px", fontWeight: 700, color: "#dc2626" }}>
                        {typeof item.p === "number" ? item.p.toExponential(3) : "-"}
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        <span
                          style={{
                            display: "inline-block",
                            padding: "2px 8px",
                            borderRadius: 12,
                            fontSize: 11,
                            fontWeight: 600,
                            background: categoryBadge.bg,
                            color: categoryBadge.color,
                            border: `1px solid ${categoryBadge.border}`,
                          }}
                        >
                          {item.category}
                        </span>
                      </td>
                      <td style={{ padding: "8px 12px", fontSize: 11, color: "#475569" }}>
                        <span style={{ fontWeight: 600 }}>{item.source || closest?.source || "GFF3"}</span>
                        {" / "}
                        <span>{item.feature_type || closest?.feature_type || "gene"}</span>
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        {closest ? (
                          <div>
                            <div>
                              <span style={{ fontWeight: 600, color: "#0f172a" }}>{closest.gene_id}</span>
                              {closest.gene_name && closest.gene_name !== closest.gene_id && (
                                <span style={{ color: "#64748b", marginLeft: 4 }}>({closest.gene_name})</span>
                              )}
                              {closest.strand && closest.strand !== "-" && (
                                <span style={{ color: "#94a3b8", marginLeft: 4, fontSize: 11 }}>
                                  [{closest.strand}]
                                </span>
                              )}
                            </div>
                            {closest.description && (
                              <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>
                                {closest.description}
                              </div>
                            )}
                          </div>
                        ) : (
                          <div>
                            <span style={{ color: "#94a3b8" }}>-</span>
                            {item.description && (
                              <div style={{ fontSize: 11, color: "#94a3b8", fontStyle: "italic", marginTop: 2 }}>
                                {item.description}
                              </div>
                            )}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: "8px 12px", fontFamily: "monospace", fontSize: 11, color: "#334155" }}>
                        {(item.gene_start || closest?.start) && (item.gene_end || closest?.end)
                          ? `${Number(item.gene_start || closest?.start).toLocaleString("id-ID")} – ${Number(
                              item.gene_end || closest?.end
                            ).toLocaleString("id-ID")}`
                          : "-"}
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        {distVal === 0 ? (
                          <span style={{ color: "#15803d", fontWeight: 600 }}>0 (Genik)</span>
                        ) : distVal !== null && distVal !== undefined ? (
                          `${distVal.toLocaleString("id-ID")} bp`
                        ) : (
                          <span style={{ color: "#94a3b8" }}>-</span>
                        )}
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        {inWindowCount > 0 ? (
                          <button
                            type="button"
                            onClick={() => setExpandedSnp(isExpanded ? null : item.snp)}
                            style={{
                              background: "#e0e7ff",
                              color: "#3730a3",
                              border: "none",
                              padding: "3px 8px",
                              borderRadius: 4,
                              fontSize: 11,
                              cursor: "pointer",
                              fontWeight: 600,
                            }}
                          >
                            {inWindowCount} gen {isExpanded ? "▲ Tutup" : "▼ Rincian"}
                          </button>
                        ) : (
                          <span style={{ color: "#94a3b8" }}>0 gen</span>
                        )}
                      </td>
                    </tr>

                    {/* Expandable detail row for genes in window */}
                    {isExpanded && item.genes_in_window && item.genes_in_window.length > 0 && (
                      <tr style={{ background: "#f8fafc" }}>
                        <td colSpan={9} style={{ padding: "10px 14px" }}>
                          <div
                            style={{
                              border: "1px solid #e2e8f0",
                              borderRadius: 6,
                              padding: 10,
                              background: "#fff",
                            }}
                          >
                            <div style={{ fontSize: 11, fontWeight: 700, color: "#334155", marginBottom: 6 }}>
                              Daftar {item.genes_in_window.length} Gen dalam Jendela ±{windowKb} kb dari {item.snp} (Chr {item.chr}:{item.pos}):
                            </div>
                            <div style={{ display: "grid", gap: 6, maxHeight: 180, overflowY: "auto" }}>
                              {item.genes_in_window.map((g, gIdx) => {
                                const gid = g.gene_id || g.id;
                                const gname = g.gene_name || g.name;
                                const gdist =
                                  g.distance !== undefined && g.distance !== null
                                    ? g.distance
                                    : g.start <= item.pos && item.pos <= g.end
                                    ? 0
                                    : item.pos < g.start
                                    ? g.start - item.pos
                                    : item.pos - g.end;
                                return (
                                  <div
                                    key={gid || gIdx}
                                    style={{
                                      display: "flex",
                                      justifyContent: "space-between",
                                      fontSize: 11,
                                      padding: "4px 8px",
                                      background: "#f8fafc",
                                      borderRadius: 4,
                                      border: "1px solid #f1f5f9",
                                    }}
                                  >
                                    <div>
                                      <strong style={{ color: "#1e40af" }}>{gid}</strong>
                                      {gname && gname !== gid && (
                                        <span style={{ color: "#64748b", marginLeft: 4 }}>({gname})</span>
                                      )}
                                      <span style={{ color: "#94a3b8", marginLeft: 8 }}>
                                        pos: {g.start?.toLocaleString("id-ID")} - {g.end?.toLocaleString("id-ID")} ({g.strand})
                                      </span>
                                      {g.desc && (
                                        <span style={{ color: "#64748b", marginLeft: 8 }}>— {g.desc}</span>
                                      )}
                                    </div>
                                    <div>
                                      <span style={{ color: gdist === 0 ? "#15803d" : "#475569", fontWeight: gdist === 0 ? 600 : 400 }}>
                                        {gdist === 0 ? "Tumpang Tindih (Genik)" : `${gdist?.toLocaleString("id-ID")} bp`}
                                      </span>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
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

      {/* Bagian Fase 5.3: Gene Ontology (GO) Enrichment Dotplot & Tabel GO */}
      <GoEnrichmentPanel
        goData={goEnrichmentData || annotationData?.go_enrichment}
        jobId={jobId}
      />
    </div>
  );
}
