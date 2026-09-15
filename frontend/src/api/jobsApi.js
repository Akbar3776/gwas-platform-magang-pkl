import { httpClient } from "./httpClient";

/**
 * Men-trigger job Quality Control (PLINK) untuk sebuah dataset yang sudah
 * selesai diunggah & dirakit (upload_status: uploaded/ready).
 *
 * @param {string} datasetId - `file_id` dari hasil upload (= dataset.id)
 * @param {{ maf?: number, geno?: number, mind?: number }} params
 * @returns {Promise<{ job_id: string, overall_status: string, celery_task_id: string }>}
 */
export async function triggerQcJob(datasetId, params = {}) {
  const { data } = await httpClient.post("/jobs/qc", {
    dataset_id: datasetId,
    maf: params.maf ?? 0.01,
    geno: params.geno ?? 0.05,
    mind: params.mind ?? 0.1,
  });
  return data;
}

export async function triggerFullPipeline(datasetId, config) {
  const { data } = await httpClient.post(`/pipeline/${datasetId}/start`, config);
  return data;
}

/**
 * Mengambil status job sekali (dipakai internal oleh useJobPolling).
 * @param {string} jobId
 */
export async function fetchJobStatus(jobId) {
  const { data } = await httpClient.get(`/jobs/${jobId}/status`);
  return data;
}
