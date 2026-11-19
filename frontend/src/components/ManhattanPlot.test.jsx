import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import * as manhattanUtils from "../utils/manhattan";
import ManhattanPlot from "./ManhattanPlot";

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
    setLineDash: jest.fn(),
    translate: jest.fn(),
    rotate: jest.fn(),
    measureText: jest.fn(() => ({ width: 20 })),
  };
}

function makeSyntheticGwasData(n) {
  const chr = new Array(n);
  const bp = new Array(n);
  const snp = new Array(n);
  const log10_p = new Array(n);
  for (let i = 0; i < n; i += 1) {
    const c = (i % 12) + 1;
    chr[i] = c;
    bp[i] = ((i / 12) | 0) * 100 + 1;
    snp[i] = `rs${i}`;
    log10_p[i] = i % 50000 === 0 ? 8.5 : (i % 100) * 0.04;
  }
  return { chr, bp, snp, log10_p };
}

describe("ManhattanPlot split memoization and 1M-SNP benchmark (A38)", () => {
  beforeAll(() => {
    jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => mockCanvas2dContext());
  });

  afterAll(() => {
    jest.restoreAllMocks();
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  test("does not recompute buildManhattanLayout on threshold slider or zoom updates", () => {
    const layoutSpy = jest.spyOn(manhattanUtils, "buildManhattanLayout");
    const sampleData = makeSyntheticGwasData(240);

    const { container } = render(<ManhattanPlot data={sampleData} />);
    expect(layoutSpy).toHaveBeenCalledTimes(1);

    const slider = container.querySelector('input[type="range"]');
    for (const val of ["5.5", "6.2", "7.0", "7.8", "8.4"]) {
      fireEvent.change(slider, { target: { value: val } });
    }
    fireEvent.click(screen.getByRole("button", { name: /1\.5x/i }));
    fireEvent.click(screen.getByRole("button", { name: /2x/i }));

    // Layout dasar harus hanya dihitung ulang jika filteredData berubah
    expect(layoutSpy).toHaveBeenCalledTimes(1);

    const chrSelect = container.querySelector("select");
    fireEvent.change(chrSelect, { target: { value: "1" } });
    expect(layoutSpy).toHaveBeenCalledTimes(2);
    layoutSpy.mockRestore();
  });

  test("benchmarks 1,000,000 SNPs across 10 threshold updates and 2 zoom changes", () => {
    const layoutSpy = jest.spyOn(manhattanUtils, "buildManhattanLayout");
    const bigData = makeSyntheticGwasData(1_000_000);

    const tRender0 = performance.now();
    const { container } = render(<ManhattanPlot data={bigData} />);
    const initialMs = performance.now() - tRender0;

    const slider = container.querySelector('input[type="range"]');
    const tUpdates0 = performance.now();
    for (let step = 0; step < 10; step += 1) {
      fireEvent.change(slider, { target: { value: (5.0 + step * 0.3).toFixed(1) } });
    }
    fireEvent.click(screen.getByRole("button", { name: /1\.5x/i }));
    fireEvent.click(screen.getByRole("button", { name: /2x/i }));
    const updatesMs = performance.now() - tUpdates0;

    console.info(
      `[A38 BENCHMARK 1M SNPs] initial=${initialMs.toFixed(1)}ms | 10 threshold + 2 zoom updates=${updatesMs.toFixed(1)}ms | buildManhattanLayoutCalls=${layoutSpy.mock.calls.length}`
    );

    expect(layoutSpy).toHaveBeenCalledTimes(1);
    layoutSpy.mockRestore();
  });
});
