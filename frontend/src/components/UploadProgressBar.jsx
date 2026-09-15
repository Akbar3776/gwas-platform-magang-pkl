/**
 * Progress bar sederhana & reusable — murni presentational, tidak menyimpan
 * state sendiri. Persentase dan label dikontrol penuh oleh parent.
 */
export function UploadProgressBar({ percent, label }) {
  const clampedPercent = Math.min(100, Math.max(0, percent));

  return (
    <div style={styles.container}>
      {label && <div style={styles.label}>{label}</div>}
      <div style={styles.track}>
        <div style={{ ...styles.fill, width: `${clampedPercent}%` }} />
      </div>
      <div style={styles.percentText}>{clampedPercent}%</div>
    </div>
  );
}

const styles = {
  container: { width: "100%", marginTop: 8, marginBottom: 8 },
  label: { fontSize: 14, color: "#374151", marginBottom: 4 },
  track: {
    width: "100%",
    height: 10,
    backgroundColor: "#e5e7eb",
    borderRadius: 999,
    overflow: "hidden",
  },
  fill: {
    height: "100%",
    backgroundColor: "#2563eb",
    borderRadius: 999,
    transition: "width 0.2s ease",
  },
  percentText: { fontSize: 12, color: "#6b7280", marginTop: 2, textAlign: "right" },
};
