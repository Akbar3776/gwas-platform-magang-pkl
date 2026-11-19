import crypto from "crypto";
import { TextEncoder } from "util";
import "@testing-library/jest-dom";
import { act, fireEvent, render, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { httpClient } from "../api/httpClient";
import { VcfUploader } from "../components/VcfUploader";
import { UPLOAD_STAGE, useChunkUpload } from "./useChunkUpload";

jest.mock("../api/httpClient", () => ({
  httpClient: {
    post: jest.fn(),
    get: jest.fn(),
  },
}));

describe("useChunkUpload cancellation, concurrency guard, and unmount safety (A12)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("cancelUpload aborts in-flight requests and resets stage to IDLE", async () => {
    const signals = [];
    httpClient.post.mockImplementation((url, _body, config) => {
      if (config?.signal) signals.push(config.signal);
      if (url === "/upload/init") {
        return Promise.resolve({ data: { file_id: "ds-cancel" } });
      }
      return new Promise(() => {});
    });

    const { result } = renderHook(() => useChunkUpload());
    const file = new File(["##fileformat=VCFv4.2\n"], "sample.vcf", { type: "text/plain" });

    act(() => {
      void result.current.startUpload(file);
    });

    await waitFor(() => {
      expect(httpClient.post).toHaveBeenCalledWith("/upload/chunk", expect.any(FormData), expect.anything());
    });
    expect(result.current.stage).toBe(UPLOAD_STAGE.UPLOADING);

    act(() => {
      result.current.cancelUpload();
    });

    expect(result.current.stage).toBe(UPLOAD_STAGE.IDLE);
    expect(signals.some((s) => s.aborted)).toBe(true);
  });

  test("ignores second startUpload call while upload loop is active", async () => {
    let resolveChunk;
    httpClient.post.mockImplementation((url) => {
      if (url === "/upload/init") {
        return Promise.resolve({ data: { file_id: "ds-1" } });
      }
      if (url === "/upload/chunk") {
        return new Promise((res) => {
          resolveChunk = res;
        });
      }
      return Promise.resolve({ data: {} });
    });
    httpClient.get.mockResolvedValue({ data: { upload_status: "READY" } });

    const { result } = renderHook(() => useChunkUpload());
    const file1 = new File(["a"], "one.vcf", { type: "text/plain" });
    const file2 = new File(["b"], "two.vcf", { type: "text/plain" });

    let p1;
    let p2;
    act(() => {
      p1 = result.current.startUpload(file1);
      p2 = result.current.startUpload(file2);
    });

    expect(await p2).toBeNull();
    const initCalls = httpClient.post.mock.calls.filter(([url]) => url === "/upload/init");
    expect(initCalls).toHaveLength(1);

    await waitFor(() => expect(typeof resolveChunk).toBe("function"));
    await act(async () => {
      resolveChunk({ data: {} });
      await p1;
    });
  });

  test("VcfUploader ignores drop while busy and never invokes onDatasetReady after unmount", async () => {
    let resolveInit;
    const signals = [];
    httpClient.post.mockImplementation((_url, _body, config) => {
      if (config?.signal) signals.push(config.signal);
      return new Promise((res) => {
        resolveInit = res;
      });
    });

    const onDatasetReady = jest.fn();
    const { container, unmount } = render(<VcfUploader onDatasetReady={onDatasetReady} />);
    const dropZone = container.querySelector("input[type='file']").parentElement;
    const file1 = new File(["a"], "first.vcf", { type: "text/plain" });
    const file2 = new File(["b"], "second.vcf", { type: "text/plain" });

    fireEvent.drop(dropZone, { dataTransfer: { files: [file1] } });
    fireEvent.drop(dropZone, { dataTransfer: { files: [file2] } });

    expect(httpClient.post).toHaveBeenCalledTimes(1);

    unmount();
    expect(signals.some((s) => s.aborted)).toBe(true);

    await act(async () => {
      resolveInit?.({ data: { file_id: "ds-late" } });
    });

    expect(onDatasetReady).not.toHaveBeenCalled();
  });

  test("sends expected_sha256 in /upload/finalize without calling whole-file arrayBuffer (A26)", async () => {
    let capturedFinalizeForm = null;
    httpClient.post.mockImplementation((url, body) => {
      if (url === "/upload/init") {
        return Promise.resolve({ data: { file_id: "ds-sha" } });
      }
      if (url === "/upload/chunk") {
        return Promise.resolve({ data: { is_complete: true } });
      }
      if (url === "/upload/finalize") {
        capturedFinalizeForm = body;
        return Promise.resolve({ data: { upload_status: "VALIDATING" } });
      }
      return Promise.resolve({ data: {} });
    });
    httpClient.get.mockResolvedValue({ data: { upload_status: "UPLOADED" } });

    const textPayload = "##fileformat=VCFv4.2\n#CHROM\tPOS\n1\t1000\trs1\tA\tG\n";
    const expectedDigest = crypto
      .createHash("sha256")
      .update(new TextEncoder().encode(textPayload))
      .digest("hex");
    const file = new File([textPayload], "cohort.vcf", { type: "text/plain" });
    file.arrayBuffer = jest.fn(() => Promise.reject(new Error("must stream slices only")));

    const { result } = renderHook(() => useChunkUpload());
    let uploadedId;
    await act(async () => {
      uploadedId = await result.current.startUpload(file);
    });

    expect(uploadedId).toBe("ds-sha");
    expect(file.arrayBuffer).not.toHaveBeenCalled();
    expect(capturedFinalizeForm).toBeInstanceOf(FormData);
    expect(capturedFinalizeForm.get("expected_sha256")).toBe(expectedDigest);
  });

  test("surfaces server checksum mismatch error when upload status returns FAILED (A26)", async () => {
    httpClient.post.mockImplementation((url) => {
      if (url === "/upload/init") {
        return Promise.resolve({ data: { file_id: "ds-fail" } });
      }
      return Promise.resolve({ data: {} });
    });
    httpClient.get.mockResolvedValue({
      data: { upload_status: "FAILED", error_message: "Checksum tidak cocok" },
    });

    const file = new File(["abc"], "bad.vcf", { type: "text/plain" });
    const { result } = renderHook(() => useChunkUpload());

    await act(async () => {
      await expect(result.current.startUpload(file)).rejects.toThrow("Checksum tidak cocok");
    });
    expect(result.current.stage).toBe(UPLOAD_STAGE.ERROR);
    expect(result.current.errorMessage).toContain("Checksum tidak cocok");
  });
});
