import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import GwasResultsPanel from "./GwasResultsPanel";
import * as gwasApi from "../api/gwasApi";

jest.mock("../api/gwasApi");

describe("GwasResultsPanel multi-locus Manhattan & QQ plots (A23)", () => {
  beforeAll(() => {
    const noop = () => {};
    jest.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => ({
      save: noop,
      restore: noop,
      clearRect: noop,
      fillRect: noop,
      strokeRect: noop,
      beginPath: noop,
      closePath: noop,
      moveTo: noop,
      lineTo: noop,
      stroke: noop,
      arc: noop,
      fill: noop,
      fillText: noop,
      translate: noop,
      rotate: noop,
      scale: noop,
      setTransform: noop,
      setLineDash: noop,
      measureText: () => ({ width: 20 }),
    }));
  });

  afterAll(() => {
    jest.restoreAllMocks();
  });

  beforeEach(() => {
    jest.clearAllMocks();
    gwasApi.fetchThresholdsData.mockResolvedValue({ bonferroni: 5e-8, selected_method: "bonferroni" });
    gwasApi.fetchLeadSnpsData.mockResolvedValue({ snps: [] });
    gwasApi.fetchInspectionData.mockResolvedValue({});
    gwasApi.fetchQcComparisonData.mockResolvedValue({});
    gwasApi.fetchPcaData.mockResolvedValue({});
    gwasApi.fetchKinshipData.mockResolvedValue({});
    gwasApi.fetchLdDecayData.mockResolvedValue({});
    gwasApi.fetchModelRecommendationData.mockResolvedValue({});
    gwasApi.fetchPeakComparisonData.mockResolvedValue({
      consistent: [],
      single_only: [],
      multi_only: [],
      summary: { n_consistent: 0, n_multi_only: 0, n_single_only: 0 },
    });
    gwasApi.fetchMultilocusResultsData.mockResolvedValue({
      status: "completed",
      methods: ["FarmCPU", "BLINK"],
      results: {
        FARMCPU: { status: "completed", total_snps: 2, min_p: 1e-8, significant_snps: [] },
        BLINK: { status: "completed", total_snps: 2, min_p: 1e-6, significant_snps: [] },
      },
    });
    gwasApi.fetchCandidateGenesData.mockResolvedValue({});
    gwasApi.fetchGoEnrichmentData.mockResolvedValue({});
    gwasApi.fetchPrimersData.mockResolvedValue({});
    gwasApi.fetchManhattanData.mockImplementation((jobId, signal, model) => {
      if (model === "FARMCPU") {
        return Promise.resolve({
          job_id: jobId,
          model: "FARMCPU",
          total_points: 2,
          chr: [1, 2],
          bp: [100, 200],
          snp: ["fc1", "fc2"],
          log10_p: [8.0, 2.5],
        });
      }
      if (model === "BLINK") {
        return Promise.resolve({
          job_id: jobId,
          model: "BLINK",
          total_points: 2,
          chr: [1, 2],
          bp: [150, 250],
          snp: ["bl1", "bl2"],
          log10_p: [6.2, 1.8],
        });
      }
      return Promise.resolve({
        job_id: jobId,
        total_points: 2,
        chr: [1, 2],
        bp: [100, 200],
        snp: ["gm1", "gm2"],
        log10_p: [5.0, 1.2],
      });
    });
  });

  test("renders model selector and fetches Manhattan & QQ data per selected model in multilocus tab", async () => {
    const { container } = render(<GwasResultsPanel jobId="job-ml-1" />);

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /Multi-Locus GWAS/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("tab", { name: /Multi-Locus GWAS/i }));

    await waitFor(() => {
      expect(gwasApi.fetchManhattanData).toHaveBeenCalledWith("job-ml-1", expect.anything(), "FARMCPU");
    });

    // Both ManhattanPlot and QQPlot canvases are rendered in the multi-locus plot section
    await waitFor(() => {
      expect(container.querySelectorAll("canvas").length).toBeGreaterThanOrEqual(2);
    });

    const selectorGroup = screen.getByRole("group", { name: /Pilih model plot multi-locus/i });
    const blinkBtn = within(selectorGroup).getByRole("button", { name: /^BLINK$/i });
    fireEvent.click(blinkBtn);

    await waitFor(() => {
      expect(gwasApi.fetchManhattanData).toHaveBeenCalledWith("job-ml-1", expect.anything(), "BLINK");
    });
  });

  test("LeadSnpTable separates significant SNPs from suggestive peaks in postgwas tab (A45)", async () => {
    gwasApi.fetchThresholdsData.mockResolvedValue({
      bonferroni: 5e-8,
      suggestive: 1e-4,
      selected_method: "bonferroni",
      selected_p: 5e-8,
    });
    gwasApi.fetchLeadSnpsData.mockResolvedValue({
      count: 0,
      snps: [],
      suggestive_count: 1,
      suggestive: [
        {
          snp: "rs_sug_1",
          chr: "2",
          pos: 12000,
          a1: "A",
          a0: "G",
          p: 2.5e-5,
          beta: 0.35,
          se: 0.08,
          n_clumped: 1,
          is_candidate: true,
          is_suggestive: true,
        },
      ],
    });

    render(<GwasResultsPanel jobId="job-a45-1" />);

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /Ambang Batas & Lead SNPs/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("tab", { name: /Ambang Batas & Lead SNPs/i }));

    await waitFor(() => {
      expect(screen.getByText(/0 Lokus Teridentifikasi/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/Tidak ada Lead SNPs terdeteksi/i)).toBeInTheDocument();
    expect(screen.getByText(/Peak Sugestif/i)).toBeInTheDocument();
    expect(screen.getByText("rs_sug_1")).toBeInTheDocument();
  });

  test("keeps tabs accessible on 409 Manhattan error so QC tab renders and shows per-tab error state (A32)", async () => {
    gwasApi.fetchManhattanData.mockRejectedValue(new Error("Hasil Manhattan belum tersedia (HTTP 409)"));
    gwasApi.fetchQcComparisonData.mockResolvedValue({
      before: { n_snps: 500, n_samples: 50, maf_hist: { edges: [0, 0.5], counts: [500] } },
      after: { n_snps: 420, n_samples: 48, maf_hist: { edges: [0, 0.5], counts: [420] } },
    });
    gwasApi.fetchPcaData.mockRejectedValue(new Error("PCA belum tersedia untuk job FAILED (HTTP 409)"));

    render(<GwasResultsPanel jobId="job-failed-409" />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/Hasil Manhattan belum tersedia/i);
    });

    // All 7 tabs stay reachable despite Manhattan 409
    const qcTab = screen.getByRole("tab", { name: /Inspeksi & QC/i });
    fireEvent.click(qcTab);

    await waitFor(() => {
      expect(screen.getByText(/Evaluasi Quality Control Genotipe/i)).toBeInTheDocument();
      expect(screen.getByText(/500/)).toBeInTheDocument();
    });

    const structureTab = screen.getByRole("tab", { name: /Struktur Populasi & LD/i });
    fireEvent.click(structureTab);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/PCA belum tersedia/i);
    });
  });
});
