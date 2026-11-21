import React, { useState, useMemo, useEffect } from "react";
import { Pagination } from "./Pagination";
import { toCsv } from "../utils/csv";
import { saveBlob } from "../utils/download";
import { downloadJobFileByName } from "../utils/jobFiles";

const FAM_TAIL = "GAAGGTGACCAAGTTCATGCT";
const HEX_TAIL = "GAAGGTCGGAGTCAACGGATT";

function renderKaspTailSequence(seq, tailSeq, tailLabel, colors) {
  if (!seq) return "-";
  const str = String(seq);
  const hasTail = str.toUpperCase().startsWith(tailSeq);
  if (!hasTail) {
    return (
      <span style={{ fontFamily: "monospace", wordBreak: "break-all", color: "#0f172a" }}>
        {str}
      </span>
    );
  }
  return (
    <span style={{ fontFamily: "monospace", wordBreak: "break-all" }}>
      <span
        style={{
          display: "inline-block",
          padding: "1px 5px",
          marginRight: 5,
          borderRadius: 4,
          background: colors.badgeBg,
          color: colors.badgeText,
          fontSize: 10,
          fontWeight: 700,
          fontFamily: "sans-serif",
        }}
      >
        {tailLabel}
      </span>
      <span style={{ color: colors.tailColor, fontWeight: 700 }}>{str.slice(0, tailSeq.length)}</span>
      <span style={{ color: "#0f172a" }}>{str.slice(tailSeq.length)}</span>
    </span>
  );
}

function renderOligoQcBadge(oligoQc) {
  if (!oligoQc || !oligoQc.analyzed) return "-";
  const warnings = Array.isArray(oligoQc.warnings) ? oligoQc.warnings : [];
  if (oligoQc.warn) {
    return (
      <div style={{ display: "grid", gap: 2 }}>
        <span
          title={warnings.join("; ") || "Peringatan struktur sekunder"}
          style={{
            display: "inline-block",
            width: "fit-content",
            padding: "2px 7px",
            borderRadius: 999,
            background: "#fef3c7",
            color: "#92400e",
            fontSize: 10.5,
            fontWeight: 700,
          }}
        >
          Peringatan
        </span>
        {warnings.length > 0 && (
          <span style={{ fontSize: 10, color: "#92400e" }}>{warnings[0]}</span>
        )}
      </div>
    );
  }
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 7px",
        borderRadius: 999,
        background: "#dcfce7",
        color: "#166534",
        fontSize: 10.5,
        fontWeight: 700,
      }}
    >
      Aman
    </span>
  );
}

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

function getMarkerRefAlt(item) {
  let ref = item?.ref || item?.vcf_ref_base || item?.fasta_ref_base || "";
  let alt = item?.alt || "";
  if ((!ref || !alt) && typeof item?.flanking_sequence === "string") {
    const bracketMatch = item.flanking_sequence.match(/\[([A-Za-z*]+)\/([A-Za-z*]+)\]/);
    if (bracketMatch) {
      if (!ref) ref = bracketMatch[1];
      if (!alt) alt = bracketMatch[2];
    }
  }
  if (!alt && item?.allele && item.allele !== ref) {
    alt = item.allele;
  }
  return { ref: ref || "-", alt: alt || "-" };
}

