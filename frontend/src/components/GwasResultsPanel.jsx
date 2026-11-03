import React, { useState, useEffect, useMemo } from "react";
import { useGwasResults } from "../hooks/useGwasResults";
import ManhattanPlot from "./ManhattanPlot";
import QQPlot from "./QQPlot";
import { ThresholdSelector } from "./ThresholdSelector";
import { LeadSnpTable } from "./LeadSnpTable";
import { ModelRecommendationCard } from "./ModelRecommendationCard";
import { PcaScatterPlot } from "./PcaScatterPlot";
import { ScreePlot } from "./ScreePlot";
import { KinshipHeatmap } from "./KinshipHeatmap";
import { LdDecayPlot } from "./LdDecayPlot";
import { DataInspectionPanel } from "./DataInspectionPanel";
import { QcComparisonPanel } from "./QcComparisonPanel";
import { PeakComparisonTable } from "./PeakComparisonTable";
import { CandidateGenesTable } from "./CandidateGenesTable";
import {
  fetchInspectionData,
  fetchQcComparisonData,
  fetchPcaData,
  fetchKinshipData,
  fetchLdDecayData,
  fetchModelRecommendationData,
  fetchThresholdsData,
  fetchLeadSnpsData,
  fetchPeakComparisonData,
  fetchMultilocusResultsData,
  fetchCandidateGenesData,
} from "../api/gwasApi";

const MAIN_TABS = [
  { key: "vis", label: "📊 Manhattan & QQ Plot" },
  { key: "postgwas", label: "🎯 Ambang Batas & Lead SNPs" },
  { key: "structure", label: "🧬 Struktur Populasi & LD" },
  { key: "inspection_qc", label: "🔍 Inspeksi & QC" },
  { key: "multilocus", label: "🔬 Multi-Locus GWAS" },
  { key: "annotation", label: "🌿 Anotasi Gen GFF3" },
];

