import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React, { useState } from "react";
import { AnnotationUploader } from "./AnnotationUploader";
import { PhenotypeUploader } from "./PhenotypeUploader";
import { VcfUploader } from "./VcfUploader";
import { fetchInspection, uploadAnnotation, uploadPhenotype } from "../api/jobsApi";

jest.mock("../api/jobsApi", () => ({
  uploadPhenotype: jest.fn(),
  deletePhenotype: jest.fn(),
  fetchInspection: jest.fn(),
  uploadAnnotation: jest.fn(),
  deleteAnnotation: jest.fn(),
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
});
