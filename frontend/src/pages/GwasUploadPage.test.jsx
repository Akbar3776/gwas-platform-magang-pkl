import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { fetchCapabilities, fetchReferencePanels } from "../api/jobsApi";
import { GwasUploadPage } from "./GwasUploadPage";

jest.mock("../api/jobsApi", () => ({
  fetchCapabilities: jest.fn(),
  fetchReferencePanels: jest.fn(),
  triggerFullPipeline: jest.fn(),
  uploadPhenotype: jest.fn(),
  deletePhenotype: jest.fn(),
  fetchInspection: jest.fn(),
  uploadAnnotation: jest.fn(),
  deleteAnnotation: jest.fn(),
  uploadGoAnnotation: jest.fn(),
  deleteGoAnnotation: jest.fn(),
  uploadOrtholog: jest.fn(),
  deleteOrtholog: jest.fn(),
  deleteReference: jest.fn(),
  fetchUploadedDatasets: jest.fn().mockResolvedValue([]),
  fetchUserJobs: jest.fn().mockResolvedValue([]),
}));

jest.mock("../components/VcfUploader", () => ({
  VcfUploader: ({ onDatasetReady }) => (
    <button type="button" onClick={() => onDatasetReady("ds-cap-1", { original_filename: "demo.vcf" })}>
      Mock Ready Dataset
    </button>
  ),
}));

describe("GwasUploadPage capabilities integration (A24)", () => {
  test("disables unavailable imputation and multilocus options with explanation", async () => {
    fetchReferencePanels.mockResolvedValue([]);
    fetchCapabilities.mockResolvedValue({
      plink2: true,
      plink: true,
      gemma: true,
      bcftools: true,
      beagle: false,
      rscript: false,
      gapit: false,
      multilocus_available: false,
      mode: "docker",
    });

    const { container } = render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <GwasUploadPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(fetchCapabilities).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByRole("button", { name: /Mock Ready Dataset/i }));

    await waitFor(() => {
      const refOption = container.querySelector('select[name="imputation_mode"] option[value="reference"]');
      const refFreeOption = container.querySelector('select[name="imputation_mode"] option[value="reference_free"]');
      expect(refOption).toBeDisabled();
      expect(refFreeOption).toBeDisabled();
    });

    const multiCheckbox = container.querySelector('input[name="enable_multilocus"]');
    expect(multiCheckbox).toBeDisabled();
    expect(container.querySelector('select[name="model"] option[value="farmcpu"]')).toBeDisabled();
    expect(screen.getByText(/Imputasi Beagle tidak tersedia/i)).toBeInTheDocument();
    expect(screen.getByText(/Multi-Locus GWAS tidak tersedia/i)).toBeInTheDocument();
    expect(screen.getByText(/FASTA Referensi Genom \(Opsional\)/i)).toBeInTheDocument();
    expect(screen.getByText(/File Pemetaan GO \(Opsional\)/i)).toBeInTheDocument();
    expect(screen.getByLabelText("MAF")).toHaveAttribute("name", "maf");
    expect(screen.getByLabelText("MIND")).toHaveAttribute("name", "mind");
    expect(screen.getByLabelText("GENO")).toHaveAttribute("name", "geno");
  });

  test("defaults QC MIND field to 0.1", async () => {
    fetchReferencePanels.mockResolvedValue([]);
    fetchCapabilities.mockResolvedValue({
      plink2: true,
      plink: true,
      gemma: true,
      bcftools: true,
      beagle: false,
      rscript: false,
      gapit: false,
      multilocus_available: false,
      mode: "docker",
    });

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <GwasUploadPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(fetchCapabilities).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole("button", { name: /Mock Ready Dataset/i }));

    expect(screen.getByLabelText("MIND")).toHaveValue(0.1);
  });
});
