import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import React from "react";
import { GoEnrichmentPanel } from "./GoEnrichmentPanel";
import { PrimerDesignPanel } from "./PrimerDesignPanel";

describe("Annotation Synthetic Banner & Heuristic Labels", () => {
  beforeAll(() => {
    jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => null);
  });

  afterAll(() => {
    jest.restoreAllMocks();
  });
  test("GoEnrichmentPanel shows simulation banner when synthetic is true", () => {
    const goData = {
      status: "completed",
      synthetic: true,
      trait: "Clove Weight",
      go_table: [
        {
          gene_id: "Gene1",
          ortholog: "AT1G00000",
          biological_process: "defense response",
          cellular_component: "nucleus",
          molecular_function: "DNA binding",
          synthetic: true,
        },
      ],
      enrichment_terms: [
        {
          go_id: "GO:0006952",
          description: "defense response",
          ontology: "BP",
          count: 1,
          gene_ratio: 1.0,
          gene_ratio_str: "1/1",
          bg_ratio: "100/27500",
          pvalue: 0.01,
          p_adjust: 0.01,
          genes: ["Gene1"],
          synthetic: true,
        },
      ],
    };

    render(<GoEnrichmentPanel goData={goData} jobId="job-1" />);
    expect(screen.getByText(/Data simulasi — bukan hasil analisis/i)).toBeInTheDocument();
  });

  test("GoEnrichmentPanel hides simulation banner for real GO annotations", () => {
    const goData = {
      status: "completed",
      synthetic: false,
      trait: "Clove Weight",
      go_table: [
        {
          gene_id: "Gene1",
          ortholog: "-",
          biological_process: "annotated GO term (GO:0006357)",
          cellular_component: "-",
          molecular_function: "-",
          source: "annotation",
          synthetic: false,
        },
      ],
      enrichment_terms: [],
    };

    render(<GoEnrichmentPanel goData={goData} jobId="job-1" />);
    expect(screen.queryByText(/Data simulasi — bukan hasil analisis/i)).not.toBeInTheDocument();
  });

  test("PrimerDesignPanel shows simulation banner when synthetic and uses heuristic primer labels", () => {
    const primersData = {
      status: "completed",
      synthetic: true,
      total_markers: 1,
      markers_with_primers: 1,
      markers: [
        {
          rank: 1,
          marker_id: "trait_1_100",
          snp: "chr1:100",
          chr: "1",
          pos: 100,
          flanking_sequence: "ACGT[C]TGCA",
          primer_pairs_count: 1,
          primer_pairs: [
            {
              left: { start: 1, len: 20, tm: 55, gc: 50, any_th: 0, three_th: 0, hairpin: 0, seq: "AAAACCCCGGGGTTTTAAAA" },
              right: { start: 180, len: 20, tm: 55, gc: 50, any_th: 0, three_th: 0, hairpin: 0, seq: "TTTTCCCCGGGGAAAATTTT" },
              product_size: 180,
            },
          ],
          primer3_output: "LEFT PRIMER ...",
          synthetic: true,
        },
      ],
    };

    const { container } = render(<PrimerDesignPanel primersData={primersData} jobId="job-1" />);
    expect(screen.getByText(/Data simulasi — bukan hasil analisis/i)).toBeInTheDocument();
    expect(screen.getByText(/desain primer heuristik/i)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/Primer3/i);
  });

  test("PrimerDesignPanel empty/no_reference state avoids Primer3 label and banner", () => {
    const { container } = render(
      <PrimerDesignPanel
        primersData={{ status: "no_reference", synthetic: false, total_markers: 0, markers: [] }}
        jobId="job-1"
      />
    );
    expect(screen.queryByText(/Data simulasi — bukan hasil analisis/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Desain Primer Heuristik Belum Tersedia/i)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/Primer3/i);
  });
});
