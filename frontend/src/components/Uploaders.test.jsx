import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React, { useState } from "react";
import { AnnotationUploader } from "./AnnotationUploader";
import { GoMappingUploader } from "./GoMappingUploader";
import { PhenotypeUploader } from "./PhenotypeUploader";
import { ReferenceUploader } from "./ReferenceUploader";
import { VcfUploader } from "./VcfUploader";
import { httpClient } from "../api/httpClient";
import {
  deleteReference,
  fetchInspection,
  uploadAnnotation,
  uploadGoAnnotation,
  uploadPhenotype,
} from "../api/jobsApi";

jest.mock("../api/httpClient", () => ({
  httpClient: {
    post: jest.fn(),
    get: jest.fn(),
    delete: jest.fn(),
  },
}));

jest.mock("../api/jobsApi", () => ({
  uploadPhenotype: jest.fn(),
  deletePhenotype: jest.fn(),
  fetchInspection: jest.fn(),
  uploadAnnotation: jest.fn(),
  deleteAnnotation: jest.fn(),
  uploadGoAnnotation: jest.fn(),
  deleteGoAnnotation: jest.fn(),
  deleteReference: jest.fn(),
}));

describe("Uploaders persist uploaded state across parent re-renders (A11)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("PhenotypeUploader keeps uploaded filename when parent re-renders after inspection", async () => {
    uploadPhenotype.mockResolvedValue({
      filename: "pheno.txt",
      columns: ["IID", "trait1"],
      numeric_columns: ["trait1"],
      preview: [],
    });
    fetchInspection.mockResolvedValue({
      matched_count: 20,
      blocking_errors: [],
    });

    function ParentHarness() {
      const [trait, setTrait] = useState("");
      const [meta] = useState({});
      return (
        <div>
          <span data-testid="selected-trait">{trait}</span>
          <PhenotypeUploader
            datasetId="ds-1"
            initialPhenotypeFilename={meta?.phenotype_filename}
            initialColumns={meta?.phenotype_columns || []}
            initialNumericTraits={meta?.numeric_traits || []}
            onInspectionReady={(_report, t) => setTrait(t)}
          />
        </div>
      );
    }

    const { container, rerender } = render(<ParentHarness />);
    const input = container.querySelector("#pheno-file-input");
    const file = new File(["IID\ttrait1\nS1\t1.5\n"], "pheno.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText("pheno.txt")).toBeInTheDocument();
      expect(screen.getByTestId("selected-trait")).toHaveTextContent("trait1");
    });

    // Force an additional parent re-render
    rerender(<ParentHarness />);
    expect(screen.getByText("pheno.txt")).toBeInTheDocument();
  });

  test("AnnotationUploader keeps uploaded filename and hasAnnotation=true when parent re-renders", async () => {
    uploadAnnotation.mockResolvedValue({
      filename: "genes.gff3",
      n_genes: 42,
      seqids: ["chr1", "chr2"],
    });

    function ParentHarness() {
      const [hasAnnotation, setHasAnnotation] = useState(false);
      const [ticks, setTicks] = useState(0);
      return (
        <div>
          <span data-testid="has-anno">{String(hasAnnotation)}</span>
          <button type="button" onClick={() => setTicks((t) => t + 1)}>
            Re-render ({ticks})
          </button>
          <AnnotationUploader
            datasetId="ds-1"
            initialAnnotationFilename={null}
            onAnnotationChanged={(meta) => setHasAnnotation(Boolean(meta))}
          />
        </div>
      );
    }

    const { container } = render(<ParentHarness />);
    const input = container.querySelector("#gff-file-input");
    const file = new File(["##gff-version 3\n"], "genes.gff3", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText("genes.gff3")).toBeInTheDocument();
      expect(screen.getByTestId("has-anno")).toHaveTextContent("true");
    });

    fireEvent.click(screen.getByRole("button", { name: /Re-render/i }));
    expect(screen.getByText("genes.gff3")).toBeInTheDocument();
    expect(screen.getByTestId("has-anno")).toHaveTextContent("true");
  });

  test("all three file inputs are keyboard-focusable via sr-only and labeled (A13)", () => {
    const { container } = render(
      <div>
        <VcfUploader onDatasetReady={jest.fn()} />
        <PhenotypeUploader datasetId="ds-1" />
        <AnnotationUploader datasetId="ds-1" />
      </div>
    );

    const vcfInput = screen.getByLabelText(/file VCF/i);
    const phenoInput = screen.getByLabelText(/File Fenotipe/i);
    const gffInput = screen.getByLabelText(/File GFF3/i);

    for (const input of [vcfInput, phenoInput, gffInput]) {
      expect(input).toHaveClass("sr-only");
      expect(window.getComputedStyle(input).display).not.toBe("none");
      input.focus();
      expect(document.activeElement).toBe(input);
    }

    const vcfTrigger = container.querySelector('[role="button"]');
    expect(vcfTrigger).toHaveAttribute("tabindex", "0");
    const clickSpy = jest.spyOn(vcfInput, "click");
    fireEvent.keyDown(vcfTrigger, { key: "Enter" });
    fireEvent.keyDown(vcfTrigger, { key: " " });
    expect(clickSpy).toHaveBeenCalledTimes(2);
    clickSpy.mockRestore();
  });

  test("ReferenceUploader uploads FASTA via useChunkUpload, stays accessible (A13), and shows contig count (A58)", async () => {
    httpClient.post.mockImplementation((url) => {
      if (url === "/upload/ds-1/reference/init") {
        return Promise.resolve({ data: { file_id: "ds-1", upload_status: "uploading" } });
      }
      if (url === "/upload/ds-1/reference/chunk") {
        return Promise.resolve({ data: { is_complete: true } });
      }
      if (url === "/upload/ds-1/reference/finalize") {
        return Promise.resolve({ data: { upload_status: "validating" } });
      }
      return Promise.resolve({ data: {} });
    });
    httpClient.get.mockResolvedValue({
      data: {
        file_id: "ds-1",
        upload_status: "uploaded",
        filename: "genome.fa",
        n_contigs: 8,
        contigs: [{ name: "chr1", length: 1000 }],
      },
    });

    render(<ReferenceUploader datasetId="ds-1" />);
    const refInput = screen.getByLabelText(/FASTA Referensi/i);
    expect(refInput).toHaveClass("sr-only");
    refInput.focus();
    expect(document.activeElement).toBe(refInput);

    const file = new File([">chr1\nACGTACGT\n"], "genome.fa", { type: "text/plain" });
    fireEvent.change(refInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText("genome.fa")).toBeInTheDocument();
      expect(screen.getByText(/8 kromosom \/ contig terindeks/i)).toBeInTheDocument();
    });
  });

  test("GoMappingUploader uploads TSV/GAF, stays accessible (A13), and shows gene count (A58)", async () => {
    uploadGoAnnotation.mockResolvedValue({
      filename: "go_map.tsv",
      format: "tsv",
      n_genes: 42,
      n_mappings: 90,
    });

    render(<GoMappingUploader datasetId="ds-1" />);
    const goInput = screen.getByLabelText(/File Pemetaan GO/i);
    expect(goInput).toHaveClass("sr-only");
    goInput.focus();
    expect(document.activeElement).toBe(goInput);

    const file = new File(["G1\tGO:0006357\n"], "go_map.tsv", { type: "text/plain" });
    fireEvent.change(goInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText("go_map.tsv")).toBeInTheDocument();
      expect(screen.getByText(/42 gen/i)).toBeInTheDocument();
    });
  });

  test("Uploaders format 422 validation array detail safely via formatApiError (A35b)", async () => {
    const err422 = {
      response: {
        status: 422,
        data: { detail: [{ loc: ["body", "file"], msg: "Tipe file ditolak" }] },
      },
    };
    uploadAnnotation.mockRejectedValueOnce(err422);
    uploadGoAnnotation.mockRejectedValueOnce(err422);
    deleteReference.mockRejectedValueOnce(err422);

    const { container } = render(
      <div>
        <AnnotationUploader datasetId="ds-1" />
        <GoMappingUploader datasetId="ds-1" />
        <ReferenceUploader datasetId="ds-1" initialReferenceFilename="ref.fa" />
      </div>
    );

    const gffInput = container.querySelector("#gff-file-input");
    fireEvent.change(gffInput, {
      target: { files: [new File(["bad"], "bad.gff3", { type: "text/plain" })] },
    });

    const goInput = screen.getByLabelText(/File Pemetaan GO/i);
    fireEvent.change(goInput, {
      target: { files: [new File(["bad"], "bad.gaf", { type: "text/plain" })] },
    });

    fireEvent.click(screen.getByRole("button", { name: /^Hapus$/i }));

    await waitFor(() => {
      const matches = screen.getAllByText(/file: Tipe file ditolak/i);
      expect(matches).toHaveLength(3);
    });
  });

  test("GoMappingUploader accepts generic .gz, shows skipped-lines warning when n_invalid_lines > 0, and surfaces detail.message (G05)", async () => {
    uploadGoAnnotation.mockResolvedValueOnce({
      filename: "Asa.GO.gz",
      format: "tsv",
      compressed: true,
      decompressed_bytes: 1812624,
      n_genes: 28317,
      n_mappings: 75526,
      n_invalid_lines: 3,
    });

    const { rerender } = render(<GoMappingUploader datasetId="ds-1" />);
    const goInput = screen.getByLabelText(/File Pemetaan GO/i);
    expect(goInput).toHaveAttribute("accept", expect.stringContaining(".gz"));
    expect(goInput.getAttribute("accept").split(",")).toContain(".gz");

    const gzFile = new File(["gz"], "Asa.GO.gz", { type: "application/gzip" });
    fireEvent.change(goInput, { target: { files: [gzFile] } });

    await waitFor(() => {
      expect(uploadGoAnnotation).toHaveBeenCalledWith("ds-1", gzFile, expect.any(Function));
      expect(screen.getByText("Asa.GO.gz")).toBeInTheDocument();
      expect(screen.getByText(/3 baris dilewati karena format tidak valid/i)).toBeInTheDocument();
    });

    uploadGoAnnotation.mockResolvedValueOnce({
      filename: "clean.GO.gz",
      format: "tsv",
      compressed: true,
      decompressed_bytes: 500,
      n_genes: 10,
      n_mappings: 12,
      n_invalid_lines: 0,
    });
    rerender(<GoMappingUploader datasetId="ds-1" />);
    fireEvent.change(goInput, { target: { files: [new File(["ok"], "clean.GO.gz", { type: "application/gzip" })] } });
    await waitFor(() => {
      expect(screen.getByText("clean.GO.gz")).toBeInTheDocument();
    });
    expect(screen.queryByText(/baris dilewati karena format tidak valid/i)).not.toBeInTheDocument();

    uploadGoAnnotation.mockRejectedValueOnce({
      response: { data: { detail: { code: "GZIP_INVALID", message: "Berkas .gz tidak valid." } } },
    });
    fireEvent.change(goInput, { target: { files: [new File(["bad"], "broken.gz", { type: "application/gzip" })] } });
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Berkas .gz tidak valid.");
    });
  });
});
