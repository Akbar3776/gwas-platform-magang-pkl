import { apiFetch, getAccessToken, setAccessToken, setAuthFailureHandler } from "./apiClient";
import { httpClient } from "./httpClient";
import { fetchJobFiles } from "./jobsApi";

describe("Unified HTTP auth refresh mutex (A40)", () => {
  beforeEach(() => {
    setAccessToken("expired-token");
    setAuthFailureHandler(() => {});
    jest.restoreAllMocks();
  });

  test("14 parallel apiFetch requests with expired token trigger exactly 1 /auth/refresh", async () => {
    let refreshCalls = 0;

    globalThis.fetch = jest.fn(async (url, init = {}) => {
      if (url === "/api/v1/auth/refresh") {
        refreshCalls += 1;
        await new Promise((r) => setTimeout(r, 10));
        return {
          ok: true,
          status: 200,
          json: async () => ({ access_token: "fresh-token" }),
        };
      }

      const headers = new Headers(init.headers || {});
      if (headers.get("Authorization") === "Bearer fresh-token") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ ok: true, url }),
        };
      }
      return {
        ok: false,
        status: 401,
        json: async () => ({ detail: "Expired" }),
      };
    });

    const responses = await Promise.all(
      Array.from({ length: 14 }, (_, i) => apiFetch(`/results/job-1/ep-${i}`))
    );

    expect(refreshCalls).toBe(1);
    expect(getAccessToken()).toBe("fresh-token");
    expect(responses).toHaveLength(14);
    for (const res of responses) {
      expect(res.ok).toBe(true);
    }
  });

  test("14 parallel mixed apiFetch + httpClient requests share the single /auth/refresh call", async () => {
    let refreshCalls = 0;
    const origAdapter = httpClient.defaults.adapter;

    globalThis.fetch = jest.fn(async (url, init = {}) => {
      if (url === "/api/v1/auth/refresh") {
        refreshCalls += 1;
        await new Promise((r) => setTimeout(r, 10));
        return {
          ok: true,
          status: 200,
          json: async () => ({ access_token: "shared-fresh-token" }),
        };
      }
      const headers = new Headers(init.headers || {});
      if (headers.get("Authorization") === "Bearer shared-fresh-token") {
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      }
      return { ok: false, status: 401, json: async () => ({ detail: "Expired" }) };
    });

    httpClient.defaults.adapter = async (config) => {
      const authHeader =
        typeof config.headers?.get === "function"
          ? config.headers.get("Authorization")
          : config.headers?.Authorization;
      if (authHeader === "Bearer shared-fresh-token") {
        return {
          data: { ok: true },
          status: 200,
          statusText: "OK",
          headers: {},
          config,
        };
      }
      const err = new Error("Request failed with status code 401");
      err.config = config;
      err.response = {
        status: 401,
        data: { detail: "Expired" },
        headers: {},
        config,
      };
      throw err;
    };

    try {
      const mixedCalls = [
        ...Array.from({ length: 7 }, (_, i) => apiFetch(`/results/job-1/fetch-${i}`)),
        ...Array.from({ length: 7 }, (_, i) => httpClient.get(`/jobs/job-1/axios-${i}`)),
      ];
      const settled = await Promise.all(mixedCalls);
      expect(refreshCalls).toBe(1);
      expect(getAccessToken()).toBe("shared-fresh-token");
      expect(settled).toHaveLength(14);
    } finally {
      httpClient.defaults.adapter = origAdapter;
    }
  });

  test("notifies auth failure once and clears token when /auth/refresh returns 401", async () => {
    let refreshCalls = 0;
    const onAuthFailure = jest.fn();
    setAuthFailureHandler(onAuthFailure);

    globalThis.fetch = jest.fn(async (url) => {
      if (url === "/api/v1/auth/refresh") {
        refreshCalls += 1;
        await new Promise((r) => setTimeout(r, 5));
        return { ok: false, status: 401, json: async () => ({ detail: "Invalid" }) };
      }
      return { ok: false, status: 401, json: async () => ({ detail: "Expired" }) };
    });

    const results = await Promise.allSettled([
      apiFetch("/results/job-1/a"),
      apiFetch("/results/job-1/b"),
      apiFetch("/results/job-1/c"),
    ]);

    expect(refreshCalls).toBe(1);
    expect(onAuthFailure).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBeNull();
    expect(results.every((r) => r.status === "rejected")).toBe(true);
  });

  test("jobsApi.fetchJobFiles forwards AbortSignal to httpClient", async () => {
    const getSpy = jest.spyOn(httpClient, "get").mockResolvedValueOnce({ data: { files: [] } });
    const controller = new AbortController();

    await fetchJobFiles("job-99", { signal: controller.signal });

    expect(getSpy).toHaveBeenCalledWith("/jobs/job-99/files", {
      signal: controller.signal,
    });
  });
});
