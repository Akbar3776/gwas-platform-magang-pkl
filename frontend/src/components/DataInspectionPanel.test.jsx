import "@testing-library/jest-dom";
import React from "react";
import { render, screen, within } from "@testing-library/react";
import { DataInspectionPanel } from "./DataInspectionPanel";

describe("DataInspectionPanel non-primary scaffold summary (W49)", () => {
  const baseReport = {
    trait: "clove-number",
    id_column: "Accession ID",
    n_vcf_samples: 233,
    n_pheno_rows: 233,
    n_matched: 223,
    missing_count: 10,
    non_numeric_count: 0,
    only_in_vcf_count: 10,
    only_in_pheno_count: 0,
    stats: { N: 223, mean: 11.2 },
    histogram: {},
    boxplot: {},
  };

  it("renders primary chromosome table and aggregate non-primary scaffold row", () => {
    render(
      <DataInspectionPanel
        jobId="job-w49"
        report={{
          ...baseReport,
          chromosome_summary: {
            threshold_bp: 2147483647,
            total_variants: 2036116,
            needs_split: true,
            chromosomes: [
              {
                chrom: "chr1",
                total_snp: 250000,
                min_pos: 100,
                max_pos: 1800000000,
                overflow_count: 0,
                invalid_pos_count: 0,
                needs_split: false,
                excluded: false,
              },
              {
                chrom: "chr7",
                total_snp: 310000,
                min_pos: 50,
                max_pos: 2161681585,
                overflow_count: 417,
                invalid_pos_count: 0,
                needs_split: true,
                excluded: false,
              },
            ],
            excluded_contigs: {
              pattern: "(?i)^(scaffold|contig|ctg|unplaced|chrun)|_(random|unplaced|alt)$",
              contigs: 10805,
              variants: 172345,
              single_marker_contigs: 498,
              examples: ["scaffold10019", "scaffold10098"],
            },
          },
        }}
      />
    );

    const table = screen.getByRole("table", { name: /Karakteristik Kromosom \(Fase 0\)/i });
    const rowHeaders = within(table).getAllByRole("rowheader");
    expect(rowHeaders.map((th) => th.textContent)).toEqual(["chr1", "chr7"]);

    const note = screen.getByRole("note");
    expect(note).toHaveTextContent(/Scaffold\/kontig non-utama:/i);
    expect(note).toHaveTextContent("10.805 kontig");
    expect(note).toHaveTextContent("172.345 SNP");
    expect(note).toHaveTextContent(/8[.,]5%/);
    expect(note).toHaveTextContent(/dibuang di normalisasi/i);
    expect(note).toHaveAttribute("title", expect.stringContaining("scaffold10019, scaffold10098"));
    expect(screen.getByText("Dibuang di normalisasi")).toBeInTheDocument();
    expect(within(note).getByText("scaffold10019, scaffold10098")).toBeInTheDocument();
  });

  it("renders legacy chromosome_summary without excluded_contigs cleanly", () => {
    render(
      <DataInspectionPanel
        report={{
          ...baseReport,
          chromosome_summary: {
            threshold_bp: 2147483647,
            needs_split: false,
            chromosomes: [
              {
                chrom: "chr1",
                total_snp: 1000,
                min_pos: 100,
                max_pos: 50000,
                overflow_count: 0,
                invalid_pos_count: 0,
                needs_split: false,
              },
            ],
          },
        }}
      />
    );

    expect(screen.getByText("Karakteristik Kromosom (Fase 0)")).toBeInTheDocument();
    expect(screen.queryByText(/Scaffold\/kontig non-utama:/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("handles legacy payload without chromosome_summary without crashing", () => {
    render(<DataInspectionPanel report={baseReport} />);
    expect(screen.getByText("Sampel VCF")).toBeInTheDocument();
    expect(screen.queryByText(/Scaffold\/kontig non-utama:/i)).not.toBeInTheDocument();
  });
});
