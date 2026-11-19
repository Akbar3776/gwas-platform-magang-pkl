import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { CandidateGenesTable } from "../CandidateGenesTable";

describe("CandidateGenesTable (A25)", () => {
  test("menampilkan kolom Sumber (GEMMA & multi-locus), mempertahankan SOURCE/TYPE GFF3, dan mengekspor Sumber ke CSV", () => {
    const capturedBlobs = [];
    const origBlob = globalThis.Blob;
    const origCreateObjectURL = URL.createObjectURL;
    const origRevokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = jest.fn(() => "blob:mock");
    URL.revokeObjectURL = jest.fn();
    globalThis.Blob = function MockBlob(parts, options) {
      capturedBlobs.push(parts.join(""));
      return new origBlob(parts, options);
    };

    try {
      render(
        <CandidateGenesTable
          jobId="job-a25"
          annotationData={{
            total_lead_snps: 2,
            mappings: [
              {
                snp: "rs_gemma",
                source: "GEMMA",
                gff_source: "Phytozome",
                feature_type: "gene",
                chr: "1",
                pos: 1500,
                p: 1e-8,
                category: "Genik",
                nearest_gene: "GENE_A",
                nearest_gene_name: "GeneA",
                distance_bp: 0,
                strand: "+",
                description: "Kinase",
                closest_gene: { gene_id: "GENE_A", gene_name: "GeneA", source: "Phytozome", feature_type: "gene" },
                genes_in_window: [
                  { gene_id: "GENE_A", gene_name: "GeneA", start: 1000, end: 2000, strand: "+", distance: 0 },
                  { gene_id: "GENE_WIN", gene_name: "GeneWin", start: 20000, end: 21000, strand: "+", distance: 18500 },
                ],
              },
              {
                snp: "rs_blink",
                source: "BLINK",
                gff_source: "Phytozome",
                feature_type: "gene",
                chr: "2",
                pos: 51000,
                p: 2e-8,
                category: "Near-gen",
                nearest_gene: "GENE_B",
                nearest_gene_name: "GeneB",
                distance_bp: 500,
                strand: "-",
                description: "Receptor",
                closest_gene: { gene_id: "GENE_B", gene_name: "GeneB", source: "Phytozome", feature_type: "gene" },
                genes_in_window: [
                  { gene_id: "GENE_B", gene_name: "GeneB", start: 51500, end: 52500, strand: "-", distance: 500 },
                ],
              },
            ],
          }}
        />
      );

      expect(screen.getByText("Sumber")).toBeInTheDocument();
      expect(screen.getByText("GEMMA")).toBeInTheDocument();
      expect(screen.getByText("BLINK")).toBeInTheDocument();
      expect(screen.getAllByText(/Phytozome/).length).toBeGreaterThanOrEqual(2);
      expect(screen.getByRole("button", { name: /Unduh Daftar Gen untuk GO \(2 Gen\)/i })).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: /Unduh Daftar Gen untuk GO \(2 Gen\)/i }));
      fireEvent.click(screen.getByRole("button", { name: /Unduh Tabel CSV/i }));
      expect(capturedBlobs).toHaveLength(2);
      expect(capturedBlobs[0]).toContain("GENE_A");
      expect(capturedBlobs[0]).toContain("GENE_B");
      expect(capturedBlobs[0]).not.toContain("GENE_WIN");
      expect(capturedBlobs[1]).toContain('"Sumber"');
      expect(capturedBlobs[1]).toContain('"GEMMA"');
      expect(capturedBlobs[1]).toContain('"BLINK"');
    } finally {
      globalThis.Blob = origBlob;
      URL.createObjectURL = origCreateObjectURL;
      URL.revokeObjectURL = origRevokeObjectURL;
    }
  });
});
