import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
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

  test("PrimerDesignPanel shows simulation banner when synthetic and restores Primer3 labels", () => {
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
    expect(screen.getByText(/Primer3/i)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/heuristik/i);
  });

  test("PrimerDesignPanel empty/no_reference state uses Primer3 label without simulation banner", () => {
    const { container } = render(
      <PrimerDesignPanel
        primersData={{ status: "no_reference", synthetic: false, total_markers: 0, markers: [] }}
        jobId="job-1"
      />
    );
    expect(screen.queryByText(/Data simulasi — bukan hasil analisis/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Desain Primer3 Belum Tersedia/i)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/heuristik/i);
  });

  test("GoEnrichmentPanel and PrimerDesignPanel export sanitized CSV via toCsv (A17)", () => {
    const capturedBlobs = [];
    const origBlob = globalThis.Blob;
    const origCreateObjectURL = URL.createObjectURL;
    const origRevokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = jest.fn(() => "blob:mock");
    URL.revokeObjectURL = jest.fn();
    globalThis.Blob = function MockBlob(parts, options) {
      if (options?.type?.includes("text/csv")) {
        capturedBlobs.push(parts.join(""));
      }
      return new origBlob(parts, options);
    };

    try {
      render(
        <GoEnrichmentPanel
          jobId="job-1"
          goData={{
            status: "completed",
            synthetic: false,
            trait: "Trait",
            go_table: [
              {
                gene_id: '=HYPERLINK("x")',
                ortholog: 'a"b',
                biological_process: "-0.42",
                cellular_component: "-cmd",
                molecular_function: "+evil",
                source_url: "https://example.org",
              },
            ],
            enrichment_terms: [
              {
                go_id: "GO:0000001",
                description: '=SUM(1,2) "quoted"',
                ontology: "BP",
                gene_ratio_str: "1/1",
                bg_ratio: "10/100",
                pvalue: -1.5e-8,
                p_adjust: "-0.42",
                count: 1,
                gene_id_str: '@Gene("A")',
              },
            ],
          }}
        />
      );
      fireEvent.click(screen.getByRole("button", { name: /Unduh Hasil_GO_Enrichment\.csv/i }));
      fireEvent.click(screen.getByRole("button", { name: /Unduh Tabel_GO_Kandidat\.csv/i }));

      render(
        <PrimerDesignPanel
          jobId="job-1"
          primersData={{
            status: "completed",
            synthetic: false,
            total_markers: 1,
            markers_with_primers: 1,
            markers: [
              {
                rank: 1,
                marker_id: '=CMD("m")',
                snp: "chr1:10",
                chr: "1",
                pos: 10,
                nearest_gene: "G1",
                flanking_sequence: "ACGT[C]TGCA",
                primer_pairs_count: 1,
                primer_pairs: [
                  {
                    left: { start: 1, len: 20, tm: -0.42, gc: 50, seq: '=SEQ("L")' },
                    right: { start: 100, len: 20, tm: 55, gc: 50, seq: "+RIGHT" },
                    product_size: 100,
                  },
                ],
                primer3_output: "LEFT PRIMER ...",
              },
            ],
          }}
        />
      );
      fireEvent.click(screen.getByRole("button", { name: /Unduh Tabel Primer \(\.csv\)/i }));

      expect(capturedBlobs).toHaveLength(3);
      expect(capturedBlobs[0]).toContain('"\'=SUM(1,2) ""quoted"""');
      expect(capturedBlobs[0]).toContain('"-0.42"');
      expect(capturedBlobs[0]).toContain('"\'@Gene(""A"")"');
      expect(capturedBlobs[1]).toContain('"\'=HYPERLINK(""x"")"');
      expect(capturedBlobs[1]).toContain('"a""b"');
      expect(capturedBlobs[1]).toContain('"-0.42"');
      expect(capturedBlobs[1]).toContain('"\'-cmd"');
      expect(capturedBlobs[2]).toContain('"\'=CMD(""m"")"');
      expect(capturedBlobs[2]).toContain('"-0.42"');
      expect(capturedBlobs[2]).toContain('"\'+RIGHT"');
    } finally {
      globalThis.Blob = origBlob;
      URL.createObjectURL = origCreateObjectURL;
      URL.revokeObjectURL = origRevokeObjectURL;
    }
  });
});
