import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import React from "react";
import { KinshipHeatmap } from "./KinshipHeatmap";
import { LdDecayPlot } from "./LdDecayPlot";
import { PcaScatterPlot } from "./PcaScatterPlot";
import QQPlot from "./QQPlot";

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
    measureText: jest.fn(() => ({ width: 20 })),
  };
}

describe("Canvas charts accessibility batch A (A37a)", () => {
  beforeAll(() => {
    jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => mockCanvas2dContext());
  });

  afterAll(() => {
    jest.restoreAllMocks();
  });
  test("QQPlot canvas has role='img' and aria-label with SNP count, lambda, and peak -log10(p)", () => {
    render(
      <QQPlot
        data={{
          log10_p: [0.1, 0.3, 0.5, 1.2, 6.4],
          lambda_gc: 1.04,
        }}
      />
    );

    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/5/));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/1\.04/));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/6\.40/));
  });

  test("PcaScatterPlot canvas exposes role='img' and aria-label with PC1/PC2 PVE and sample count", () => {
    render(
      <PcaScatterPlot
        data={{
          pve: [0.345, 0.128, 0.05],
          samples: [
            { iid: "S1", pcs: [1.1, 0.2], cluster: 0 },
            { iid: "S2", pcs: [-0.8, 0.5], cluster: 1 },
            { iid: "S3", pcs: [0.3, -0.4], cluster: 0 },
          ],
        }}
        pcX={1}
        pcY={2}
      />
    );

    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/PC1.*34\.5%/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/PC2.*12\.8%/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/3 sampel/i));
  });

  test("KinshipHeatmap canvas exposes role='img' and aria-label with matrix dimensions", () => {
    render(
      <KinshipHeatmap
        data={{
          order: ["S1", "S2", "S3"],
          matrix: [
            [1.0, 0.12, -0.04],
            [0.12, 0.98, 0.25],
            [-0.04, 0.25, 1.01],
          ],
        }}
      />
    );

    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/3\s*x\s*3/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/kinship/i));
  });

  test("LdDecayPlot canvas exposes role='img' and aria-label with bins, cutoff r2, and chosen kb", () => {
    render(
      <LdDecayPlot
        data={{
          bins: [
            { bin_kb: 10, mean_r2: 0.65, n_pairs: 20 },
            { bin_kb: 100, mean_r2: 0.22, n_pairs: 45 },
          ],
          cutoff_r2: 0.2,
          chosen_kb: 120,
        }}
      />
    );

    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/2 bin/i));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/0\.2/));
    expect(img).toHaveAttribute("aria-label", expect.stringMatching(/120 kb/i));
  });
});
