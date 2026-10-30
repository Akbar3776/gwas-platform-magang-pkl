/**
 * apiClient.js
 *
 * Wrapper fetch yang:
 * - Menyisipkan header `Authorization: Bearer <accessToken>` otomatis.
 * - Kalau server balas 401 (access token expired), coba tukar via
 *   POST /auth/refresh (memakai refresh token di HttpOnly cookie) SEKALI,
 *   lalu ulangi request asli dengan access token baru.
 * - Kalau refresh juga gagal, panggil `onAuthFailure`.
 */

const API_BASE = "/api/v1";

let currentAccessToken = null;
let onAuthFailure = () => {};

export function setAccessToken(token) {
  currentAccessToken = token;
}

export function getAccessToken() {
  return currentAccessToken;
}

export function setAuthFailureHandler(handler) {
  onAuthFailure = handler;
}

export function notifyAuthFailure() {
  onAuthFailure();
}

export async function refreshAccessToken() {
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error("Refresh token tidak valid");
  }
  const data = await res.json();
  currentAccessToken = data.access_token;
  return data.access_token;
}

/**
 * Pengganti `fetch` biasa untuk semua panggilan ke API yang butuh auth.
 * Penggunaan sama seperti fetch: `apiFetch("/jobs/123/results")`.
 */
export async function apiFetch(path, options = {}, _isRetry = false) {
  const headers = new Headers(options.headers || {});
  if (currentAccessToken) {
    headers.set("Authorization", `Bearer ${currentAccessToken}`);
  }

  const normalizedPath = path.startsWith(API_BASE)
    ? path.slice(API_BASE.length)
    : path;

  const response = await fetch(`${API_BASE}${normalizedPath}`, {
    ...options,
    headers,
    credentials: "include",
  });

  if (response.status === 401 && !_isRetry) {
    try {
      await refreshAccessToken();
      return apiFetch(normalizedPath, options, true);
    } catch {
      onAuthFailure();
      throw new Error("Sesi berakhir, silakan login kembali");
    }
  }

  return response;
}
