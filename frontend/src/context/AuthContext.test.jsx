import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { AuthProvider, useAuth } from "./AuthContext";

jest.mock("../api/apiClient", () => ({
  apiFetch: jest.fn(),
  setAccessToken: jest.fn(),
  setAuthFailureHandler: jest.fn(),
}));

const { apiFetch, setAccessToken } = require("../api/apiClient");

function LogoutHarness() {
  const { status, logout } = useAuth();
  return (
    <div>
      <div data-testid="status">{status}</div>
      <button type="button" onClick={() => logout()}>
        Keluar
      </button>
    </div>
  );
}

describe("AuthContext logout resilience (A34)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        access_token: "tok-initial",
        user: { email: "tester@example.com", full_name: "Tester" },
      }),
    });
  });

  test("logout membersihkan sesi tanpa unhandled rejection meski apiFetch(/auth/logout) reject", async () => {
    apiFetch.mockRejectedValueOnce(new Error("Network offline"));
    render(
      <AuthProvider>
        <LogoutHarness />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("authenticated");
    });

    fireEvent.click(screen.getByRole("button", { name: "Keluar" }));

    await waitFor(() => {
      expect(setAccessToken).toHaveBeenLastCalledWith(null);
      expect(screen.getByTestId("status")).toHaveTextContent("unauthenticated");
    });
  });
});
