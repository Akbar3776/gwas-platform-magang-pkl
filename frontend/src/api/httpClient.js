import axios from "axios";
import { getAccessToken, refreshAccessToken } from "./apiClient";

/**
 * Instance axios terpusat. Semua hook/service memakai ini agar base URL,
 * header Authorization, dan auto-refresh 401 hanya diatur di satu tempat.
 */
export const httpClient = axios.create({
  baseURL: "/api/v1",
  withCredentials: true,
});

function setAuthorizationHeader(headers, token) {
  if (!token) return headers;
  if (typeof headers?.set === "function") {
    headers.set("Authorization", `Bearer ${token}`);
    return headers;
  }
  return { ...(headers || {}), Authorization: `Bearer ${token}` };
}

httpClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  config._requestToken = token;
  if (token) {
    config.headers = setAuthorizationHeader(config.headers, token);
  }
  return config;
});

httpClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      originalRequest._retry = true;
      try {
        const newToken = await refreshAccessToken(originalRequest._requestToken);
        originalRequest.headers = setAuthorizationHeader(originalRequest.headers, newToken);
        return httpClient(originalRequest);
      } catch (refreshError) {
        return Promise.reject(refreshError);
      }
    }
    return Promise.reject(error);
  }
);
