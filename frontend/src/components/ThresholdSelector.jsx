import React from "react";

function formatThreshold(val) {
  if (val === null || val === undefined) return "-";
  const num = Number(val);
  if (isNaN(num)) return String(val);
  return num.toExponential(2);
}

export function ThresholdSelector({
  thresholds,
  activeMethod = "bonferroni",
  onChangeMethod,
}) {
  if (!thresholds || thresholds.status === "not_available" || thresholds.status === "skipped") {
    return (
      <div style={{ padding: 12, background: "#f8fafc", borderRadius: 8, color: "#64748b", fontSize: 13 }}>
        Data ambang batas signifikansi belum tersedia untuk job ini.
      </div>
    );
  }

  const {
    bonferroni = 5e-8,
    fdr = null,
    lod_p = null,
    lod_score = 5.6,
    suggestive = 1e-5,
    selected_method = "bonferroni",
    n_tests = 0,
    warning = null,
  } = thresholds;

  const currentMethod = (activeMethod || selected_method || "bonferroni").toLowerCase();

  // Hitung fallback lod_p jika backend belum menyertakannya
  const effectiveLodP = lod_p !== null && lod_p !== undefined
    ? Number(lod_p)
    : Math.pow(10, -Number(lod_score || 5.6));

  const methods = [
    {
      id: "bonferroni",
      name: "Bonferroni",
      value: bonferroni,
      log10: bonferroni > 0 ? -Math.log10(bonferroni) : 7.3,
      desc: n_tests > 0 ? `0.05 / ${Number(n_tests).toLocaleString("id-ID")} varian` : "0.05 / N varian",
      color: "#dc2626",
    },
    {
      id: "suggestive",
      name: "Suggestive",
      value: suggestive,
      log10: suggestive > 0 ? -Math.log10(suggestive) : 5.0,
      desc: "Ambang sugestif eksploratif (1e-5)",
      color: "#f59e0b",
    },
    {
      id: "fdr",
      name: "FDR (BH)",
      value: fdr,
      log10: fdr > 0 ? -Math.log10(fdr) : null,
      desc: fdr ? "Benjamini-Hochberg q < 0.05" : "Tidak ada SNP lolos",
      color: "#2563eb",
      disabled: fdr === null || fdr === undefined || isNaN(Number(fdr)),
    },
    {
      id: "lod",
      name: `LOD ${lod_score || 5.6}`,
      value: effectiveLodP,
      log10: Number(lod_score || 5.6),
      desc: `Ambang log-of-odds (LOD ${lod_score || 5.6})`,
      color: "#7c3aed",
      disabled: effectiveLodP === null || effectiveLodP === undefined || isNaN(Number(effectiveLodP)),
    },
  ];

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {warning && (
        <div
          style={{
            padding: "8px 12px",
            background: "#fffbeb",
            borderRadius: 6,
            color: "#92400e",
            fontSize: 12,
            border: "1px solid #fef3c7",
          }}
        >
          ℹ️ {warning}
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 10,
        }}
      >
        {methods.map((m) => {
          const isSelected = currentMethod === m.id;
          return (
            <div
              key={m.id}
              onClick={() => {
                if (!m.disabled && onChangeMethod) onChangeMethod(m.id);
              }}
              style={{
                padding: 10,
                borderRadius: 8,
                background: isSelected ? "#f8fafc" : "#fff",
                border: `2px solid ${isSelected ? m.color : "#e2e8f0"}`,
                cursor: m.disabled ? "not-allowed" : "pointer",
                opacity: m.disabled ? 0.6 : 1,
                boxShadow: isSelected ? `0 0 0 1px ${m.color}` : "none",
                transition: "all 0.15s ease-in-out",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: m.color }}>
                  {m.name}
                </span>
                {isSelected && (
                  <span
                    style={{
                      fontSize: 9,
                      fontWeight: 800,
                      background: m.color,
                      color: "#fff",
                      padding: "1px 5px",
                      borderRadius: 4,
                    }}
                  >
                    AKTIF
                  </span>
                )}
              </div>
              <div style={{ fontSize: 16, fontWeight: 800, color: "#0f172a", margin: "4px 0" }}>
                {formatThreshold(m.value)}
              </div>
              <div style={{ fontSize: 11, color: "#64748b" }}>
                {m.log10 !== null && (
                  <span style={{ fontWeight: 600, color: "#334155" }}>
                    -log10: {m.log10.toFixed(2)} ·{" "}
                  </span>
                )}
                {m.desc}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
