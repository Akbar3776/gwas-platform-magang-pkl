import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { DataInspectionPanel } from "./DataInspectionPanel";
import { LeadSnpTable } from "./LeadSnpTable";
import { PeakComparisonTable } from "./PeakComparisonTable";

describe("Tables accessibility (A36)", () => {
  test("LeadSnpTable sortable headers use native buttons inside th with aria-sort", () => {
    render(
      <LeadSnpTable
        leadSnps={[
          { snp: "rs10", chr: 1, pos: 500, p: 1e-8, beta: 0.25 },
          { snp: "rs2", chr: 2, pos: 1200, p: 1e-7, beta: -0.1 },
        ]}
        thresholds={{ selected_p: 5e-8, selected_method: "bonferroni" }}
      />
    );

    const pBtn = screen.getByRole("button", { name: /P-value/i });
    const pTh = pBtn.closest("th");
    expect(pTh).toHaveAttribute("aria-sort", "ascending");

    const chrBtn = screen.getByRole("button", { name: /Chr/i });
    const chrTh = chrBtn.closest("th");
    expect(chrTh).toHaveAttribute("aria-sort", "none");

    fireEvent.click(chrBtn);
    expect(chrTh).toHaveAttribute("aria-sort", "descending");
    expect(pTh).toHaveAttribute("aria-sort", "none");

    fireEvent.click(chrBtn);
    expect(chrTh).toHaveAttribute("aria-sort", "ascending");
  });

  test("PeakComparisonTable filter cards use native buttons with aria-pressed", () => {
    render(
      <PeakComparisonTable
        comparisonData={{
          consistent_peaks: [{ snp: "rs1", chr: 1, pos: 100, p_single: 1e-8, p_multi: 1e-9, method: "BLINK" }],
          multi_only_peaks: [],
          single_only_peaks: [],
        }}
        multilocusData={{ models_executed: {} }}
      />
    );

    const consistentCard = screen.getByRole("button", { name: /Konsisten \(Terkonfirmasi\)/i });
    expect(consistentCard).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(consistentCard);
    expect(consistentCard).toHaveAttribute("aria-pressed", "true");
  });
});

describe("DataInspectionPanel Fase 0 chromosome summary (W22)", () => {
  const baseReport = {
    trait: "Clove_Weight",
    id_column: "IID",
    n_vcf_samples: 50,
    n_pheno_rows: 50,
    n_matched: 48,
    missing_count: 2,
    non_numeric_count: 0,
    only_in_vcf_count: 2,
    only_in_pheno_count: 0,
    stats: { N: 48, mean: 1.5 },
    histogram: {},
    boxplot: {},
  };

  test("renders chromosome table with thousands formatting, split badge, Beagle range badge, and accessible scopes", () => {
    render(
      <DataInspectionPanel
        jobId="job-w22"
        report={{
          ...baseReport,
          chromosome_summary: {
            threshold_bp: 2147483647,
            needs_split: true,
            chromosomes: [
              {
                chrom: "chr1",
                total_snp: 4500,
                min_pos: 100,
                max_pos: 1800000000,
                overflow_count: 0,
                invalid_pos_count: 0,
                needs_split: false,
              },
              {
                chrom: "chr5",
                total_snp: 3200,
                min_pos: 500,
                max_pos: 2120000000,
                overflow_count: 0,
                invalid_pos_count: 0,
                needs_split: false,
              },
              {
                chrom: "chr7",
                total_snp: 12000,
                min_pos: 100,
                max_pos: 2300000000,
                overflow_count: 12,
                invalid_pos_count: 1,
                needs_split: true,
              },
            ],
          },
        }}
      />
    );

    expect(screen.getByText("Karakteristik Kromosom (Fase 0)")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: /Karakteristik Kromosom \(Fase 0\)/i });

    const colHeaders = within(table).getAllByRole("columnheader");
    expect(colHeaders).toHaveLength(7);
    colHeaders.forEach((th) => expect(th).toHaveAttribute("scope", "col"));

    const rowHeaders = within(table).getAllByRole("rowheader");
    expect(rowHeaders.map((th) => th.textContent)).toEqual(["chr1", "chr5", "chr7"]);
    rowHeaders.forEach((th) => expect(th).toHaveAttribute("scope", "row"));

    expect(within(table).getByText(Number(2300000000).toLocaleString("id-ID"))).toBeInTheDocument();
    expect(within(table).getByText(Number(12000).toLocaleString("id-ID"))).toBeInTheDocument();
    expect(within(table).getByText("Dipecah")).toBeInTheDocument();
    expect(within(table).getByText("Rentang Beagle")).toBeInTheDocument();
    expect(screen.queryByText(/Tidak ada kromosom melebihi ambang/i)).not.toBeInTheDocument();
  });

  test("renders empty overflow status banner when no chromosomes exceed threshold", () => {
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
                min_pos: 50,
                max_pos: 150000,
                overflow_count: 0,
                invalid_pos_count: 0,
                needs_split: false,
              },
            ],
          },
        }}
      />
    );

    expect(screen.getByText(/Tidak ada kromosom melebihi ambang/i)).toBeInTheDocument();
  });

  test("handles legacy payload without chromosome_summary gracefully without crashing", () => {
    render(<DataInspectionPanel report={baseReport} />);

    expect(screen.getByText("Sampel VCF")).toBeInTheDocument();
    expect(screen.queryByText("Karakteristik Kromosom (Fase 0)")).not.toBeInTheDocument();
  });
});
