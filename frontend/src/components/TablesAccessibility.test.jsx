import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
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
