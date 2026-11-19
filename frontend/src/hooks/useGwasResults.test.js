import { act, renderHook, waitFor } from "@testing-library/react";
import { fetchManhattanData } from "../api/gwasApi";
import { useGwasResults } from "./useGwasResults";

jest.mock("../api/gwasApi", () => {
  class MockApiError extends Error {}
  return {
    ApiError: MockApiError,
    fetchManhattanData: jest.fn(),
  };
});

describe("useGwasResults jobId isolation and active controller guard (A31)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("clears data A and keeps isLoading true while B is pending after aborting old controller", async () => {
    let resolveA;
    let resolveB;
    let rejectStaleA;
    let callCount = 0;

    fetchManhattanData.mockImplementation((id) => {
      callCount += 1;
      if (id === "job-A" && callCount === 1) {
        return new Promise((res) => {
          resolveA = res;
        });
      }
      if (id === "job-A") {
        return new Promise((_res, rej) => {
          rejectStaleA = rej;
        });
      }
      return new Promise((res) => {
        resolveB = res;
      });
    });

    const { result, rerender } = renderHook(({ jobId }) => useGwasResults(jobId), {
      initialProps: { jobId: "job-A" },
    });

    await act(async () => {
      resolveA({ job_id: "job-A", total_points: 5 });
    });

    await waitFor(() => {
      expect(result.current.data?.job_id).toBe("job-A");
      expect(result.current.isLoading).toBe(false);
    });

    // Trigger refetch for A (slow in-flight controller A), then immediately switch A -> B
    act(() => {
      result.current.refetch();
    });
    rerender({ jobId: "job-B" });

    expect(result.current.data).toBeNull();
    expect(result.current.isLoading).toBe(true);

    // Late settlement from aborted/stale request A must NOT turn off loading for B
    await act(async () => {
      const abortErr = new Error("Aborted");
      abortErr.name = "AbortError";
      rejectStaleA(abortErr);
    });
    expect(result.current.isLoading).toBe(true);
    expect(result.current.data).toBeNull();

    await act(async () => {
      resolveB({ job_id: "job-B", total_points: 12 });
    });

    await waitFor(() => {
      expect(result.current.data?.job_id).toBe("job-B");
      expect(result.current.isLoading).toBe(false);
    });
  });
});
