import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { fetchInspection, fetchReferencePanels, triggerFullPipeline, uploadPhenotype } from "../api/jobsApi";
import { GwasUploadPage } from "../pages/GwasUploadPage";
import { ErrorBoundary } from "./ErrorBoundary";

jest.mock("../api/jobsApi", () => ({
  fetchReferencePanels: jest.fn(),
  triggerFullPipeline: jest.fn(),
  uploadPhenotype: jest.fn(),
  deletePhenotype: jest.fn(),
  fetchInspection: jest.fn(),
  uploadAnnotation: jest.fn(),
  deleteAnnotation: jest.fn(),
  fetchUploadedDatasets: jest.fn().mockResolvedValue([]),
  fetchUserJobs: jest.fn().mockResolvedValue([]),
}));

jest.mock("./VcfUploader", () => ({
  VcfUploader: ({ onDatasetReady }) => (
    <button type="button" onClick={() => onDatasetReady("ds-repro", { original_filename: "demo.vcf" })}>
      Mock Ready Dataset
    </button>
  ),
}));

function CrashChild() {
  throw new Error("Simulated render crash");
}

describe("ErrorBoundary & 422 Validation Handling (A15)", () => {
  test("renders fallback message and reload button when child throws", () => {
    const errorSpy = jest.spyOn(console, "error").mockImplementation(() => {});
    try {
      render(
        <ErrorBoundary>
          <CrashChild />
        </ErrorBoundary>
      );
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(screen.getByText(/Terjadi kesalahan/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Muat Ulang/i })).toBeInTheDocument();
    } finally {
      errorSpy.mockRestore();
    }
  });

  test("422 array error after beagle 8g -> none -> submit shows message without crashing app", async () => {
    fetchReferencePanels.mockResolvedValue([]);
    uploadPhenotype.mockResolvedValue({
      filename: "pheno.txt",
      columns: ["IID", "trait1"],
      numeric_columns: ["trait1"],
      preview: [],
    });
    fetchInspection.mockResolvedValue({ matched_count: 10, blocking_errors: [] });
    triggerFullPipeline.mockRejectedValue({
      response: {
        status: 422,
        data: {
          detail: [
            {
              type: "value_error",
              loc: ["body", "imputation", "beagle_max_memory"],
              msg: "BEAGLE_MAX_MEMORY harus berbentuk -Xmx8g",
            },
          ],
        },
      },
    });

    const { container } = render(
      <ErrorBoundary>
        <MemoryRouter>
          <GwasUploadPage />
        </MemoryRouter>
      </ErrorBoundary>
    );

    fireEvent.click(screen.getByRole("button", { name: /Mock Ready Dataset/i }));
    const phenoInput = container.querySelector("#pheno-file-input");
    fireEvent.change(phenoInput, {
      target: { files: [new File(["IID\ttrait1\nS1\t1.0\n"], "pheno.txt", { type: "text/plain" })] },
    });

    await waitFor(() => expect(screen.getByText("pheno.txt")).toBeInTheDocument());

    const impSelect = container.querySelector('select[name="imputation_mode"]');
    fireEvent.change(impSelect, { target: { name: "imputation_mode", value: "reference_free" } });
    const memInput = container.querySelector('input[name="beagle_max_memory"]');
    fireEvent.change(memInput, { target: { name: "beagle_max_memory", value: "8g" } });
    fireEvent.change(impSelect, { target: { name: "imputation_mode", value: "none" } });

    const submitBtn = screen.getByRole("button", { name: /Mulai Analisis Pipeline GWAS Lengkap/i });
    await waitFor(() => expect(submitBtn).toBeEnabled());

    const errorSpy = jest.spyOn(console, "error").mockImplementation(() => {});
    try {
      fireEvent.click(submitBtn);
      await waitFor(() => {
        expect(triggerFullPipeline).toHaveBeenCalledTimes(1);
      });
      await waitFor(() => {
        const alertEl = screen.getByRole("alert");
        expect(alertEl).toHaveTextContent(/beagle_max_memory/i);
        expect(screen.queryByRole("button", { name: /Muat Ulang/i })).not.toBeInTheDocument();
      });
    } finally {
      errorSpy.mockRestore();
    }
  });
});
