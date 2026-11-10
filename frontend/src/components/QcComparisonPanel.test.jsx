import React from "react";
import { render, screen } from "@testing-library/react";
import { QcComparisonPanel } from "./QcComparisonPanel";

// Mock canvas setup so HistogramChart doesn't throw in jsdom
jest.mock("../utils/canvas", () => ({
  setupHiDpiCanvas: jest.fn(() => ({
    fillRect: jest.fn(),
    strokeRect: jest.fn(),
    fillText: jest.fn(),
    beginPath: jest.fn(),
    moveTo: jest.fn(),
    lineTo: jest.fn(),
    stroke: jest.fn(),
  })),
  niceTicks: jest.fn(() => [0, 0.25, 0.5]),
}));

describe("QcComparisonPanel", () => {
  it("renders fallback message when comparison is null or skipped", () => {
    const { rerender } = render(<QcComparisonPanel comparison={null} />);
    expect(screen.getByText(/Data evaluasi perbandingan QC belum tersedia/i)).toBeInTheDocument();

    rerender(<QcComparisonPanel comparison={{ status: "skipped" }} />);
    expect(screen.getByText(/Data evaluasi perbandingan QC belum tersedia/i)).toBeInTheDocument();
  });

  it("renders before and after counts and breakdown correctly", () => {
    const mockComparison = {
      before: {
        n_snps: 2035699,
        n_samples: 233,
        maf_hist: { edges: [0, 0.05, 0.5], counts: [800000, 1200000] },
      },
      after: {
        n_snps: 356262,
        n_samples: 209,
        maf_hist: { edges: [0, 0.05, 0.5], counts: [0, 356262] },
      },
      removed: {
        overflow_32bit: 185,
        maf: 293307,
        geno: 1380777,
        mind: 14,
        hwe: 0,
        non_snp: 0,
        multiallelic: 0,
      },
      snp_loss_fraction: 0.825,
      warning: "SNP berkurang 82.5% (>50%) — periksa filter",
    };

    render(<QcComparisonPanel comparison={mockComparison} />);

    // Warning
    expect(screen.getByRole("alert")).toHaveTextContent("SNP berkurang 82.5%");

    // Summary cards
    expect(screen.getByText("SNP Sebelum QC")).toBeInTheDocument();
    expect(screen.getByText("SNP Lolos QC")).toBeInTheDocument();
    expect(screen.getByText("Sampel Sebelum QC")).toBeInTheDocument();
    expect(screen.getByText("Sampel Lolos QC")).toBeInTheDocument();
    expect(screen.getByText("(-82.5%)")).toBeInTheDocument();

    // Removal breakdown
    expect(screen.getByText("Rincian Varian & Sampel Terbuang per Kriteria QC:")).toBeInTheDocument();
    expect(screen.getByText("Overflow 32-bit (Fase 0)")).toBeInTheDocument();
    expect(screen.getByText("185")).toBeInTheDocument();
    expect(screen.getByText("Missing SNP (GENO)")).toBeInTheDocument();
    expect(screen.getByText("Missing Sampel (MIND)")).toBeInTheDocument();
  });
});
