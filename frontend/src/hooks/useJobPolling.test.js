import { act, renderHook, waitFor } from "@testing-library/react";
import { fetchJobStatus } from "../api/jobsApi";
import { useJobPolling } from "./useJobPolling";

jest.mock("../api/jobsApi", () => ({
  fetchJobStatus: jest.fn(),
}));

describe("useJobPolling jobId isolation (A31)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("resets jobStatus and ignores stale job-A response on rerender A->B", async () => {
    let resolveA;
    let resolveB;
    fetchJobStatus.mockImplementation((id) => {
      if (id === "job-A") {
        return new Promise((res) => {
          resolveA = res;
        });
      }
      return new Promise((res) => {
        resolveB = res;
      });
    });

    const { result, rerender } = renderHook(({ jobId }) => useJobPolling(jobId), {
      initialProps: { jobId: "job-A" },
    });

    await act(async () => {
      resolveA({ job_id: "job-A", overall_status: "COMPLETED", current_step: "ANNOTATION" });
    });

    await waitFor(() => {
      expect(result.current.jobStatus?.job_id).toBe("job-A");
    });

    // Switch A -> B while B is still pending: A data must clear immediately
    rerender({ jobId: "job-B" });
    expect(result.current.jobStatus).toBeNull();
    expect(result.current.pollError).toBeNull();
    expect(result.current.isPolling).toBe(true);

    await act(async () => {
      resolveB({ job_id: "job-B", overall_status: "RUNNING_QC", current_step: "QC" });
    });

    await waitFor(() => {
      expect(result.current.jobStatus?.job_id).toBe("job-B");
    });
  });
});
