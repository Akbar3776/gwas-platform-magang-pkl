import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { apiFetch, setAccessToken, setAuthFailureHandler } from "../api/apiClient";

const API_BASE = "/api/v1";

/**
 * AuthContext.jsx
 *
 * State yang disediakan:
 *   - user            : object user yang login, atau null
 *   - status          : "checking" | "authenticated" | "unauthenticated"
 *   - login(email, password)
 *   - register(email, password, fullName)
 *   - logout()
 *
 * Alur saat aplikasi pertama dimuat (refresh halaman browser):
 *   1. Access token hilang (cuma disimpan di memori — lihat apiClient.js).
 *   2. useEffect di bawah otomatis memanggil POST /auth/refresh, yang
 *      memakai refresh token di HttpOnly cookie (terkirim otomatis oleh
 *      browser, JS tidak pernah menyentuhnya).
 *   3. Kalau berhasil -> user tetap dianggap login, dapat access token baru.
 *      Kalau gagal (cookie tidak ada/expired) -> status "unauthenticated".
 * Selama proses ini, status = "checking", dipakai ProtectedRoute untuk
 * menampilkan loading state alih-alih langsung redirect ke /login (supaya
 * user yang sebenarnya masih login tidak "kelihatan ke-logout sekilas"
 * setiap kali reload halaman).
 */

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState("checking");

  const handleAuthFailure = useCallback(() => {
    setAccessToken(null);
    setUser(null);
    setStatus("unauthenticated");
  }, []);

  useEffect(() => {
    setAuthFailureHandler(handleAuthFailure);
  }, [handleAuthFailure]);

  // Coba pulihkan sesi lewat refresh token cookie saat app pertama dimuat.
  useEffect(() => {
    async function restoreSession() {
      try {
        const res = await fetch(`${API_BASE}/auth/refresh`, {
          method: "POST",
          credentials: "include",
        });
        if (!res.ok) throw new Error("no session");
        const data = await res.json();
        setAccessToken(data.access_token);
        setUser(data.user);
        setStatus("authenticated");
      } catch {
        setStatus("unauthenticated");
      }
    }
    restoreSession();
  }, []);

  const login = useCallback(async (email, password) => {
    // Backend memakai OAuth2PasswordRequestForm -> body harus form-urlencoded,
    // dengan field "username" (dipetakan ke email di sisi backend).
    const body = new URLSearchParams();
    body.set("username", email);
    body.set("password", password);

    const res = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      credentials: "include",
      body,
    });

    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      throw new Error(detail?.detail || "Login gagal. Periksa kembali email dan password Anda.");
    }

    const data = await res.json();
    setAccessToken(data.access_token);
    setUser(data.user);
    setStatus("authenticated");
    return data.user;
  }, []);

  const register = useCallback(async (email, password, fullName) => {
    const res = await fetch(`${API_BASE}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, full_name: fullName }),
    });

    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      throw new Error(detail?.detail || "Pendaftaran gagal. Coba lagi.");
    }
    // Registrasi berhasil tidak otomatis login — arahkan user ke halaman login.
    return res.json();
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } finally {
      setAccessToken(null);
      setUser(null);
      setStatus("unauthenticated");
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, status, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth harus dipakai di dalam <AuthProvider>");
  return ctx;
}

/**
 * ProtectedRoute
 *
 * Bungkus rute dashboard dengan ini di react-router:
 *
 *   <Route element={<ProtectedRoute />}>
 *     <Route path="/dashboard" element={<Dashboard />} />
 *     <Route path="/jobs/:jobId" element={<ResultDashboard />} />
 *   </Route>
 *
 * - Saat status masih "checking" (proses restore sesi via refresh token):
 *   tampilkan loading, JANGAN langsung redirect (mencegah "flash" ke halaman
 *   login untuk user yang sebenarnya masih valid sesinya).
 * - Saat "unauthenticated": redirect ke /login, sambil menyimpan lokasi asal
 *   di state supaya bisa diarahkan balik setelah login berhasil.
 */
export function ProtectedRoute() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "checking") {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-slate-500">
        Memeriksa sesi login…
      </div>
    );
  }

  if (status === "unauthenticated") {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Merender rute anak (Dashboard, ResultDashboard, dll) lewat <Outlet/>,
  // sesuai pola nested route react-router v6 pada contoh pemakaian di atas.
  return <Outlet />;
}
