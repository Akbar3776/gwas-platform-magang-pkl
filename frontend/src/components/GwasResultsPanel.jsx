import React, { useState, useEffect, useMemo } from "react";
import { useGwasResults } from "../hooks/useGwasResults";
import { useResource } from "../hooks/useResource";
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
import { PrimerDesignPanel } from "./PrimerDesignPanel";
import {
  fetchManhattanData,
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
  fetchGoEnrichmentData,
  fetchPrimersData,
} from "../api/gwasApi";

const MAIN_TABS = [
  { key: "vis", label: "📊 Manhattan & QQ Plot" },
  { key: "postgwas", label: "🎯 Ambang Batas & Lead SNPs" },
  { key: "structure", label: "🧬 Struktur Populasi & LD" },
  { key: "inspection_qc", label: "🔍 Inspeksi & QC" },
  { key: "multilocus", label: "🔬 Multi-Locus GWAS" },
  { key: "annotation", label: "🌿 Anotasi Gen GFF3 & GO" },
  { key: "primers", label: "🧪 Flanking & Primer3 / KASP" },
];

const MULTILOCUS_MODEL_LABELS = {
  FARMCPU: "FarmCPU",
  BLINK: "BLINK",
  MLMM: "MLMM",
};

function TabResourceNotice({ resources = [], label = "data" }) {
  const failed = resources.filter((r) => Boolean(r?.error));
  const loadingOnly =
    resources.length > 0 && resources.some((r) => r?.isLoading) && resources.every((r) => r?.data == null && !r?.error);

  if (!failed.length && !loadingOnly) {
    return null;
  }

  return (
    <div style={{ display: "grid", gap: 8 }}>
      {loadingOnly && (
        <div role="status" style={{ padding: 14, borderRadius: 8, background: "#f8fafc", color: "#475569", fontSize: 13 }}>
          Memuat {label}...
        </div>
      )}
      {failed.map((res, idx) => (
        <div
          key={idx}
          role="alert"
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 10,
            flexWrap: "wrap",
            padding: "10px 14px",
            borderRadius: 8,
            background: "#fef2f2",
            border: "1px solid #fecaca",
            color: "#b91c1c",
            fontSize: 13,
          }}
        >
          <span>{res.error}</span>
          {res.refetch && (
            <button
              type="button"
              onClick={res.refetch}
              style={{
                padding: "4px 10px",
                borderRadius: 6,
                border: "1px solid #fca5a5",
                background: "#fff",
                color: "#991b1b",
                fontSize: 12,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Coba Lagi
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

export default function GwasResultsPanel({ jobId }) {
  const { data: manhattanData, isLoading: isManhattanLoading, error: manhattanError, refetch } =
    useGwasResults(jobId);

  const [activeTab, setActiveTab] = useState("vis");
  const [visSubTab, setVisSubTab] = useState("manhattan");
  const [selectedMultiModel, setSelectedMultiModel] = useState("");
  const [multiPlotData, setMultiPlotData] = useState(null);
  const [multiPlotLoading, setMultiPlotLoading] = useState(false);
  const [multiPlotError, setMultiPlotError] = useState(null);
  const [activeThresholdMethod, setActiveThresholdMethod] = useState("bonferroni");

  const thresholdsRes = useResource(fetchThresholdsData, jobId, { fallbackMessage: "Gagal memuat ambang batas." });
  const thresholdsData = thresholdsRes.data;

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

  // Lead SNP dibagi ulang oleh backend sesuai ambang aktif; tunggu ambang selesai dimuat
  // agar tidak memuat dua kali (ambang default lalu ambang pipeline).
  const hasThresholds = Boolean(
    thresholdsData && thresholdsData.status !== "not_available" && thresholdsData.status !== "skipped"
  );
  const leadSnpsThreshold = hasThresholds ? computedThreshold : null;
  const leadSnpsRes = useResource(
    (id, signal) => fetchLeadSnpsData(id, signal, leadSnpsThreshold),
    jobId,
    {
      enabled: !thresholdsRes.isLoading,
      key: leadSnpsThreshold,
      fallbackMessage: "Gagal memuat Lead SNPs.",
    }
  );
  const inspectionRes = useResource(fetchInspectionData, jobId, { fallbackMessage: "Gagal memuat data inspeksi." });
  const qcRes = useResource(fetchQcComparisonData, jobId, { fallbackMessage: "Gagal memuat data perbandingan QC." });
  const pcaRes = useResource(fetchPcaData, jobId, { fallbackMessage: "Gagal memuat data PCA." });
  const kinshipRes = useResource(fetchKinshipData, jobId, { fallbackMessage: "Gagal memuat matriks kinship." });
  const ldDecayRes = useResource(fetchLdDecayData, jobId, { fallbackMessage: "Gagal memuat kurva LD decay." });
  const modelRecRes = useResource(fetchModelRecommendationData, jobId, { fallbackMessage: "Gagal memuat rekomendasi model." });
  const peakRes = useResource(fetchPeakComparisonData, jobId, { fallbackMessage: "Gagal memuat perbandingan puncak." });
  const multilocusRes = useResource(fetchMultilocusResultsData, jobId, { fallbackMessage: "Gagal memuat hasil multi-locus." });
  const annotationRes = useResource(fetchCandidateGenesData, jobId, { fallbackMessage: "Gagal memuat anotasi kandidat." });
  const goRes = useResource(fetchGoEnrichmentData, jobId, { fallbackMessage: "Gagal memuat data GO enrichment." });
  const primersRes = useResource(fetchPrimersData, jobId, { fallbackMessage: "Gagal memuat data Primer3." });

  const leadSnpsData = leadSnpsRes.data;
  const inspectionData = inspectionRes.data;
  const qcData = qcRes.data;
  const pcaData = pcaRes.data;
  const kinshipData = kinshipRes.data;
  const ldDecayData = ldDecayRes.data;
  const modelRecData = modelRecRes.data;
  const peakData = peakRes.data;
  const multilocusSummary = multilocusRes.data;
  const annotationData = annotationRes.data;
  const goEnrichmentData = goRes.data;
  const primersData = primersRes.data;

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

  const suggestiveSnpsList = useMemo(() => {
    if (!leadSnpsData || Array.isArray(leadSnpsData)) return [];
    if (Array.isArray(leadSnpsData.suggestive)) return leadSnpsData.suggestive;
    if (Array.isArray(leadSnpsData.sugestif)) return leadSnpsData.sugestif;
    return [];
  }, [leadSnpsData]);

  const hasMultilocusData = useMemo(
    () =>
      Boolean(
        (peakData && peakData.status !== "skipped" && peakData.status !== "not_available") ||
          (multilocusSummary && multilocusSummary.status !== "skipped" && multilocusSummary.status !== "not_available")
      ),
    [peakData, multilocusSummary]
  );

  const availableMultilocusModels = useMemo(() => {
    const resKeys = Object.keys(multilocusSummary?.results || {})
      .map((k) => k.toUpperCase())
      .filter((k) => k in MULTILOCUS_MODEL_LABELS);
    if (resKeys.length > 0) return resKeys;
    if (Array.isArray(multilocusSummary?.methods) && multilocusSummary.methods.length > 0) {
      const parsed = multilocusSummary.methods
        .map((m) => String(m).toUpperCase())
        .filter((k) => k in MULTILOCUS_MODEL_LABELS);
      if (parsed.length > 0) return parsed;
    }
    return ["FARMCPU", "BLINK", "MLMM"];
  }, [multilocusSummary]);

  useEffect(() => {
    if (availableMultilocusModels.length === 0) return;
    if (!selectedMultiModel || !availableMultilocusModels.includes(selectedMultiModel)) {
      setSelectedMultiModel(availableMultilocusModels[0]);
    }
  }, [availableMultilocusModels, selectedMultiModel]);

  useEffect(() => {
    setMultiPlotData(null);
    setMultiPlotError(null);
  }, [jobId]);

  useEffect(() => {
    if (activeTab !== "multilocus" || !hasMultilocusData || !jobId || !selectedMultiModel) {
      return undefined;
    }
    const controller = new AbortController();
    let cancelled = false;
    setMultiPlotLoading(true);
    setMultiPlotError(null);

    fetchManhattanData(jobId, controller.signal, selectedMultiModel)
      .then((res) => {
        if (!cancelled) {
          setMultiPlotData(res);
        }
      })
      .catch((err) => {
        if (err && err.name === "AbortError") return;
        if (!cancelled) {
          setMultiPlotData(null);
          setMultiPlotError(err?.message || `Gagal memuat plot model ${selectedMultiModel}`);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setMultiPlotLoading(false);
        }
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [activeTab, hasMultilocusData, jobId, selectedMultiModel]);

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
        isManhattanLoading ? (
          <div className="gwas-results-state" role="status" aria-live="polite">
            <div className="gwas-spinner" />
            <p>Memuat hasil analisis GWAS...</p>
          </div>
        ) : manhattanError ? (
          <div className="gwas-results-state gwas-results-error" role="alert">
            <p>Gagal memuat hasil: {manhattanError}</p>
            <button type="button" onClick={refetch}>
              Coba Lagi
            </button>
          </div>
        ) : (
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
        )
      )}

      {/* TAB 2: POST-GWAS & LEAD SNPS */}
      {activeTab === "postgwas" && (
        <div style={{ display: "grid", gap: 18 }}>
          <TabResourceNotice resources={[thresholdsRes, leadSnpsRes]} label="data ambang batas & Lead SNPs" />
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
              suggestiveSnps={suggestiveSnpsList}
              activeThreshold={computedThreshold}
              activeThresholdMethod={activeThresholdLabel}
              pipelineThreshold={hasThresholds ? Number(thresholdsData.selected_p) || null : null}
              isLoading={leadSnpsRes.isLoading}
              jobId={jobId}
            />
          </div>
        </div>
      )}

      {/* TAB 3: STRUKTUR POPULASI & LD DECAY */}
      {activeTab === "structure" && (
        <div style={{ display: "grid", gap: 20 }}>
          <TabResourceNotice resources={[modelRecRes, pcaRes, kinshipRes, ldDecayRes]} label="data struktur populasi & LD" />
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
          <TabResourceNotice resources={[inspectionRes, qcRes]} label="data inspeksi & QC" />
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
        <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0", display: "grid", gap: 14 }}>
          <TabResourceNotice resources={[peakRes, multilocusRes]} label="data Multi-Locus GWAS" />
          {hasMultilocusData ? (
            <div style={{ display: "grid", gap: 18 }}>
              <div
                style={{
                  background: "#f8fafc",
                  padding: 14,
                  borderRadius: 8,
                  border: "1px solid #e2e8f0",
                  display: "grid",
                  gap: 12,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
                  <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#0f172a" }}>
                    Manhattan &amp; QQ Plot per Model Multi-Locus
                  </h3>
                  <div
                    role="group"
                    aria-label="Pilih model plot multi-locus"
                    style={{ display: "flex", gap: 6, flexWrap: "wrap" }}
                  >
                    {availableMultilocusModels.map((mKey) => {
                      const active = selectedMultiModel === mKey;
                      return (
                        <button
                          key={mKey}
                          type="button"
                          aria-pressed={active}
                          onClick={() => setSelectedMultiModel(mKey)}
                          style={{
                            padding: "6px 12px",
                            borderRadius: 6,
                            border: "1px solid",
                            borderColor: active ? "#1e40af" : "#cbd5e1",
                            background: active ? "#1e40af" : "#ffffff",
                            color: active ? "#ffffff" : "#334155",
                            fontWeight: active ? 700 : 500,
                            fontSize: 12,
                            cursor: "pointer",
                          }}
                        >
                          {MULTILOCUS_MODEL_LABELS[mKey] || mKey}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {multiPlotLoading && (
                  <div role="status" style={{ padding: 16, textAlign: "center", color: "#475569", fontSize: 13 }}>
                    Memuat plot {MULTILOCUS_MODEL_LABELS[selectedMultiModel] || selectedMultiModel}...
                  </div>
                )}

                {multiPlotError && !multiPlotLoading && (
                  <div role="alert" style={{ padding: 12, borderRadius: 6, background: "#fef2f2", color: "#b91c1c", fontSize: 13 }}>
                    {multiPlotError}
                  </div>
                )}

                {multiPlotData && multiPlotData.total_points > 0 && !multiPlotLoading && (
                  <div style={{ display: "grid", gap: 16 }}>
                    <ManhattanPlot data={multiPlotData} significanceThreshold={computedThreshold} />
                    <QQPlot data={multiPlotData} />
                  </div>
                )}
              </div>

              <PeakComparisonTable comparison={peakData} multilocusSummary={multilocusSummary} />
            </div>
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

      {/* TAB 6: ANOTASI GEN KANDIDAT & GO ENRICHMENT */}
      {activeTab === "annotation" && (
        <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0", display: "grid", gap: 14 }}>
          <TabResourceNotice resources={[annotationRes, goRes]} label="data anotasi gen & GO enrichment" />
          <CandidateGenesTable
            annotationData={annotationData}
            goEnrichmentData={goEnrichmentData}
            jobId={jobId}
          />
        </div>
      )}

      {/* TAB 7: FLANKING SEQUENCE & PRIMER DESIGN */}
      {activeTab === "primers" && (
        <div style={{ background: "#fff", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0", display: "grid", gap: 14 }}>
          <TabResourceNotice resources={[primersRes]} label="data desain primer Primer3" />
          <PrimerDesignPanel primersData={primersData} jobId={jobId} />
        </div>
      )}
    </div>
  );
}
