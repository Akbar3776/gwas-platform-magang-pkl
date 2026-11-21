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

  it("renders Fase 0 split chromosome parts and INVALID_POS label for new pipeline payloads", () => {
    render(
      <QcComparisonPanel
        comparison={{
          before: {
            n_snps: 5000,
            n_samples: 120,
            maf_hist: { edges: [0, 0.5], counts: [5000] },
          },
          after: {
            n_snps: 4200,
            n_samples: 115,
            maf_hist: { edges: [0, 0.5], counts: [4200] },
          },
          removed: {
            overflow_32bit: 0,
            invalid_pos: 2,
            maf: 500,
            geno: 300,
            mind: 5,
            hwe: 0,
            non_snp: 0,
            multiallelic: 0,
          },
          split_chromosomes: ["chr7"],
          split_chrom_parts: { chr7: 3 },
          snp_loss_fraction: 0.16,
        }}
      />
    );

    expect(screen.getByText("Kromosom dipecah: chr7 (3 bagian)")).toBeInTheDocument();
    expect(screen.getByText("INVALID_POS dibuang")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.queryByText("Overflow 32-bit (Fase 0)")).not.toBeInTheDocument();
  });
});
