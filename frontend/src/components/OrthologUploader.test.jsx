import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { deleteOrtholog, uploadOrtholog } from "../api/jobsApi";
import { OrthologUploader } from "./OrthologUploader";

jest.mock("../api/jobsApi", () => ({
  uploadOrtholog: jest.fn(),
  deleteOrtholog: jest.fn(),
}));

describe("OrthologUploader (G16)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("renders expected accept attribute, sr-only input, and keyboard accessible label", () => {
    render(<OrthologUploader datasetId="ds-1" />);
    const input = document.getElementById("ortholog-file-input");
    expect(input).toHaveClass("sr-only");
    expect(input).toHaveAttribute("accept", ".gz,.blast,.tsv,.txt,.tab,.out,.m8");

    const label = screen.getByRole("button", { name: /Pilih File Ortolog BLAST/i });
    const clickSpy = jest.spyOn(input, "click").mockImplementation(() => {});
    fireEvent.keyDown(label, { key: "Enter" });
    fireEvent.keyDown(label, { key: " " });
    expect(clickSpy).toHaveBeenCalledTimes(2);
    clickSpy.mockRestore();
  });

  test("uploads BLAST file with progress, shows n_queries & n_ties, handles delete", async () => {
    const onChanged = jest.fn();
    uploadOrtholog.mockImplementation(async (_dsId, _file, onProgress) => {
      onProgress?.(60);
      return {
        filename: "Asa_Ath.blast.gz",
        n_queries: 47736,
        n_ties: 2350,
        n_rows: 2343951,
        n_invalid_lines: 0,
      };
    });
    deleteOrtholog.mockResolvedValue({ deleted: true });

    render(<OrthologUploader datasetId="ds-1" onOrthologChanged={onChanged} />);

    const input = document.getElementById("ortholog-file-input");
    const file = new File(["q\ts"], "Asa_Ath.blast.gz", { type: "application/gzip" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(uploadOrtholog).toHaveBeenCalledWith("ds-1", file, expect.any(Function));
      expect(screen.getByText("Asa_Ath.blast.gz")).toBeInTheDocument();
    });
    expect(screen.getByText(/47\.736 gen/i)).toBeInTheDocument();
    expect(screen.getByText(/2\.350 lokus setara/i)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledWith(
      expect.objectContaining({
        filename: "Asa_Ath.blast.gz",
        n_queries: 47736,
        n_ties: 2350,
      })
    );

    fireEvent.click(screen.getByRole("button", { name: /Hapus/i }));
    await waitFor(() => {
      expect(deleteOrtholog).toHaveBeenCalledWith("ds-1");
      expect(onChanged).toHaveBeenLastCalledWith(null);
    });
  });

  test("shows structured detail.message returned by server on upload failure", async () => {
    uploadOrtholog.mockRejectedValue({
      response: {
        data: {
          detail: {
            code: "NO_VALID_ROWS",
            message: "Berkas BLAST tidak memuat satu pun baris valid outfmt6 (12 kolom).",
          },
        },
      },
    });

    render(<OrthologUploader datasetId="ds-1" />);
    const input = document.getElementById("ortholog-file-input");
    fireEvent.change(input, {
      target: { files: [new File(["bad"], "bad.tsv", { type: "text/plain" })] },
    });

    await waitFor(() => {
      expect(
        screen.getByText(/Berkas BLAST tidak memuat satu pun baris valid outfmt6/i)
      ).toBeInTheDocument();
    });
  });
});
