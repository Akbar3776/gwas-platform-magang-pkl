import "@testing-library/jest-dom";
import { extractDownloadErrorMessage, saveBlob } from "./download";

describe("utils/download (A34)", () => {
  test("saveBlob membuat tautan unduh sementara dan selalu mencabut URL objeknya", () => {
    const origCreate = window.URL.createObjectURL;
    const origRevoke = window.URL.revokeObjectURL;
    const clickSpy = jest.fn();
    const origCreateEl = document.createElement.bind(document);

    window.URL.createObjectURL = jest.fn(() => "blob:download-test");
    window.URL.revokeObjectURL = jest.fn();
    jest.spyOn(document, "createElement").mockImplementation((tag) => {
      const el = origCreateEl(tag);
      if (tag === "a") {
        el.click = clickSpy;
      }
      return el;
    });

    try {
      const blob = new Blob(["chr1\t100"], { type: "text/plain" });
      saveBlob(blob, "sample.tsv");

      expect(window.URL.createObjectURL).toHaveBeenCalledWith(blob);
      expect(clickSpy).toHaveBeenCalledTimes(1);
      expect(window.URL.revokeObjectURL).toHaveBeenCalledWith("blob:download-test");
    } finally {
      document.createElement.mockRestore();
      window.URL.createObjectURL = origCreate;
      window.URL.revokeObjectURL = origRevoke;
    }
  });

  test("extractDownloadErrorMessage membaca detail JSON atau teks di dalam Blob error", async () => {
    const jsonBlob = new Blob(
      [JSON.stringify({ detail: [{ loc: ["query", "file_key"], msg: "not found" }] })],
      { type: "application/json" }
    );
    const jsonMsg = await extractDownloadErrorMessage(
      { response: { data: jsonBlob } },
      "Fallback"
    );
    expect(jsonMsg).toBe("query.file_key: not found");

    const textBlob = new Blob(["Izin akses berkas ditolak"], { type: "text/plain" });
    const textMsg = await extractDownloadErrorMessage(
      { response: { data: textBlob } },
      "Fallback"
    );
    expect(textMsg).toBe("Izin akses berkas ditolak");

    const plainMsg = await extractDownloadErrorMessage(new Error("Timeout"), "Fallback");
    expect(plainMsg).toBe("Timeout");
  });
});
