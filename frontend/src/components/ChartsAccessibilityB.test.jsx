import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import React from "react";
import { BoxPlotChart } from "./BoxPlotChart";
import { GoEnrichmentPanel } from "./GoEnrichmentPanel";
import { HistogramChart } from "./HistogramChart";
import { ScreePlot } from "./ScreePlot";

function mockCanvas2dContext() {
  return {
    fillRect: jest.fn(),
    strokeRect: jest.fn(),
    clearRect: jest.fn(),
    beginPath: jest.fn(),
    closePath: jest.fn(),
    moveTo: jest.fn(),
    lineTo: jest.fn(),
    arc: jest.fn(),
    stroke: jest.fn(),
    fill: jest.fn(),
    fillText: jest.fn(),
    save: jest.fn(),
    restore: jest.fn(),
    scale: jest.fn(),
    resetTransform: jest.fn(),
    setLineDash: jest.fn(),
    translate: jest.fn(),
    rotate: jest.fn(),
    createLinearGradient: jest.fn(() => ({ addColorStop: jest.fn() })),
    measureText: jest.fn(() => ({ width: 20 })),
  };
}

describe("Canvas charts accessibility batch B (A37b)", () => {
  beforeAll(() => {
    jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => mockCanvas2dContext());
  });

  afterAll(() => {
    jest.restoreAllMocks();
  });

  test("ScreePlot canvas has role='img' and aria-label with PC1/PC2 and cumulative PVE", () => {
    render(<ScreePlot pve={[0.42, 0.18, 0.1]} cumulativePve={[0.42, 0.6, 0.7]} />);

    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/PC1.*42\.0%/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/PC2.*18\.0%/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/70\.0%/i));
  });

  test("HistogramChart canvas has role='img' and aria-label with sample total and bin range", () => {
    render(<HistogramChart edges={[0, 10, 20, 30]} counts={[5, 12, 3]} />);

    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/20 sampel/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/3 bin/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/0.*30/));
  });

  test("BoxPlotChart canvas has role='img' and aria-label with median, IQR, and outlier count", () => {
    render(
      <BoxPlotChart
        data={{
          whisker_low: 2,
          q1: 10,
          median: 15.5,
          q3: 22,
          whisker_high: 30,
          outliers: [{ iid: "S9", value: 45 }],
        }}
      />
    );

    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/median 15\.50/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/10\.00.*22\.00/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/1 outlier/i));
  });

  test("GoEnrichmentPanel canvas has role='img' and aria-label with domain, term count, and top term", () => {
    render(
      <GoEnrichmentPanel
        goData={{
          trait: "Grain Yield",
          enrichment_terms: [
            {
              go_id: "GO:0006357",
              description: "regulation of transcription",
              ontology: "BP",
              count: 8,
              gene_ratio: 0.4,
              p_adjust: 0.0012,
            },
          ],
        }}
      />
    );

    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/BP/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/1 istilah/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/regulation of transcription/i));
  });
});
