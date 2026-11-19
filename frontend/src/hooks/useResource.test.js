import { act, renderHook, waitFor } from "@testing-library/react";
import { useResource } from "./useResource";

describe("useResource", () => {
  test("memuat data, menampilkan error terformat, dan memulihkan state saat refetch", async () => {
    const fetcher = jest
      .fn()
      .mockRejectedValueOnce({
        response: { data: { detail: [{ loc: ["query", "job_id"], msg: "invalid uuid" }] } },
      })
      .mockResolvedValueOnce({ ok: true });

    const { result } = renderHook(() =>
      useResource(fetcher, "job-1", { fallbackMessage: "Gagal muat QC." })
    );

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBe("query.job_id: invalid uuid");

    await act(async () => {
      result.current.refetch();
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
    expect(result.current.error).toBeNull();
    expect(result.current.data).toEqual({ ok: true });
  });

  test("meng-abort signal lama dan mengabaikan response basi saat jobId berubah", async () => {
    let resolveFirst;
    const signals = [];
    const fetcher = jest.fn((jobId, signal) => {
      signals.push(signal);
      if (jobId === "job-A") {
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve({ jobId: "job-B" });
    });

    const { result, rerender } = renderHook(
      ({ jobId }) => useResource(fetcher, jobId),
      { initialProps: { jobId: "job-A" } }
    );

    rerender({ jobId: "job-B" });

    await waitFor(() => {
      expect(result.current.data).toEqual({ jobId: "job-B" });
    });
    expect(signals[0].aborted).toBe(true);

    await act(async () => {
      resolveFirst({ jobId: "job-A" });
    });
    expect(result.current.data).toEqual({ jobId: "job-B" });
  });
});
