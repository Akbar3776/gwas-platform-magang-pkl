import React from "react";

export function Pagination({
  currentPage = 1,
  totalItems = 0,
  pageSize = 10,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 25, 50, 100],
  itemLabel = "item",
}) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const safePage = Math.min(Math.max(1, currentPage), totalPages);

  if (totalItems <= 0) return null;

  const startIdx = (safePage - 1) * pageSize + 1;
  const endIdx = Math.min(totalItems, safePage * pageSize);

  // Generate a sliding window of page numbers (e.g. 1 ... 4 5 6 ... 10)
  const getPageNumbers = () => {
    if (totalPages <= 7) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }

    const pages = [1];
    const left = Math.max(2, safePage - 1);
    const right = Math.min(totalPages - 1, safePage + 1);

    if (left > 2) pages.push("...");
    for (let p = left; p <= right; p++) pages.push(p);
    if (right < totalPages - 1) pages.push("...");
    pages.push(totalPages);

    return pages;
  };

  const btnStyle = (disabled, active = false) => ({
    padding: "4px 9px",
    fontSize: 12,
    fontWeight: active ? 700 : 500,
    borderRadius: 5,
    border: `1px solid ${active ? "#2563eb" : "#cbd5e1"}`,
    background: active ? "#2563eb" : disabled ? "#f8fafc" : "#fff",
    color: active ? "#fff" : disabled ? "#94a3b8" : "#334155",
    cursor: disabled ? "not-allowed" : "pointer",
    minWidth: 28,
    textAlign: "center",
    transition: "all 0.15s ease",
  });

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 12,
        padding: "10px 14px",
        background: "#f8fafc",
        borderTop: "1px solid #e2e8f0",
        borderRadius: "0 0 8px 8px",
        fontSize: 12,
        color: "#475569",
      }}
    >
      {/* Range summary & page size picker */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span>
          Menampilkan <strong>{startIdx.toLocaleString("id-ID")}</strong>–
          <strong>{endIdx.toLocaleString("id-ID")}</strong> dari{" "}
          <strong>{totalItems.toLocaleString("id-ID")}</strong> {itemLabel}
        </span>

        {onPageSizeChange && pageSizeOptions && pageSizeOptions.length > 0 && (
          <div style={{ display: "inline-flex", alignItems: "center", gap: 6, marginLeft: 8 }}>
            <span style={{ fontSize: 11, color: "#64748b" }}>Per halaman:</span>
            <select
              value={pageSize}
              onChange={(e) => {
                const newSize = Number(e.target.value);
                if (onPageSizeChange) onPageSizeChange(newSize);
                if (onPageChange) onPageChange(1);
              }}
              style={{
                padding: "3px 8px",
                fontSize: 11,
                border: "1px solid #cbd5e1",
                borderRadius: 4,
                background: "#fff",
                color: "#1e293b",
                cursor: "pointer",
              }}
            >
              {pageSizeOptions.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Page navigation buttons */}
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <button
          type="button"
          onClick={() => onPageChange(1)}
          disabled={safePage <= 1}
          style={btnStyle(safePage <= 1)}
          title="Halaman Pertama"
        >
          «
        </button>
        <button
          type="button"
          onClick={() => onPageChange(safePage - 1)}
          disabled={safePage <= 1}
          style={btnStyle(safePage <= 1)}
          title="Halaman Sebelumnya"
        >
          ‹
        </button>

        {getPageNumbers().map((p, idx) =>
          p === "..." ? (
            <span key={`dots-${idx}`} style={{ padding: "0 4px", color: "#94a3b8" }}>
              …
            </span>
          ) : (
            <button
              key={`page-${p}`}
              type="button"
              onClick={() => onPageChange(p)}
              style={btnStyle(false, p === safePage)}
            >
              {p}
            </button>
          )
        )}

        <button
          type="button"
          onClick={() => onPageChange(safePage + 1)}
          disabled={safePage >= totalPages}
          style={btnStyle(safePage >= totalPages)}
          title="Halaman Berikutnya"
        >
          ›
        </button>
        <button
          type="button"
          onClick={() => onPageChange(totalPages)}
          disabled={safePage >= totalPages}
          style={btnStyle(safePage >= totalPages)}
          title="Halaman Terakhir"
        >
          »
        </button>
      </div>
    </div>
  );
}

export default Pagination;
