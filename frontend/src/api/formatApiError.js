export function formatApiError(input, fallback = "Terjadi kesalahan yang tidak diketahui.") {
  if (input === null || input === undefined) return fallback;
  if (typeof input === "string") return input.trim() || fallback;

  const rawDetail =
    input?.response?.data?.detail !== undefined && input?.response?.data?.detail !== null
      ? input.response.data.detail
      : input?.detail !== undefined && input?.detail !== null
      ? input.detail
      : input;

  if (typeof rawDetail === "string") {
    return rawDetail.trim() || fallback;
  }

  if (rawDetail instanceof Error) {
    return rawDetail.message?.trim() || fallback;
  }

  if (Array.isArray(rawDetail)) {
    const messages = rawDetail
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (item && typeof item === "object") {
          const loc = Array.isArray(item.loc)
            ? item.loc.filter((seg) => seg !== "body").join(".")
            : "";
          const msg = item.msg || item.message || JSON.stringify(item);
          return loc ? `${loc}: ${msg}` : String(msg);
        }
        return String(item ?? "");
      })
      .filter(Boolean);
    return messages.length > 0 ? messages.join("; ") : fallback;
  }

  if (rawDetail && typeof rawDetail === "object") {
    if (typeof rawDetail.message === "string" && rawDetail.message.trim()) {
      return rawDetail.message.trim();
    }
    if (typeof rawDetail.msg === "string" && rawDetail.msg.trim()) {
      return rawDetail.msg.trim();
    }
    try {
      return JSON.stringify(rawDetail);
    } catch {
      return fallback;
    }
  }

  return String(rawDetail) || fallback;
}
