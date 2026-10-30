import axios from "axios";
import { getAccessToken, notifyAuthFailure, refreshAccessToken } from "./apiClient";

/**
 * Instance axios terpusat. Semua hook/service memakai ini agar base URL,
 * header Authorization, dan auto-refresh 401 hanya diatur di satu tempat.
 */
export const httpClient = axios.create({
  baseURL: "/api/v1",
  withCredentials: true,
});

httpClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
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
        const newToken = await refreshAccessToken();
        originalRequest.headers.Authorization = `Bearer ${newToken}`;
        return httpClient(originalRequest);
      } catch {
        notifyAuthFailure();
      }
    }
    return Promise.reject(error);
  }
);
