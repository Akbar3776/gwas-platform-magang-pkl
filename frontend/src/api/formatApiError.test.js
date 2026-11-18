import { formatApiError } from "./formatApiError";

describe("formatApiError (A15)", () => {
  test("returns string details unchanged", () => {
    expect(formatApiError("Token tidak valid")).toBe("Token tidak valid");
    expect(formatApiError({ response: { data: { detail: "Dataset tidak ditemukan" } } })).toBe(
      "Dataset tidak ditemukan"
    );
  });

  test("formats FastAPI/Pydantic 422 array into readable string", () => {
    const detail = [
      {
        type: "value_error",
        loc: ["body", "imputation", "beagle_max_memory"],
        msg: "BEAGLE_MAX_MEMORY harus berbentuk -Xmx8g",
        input: "8g",
      },
    ];
    const formatted = formatApiError(detail);
    expect(typeof formatted).toBe("string");
    expect(formatted).toContain("beagle_max_memory");
    expect(formatted).toContain("BEAGLE_MAX_MEMORY harus berbentuk -Xmx8g");
  });

  test("formats plain object or Axios error object safely into string", () => {
    expect(formatApiError({ msg: "Invalid payload" })).toBe("Invalid payload");
    const jsonFallback = formatApiError({ code: "ERR_422", field: "maf" });
    expect(typeof jsonFallback).toBe("string");
    expect(jsonFallback).toContain("ERR_422");
    expect(formatApiError(null, "Default error")).toBe("Default error");
  });
});
