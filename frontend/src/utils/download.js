import { formatApiError } from "../api/formatApiError";

/**
 * Menyimpan objek Blob sebagai unduhan browser lalu mencabut URL objeknya.
 * @param {Blob} blob
 * @param {string} filename
 */
export function saveBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename || "download";
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    window.URL.revokeObjectURL(url);
  }
}

async function readBlobText(blob) {
  if (typeof blob.text === "function") {
    return blob.text();
  }
  if (typeof FileReader !== "undefined") {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("Gagal membaca Blob"));
      reader.readAsText(blob);
    });
  }
  return "";
}

/**
 * Mengekstrak pesan error yang terbaca dari respons gagal Axios/fetch, termasuk
 * respons `responseType: "blob"` yang membungkus JSON `{ detail }` di dalam Blob.
 */
export async function extractDownloadErrorMessage(
  err,
  fallback = "Gagal mengunduh file."
) {
  const rawData = err?.response?.data;
  if (typeof Blob !== "undefined" && rawData instanceof Blob) {
    try {
      const text = await readBlobText(rawData);
      if (text) {
        try {
          return formatApiError(JSON.parse(text), fallback);
        } catch {
          return text.trim() || fallback;
        }
      }
    } catch {
      // Abaikan error saat membaca Blob dan gunakan pesan fallback standar.
    }
  }
  return formatApiError(err, fallback);
}
