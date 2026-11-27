import { httpClient } from "./httpClient";
import { extractDownloadErrorMessage, saveBlob } from "../utils/download";

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
  try {
    const response = await httpClient.get(`/upload/${fileId}/download`, {
      responseType: "blob",
    });
    saveBlob(response.data, filename || `dataset_${fileId}.vcf`);
  } catch (err) {
    const message = await extractDownloadErrorMessage(err, "Gagal mengunduh file dataset.");
    throw new Error(message);
  }
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
export async function fetchJobFiles(jobId, { signal } = {}) {
  const config = signal ? { signal } : undefined;
  const { data } = await httpClient.get(`/jobs/${jobId}/files`, config);
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
  try {
    const response = await httpClient.get(`/jobs/${jobId}/files/content`, {
      params: { file_key: fileKey, preview: false },
      responseType: "blob",
    });
    saveBlob(response.data, filename || "job_file");
  } catch (err) {
    const message = await extractDownloadErrorMessage(err, "Gagal mengunduh file job.");
    throw new Error(message);
  }
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
 * Helper bersama untuk mengunggah file dataset tunggal via FormData.
 */
async function postDatasetFile(url, file, onProgress) {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await httpClient.post(url, formData, {
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
 * Unggah file anotasi GFF3 (.gff3/.gff/.gff3.gz) untuk dataset.
 */
export async function uploadAnnotation(fileId, file, onProgress) {
  return postDatasetFile(`/upload/${fileId}/annotation`, file, onProgress);
}

/**
 * Hapus file anotasi GFF3 dari dataset.
 */
export async function deleteAnnotation(fileId) {
  const { data } = await httpClient.delete(`/upload/${fileId}/annotation`);
  return data;
}

/**
 * Unggah file pemetaan Gene Ontology (.tsv/.txt/.gaf/.gaf.gz) untuk dataset.
 */
export async function uploadGoAnnotation(fileId, file, onProgress) {
  return postDatasetFile(`/upload/${fileId}/go-annotation`, file, onProgress);
}

/**
 * Hapus file pemetaan Gene Ontology dari dataset.
 */
export async function deleteGoAnnotation(fileId) {
  const { data } = await httpClient.delete(`/upload/${fileId}/go-annotation`);
  return data;
}

/**
 * Unggah file hasil BLAST outfmt6 untuk membangun indeks best-hit ortolog dataset.
 */
export async function uploadOrtholog(fileId, file, onProgress) {
  return postDatasetFile(`/upload/${fileId}/ortholog`, file, onProgress);
}

/**
 * Hapus indeks best-hit ortolog dari dataset.
 */
export async function deleteOrtholog(fileId) {
  const { data } = await httpClient.delete(`/upload/${fileId}/ortholog`);
  return data;
}

/**
 * Hapus FASTA referensi genom dan indeks .fai dari dataset.
 */
export async function deleteReference(fileId) {
  const { data } = await httpClient.delete(`/upload/${fileId}/reference`);
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
  try {
    const response = await httpClient.get(`/upload/${fileId}/inspection/files/${filename}`, {
      responseType: "blob",
    });
    saveBlob(response.data, filename);
  } catch (err) {
    const message = await extractDownloadErrorMessage(err, "Gagal mengunduh file inspeksi.");
    throw new Error(message);
  }
}

/**
 * Ambil daftar kapabilitas tools (PLINK2, GEMMA, Beagle, Rscript, dsb).
 */
export async function fetchCapabilities() {
  const { data } = await httpClient.get("/pipeline/capabilities");
  return data;
}

export async function fetchReferencePanels() {
  const { data } = await httpClient.get("/reference-panels");
  return data;
}
