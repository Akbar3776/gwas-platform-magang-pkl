/**
 * API client tipis untuk mengambil data hasil GWAS dari backend FastAPI.
 * Dipisah dari hook agar mudah di-mock saat unit test dan bisa dipakai
 * ulang di bagian lain (mis. tombol export CSV, tabel ringkasan, dsb).
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api/v1";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Mengambil data ringan (CHR, BP, SNP, LOG10_P) untuk Manhattan/QQ Plot.
 * Endpoint yang sama dipakai untuk kedua jenis plot agar tidak perlu
 * dua kali round-trip ke server.
 *
 * @param {string} jobId
 * @param {AbortSignal} [signal] - untuk membatalkan request saat unmount
 * @returns {Promise<{job_id:string, total_points:number, chr:number[], bp:number[], snp:string[], log10_p:number[]}>}
 */
export async function fetchManhattanData(jobId, signal) {
  const response = await fetch(`${API_BASE_URL}/results/${jobId}/manhattan-data`, {
    signal,
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    let detail = `Gagal mengambil data hasil (HTTP ${response.status})`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch {
      // Response bukan JSON valid, gunakan pesan default di atas.
    }
    throw new ApiError(detail, response.status);
  }

  return response.json();
}
