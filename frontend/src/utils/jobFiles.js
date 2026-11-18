import { downloadJobFile, fetchJobFiles } from "../api/jobsApi";

/**
 * Pilih entri file job dari respons /files berdasarkan kecocokan file_key atau nama file,
 * dengan prioritas tahap (stage) tertentu jika disediakan.
 */
export function selectJobFileByName(files, filename, preferredStage = null) {
  if (!Array.isArray(files) || !filename) return null;
  if (preferredStage) {
    const stageMatch = files.find(
      (f) => f?.stage === preferredStage && (f.file_key === filename || f.filename === filename)
    );
    if (stageMatch) return stageMatch;
  }
  return files.find((f) => f?.file_key === filename || f?.filename === filename) || null;
}

/** Resolusi file_key persis dari /jobs/{jobId}/files lalu unduh via downloadJobFile. */
export async function downloadJobFileByName(jobId, filename, preferredStage = null) {
  const res = await fetchJobFiles(jobId);
  const match = selectJobFileByName(res?.files, filename, preferredStage);
  if (!match?.file_key) {
    throw new Error(`File ${filename} tidak ditemukan pada job ini.`);
  }
  await downloadJobFile(jobId, match.file_key, match.filename || filename);
}
