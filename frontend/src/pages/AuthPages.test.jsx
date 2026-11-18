import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import LoginPage from "./LoginPage.jsx";
import RegisterPage from "./RegisterPage.jsx";

jest.mock("../context/AuthContext.jsx", () => ({
  useAuth: jest.fn(),
}));

describe("Auth pages accessibility and error semantics (A18)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("LoginPage exposes autocomplete/name attributes and accessible role=alert error", async () => {
    const loginMock = jest.fn().mockRejectedValue(new Error("Email atau password salah."));
    useAuth.mockReturnValue({ login: loginMock });

    const { container } = render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );

    const emailInput = screen.getByLabelText(/Email/i);
    const passwordInput = screen.getByLabelText(/Password/i);

    expect(emailInput).toHaveAttribute("name", "email");
    expect(emailInput).toHaveAttribute("autocomplete", "email");
    expect(passwordInput).toHaveAttribute("name", "password");
    expect(passwordInput).toHaveAttribute("autocomplete", "current-password");
    expect(emailInput).not.toHaveAttribute("aria-invalid", "true");

    fireEvent.change(emailInput, { target: { value: "user@example.com" } });
    fireEvent.change(passwordInput, { target: { value: "wrongpass" } });
    fireEvent.submit(container.querySelector("form"));

    const alert = await waitFor(() => screen.getByRole("alert"));
    expect(alert).toHaveTextContent("Email atau password salah.");
    expect(alert.id).toBeTruthy();

    expect(emailInput).toHaveAttribute("aria-invalid", "true");
    expect(emailInput).toHaveAttribute("aria-describedby", alert.id);
    expect(passwordInput).toHaveAttribute("aria-invalid", "true");
    expect(passwordInput).toHaveAttribute("aria-describedby", alert.id);
  });

  test("RegisterPage exposes autocomplete/name attributes and accessible role=alert error", async () => {
    const registerMock = jest.fn().mockRejectedValue(new Error("Email sudah terdaftar."));
    useAuth.mockReturnValue({ register: registerMock });

    const { container } = render(
      <MemoryRouter>
        <RegisterPage />
      </MemoryRouter>
    );

    const nameInput = screen.getByLabelText(/Nama Lengkap/i);
    const emailInput = screen.getByLabelText(/Email/i);
    const passwordInput = screen.getByLabelText(/Password/i);

    expect(nameInput).toHaveAttribute("name", "fullName");
    expect(nameInput).toHaveAttribute("autocomplete", "name");
    expect(emailInput).toHaveAttribute("name", "email");
    expect(emailInput).toHaveAttribute("autocomplete", "email");
    expect(passwordInput).toHaveAttribute("name", "password");
    expect(passwordInput).toHaveAttribute("autocomplete", "new-password");

    fireEvent.change(nameInput, { target: { value: "Peneliti Uji" } });
    fireEvent.change(emailInput, { target: { value: "dup@example.com" } });
    fireEvent.change(passwordInput, { target: { value: "password123" } });
    fireEvent.submit(container.querySelector("form"));

    const alert = await waitFor(() => screen.getByRole("alert"));
    expect(alert).toHaveTextContent("Email sudah terdaftar.");
    expect(alert.id).toBeTruthy();

    for (const input of [nameInput, emailInput, passwordInput]) {
      expect(input).toHaveAttribute("aria-invalid", "true");
      expect(input).toHaveAttribute("aria-describedby", alert.id);
    }
  });
});
