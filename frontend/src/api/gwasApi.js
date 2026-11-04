/**
 * API client untuk mengambil data hasil GWAS di setiap tahap analisis dari backend FastAPI.
 */

import { apiFetch } from "./apiClient";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function handleJsonResponse(response, defaultErrorMsg) {
  if (!response.ok) {
    let detail = `${defaultErrorMsg} (HTTP ${response.status})`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch {
      // response bukan JSON valid
    }
    throw new ApiError(detail, response.status);
  }
  return response.json();
}

/**
 * Mengambil data ringan (CHR, BP, SNP, LOG10_P) untuk Manhattan/QQ Plot.
 */
export async function fetchManhattanData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/manhattan-data`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data Manhattan plot");
}

/**
 * Mengambil data hasil Inspeksi Fenotipe & Sampel (Fase 1).
 */
export async function fetchInspectionData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/inspection`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data inspeksi fenotipe");
}

/**
 * Mengambil data perbandingan sebelum/sesudah QC (Fase 2).
 */
export async function fetchQcComparisonData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/qc-comparison`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data perbandingan QC");
}

/**
 * Mengambil data PCA & Scree plot (Fase 3).
 */
export async function fetchPcaData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/pca`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data PCA");
}

/**
 * Mengambil data matriks kekerabatan Kinship / KING (Fase 3).
 */
export async function fetchKinshipData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/kinship`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data kinship");
}

/**
 * Mengambil data evaluasi kurva LD decay (Fase 4D).
 */
export async function fetchLdDecayData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/ld-decay`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data LD decay");
}

/**
 * Mengambil data rekomendasi model GWAS GLM vs LMM (Fase 3).
 */
export async function fetchModelRecommendationData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/model-recommendation`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data rekomendasi model");
}

/**
 * Mengambil data ambang batas signifikansi (Fase 4E).
 */
export async function fetchThresholdsData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/thresholds`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data ambang signifikansi");
}

/**
 * Mengambil daftar Lead SNPs hasil clumping (Fase 4F).
 */
export async function fetchLeadSnpsData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/lead-snps`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data Lead SNPs");
}

/**
 * Mengambil perbandingan puncak GWAS single vs multi-locus (Fase 4H).
 */
export async function fetchPeakComparisonData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/peak-comparison`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data perbandingan puncak GWAS");
}

/**
 * Mengambil hasil detail Multi-Locus GWAS (FarmCPU, BLINK, MLMM) (Fase 4C).
 */
export async function fetchMultilocusResultsData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/multilocus-results`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data hasil multi-locus GWAS");
}

/**
 * Mengambil pemetaan gen kandidat GFF3 dan daftar GO (Fase 5).
 */
export async function fetchCandidateGenesData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/candidate-genes`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data anotasi gen kandidat");
}

/**
 * Mengambil hasil Gene Ontology (GO) Enrichment & Tabel GO (Fase 5.3).
 */
export async function fetchGoEnrichmentData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/go-enrichment`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data GO enrichment");
}

/**
 * Mengambil sekuens flanking 201 bp dan desain primer Primer3 (Fase 5.4 / 6–8).
 */
export async function fetchPrimersData(jobId, signal) {
  const response = await apiFetch(`/results/${jobId}/primers`, {
    signal,
    headers: { Accept: "application/json" },
  });
  return handleJsonResponse(response, "Gagal mengambil data desain primer");
}