export function PrimerDesignPanel({ primersData = null, jobId = null }) {
  const [activeSubTab, setActiveSubTab] = useState("primer3"); // "primer3" | "kasp"
  const [searchText, setSearchText] = useState("");
  const [viewMode, setViewMode] = useState("both"); // "both" | "primer3" | "table"
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(5);
  const [downloadError, setDownloadError] = useState(null);

  useEffect(() => {
    setCurrentPage(1);
  }, [searchText, viewMode]);

  const markers = useMemo(() => primersData?.markers || [], [primersData]);

  const flankBp = useMemo(() => {
    const top = Number(primersData?.flank_bp);
    if (Number.isFinite(top) && top > 0) return top;
    const itemFlank = markers.find((m) => Number(m?.flank_bp) > 0)?.flank_bp;
    if (Number(itemFlank) > 0) return Number(itemFlank);
    const seqLen = markers.find((m) => Number(m?.sequence_length) > 0)?.sequence_length;
    if (Number(seqLen) > 1) return Math.floor((Number(seqLen) - 1) / 2);
    return 100;
  }, [primersData, markers]);
  const totalFlankBp = flankBp * 2 + 1;

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

  const handleDownloadArtifact = async (filename) => {
    if (!jobId || !filename) return;
    setDownloadError(null);
    try {
      await downloadJobFileByName(jobId, filename, "annotation");
    } catch (err) {
      setDownloadError(err?.message || `Gagal mengunduh ${filename}`);
    }
  };

  const handleDownloadPrimerTxt = () => {
    if (!markers.length) return;
    const blocks = markers.map(
      (m) => `### ${m.rank}. ${m.marker_id}\n\n> ${m.flanking_sequence}\n\n\`\`\`\n${m.primer3_output}\n\`\`\`\n`
    );
    const blob = new Blob([blocks.join("\n")], { type: "text/plain;charset=utf-8" });
    saveBlob(blob, `Flanking_Primer3_${jobId || "gwas"}.txt`);
  };

  const handleDownloadPrimerCsv = () => {
    if (!markers.length) return;
    const headers = [
      "Rank",
      "Marker_ID",
      "SNP",
      "Chr",
      "Pos",
      "REF",
      "ALT",
      "Nearby_Variants_Count",
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
      "Flanking_Sequence",
    ];
    const rows = [];
    for (const m of markers) {
      const { ref, alt } = getMarkerRefAlt(m);
      const nearbyCount =
        m.nearby_variants_count ?? (Array.isArray(m.nearby_variants) ? m.nearby_variants.length : 0);
      if (!m.primer_pairs || m.primer_pairs.length === 0) {
        rows.push([
          m.rank,
          m.marker_id,
          m.snp,
          m.chr,
          m.pos,
          ref,
          alt,
          nearbyCount,
          m.nearest_gene || "-",
          0,
          "-",
          "-",
          "-",
          "-",
          m.status === "no_reference" ? "no reference FASTA" : "no primers found",
          "-",
          "-",
          "-",
          "-",
          m.status === "no_reference" ? "no reference FASTA" : "no primers found",
          "-",
          m.flanking_sequence,
        ]);
      } else {
        m.primer_pairs.forEach((p, idx) => {
          rows.push([
            m.rank,
            m.marker_id,
            m.snp,
            m.chr,
            m.pos,
            ref,
            alt,
            nearbyCount,
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
          ]);
        });
      }
    }
    const blob = new Blob([toCsv(headers, rows)], { type: "text/csv;charset=utf-8" });
    saveBlob(blob, `Desain_Primer_Kandidat_${jobId || "gwas"}.csv`);
  };

  const isSynthetic = Boolean(
    primersData?.synthetic || markers.some((m) => Boolean(m?.synthetic))
  );
  const isNoReference =
    primersData?.status === "no_reference" ||
    primersData?.status === "no_reference_for_chr" ||
    (markers.length > 0 &&
      markers.every((m) => m.status === "no_reference" || m.status === "no_reference_for_chr"));

  const candidateRegions = primersData?.candidate_regions || null;
  const regionsStatus = primersData?.regions_status || null;
  const renderCandidateRegionsCard = () => {
    if (!candidateRegions || isSynthetic) return null;
    const windowKb = Math.round(Number(candidateRegions.window_bp || 0) / 1000);
    return (
      <div
        style={{
          background: "#f8fafc",
          border: "1px solid #cbd5e1",
          borderRadius: 8,
          padding: 12,
          display: "grid",
          gap: 8,
          textAlign: "left",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a" }}>
              Region Kandidat (Fase 5.4)
            </div>
            <div style={{ fontSize: 12, color: "#475569", marginTop: 2 }}>
              {Number(candidateRegions.written_regions ?? candidateRegions.n_regions ?? 0)}/
              {Number(candidateRegions.n_regions || 0)} region · Total{" "}
              {Number(candidateRegions.total_bp || 0).toLocaleString("id-ID")} bp
              {windowKb > 0 ? ` (jendela ±${windowKb} kb)` : ""}
              {candidateRegions.truncated ? " · Dipotong sesuai batas total bp" : ""}
              {regionsStatus === "no_reference" ? " · FASTA referensi tidak tersedia" : ""}
            </div>
          </div>

          {jobId && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {candidateRegions.bed && (
                <button
                  type="button"
                  onClick={() => handleDownloadArtifact(candidateRegions.bed)}
                  style={{
                    padding: "4px 10px",
                    fontSize: 11.5,
                    borderRadius: 6,
                    border: "1px solid #cbd5e1",
                    background: "#fff",
                    color: "#1e293b",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  📥 candidate_regions.bed
                </button>
              )}
              {candidateRegions.fasta && (
                <button
                  type="button"
                  onClick={() => handleDownloadArtifact(candidateRegions.fasta)}
                  style={{
                    padding: "4px 10px",
                    fontSize: 11.5,
                    borderRadius: 6,
                    border: "1px solid #cbd5e1",
                    background: "#fff",
                    color: "#1e293b",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  📥 candidate_regions.fa
                </button>
              )}
              {candidateRegions.csv && (
                <button
                  type="button"
                  onClick={() => handleDownloadArtifact(candidateRegions.csv)}
                  style={{
                    padding: "4px 10px",
                    fontSize: 11.5,
                    borderRadius: 6,
                    border: "1px solid #cbd5e1",
                    background: "#fff",
                    color: "#1e293b",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  📥 candidate_regions.csv
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    );
  };

  const kaspData = primersData?.kasp || null;
  const kaspMarkers = Array.isArray(kaspData?.markers) ? kaspData.markers : [];
  const kaspFiles = kaspData?.files || {};

  const renderKaspSection = () => {
    if (!kaspData || kaspData.status === "not_available") {
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
          <div style={{ fontSize: 22, marginBottom: 6 }}>🧬</div>
          <div style={{ fontWeight: 600, color: "#334155", marginBottom: 4 }}>
            Desain Primer KASP Belum tersedia
          </div>
          <p style={{ margin: 0, fontSize: 13, maxWidth: 520, marginInline: "auto" }}>
            Data desain primer KASP (EasyKASP + analisis struktur sekunder oligo) tidak ditemukan pada hasil job ini.
          </p>
        </div>
      );
    }

    if (kaspData.status === "skipped") {
      return (
        <div
          role="status"
          style={{
            padding: 20,
            borderRadius: 8,
            background: "#f8fafc",
            border: "1px solid #cbd5e1",
            color: "#475569",
            fontSize: 13,
          }}
        >
          <strong style={{ display: "block", color: "#334155", marginBottom: 4 }}>
            Desain Primer KASP Dilewati
          </strong>
          <span>{kaspData.reason || "FASTA referensi tidak tersedia."}</span>
        </div>
      );
    }

    if (kaspData.status === "error" || kaspData.status === "failed") {
      return (
        <div
          role="alert"
          style={{
            padding: 16,
            borderRadius: 8,
            background: "#fef2f2",
            border: "1px solid #fecaca",
            color: "#991b1b",
            fontSize: 13,
          }}
        >
          <strong>Desain Primer KASP Gagal:</strong>{" "}
          {kaspData.reason || "Terjadi kesalahan saat merancang primer KASP."}
        </div>
      );
    }

    const inputTsvFile = kaspFiles.input_tsv || "kasp_input.tsv";
    const primersTsvFile = kaspFiles.primers_tsv || "kasp_primers.tsv";
    const noTailFaFile = kaspFiles.primers_no_tail_fa || "kasp_primers_no_tail.fa";

    return (
      <div style={{ display: "grid", gap: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
          <div>
            <h4 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#0f172a" }}>
              Desain Primer KASP (EasyKASP + Struktur Sekunder Tail)
            </h4>
            <p style={{ margin: "3px 0 0", fontSize: 12, color: "#64748b" }}>
              Primer alel X ber-tail FAM (biru) dan alel Y ber-tail HEX (merah), beserta evaluasi struktur sekunder oligo bertail.
            </p>
          </div>
          {jobId && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                onClick={() => handleDownloadArtifact(inputTsvFile)}
                style={{
                  padding: "6px 12px",
                  fontSize: 12,
                  borderRadius: 6,
                  border: "1px solid #cbd5e1",
                  background: "#fff",
                  color: "#1e293b",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                📥 kasp_input.tsv
              </button>
              <button
                type="button"
                onClick={() => handleDownloadArtifact(primersTsvFile)}
                style={{
                  padding: "6px 12px",
                  fontSize: 12,
                  borderRadius: 6,
                  border: "1px solid #cbd5e1",
                  background: "#fff",
                  color: "#1e293b",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                📥 kasp_primers.tsv
              </button>
              <button
                type="button"
                onClick={() => handleDownloadArtifact(noTailFaFile)}
                style={{
                  padding: "6px 12px",
                  fontSize: 12,
                  borderRadius: 6,
                  border: "1px solid #cbd5e1",
                  background: "#fff",
                  color: "#1e293b",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                📥 kasp_primers_no_tail.fa
              </button>
            </div>
          )}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
          <div style={{ padding: "10px 14px", background: "#f8fafc", borderRadius: 6, border: "1px solid #e2e8f0" }}>
            <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", fontWeight: 600 }}>Total SNP</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#0f172a" }}>{Number(kaspData.total ?? kaspMarkers.length)}</div>
          </div>
          <div style={{ padding: "10px 14px", background: "#f0fdf4", borderRadius: 6, border: "1px solid #bbf7d0" }}>
            <div style={{ fontSize: 11, color: "#166534", textTransform: "uppercase", fontWeight: 600 }}>Berhasil Didesain</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#15803d" }}>{Number(kaspData.designed || 0)}</div>
          </div>
          <div style={{ padding: "10px 14px", background: "#fef2f2", borderRadius: 6, border: "1px solid #fecaca" }}>
            <div style={{ fontSize: 11, color: "#991b1b", textTransform: "uppercase", fontWeight: 600 }}>Gagal</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#b91c1c" }}>{Number(kaspData.failed || 0)}</div>
          </div>
          <div style={{ padding: "10px 14px", background: "#eff6ff", borderRadius: 6, border: "1px solid #bfdbfe" }}>
            <div style={{ fontSize: 11, color: "#1e40af", textTransform: "uppercase", fontWeight: 600 }}>Keberhasilan (%)</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#2563eb" }}>
              {Number(kaspData.success_rate || 0).toFixed(1)}%
            </div>
          </div>
        </div>

        {kaspMarkers.length > 0 ? (
          <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 6 }}>
            <table
              aria-label="Desain Primer KASP"
              style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5, textAlign: "left" }}
            >
              <thead>
                <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0", color: "#475569" }}>
                  <th scope="col" style={{ padding: "8px 10px" }}>SNP ID</th>
                  <th scope="col" style={{ padding: "8px 10px" }}>Allele X / Y</th>
                  <th scope="col" style={{ padding: "8px 10px" }}>Primer_X (FAM)</th>
                  <th scope="col" style={{ padding: "8px 10px" }}>Primer_Y (HEX)</th>
                  <th scope="col" style={{ padding: "8px 10px" }}>Primer_C</th>
                  <th scope="col" style={{ padding: "8px 10px" }}>Arah</th>
                  <th scope="col" style={{ padding: "8px 10px" }}>Status</th>
                  <th scope="col" style={{ padding: "8px 10px" }}>Struktur Sekunder</th>
                </tr>
              </thead>
              <tbody>
                {kaspMarkers.map((m, idx) => {
                  const isOk = String(m.status || "").toUpperCase() === "OK";
                  const notesText = Array.isArray(m.notes) ? m.notes.join("; ") : String(m.notes || "");
                  return (
                    <tr key={m.snp_id || idx} style={{ borderBottom: "1px solid #f1f5f9", verticalAlign: "top" }}>
                      <th scope="row" style={{ padding: "8px 10px", fontWeight: 700, color: "#0f172a" }}>
                        {m.snp_id}
                      </th>
                      <td style={{ padding: "8px 10px", fontFamily: "monospace", whiteSpace: "nowrap" }}>
                        {m.allele_x || "-"} / {m.allele_y || "-"}
                      </td>
                      <td style={{ padding: "8px 10px" }}>
                        {renderKaspTailSequence(m.primer_x, FAM_TAIL, "FAM", {
                          badgeBg: "#dbeafe",
                          badgeText: "#1d4ed8",
                          tailColor: "#1d4ed8",
                        })}
                      </td>
                      <td style={{ padding: "8px 10px" }}>
                        {renderKaspTailSequence(m.primer_y, HEX_TAIL, "HEX", {
                          badgeBg: "#fee2e2",
                          badgeText: "#b91c1c",
                          tailColor: "#dc2626",
                        })}
                      </td>
                      <td style={{ padding: "8px 10px", fontFamily: "monospace", wordBreak: "break-all", color: "#0f172a" }}>
                        {m.primer_c || "-"}
                      </td>
                      <td style={{ padding: "8px 10px", whiteSpace: "nowrap" }}>{m.direction || "-"}</td>
                      <td style={{ padding: "8px 10px" }}>
                        <span
                          style={{
                            display: "inline-block",
                            padding: "2px 7px",
                            borderRadius: 4,
                            background: isOk ? "#dcfce7" : "#fee2e2",
                            color: isOk ? "#166534" : "#991b1b",
                            fontWeight: 700,
                            fontSize: 10.5,
                          }}
                        >
                          {m.status || "-"}
                        </span>
                        {notesText && (
                          <div style={{ fontSize: 10.5, color: "#64748b", marginTop: 2 }}>{notesText}</div>
                        )}
                      </td>
                      <td style={{ padding: "8px 10px" }}>{renderOligoQcBadge(m.oligo_qc)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ padding: 14, borderRadius: 6, background: "#f8fafc", color: "#64748b", fontSize: 12.5 }}>
            Tidak ada marker KASP untuk ditampilkan.
          </div>
        )}
      </div>
    );
  };

  const renderPrimer3Section = () => {
    if ((!primersData || !markers.length || isNoReference) && !isSynthetic) {
      return (
        <div style={{ display: "grid", gap: 12 }}>
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
              Sekuens Flanking &amp; Desain Primer3 Belum Tersedia
            </div>
            <p style={{ margin: 0, fontSize: 13, maxWidth: 540, marginInline: "auto" }}>
              {isNoReference
                ? "Referensi FASTA tidak tersedia; unggah berkas FASTA referensi untuk mengekstrak sekuens flanking dan desain primer Primer3."
                : "Tidak ditemukan Lead SNP kandidat pada job ini untuk ekstraksi sekuens flanking dan desain primer Primer3."}
            </p>
          </div>
          {renderCandidateRegionsCard()}
        </div>
      );
    }

    return (
      <div style={{ display: "grid", gap: 16 }}>
        {isSynthetic && (
          <div
            role="note"
            style={{
              background: "#fffbeb",
              border: "1px solid #f59e0b",
              color: "#92400e",
              borderRadius: 8,
              padding: "10px 14px",
              fontSize: 12.5,
              fontWeight: 600,
            }}
          >
            ⚠️ Data simulasi — bukan hasil analisis. Sekuens flanking dan desain primer di bawah berasal dari mode demo (`ALLOW_SYNTHETIC_ANNOTATION=true`).
          </div>
        )}

        {/* Header & Tombol Ekspor */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
          <div>
            <h4 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#0f172a" }}>
              Ekstraksi Sekuens Flanking ({totalFlankBp} bp) &amp; Desain Primer3 — Fase 5.4 / Fase 6–8
            </h4>
            <p style={{ margin: "3px 0 0", fontSize: 12, color: "#64748b" }}>
              Sekuens {flankBp} bp upstream + `[Alel Target]` + {flankBp} bp downstream beserta pasangan `LEFT PRIMER` &amp; `RIGHT PRIMER`.
            </p>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={handleDownloadPrimerTxt}
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
              📥 Unduh Laporan Desain Primer (.txt)
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
            <div style={{ fontSize: 18, fontWeight: 700, color: "#2563eb" }}>
              {totalFlankBp} bp (±{flankBp} bp)
            </div>
          </div>
          <div style={{ padding: "10px 14px", background: "#fffbeb", borderRadius: 6, border: "1px solid #fde68a" }}>
            <div style={{ fontSize: 11, color: "#92400e", textTransform: "uppercase", fontWeight: 600 }}>Parameter GC% &amp; Tm</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: "#b45309", marginTop: 2 }}>GC 35–50% · Tm ~54–56°C</div>
          </div>
        </div>

        {renderCandidateRegionsCard()}

      {/* Filter & Mode Tampilan */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div style={{ display: "flex", gap: 6 }}>
          {[
            { id: "both", label: "Blok Desain Primer + Tabel" },
            { id: "primer3", label: "Format Blok Desain Primer" },
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
        {paginatedMarkers.map((item) => {
          const { ref, alt } = getMarkerRefAlt(item);
          const nearbyCount =
            item.nearby_variants_count !== undefined && item.nearby_variants_count !== null
              ? Number(item.nearby_variants_count)
              : Array.isArray(item.nearby_variants)
                ? item.nearby_variants.length
                : null;
          return (
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
                <span
                  style={{
                    fontSize: 11,
                    padding: "2px 8px",
                    borderRadius: 12,
                    background: "#eff6ff",
                    color: "#1e40af",
                    fontWeight: 600,
                  }}
                >
                  REF/ALT: {ref}/{alt}
                </span>
                {nearbyCount !== null && (
                  <span
                    style={{
                      fontSize: 11,
                      padding: "2px 8px",
                      borderRadius: 12,
                      background: nearbyCount > 0 ? "#fef3c7" : "#f1f5f9",
                      color: nearbyCount > 0 ? "#92400e" : "#475569",
                      fontWeight: 600,
                    }}
                  >
                    Varian tetangga: {nearbyCount}
                    {nearbyCount > 0 ? " (dikecualikan Primer3)" : ""}
                  </span>
                )}
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

            {/* Sekuens Flanking */}
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
          );
        })}
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
  };

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div
        role="tablist"
        aria-label="Jenis Desain Primer"
        style={{ display: "flex", gap: 8, borderBottom: "1px solid #e2e8f0", paddingBottom: 8, flexWrap: "wrap" }}
      >
        <button
          role="tab"
          type="button"
          aria-selected={activeSubTab === "primer3"}
          onClick={() => setActiveSubTab("primer3")}
          style={{
            padding: "6px 14px",
            borderRadius: 6,
            border: "1px solid",
            borderColor: activeSubTab === "primer3" ? "#2563eb" : "#cbd5e1",
            background: activeSubTab === "primer3" ? "#2563eb" : "#fff",
            color: activeSubTab === "primer3" ? "#fff" : "#334155",
            fontSize: 12.5,
            fontWeight: 700,
            cursor: "pointer",
          }}
        >
          PCR Konvensional
        </button>
        <button
          role="tab"
          type="button"
          aria-selected={activeSubTab === "kasp"}
          onClick={() => setActiveSubTab("kasp")}
          style={{
            padding: "6px 14px",
            borderRadius: 6,
            border: "1px solid",
            borderColor: activeSubTab === "kasp" ? "#2563eb" : "#cbd5e1",
            background: activeSubTab === "kasp" ? "#2563eb" : "#fff",
            color: activeSubTab === "kasp" ? "#fff" : "#334155",
            fontSize: 12.5,
            fontWeight: 700,
            cursor: "pointer",
          }}
        >
          KASP
          {kaspData?.status === "completed" ? ` (${Number(kaspData.designed || 0)}/${Number(kaspData.total || 0)})` : ""}
        </button>
      </div>

      {downloadError && (
        <div role="alert" style={{ padding: 10, borderRadius: 6, background: "#fef2f2", color: "#b91c1c", fontSize: 12 }}>
          {downloadError}
        </div>
      )}

      <div role="tabpanel">
        {activeSubTab === "kasp" ? renderKaspSection() : renderPrimer3Section()}
      </div>
    </div>
  );
}