export default function GwasResultsPanel({ jobId }) {
  const { data: manhattanData, isLoading: isManhattanLoading, error: manhattanError, refetch } =
    useGwasResults(jobId);

  const [activeTab, setActiveTab] = useState("vis");
  const [visSubTab, setVisSubTab] = useState("manhattan");

  // Stage states
  const [thresholdsData, setThresholdsData] = useState(null);
  const [activeThresholdMethod, setActiveThresholdMethod] = useState("bonferroni");
  const [leadSnpsData, setLeadSnpsData] = useState(null);
  const [inspectionData, setInspectionData] = useState(null);
  const [qcData, setQcData] = useState(null);
  const [pcaData, setPcaData] = useState(null);
  const [kinshipData, setKinshipData] = useState(null);
  const [ldDecayData, setLdDecayData] = useState(null);
  const [modelRecData, setModelRecData] = useState(null);
  const [peakData, setPeakData] = useState(null);
  const [multilocusSummary, setMultilocusSummary] = useState(null);
  const [annotationData, setAnnotationData] = useState(null);

  // Fetch all supplementary pipeline stage data
  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    const sig = controller.signal;

    fetchThresholdsData(jobId, sig).then(setThresholdsData).catch(() => {});
    fetchLeadSnpsData(jobId, sig).then(setLeadSnpsData).catch(() => {});
    fetchInspectionData(jobId, sig).then(setInspectionData).catch(() => {});
    fetchQcComparisonData(jobId, sig).then(setQcData).catch(() => {});
    fetchPcaData(jobId, sig).then(setPcaData).catch(() => {});
    fetchKinshipData(jobId, sig).then(setKinshipData).catch(() => {});
    fetchLdDecayData(jobId, sig).then(setLdDecayData).catch(() => {});
    fetchModelRecommendationData(jobId, sig).then(setModelRecData).catch(() => {});
    fetchPeakComparisonData(jobId, sig).then(setPeakData).catch(() => {});
    fetchMultilocusResultsData(jobId, sig).then(setMultilocusSummary).catch(() => {});
    fetchCandidateGenesData(jobId, sig).then(setAnnotationData).catch(() => {});

    return () => controller.abort();
  }, [jobId]);

  // Sinkronkan metode ambang aktif dengan rekomendasi/output pipeline
  useEffect(() => {
    if (thresholdsData?.selected_method) {
      setActiveThresholdMethod(thresholdsData.selected_method.toLowerCase());
    }
  }, [thresholdsData]);

  // Compute active significance threshold from user selection
  const computedThreshold = useMemo(() => {
    if (!thresholdsData) return 5e-8;
    const m = (activeThresholdMethod || "bonferroni").toLowerCase();
    if (m === "bonferroni") return thresholdsData.bonferroni || 5e-8;
    if (m === "suggestive") return thresholdsData.suggestive || 1e-5;
    if (m === "fdr") return thresholdsData.fdr || thresholdsData.bonferroni || 5e-8;
    if (m === "lod") return thresholdsData.lod_p || Math.pow(10, -Number(thresholdsData.lod_score || 5.6));
    return 5e-8;
  }, [thresholdsData, activeThresholdMethod]);

  const activeThresholdLabel = useMemo(() => {
    const m = (activeThresholdMethod || "bonferroni").toLowerCase();
    if (m === "bonferroni") return "Bonferroni";
    if (m === "suggestive") return "Suggestive";
    if (m === "fdr") return "FDR";
    if (m === "lod") return `LOD ${thresholdsData?.lod_score || 5.6}`;
    return m.toUpperCase();
  }, [activeThresholdMethod, thresholdsData?.lod_score]);

  const leadSnpsList = useMemo(() => {
    if (!leadSnpsData) return [];
    if (Array.isArray(leadSnpsData)) return leadSnpsData;
    if (Array.isArray(leadSnpsData.snps)) return leadSnpsData.snps;
    return [];
  }, [leadSnpsData]);

  if (isManhattanLoading) {
    return (
      <div className="gwas-results-state" role="status" aria-live="polite">
        <div className="gwas-spinner" />
        <p>Memuat hasil analisis GWAS...</p>
      </div>
    );
  }

  if (manhattanError) {
    return (
      <div className="gwas-results-state gwas-results-error" role="alert">
        <p>Gagal memuat hasil: {manhattanError}</p>
        <button type="button" onClick={refetch}>
          Coba Lagi
        </button>
      </div>
    );
  }

  return (
    <div className="gwas-results-panel">
      {/* Main Tab Navigation */}
      <div
        style={{
          display: "flex",
          borderBottom: "2px solid #e2e8f0",
          overflowX: "auto",
          gap: 4,
          paddingBottom: 2,
          marginBottom: 16,
        }}
        role="tablist"
      >
        {MAIN_TABS.map((tab) => (
          <button
            key={tab.key}
            role="tab"
            type="button"
            aria-selected={activeTab === tab.key}
            onClick={() => setActiveTab(tab.key)}
            style={{
              padding: "10px 16px",
              background: activeTab === tab.key ? "#2563eb" : "#f8fafc",
              color: activeTab === tab.key ? "#ffffff" : "#475569",
              border: "1px solid",
              borderColor: activeTab === tab.key ? "#2563eb" : "#e2e8f0",
              borderRadius: "8px 8px 0 0",
              fontWeight: activeTab === tab.key ? 700 : 500,
              fontSize: 13,
              cursor: "pointer",
              whiteSpace: "nowrap",
              transition: "all 0.15s ease",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* TAB 1: VISUALISASI MANHATTAN & QQ PLOT */}
      {activeTab === "vis" && (
        <div style={{ display: "grid", gap: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                onClick={() => setVisSubTab("manhattan")}
                style={{
                  padding: "6px 14px",
                  borderRadius: 6,
                  border: "1px solid",
                  borderColor: visSubTab === "manhattan" ? "#1e40af" : "#cbd5e1",
                  background: visSubTab === "manhattan" ? "#1e40af" : "#fff",
                  color: visSubTab === "manhattan" ? "#fff" : "#334155",
                  fontWeight: 600,
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                Manhattan Plot
              </button>
              <button
                type="button"
                onClick={() => setVisSubTab("qq")}
                style={{
                  padding: "6px 14px",
                  borderRadius: 6,
                  border: "1px solid",
                  borderColor: visSubTab === "qq" ? "#1e40af" : "#cbd5e1",
                  background: visSubTab === "qq" ? "#1e40af" : "#fff",
                  color: visSubTab === "qq" ? "#fff" : "#334155",
                  fontWeight: 600,
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                QQ Plot
              </button>
            </div>

            {manhattanData && (
              <span style={{ fontSize: 12, color: "#64748b", fontWeight: 600 }}>
                {manhattanData.total_points.toLocaleString("id-ID")} varian ditampilkan
              </span>
            )}
          </div>

          {/* Quick Threshold Toggle Bar for Manhattan Plot */}
          {visSubTab === "manhattan" && thresholdsData && (
            <div style={{ background: "#f8fafc", padding: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}>
              <ThresholdSelector
                thresholds={thresholdsData}
                activeMethod={activeThresholdMethod}
                onChangeMethod={setActiveThresholdMethod}
              />
            </div>
          )}

          <div className="gwas-results-body">
            {manhattanData && manhattanData.total_points > 0 ? (
              visSubTab === "manhattan" ? (
                <ManhattanPlot data={manhattanData} significanceThreshold={computedThreshold} />
              ) : (
                <QQPlot data={manhattanData} />
              )
            ) : (
              <div style={{ padding: 24, textAlign: "center", color: "#64748b" }}>
                Tidak ada data SNP yang dapat dirender.
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB 2: POST-GWAS & LEAD SNPS */}
      {activeTab === "postgwas" && (
        <div style={{ display: "grid", gap: 18 }}>
          <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0" }}>
            <h3 style={{ margin: "0 0 12px", fontSize: 15, fontWeight: 700, color: "#0f172a" }}>
              Penetapan Ambang Batas Signifikansi Dinamis (Fase 4E)
            </h3>
            <ThresholdSelector
              thresholds={thresholdsData}
              activeMethod={activeThresholdMethod}
              onChangeMethod={setActiveThresholdMethod}
            />
          </div>

          <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0" }}>
            <LeadSnpTable
              leadSnps={leadSnpsList}
              activeThreshold={computedThreshold}
              activeThresholdMethod={activeThresholdLabel}
              jobId={jobId}
            />
          </div>
        </div>
      )}

      {/* TAB 3: STRUKTUR POPULASI & LD DECAY */}
      {activeTab === "structure" && (
        <div style={{ display: "grid", gap: 20 }}>
          {modelRecData && <ModelRecommendationCard recommendation={modelRecData} />}

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))", gap: 16 }}>
            {pcaData ? (
              <>
                <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0" }}>
                  <PcaScatterPlot data={pcaData} />
                </div>
                <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0" }}>
                  <ScreePlot pve={pcaData.pve} cumulativePve={pcaData.cumulative_pve} />
                </div>
              </>
            ) : (
              <div style={{ padding: 20, background: "#f8fafc", borderRadius: 8, color: "#64748b" }}>
                Data PCA sedang diproses atau tidak tersedia.
              </div>
            )}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))", gap: 16 }}>
            {kinshipData && (
              <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0" }}>
                <KinshipHeatmap data={kinshipData} />
              </div>
            )}

            {ldDecayData && (
              <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0" }}>
                <LdDecayPlot data={ldDecayData} />
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB 4: INSPEKSI FENOTIPE & QC */}
      {activeTab === "inspection_qc" && (
        <div style={{ display: "grid", gap: 20 }}>
          {inspectionData ? (
            <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0" }}>
              <h3 style={{ margin: "0 0 14px", fontSize: 15, fontWeight: 700, color: "#0f172a" }}>
                Inspeksi Data Fenotipe & Konsistensi ID (Fase 1)
              </h3>
              <DataInspectionPanel report={inspectionData} jobId={jobId} />
            </div>
          ) : (
            <div style={{ padding: 20, background: "#f8fafc", borderRadius: 8, color: "#64748b" }}>
              Data inspeksi fenotipe tidak ditemukan untuk job ini.
            </div>
          )}

          {qcData ? (
            <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0" }}>
              <h3 style={{ margin: "0 0 14px", fontSize: 15, fontWeight: 700, color: "#0f172a" }}>
                Evaluasi Quality Control Genotipe (Fase 2)
              </h3>
              <QcComparisonPanel comparison={qcData} />
            </div>
          ) : (
            <div style={{ padding: 20, background: "#f8fafc", borderRadius: 8, color: "#64748b" }}>
              Data perbandingan QC tidak tersedia.
            </div>
          )}
        </div>
      )}

      {/* TAB 5: MULTI-LOCUS GWAS */}
      {activeTab === "multilocus" && (
        <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0" }}>
          {((peakData && peakData.status !== "skipped" && peakData.status !== "not_available") ||
            (multilocusSummary && multilocusSummary.status !== "skipped" && multilocusSummary.status !== "not_available")) ? (
            <PeakComparisonTable comparison={peakData} multilocusSummary={multilocusSummary} />
          ) : (
            <div
              style={{
                padding: 24,
                textAlign: "center",
                background: "#f8fafc",
                borderRadius: 8,
                border: "1px dashed #cbd5e1",
                color: "#64748b",
              }}
            >
              <div style={{ fontSize: 24, marginBottom: 8 }}>🔬</div>
              <div style={{ fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                Analisis Multi-Locus GWAS (FarmCPU, BLINK, MLMM) Dilewati atau Belum Tersedia
              </div>
              <p style={{ margin: 0, fontSize: 13, maxWidth: 500, marginInline: "auto" }}>
                Opsi multi-locus tidak diaktifkan pada konfigurasi pipeline job ini, atau runtime R
                (GAPIT) tidak terpasang di host server.
              </p>
            </div>
          )}
        </div>
      )}

      {/* TAB 6: ANOTASI GEN KANDIDAT */}
      {activeTab === "annotation" && (
        <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0" }}>
          <CandidateGenesTable annotationData={annotationData} jobId={jobId} />
        </div>
      )}
    </div>
  );
}
