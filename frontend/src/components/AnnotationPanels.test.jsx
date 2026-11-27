import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { GoEnrichmentPanel } from "./GoEnrichmentPanel";
import { PrimerDesignPanel } from "./PrimerDesignPanel";
import { downloadJobFileByName } from "../utils/jobFiles";

jest.mock("../utils/jobFiles", () => ({
  downloadJobFileByName: jest.fn(() => Promise.resolve()),
}));

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

  test("GoEnrichmentPanel shows upload GO mapping guidance when unavailable even with empty table (A58)", () => {
    render(
      <GoEnrichmentPanel
        goData={{ status: "unavailable", synthetic: false, go_table: [], enrichment_terms: [] }}
        jobId="job-1"
      />
    );
    expect(screen.getByText(/file pemetaan GO/i)).toBeInTheDocument();
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

  test("PrimerDesignPanel renders dynamic flank size, REF/ALT, nearby variants, and candidate region downloads (W23)", async () => {
    downloadJobFileByName.mockClear();

    render(
      <PrimerDesignPanel
        jobId="job-1"
        primersData={{
          status: "completed",
          synthetic: false,
          flank_bp: 150,
          regions_status: "completed",
          candidate_regions: {
            csv: "candidate_regions.csv",
            bed: "candidate_regions.bed",
            fasta: "candidate_regions.fa",
            n_regions: 2,
            written_regions: 2,
            total_bp: 400002,
            window_bp: 200000,
          },
          total_markers: 1,
          markers_with_primers: 1,
          markers: [
            {
              rank: 1,
              marker_id: "trait_7_5500",
              snp: "SNP_74098",
              chr: "chr7",
              pos: 5500,
              ref: "T",
              alt: "C",
              nearby_variants_count: 1,
              nearby_variants: [{ pos: 5540, ref: "A", alt: "G", offset: 40 }],
              flank_bp: 150,
              sequence_length: 301,
              flanking_sequence: "ACGT[T/C]TGCA",
              primer_pairs_count: 1,
              primer_pairs: [
                {
                  left: { start: 1, len: 20, tm: 55, gc: 50, any_th: 0, three_th: 0, hairpin: 0, seq: "AAAACCCCGGGGTTTTAAAA" },
                  right: { start: 280, len: 20, tm: 55, gc: 50, any_th: 0, three_th: 0, hairpin: 0, seq: "TTTTCCCCGGGGAAAATTTT" },
                  product_size: 280,
                },
              ],
              primer3_output: "LEFT PRIMER ...",
            },
          ],
        }}
      />
    );

    expect(screen.getByText(/301 bp \(±150 bp\)/i)).toBeInTheDocument();
    expect(screen.getByText(/REF\/ALT: T\/C/i)).toBeInTheDocument();
    expect(screen.getByText(/Varian tetangga: 1/i)).toBeInTheDocument();
    expect(screen.getByText(/Region Kandidat \(Fase 5\.4\)/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /candidate_regions\.bed/i }));
    fireEvent.click(screen.getByRole("button", { name: /candidate_regions\.fa/i }));

    await waitFor(() => {
      expect(downloadJobFileByName).toHaveBeenCalledWith("job-1", "candidate_regions.bed", "annotation");
      expect(downloadJobFileByName).toHaveBeenCalledWith("job-1", "candidate_regions.fa", "annotation");
    });
  });

  test("PrimerDesignPanel KASP sub-tab renders golden SNP_74098 table, accessible FAM/HEX tail labels, QC badge, and 3 exports (W43)", async () => {
    downloadJobFileByName.mockClear();

    render(
      <PrimerDesignPanel
        jobId="job-1"
        primersData={{
          status: "completed",
          synthetic: false,
          flank_bp: 100,
          total_markers: 1,
          markers_with_primers: 1,
          markers: [
            {
              rank: 1,
              marker_id: "trait_7_5500",
              snp: "SNP_74098",
              chr: "chr7",
              pos: 5500,
              flanking_sequence: "ACGT[T/C]TGCA",
              primer_pairs_count: 0,
              primer_pairs: [],
            },
          ],
          kasp: {
            status: "completed",
            total: 1,
            designed: 1,
            failed: 0,
            success_rate: 100.0,
            files: {
              input_tsv: "kasp_input.tsv",
              primers_tsv: "kasp_primers.tsv",
              primers_no_tail_fa: "kasp_primers_no_tail.fa",
            },
            markers: [
              {
                snp_id: "SNP_74098",
                allele_x: "T",
                allele_y: "C",
                primer_x: "GAAGGTGACCAAGTTCATGCTAATTGGGATGATTGTCTCAATAGGAAA",
                primer_y: "GAAGGTCGGAGTCAACGGATTAATTGGGATGATTGTCTCAATAGGAAG",
                primer_c: "AAATTCTGTATCATCCGTTCTGGACTTG",
                direction: "reverse",
                status: "OK",
                oligo_qc: {
                  analyzed: true,
                  warn: true,
                  warnings: ["Primer_X hairpin Tm 46.0C"],
                },
              },
            ],
          },
        }}
      />
    );

    fireEvent.click(screen.getByRole("tab", { name: /KASP/i }));

    const table = screen.getByRole("table", { name: /Desain Primer KASP/i });
    const headers = within(table).getAllByRole("columnheader");
    expect(headers).toHaveLength(8);
    headers.forEach((th) => expect(th).toHaveAttribute("scope", "col"));

    expect(within(table).getByText("SNP_74098")).toBeInTheDocument();
    expect(within(table).getByText("T / C")).toBeInTheDocument();
    expect(within(table).getByText("reverse")).toBeInTheDocument();
    expect(within(table).getByText("OK")).toBeInTheDocument();
    expect(within(table).getByText("FAM")).toBeInTheDocument();
    expect(within(table).getByText("HEX")).toBeInTheDocument();
    expect(within(table).getByText("AAATTCTGTATCATCCGTTCTGGACTTG")).toBeInTheDocument();
    expect(within(table).getByText("Peringatan")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /kasp_input\.tsv/i }));
    fireEvent.click(screen.getByRole("button", { name: /kasp_primers\.tsv/i }));
    fireEvent.click(screen.getByRole("button", { name: /kasp_primers_no_tail\.fa/i }));

    await waitFor(() => {
      expect(downloadJobFileByName).toHaveBeenCalledWith("job-1", "kasp_input.tsv", "annotation");
      expect(downloadJobFileByName).toHaveBeenCalledWith("job-1", "kasp_primers.tsv", "annotation");
      expect(downloadJobFileByName).toHaveBeenCalledWith("job-1", "kasp_primers_no_tail.fa", "annotation");
    });
  });

  test("PrimerDesignPanel KASP sub-tab handles legacy payload without kasp and skipped state cleanly (W43)", () => {
    const { rerender } = render(
      <PrimerDesignPanel
        jobId="job-1"
        primersData={{ status: "completed", total_markers: 0, markers: [] }}
      />
    );

    fireEvent.click(screen.getByRole("tab", { name: /^KASP$/i }));
    expect(screen.getByText(/Belum tersedia/i)).toBeInTheDocument();

    rerender(
      <PrimerDesignPanel
        jobId="job-1"
        primersData={{
          status: "no_reference",
          total_markers: 0,
          markers: [],
          kasp: { status: "skipped", reason: "FASTA referensi tidak tersedia" },
        }}
      />
    );
    expect(screen.getByText(/Desain Primer KASP Dilewati/i)).toBeInTheDocument();
    expect(screen.getByText(/FASTA referensi tidak tersedia/i)).toBeInTheDocument();
  });

  test("GoEnrichmentPanel renders unknown GO column and go_stats summary bar safely (G10)", () => {
    const { rerender } = render(
      <GoEnrichmentPanel
        jobId="job-1"
        goData={{
          status: "completed",
          synthetic: false,
          trait: "Trait",
          go_stats: {
            obo: {
              available: true,
              version: "releases/2026-07-26",
              n_rows_obsolete_dropped: 3634,
            },
            match: { pct_gff_features_matched: 49.17 },
            background: { n_universe: 25346 },
          },
          go_table: [
            {
              gene_id: "GeneUnk",
              ortholog: "-",
              biological_process: "-",
              cellular_component: "-",
              molecular_function: "-",
              unknown_go: "GO:7777777",
              unknown_ids: ["GO:7777777"],
              source: "mapping",
            },
          ],
          enrichment_terms: [],
        }}
      />
    );

    expect(screen.getByRole("columnheader", { name: /Domain tidak diketahui/i })).toBeInTheDocument();
    expect(screen.getByText("GO:7777777")).toBeInTheDocument();
    expect(
      screen.getByText(/Ontologi GO releases\/2026-07-26/i)
    ).toHaveTextContent(/background 25\.346 gen.*obsolete dibuang 3\.634/i);

    // Payload lama tanpa go_stats -> tidak crash dan baris info tidak dirender
    rerender(
      <GoEnrichmentPanel
        jobId="job-legacy"
        goData={{
          status: "completed",
          synthetic: false,
          go_table: [
            {
              gene_id: "GeneLegacy",
              ortholog: "-",
              biological_process: "GO:0006357",
              cellular_component: "-",
              molecular_function: "-",
            },
          ],
          enrichment_terms: [],
        }}
      />
    );
    expect(screen.getByText("GeneLegacy")).toBeInTheDocument();
    expect(screen.queryByText(/Ontologi GO/i)).not.toBeInTheDocument();
  });

  test("GoEnrichmentPanel renders BLAST ortholog column, tooltip detail, and handles missing detail (G16)", () => {
    render(
      <GoEnrichmentPanel
        jobId="job-orth"
        goData={{
          status: "completed",
          synthetic: false,
          go_table: [
            {
              gene_id: "Asa7G00205",
              ortholog: "AT3G05510",
              ortholog_detail: {
                subject_id: "AT3G05510.2",
                pident: 29.07,
                evalue: 2e-21,
                bitscore: 99.4,
                n_loci_at_best: 2,
              },
              biological_process: "GO:0006357",
              cellular_component: "-",
              molecular_function: "-",
              source: "mapping",
            },
            {
              gene_id: "AsaNoOrth",
              ortholog: "-",
              ortholog_detail: null,
              biological_process: "-",
              cellular_component: "-",
              molecular_function: "-",
              source: "unavailable",
            },
          ],
          enrichment_terms: [],
        }}
      />
    );

    expect(
      screen.getByRole("columnheader", { name: /Ortolog \/ Homolog \(BLAST best hit\)/i })
    ).toBeInTheDocument();

    const orthCell = screen.getByText("AT3G05510").closest("td");
    expect(orthCell).toHaveAttribute(
      "title",
      expect.stringMatching(/identitas 29,07% · e-value 2e-21 · bitscore 99,4 \(\+1 lokus setara\)/i)
    );
    expect(orthCell.querySelector("a")).toBeNull();
  });

  test("GoEnrichmentPanel renders accessible warning banner when stale=true and hides when not stale (G22)", () => {
    const basePayload = {
      status: "completed",
      synthetic: false,
      go_table: [
        {
          gene_id: "Asa7G00205",
          ortholog: "AT3G05510",
          biological_process: "GO:0006357",
          cellular_component: "-",
          molecular_function: "-",
          source: "mapping",
        },
      ],
      enrichment_terms: [],
    };

    const { rerender } = render(
      <GoEnrichmentPanel
        jobId="job-stale-gff"
        goData={{ ...basePayload, stale: true, stale_reason: "gff_unavailable" }}
      />
    );

    const gffBanner = screen.getByRole("status");
    expect(gffBanner).toHaveTextContent(/hasil sebelumnya/i);
    expect(gffBanner).toHaveTextContent(/GFF3/i);
    expect(gffBanner.querySelector('[aria-hidden="true"]')).not.toBeNull();

    rerender(
      <GoEnrichmentPanel
        jobId="job-stale-err"
        goData={{ ...basePayload, stale: true, stale_reason: "recompute_failed" }}
      />
    );
    const errBanner = screen.getByRole("status");
    expect(errBanner).toHaveTextContent(/menghitung ulang/i);

    rerender(<GoEnrichmentPanel jobId="job-fresh" goData={{ ...basePayload, stale: false }} />);
    expect(screen.queryByRole("status")).toBeNull();

    rerender(<GoEnrichmentPanel jobId="job-clean" goData={basePayload} />);
    expect(screen.queryByRole("status")).toBeNull();
  });
});
