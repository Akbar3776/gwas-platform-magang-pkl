/**
 * chunkedUpload.js
 *
 * Fungsi utilitas React untuk memecah file besar (VCF) menjadi beberapa
 * chunk dan mengirimkannya secara berurutan ke backend FastAPI.
 *
 * Alur:
 *   1. POST /upload/init   -> dapatkan file_id
 *   2. POST /upload/chunk  -> kirim tiap potongan file satu per satu
 *   3. POST /upload/finalize -> validasi semua chunk dan trigger assembly
 */

import { httpClient } from "./httpClient";

const CHUNK_SIZE = 5 * 1024 * 1024; // 5 MB per chunk

/**
 * @param {File} file - file VCF yang dipilih user dari <input type="file">
 * @param {(progressPercent: number) => void} onProgress - callback untuk update progress bar
 * @returns {Promise<{ fileId: string, isComplete: boolean }>}
 */
export async function uploadFileInChunks(file, onProgress) {
  // -- 1. Inisiasi sesi upload --
  const initResponse = await httpClient.post("/upload/init", {
    original_filename: file.name,
    total_size_bytes: file.size,
  });

  const fileId = initResponse.data.file_id;
  const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

  // -- 2. Kirim tiap chunk secara berurutan --
  // Sengaja sekuensial (bukan Promise.all paralel) agar tidak membebani
  // koneksi/browser saat file berukuran GB, dan agar progress bar akurat.
  let isComplete = false;

  for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
    const start = chunkIndex * CHUNK_SIZE;
    const end = Math.min(start + CHUNK_SIZE, file.size);
    const chunkBlob = file.slice(start, end); // memotong file tanpa load penuh ke memori

    const formData = new FormData();
    formData.append("file_id", fileId);
    formData.append("chunk_index", chunkIndex);
    formData.append("total_chunks", totalChunks);
    formData.append("chunk", chunkBlob, file.name);

    await httpClient.post("/upload/chunk", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });

    const percent = Math.round(((chunkIndex + 1) / totalChunks) * 100);
    onProgress?.(percent);

  }

  await httpClient.post("/upload/finalize", makeFinalizeForm(fileId, totalChunks));
  isComplete = true;

  return { fileId, isComplete };
}

/**
 * Melanjutkan upload yang sempat terputus: cek chunk mana saja yang sudah
 * diterima server, lalu lanjutkan hanya dari chunk yang belum terkirim.
 *
 * @param {File} file
 * @param {string} fileId - file_id dari sesi upload sebelumnya
 * @param {(progressPercent: number) => void} onProgress
 */
export async function resumeUploadInChunks(file, fileId, onProgress) {
  const statusResponse = await httpClient.get(`/upload/${fileId}/status`);
  const receivedIndices = new Set(statusResponse.data.received_chunk_indices);

  const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
  let isComplete = false;

  for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
    if (receivedIndices.has(chunkIndex)) {
      onProgress?.(Math.round(((chunkIndex + 1) / totalChunks) * 100));
      continue; // lewati chunk yang sudah pernah diterima server
    }

    const start = chunkIndex * CHUNK_SIZE;
    const end = Math.min(start + CHUNK_SIZE, file.size);
    const chunkBlob = file.slice(start, end);

    const formData = new FormData();
    formData.append("file_id", fileId);
    formData.append("chunk_index", chunkIndex);
    formData.append("total_chunks", totalChunks);
    formData.append("chunk", chunkBlob, file.name);

    await httpClient.post("/upload/chunk", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });

    onProgress?.(Math.round(((chunkIndex + 1) / totalChunks) * 100));
  }

  await httpClient.post("/upload/finalize", makeFinalizeForm(fileId, totalChunks));
  isComplete = true;

  return { fileId, isComplete };
}

function makeFinalizeForm(fileId, totalChunks) {
  const formData = new FormData();
  formData.append("file_id", fileId);
  formData.append("total_chunks", totalChunks);
  return formData;
}

/* ------------------------------------------------------------------------
 * Contoh pemakaian di dalam komponen React:
 *
 * function UploadPage() {
 *   const [progress, setProgress] = useState(0);
 *
 *   const handleFileChange = async (event) => {
 *     const file = event.target.files[0];
 *     const { fileId, isComplete } = await uploadFileInChunks(file, setProgress);
 *     console.log("Upload selesai, assembly berjalan di background:", fileId, isComplete);
 *   };
 *
 *   return (
 *     <div>
 *       <input type="file" accept=".vcf,.vcf.gz" onChange={handleFileChange} />
 *       <progress value={progress} max={100} />
 *     </div>
 *   );
 * }
 * ---------------------------------------------------------------------- */
