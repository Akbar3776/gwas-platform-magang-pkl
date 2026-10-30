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

/**
 * Mengambil seluruh log proses eksekusi untuk setiap tahapan pipeline.
 * @param {string} jobId
 */
export async function fetchJobLogs(jobId) {
  const { data } = await httpClient.get(`/jobs/${jobId}/logs`);
  return data;
}

/**
 * Mengambil daftar dataset/file VCF yang pernah diunggah user.
 */
export async function fetchUploadedDatasets() {
  const { data } = await httpClient.get("/upload/datasets");
  return data;
}

/**
 * Pratinjau isi (120 baris pertama) file VCF yang telah diunggah.
 * @param {string} fileId
 */
export async function previewUploadedDataset(fileId) {
  const { data } = await httpClient.get(`/upload/${fileId}/download`, {
    params: { preview: true },
    responseType: "text",
  });
  return data;
}

/**
 * Mengunduh file VCF yang telah diunggah.
 * @param {string} fileId
 * @param {string} filename
 */
export async function downloadUploadedDataset(fileId, filename) {
  const response = await httpClient.get(`/upload/${fileId}/download`, {
    responseType: "blob",
  });
  const blobUrl = window.URL.createObjectURL(response.data);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename || `dataset_${fileId}.vcf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(blobUrl);
}

/**
 * Menghapus file VCF yang telah diunggah dari server.
 * @param {string} fileId
 */
export async function deleteUploadedDataset(fileId) {
  const { data } = await httpClient.delete(`/upload/${fileId}`);
  return data;
}

/**
 * Mengambil daftar seluruh job milik user (yang sedang berjalan maupun sudah selesai).
 */
export async function fetchUserJobs() {
  const { data } = await httpClient.get("/jobs");
  return data;
}

/**
 * Menghentikan eksekusi job yang sedang berjalan.
 * @param {string} jobId
 */
export async function cancelJob(jobId) {
  const { data } = await httpClient.post(`/jobs/${jobId}/cancel`);
  return data;
}

/**
 * Menjalankan ulang (retry) atau melanjutkan (resume) job yang berhenti (failed/cancelled).
 * @param {string} jobId
 * @param {{ fromScratch?: boolean }} options
 */
export async function retryJob(jobId, { fromScratch = false } = {}) {
  const { data } = await httpClient.post(`/jobs/${jobId}/retry`, null, {
    params: { from_scratch: Boolean(fromScratch) },
  });
  return data;
}

/**
 * Menghapus job beserta seluruh artefak file kerjanya.
 * @param {string} jobId
 */
export async function deleteJob(jobId) {
  const { data } = await httpClient.delete(`/jobs/${jobId}`);
  return data;
}

/**
 * Mengambil daftar file input & file hasil/intermediate pada sebuah job.
 * @param {string} jobId
 */
export async function fetchJobFiles(jobId) {
  const { data } = await httpClient.get(`/jobs/${jobId}/files`);
  return data;
}

/**
 * Pratinjau teks (150 baris pertama) dari file pada sebuah job.
 * @param {string} jobId
 * @param {string} fileKey
 */
export async function previewJobFile(jobId, fileKey) {
  const { data } = await httpClient.get(`/jobs/${jobId}/files/content`, {
    params: { file_key: fileKey, preview: true },
    responseType: "text",
  });
  return data;
}

/**
 * Mengunduh file spesifik dari sebuah job yang sedang berjalan maupun selesai.
 * @param {string} jobId
 * @param {string} fileKey
 * @param {string} filename
 */
export async function downloadJobFile(jobId, fileKey, filename) {
  const response = await httpClient.get(`/jobs/${jobId}/files/content`, {
    params: { file_key: fileKey, preview: false },
    responseType: "blob",
  });
  const blobUrl = window.URL.createObjectURL(response.data);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename || "job_file";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(blobUrl);
}

/**
 * Unggah file fenotipe (.txt/.tsv/.csv) untuk dataset.
 */
export async function uploadPhenotype(fileId, file) {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await httpClient.post(`/upload/${fileId}/phenotype`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

/**
 * Hapus file fenotipe dari dataset.
 */
export async function deletePhenotype(fileId) {
  const { data } = await httpClient.delete(`/upload/${fileId}/phenotype`);
  return data;
}

/**
 * Unggah file anotasi GFF3 (.gff3/.gff/.gff3.gz) untuk dataset.
 */
export async function uploadAnnotation(fileId, file, onProgress) {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await httpClient.post(`/upload/${fileId}/annotation`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (e) => {
      if (onProgress && e.total) {
        onProgress(Math.round((e.loaded * 100) / e.total));
      }
    },
  });
  return data;
}

/**
 * Hapus file anotasi GFF3 dari dataset.
 */
export async function deleteAnnotation(fileId) {
  const { data } = await httpClient.delete(`/upload/${fileId}/annotation`);
  return data;
}

/**
 * Lakukan inspeksi sinkron (Fase 1: Pre-run Inspection) untuk VCF dan fenotipe.
 */
export async function fetchInspection(fileId, { trait, idColumn = "IID", fidColumn = "FID" }) {
  const { data } = await httpClient.get(`/upload/${fileId}/inspection`, {
    params: { trait, id_column: idColumn, fid_column: fidColumn },
  });
  return data;
}

/**
 * Unduh file teks hasil rekonsiliasi ID (keep_ids.txt, dsb).
 */
export async function downloadInspectionFile(fileId, filename) {
  const response = await httpClient.get(`/upload/${fileId}/inspection/files/${filename}`, {
    responseType: "blob",
  });
  const blobUrl = window.URL.createObjectURL(response.data);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(blobUrl);
}

/**
 * Ambil daftar kapabilitas tools (PLINK2, GEMMA, Beagle, Rscript, dsb).
 */
export async function fetchCapabilities() {
  const { data } = await httpClient.get("/pipeline/capabilities");
  return data;
}
