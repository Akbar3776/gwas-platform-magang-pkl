import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import App from "./App";
import { AuthProvider, ProtectedRoute } from "./context/AuthContext";

jest.mock("./pages/GwasUploadPage", () => ({
  GwasUploadPage: () => <div>Mock Upload Page</div>,
}));

jest.mock("./pages/JobStatusPage", () => ({
  __esModule: true,
  default: () => <div>Mock Job Status Page</div>,
}));

jest.mock("./pages/ResultsPage", () => ({
  __esModule: true,
  default: () => <div>Mock Results Page</div>,
}));

describe("Routing, document.title, 404 NotFoundPage, and session status (A39)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    document.title = "GWAS Platform";
    globalThis.fetch = jest.fn().mockResolvedValue({ ok: false, status: 401 });
  });

  test("renders 404 NotFoundPage on unknown route and updates document.title", async () => {
    render(
      <MemoryRouter
        initialEntries={["/halaman-tidak-dikenal"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(screen.getByRole("heading", { name: /404/i })).toBeInTheDocument();
    expect(document.title).toMatch(/404|Tidak Ditemukan/i);
    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    });
  });

  test("sets document.title on /login and /register routes", async () => {
    const { unmount } = render(
      <MemoryRouter
        initialEntries={["/login"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    );
    expect(document.title).toMatch(/Masuk/i);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(1));
    unmount();

    render(
      <MemoryRouter
        initialEntries={["/register"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    );
    expect(document.title).toMatch(/Daftar/i);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(2));
  });

  test("ProtectedRoute checking state uses role='status' without missing h-screen class", () => {
    globalThis.fetch = jest.fn(() => new Promise(() => {}));

    render(
      <MemoryRouter
        initialEntries={["/upload"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route path="/upload" element={<div>Protected</div>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    const statusNode = screen.getByRole("status");
    expect(statusNode).toHaveTextContent(/Memeriksa sesi login/i);
    expect(statusNode).not.toHaveClass("h-screen");
    expect(statusNode).toHaveClass("session-status");
  });

  test("brand link points to /login when logged out and /upload when authenticated (A53)", async () => {
    const { unmount } = render(
      <MemoryRouter
        initialEntries={["/login"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("link", { name: "GWAS Platform" })).toHaveAttribute("href", "/login");
    unmount();

    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        access_token: "tok-a53",
        user: { email: "researcher@example.com", full_name: "Peneliti" },
      }),
    });

    render(
      <MemoryRouter
        initialEntries={["/upload"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText("Mock Upload Page")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "GWAS Platform" })).toHaveAttribute("href", "/upload");
  });

  test("lazy-loaded protected route renders inside Suspense and sets route title", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        access_token: "tok-a39",
        user: { email: "researcher@example.com", full_name: "Peneliti" },
      }),
    });

    render(
      <MemoryRouter
        initialEntries={["/jobs/job-77/results"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Mock Results Page")).toBeInTheDocument();
    });
    expect(document.title).toMatch(/Hasil Analisis GWAS/i);
  });
});
