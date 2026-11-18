import "@testing-library/jest-dom";
import { act, render, screen } from "@testing-library/react";
import React from "react";
import { fetchJobFiles } from "../api/jobsApi";
import { JobFilesPanel } from "./JobFilesPanel";

jest.mock("../api/jobsApi", () => ({
  fetchJobFiles: jest.fn(),
  previewJobFile: jest.fn(),
  downloadJobFile: jest.fn(),
}));

describe("JobFilesPanel polling lifecycle and stale response guard (A14)", () => {
  afterEach(() => {
    jest.useRealTimers();
    jest.clearAllMocks();
  });

  test("stops polling /files once job status reaches COMPLETED (fake timers)", async () => {
    jest.useFakeTimers();
    fetchJobFiles
      .mockResolvedValueOnce({
        job_id: "job-1",
        overall_status: "running_qc",
        files: [{ file_key: "k1", filename: "qc.log", stage: "qc", size_bytes: 120 }],
      })
      .mockResolvedValueOnce({
        job_id: "job-1",
        overall_status: "completed",
        files: [
          { file_key: "k1", filename: "qc.log", stage: "qc", size_bytes: 120 },
          { file_key: "k2", filename: "assoc.tsv", stage: "gwas", size_bytes: 450 },
        ],
      });

    render(<JobFilesPanel jobId="job-1" isRunning={true} />);

    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchJobFiles).toHaveBeenCalledTimes(1);
    expect(screen.getByText("qc.log")).toBeInTheDocument();

    await act(async () => {
      jest.advanceTimersByTime(3000);
      await Promise.resolve();
    });
    expect(fetchJobFiles).toHaveBeenCalledTimes(2);
    expect(screen.getByText("assoc.tsv")).toBeInTheDocument();

    await act(async () => {
      jest.advanceTimersByTime(15000);
      await Promise.resolve();
    });
    expect(fetchJobFiles).toHaveBeenCalledTimes(2);
  });

  test("ignores stale /files response belonging to previous jobId", async () => {
    let resolveOld;
    const oldPromise = new Promise((res) => {
      resolveOld = res;
    });

    fetchJobFiles.mockImplementation((id) => {
      if (id === "job-old") return oldPromise;
      return Promise.resolve({
        job_id: "job-new",
        overall_status: "completed",
        files: [{ file_key: "new-1", filename: "new_output.tsv", stage: "gwas", size_bytes: 88 }],
      });
    });

    const { rerender } = render(<JobFilesPanel jobId="job-old" isRunning={false} />);
    expect(fetchJobFiles).toHaveBeenCalledWith("job-old", expect.anything());

    rerender(<JobFilesPanel jobId="job-new" isRunning={false} />);

    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText("new_output.tsv")).toBeInTheDocument();

    await act(async () => {
      resolveOld({
        job_id: "job-old",
        overall_status: "running_qc",
        files: [{ file_key: "old-1", filename: "stale_old.log", stage: "qc", size_bytes: 10 }],
      });
      await Promise.resolve();
    });

    expect(screen.getByText("new_output.tsv")).toBeInTheDocument();
    expect(screen.queryByText("stale_old.log")).not.toBeInTheDocument();
  });
});
