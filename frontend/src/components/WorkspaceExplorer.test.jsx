import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { downloadUploadedDataset, fetchUploadedDatasets, fetchUserJobs } from "../api/jobsApi";
import { WorkspaceExplorer } from "./WorkspaceExplorer";

jest.mock("../api/jobsApi", () => ({
  cancelJob: jest.fn(),
  deleteJob: jest.fn(),
  deleteUploadedDataset: jest.fn(),
  downloadUploadedDataset: jest.fn(),
  fetchUploadedDatasets: jest.fn(),
  fetchUserJobs: jest.fn(),
  previewUploadedDataset: jest.fn(),
  retryJob: jest.fn(),
}));

describe("WorkspaceExplorer error & loading state resilience (A33)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("menampilkan state loading awal sebelum data workspace datang", async () => {
    let resolveDatasets;
    let resolveJobs;
    fetchUploadedDatasets.mockReturnValue(
      new Promise((resolve) => {
        resolveDatasets = resolve;
      })
    );
    fetchUserJobs.mockReturnValue(
      new Promise((resolve) => {
        resolveJobs = resolve;
      })
    );

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkspaceExplorer />
      </MemoryRouter>
    );

    expect(screen.getByText(/Memuat daftar file VCF/i)).toBeInTheDocument();
    expect(screen.getByText(/Memuat daftar job/i)).toBeInTheDocument();

    resolveDatasets([]);
    resolveJobs([]);

    await waitFor(() => {
      expect(screen.getByText(/Belum ada riwayat file VCF yang diunggah/i)).toBeInTheDocument();
    });
  });

  test("mempertahankan data lama dan menampilkan alert saat refresh gagal", async () => {
    fetchUploadedDatasets
      .mockResolvedValueOnce([
        {
          file_id: "ds-1",
          original_filename: "rice_panel.vcf.gz",
          file_size_bytes: 2048,
          upload_status: "uploaded",
          file_exists: true,
          uploaded_at: "2026-11-19T10:00:00Z",
        },
      ])
      .mockRejectedValueOnce({
        response: { data: { detail: "Server workspace sedang sibuk." } },
      });

    fetchUserJobs
      .mockResolvedValueOnce([
        {
          job_id: "job-1",
          dataset_filename: "rice_panel.vcf.gz",
          overall_status: "completed",
          file_count: 4,
          updated_at: "2026-11-19T10:05:00Z",
        },
      ])
      .mockRejectedValueOnce(new Error("Timeout koneksi daftar job."));

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkspaceExplorer />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getAllByText("rice_panel.vcf.gz")).toHaveLength(2);
    });

    fireEvent.click(screen.getByRole("button", { name: /Segarkan/i }));

    await waitFor(() => {
      const alerts = screen.getAllByRole("alert");
      expect(alerts).toHaveLength(2);
      expect(alerts[0]).toHaveTextContent(/Server workspace sedang sibuk/i);
      expect(alerts[1]).toHaveTextContent(/Timeout koneksi daftar job/i);
    });

    // Data lama tetap bertahan meski kedua endpoint gagal pada refresh
    expect(screen.getAllByText("rice_panel.vcf.gz")).toHaveLength(2);
  });

  test("menampilkan pesan alert saat unduh dataset workspace gagal tanpa unhandled rejection (A34)", async () => {
    fetchUploadedDatasets.mockResolvedValueOnce([
      {
        file_id: "ds-dl",
        original_filename: "dl_test.vcf.gz",
        file_size_bytes: 512,
        upload_status: "uploaded",
        file_exists: true,
        uploaded_at: "2026-11-19T10:00:00Z",
      },
    ]);
    fetchUserJobs.mockResolvedValueOnce([]);
    downloadUploadedDataset.mockRejectedValueOnce(
      new Error("Dataset sumber sudah tidak ditemukan di disk.")
    );

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkspaceExplorer />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("dl_test.vcf.gz")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Unduh" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        /Dataset sumber sudah tidak ditemukan di disk/i
      );
    });
  });
});
