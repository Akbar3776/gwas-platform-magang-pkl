import React, { useState, useMemo } from "react";

export function CandidateGenesTable({ annotationData = null, jobId = null }) {
  const [filterText, setFilterText] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("ALL");
  const [expandedSnp, setExpandedSnp] = useState(null);

  const mappings = useMemo(() => annotationData?.mappings || [], [annotationData]);
  const uniqueGenes = useMemo(() => annotationData?.candidate_genes_list || [], [annotationData]);
  const windowKb = Math.round((annotationData?.window_bp || 50000) / 1000);

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
      const snpMatch = item.snp?.toLowerCase().includes(q);
      const chrMatch = String(item.chr).toLowerCase().includes(q);
      const geneIdMatch = item.closest_gene?.gene_id?.toLowerCase().includes(q);
      const geneNameMatch = item.closest_gene?.gene_name?.toLowerCase().includes(q);
      const inWindowMatch = item.genes_in_window?.some(
        (g) => g.gene_id?.toLowerCase().includes(q) || g.gene_name?.toLowerCase().includes(q)
      );
      return snpMatch || chrMatch || geneIdMatch || geneNameMatch || inWindowMatch;
    });
  }, [mappings, filterText, categoryFilter]);

  const handleDownloadGoList = () => {
    if (!uniqueGenes.length) return;
    const content = uniqueGenes.join("\n");
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
                <th style={{ padding: "8px 12px" }}>Gen Terdekat</th>
                <th style={{ padding: "8px 12px" }}>Jarak (bp)</th>
                <th style={{ padding: "8px 12px" }}>Gen dalam Jendela (±{windowKb} kb)</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item, idx) => {
                const isExpanded = expandedSnp === item.snp;
                const closest = item.closest_gene;
                const inWindowCount = item.genes_in_window_count || item.genes_in_window?.length || 0;

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
                        {item.p ? item.p.toExponential(3) : "-"}
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
                      <td style={{ padding: "8px 12px" }}>
                        {closest ? (
                          <div>
                            <span style={{ fontWeight: 600, color: "#0f172a" }}>{closest.gene_id}</span>
                            {closest.gene_name && closest.gene_name !== closest.gene_id && (
                              <span style={{ color: "#64748b", marginLeft: 4 }}>({closest.gene_name})</span>
                            )}
                          </div>
                        ) : (
                          <span style={{ color: "#94a3b8" }}>-</span>
                        )}
                      </td>
                      <td style={{ padding: "8px 12px" }}>
                        {item.distance_to_closest_gene === 0 ? (
                          <span style={{ color: "#15803d", fontWeight: 600 }}>0 (Genik)</span>
                        ) : item.distance_to_closest_gene !== null && item.distance_to_closest_gene !== undefined ? (
                          `${item.distance_to_closest_gene.toLocaleString("id-ID")} bp`
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
                        <td colSpan={7} style={{ padding: "10px 14px" }}>
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
                              {item.genes_in_window.map((g, gIdx) => (
                                <div
                                  key={g.gene_id || gIdx}
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
                                    <strong style={{ color: "#1e40af" }}>{g.gene_id}</strong>
                                    {g.gene_name && g.gene_name !== g.gene_id && (
                                      <span style={{ color: "#64748b", marginLeft: 4 }}>({g.gene_name})</span>
                                    )}
                                    <span style={{ color: "#94a3b8", marginLeft: 8 }}>
                                      pos: {g.start?.toLocaleString("id-ID")} - {g.end?.toLocaleString("id-ID")} ({g.strand})
                                    </span>
                                  </div>
                                  <div>
                                    <span style={{ color: g.distance === 0 ? "#15803d" : "#475569", fontWeight: g.distance === 0 ? 600 : 400 }}>
                                      {g.distance === 0 ? "Tumpang Tindih (Genik)" : `${g.distance?.toLocaleString("id-ID")} bp`}
                                    </span>
                                  </div>
                                </div>
                              ))}
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
    </div>
  );
}
