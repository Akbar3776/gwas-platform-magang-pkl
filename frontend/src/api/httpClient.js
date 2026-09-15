import axios from "axios";
import { getAccessToken } from "./apiClient";

/**
 * Instance axios terpusat. Semua hook/service memakai ini agar base URL
 * dan konfigurasi (timeout, header default, dll) hanya diatur di satu tempat.
 */
export const httpClient = axios.create({
  baseURL: "/api/v1", // sesuaikan dengan base URL backend FastAPI (mis. proxy Vite/Nginx)
  withCredentials: true,
});

httpClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});
