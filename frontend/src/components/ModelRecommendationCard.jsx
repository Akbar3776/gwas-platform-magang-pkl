import React from "react";

export function ModelRecommendationCard({ recommendation }) {
  if (!recommendation) return null;

  const {
    recommended_model = "lmm",
    model_used = "lmm",
    recommended_pc_covariates = 3,
    pca_finding = "",
    kinship_finding = "",
    structure_detected = false,
    kinship_detected = false,
    rationale = [],
  } = recommendation;

  const isLmm = recommended_model.toLowerCase() === "lmm";

  return (
    <div
      style={{
        padding: 16,
        background: isLmm ? "#f0fdf4" : "#eff6ff",
        borderRadius: 10,
        border: `1px solid ${isLmm ? "#bbf7d0" : "#bfdbfe"}`,
        display: "grid",
        gap: 12,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <span
            style={{
              fontSize: 11,
              fontWeight: 800,
              textTransform: "uppercase",
              padding: "3px 8px",
              borderRadius: 4,
              background: isLmm ? "#15803d" : "#1d4ed8",
              color: "#fff",
              letterSpacing: "0.05em",
            }}
          >
            Rekomendasi Model: {recommended_model.toUpperCase()}
          </span>
          <span style={{ marginLeft: 8, fontSize: 13, color: "#334155" }}>
            (Model dijalankan: <strong>{model_used.toUpperCase()}</strong>)
          </span>
        </div>
        <div style={{ fontSize: 12, color: "#475569" }}>
          Kovariat PC yang disarankan: <strong>{recommended_pc_covariates} PC</strong>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 10 }}>
        <div style={{ padding: 10, background: "#fff", borderRadius: 6, border: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: structure_detected ? "#b45309" : "#15803d" }}>
            {structure_detected ? "● Stratifikasi Terdeteksi" : "○ Homogen"}
          </div>
          <div style={{ fontSize: 12, color: "#334155", marginTop: 2 }}>{pca_finding}</div>
        </div>
        <div style={{ padding: 10, background: "#fff", borderRadius: 6, border: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: kinship_detected ? "#b45309" : "#15803d" }}>
            {kinship_detected ? "● Kekerabatan Terdeteksi" : "○ Tidak Berkerabat Kuat"}
          </div>
          <div style={{ fontSize: 12, color: "#334155", marginTop: 2 }}>{kinship_finding}</div>
        </div>
      </div>

      {rationale.length > 0 && (
        <div style={{ fontSize: 12, color: "#334155" }}>
          <strong style={{ display: "block", marginBottom: 4 }}>Dasar Pertimbangan Genetika:</strong>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {rationale.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
