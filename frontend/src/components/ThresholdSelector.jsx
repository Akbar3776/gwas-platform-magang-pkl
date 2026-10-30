import React from "react";

export function ThresholdSelector({
  thresholds,
  activeMethod = "bonferroni",
  onChangeMethod,
}) {
  if (!thresholds) return null;

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

  const current = activeMethod || selected_method;

  const methods = [
    {
      id: "bonferroni",
      name: "Bonferroni",
      value: bonferroni,
      desc: `0.05 / ${n_tests.toLocaleString("id-ID")}`,
      color: "#dc2626",
    },
    {
      id: "suggestive",
      name: "Suggestive",
      value: suggestive,
      desc: "Ambang batas sugestif (1e-5)",
      color: "#f59e0b",
    },
    {
      id: "fdr",
      name: "FDR (BH)",
      value: fdr,
      desc: fdr ? "Benjamini-Hochberg q < 0.05" : "Tidak ada SNP lolos",
      color: "#2563eb",
      disabled: fdr === null,
    },
    {
      id: "lod",
      name: `LOD ${lod_score}`,
      value: lod_p,
      desc: `p < 10^-${lod_score}`,
      color: "#7c3aed",
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
          const isSelected = current.toLowerCase() === m.id;
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
                  <span style={{ fontSize: 10, fontWeight: 800, color: m.color }}>
                    AKTIF
                  </span>
                )}
              </div>
              <div style={{ fontSize: 16, fontWeight: 800, color: "#0f172a", margin: "4px 0" }}>
                {m.value ? m.value.toExponential(2) : "-"}
              </div>
              <div style={{ fontSize: 11, color: "#64748b" }}>{m.desc}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
