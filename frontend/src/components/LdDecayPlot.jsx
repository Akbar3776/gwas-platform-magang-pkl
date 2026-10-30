import React, { useEffect, useRef, useState } from "react";
import { setupHiDpiCanvas, niceTicks } from "../utils/canvas";

const MARGIN = { top: 32, right: 24, bottom: 44, left: 48 };

const EMPTY_BINS = [];

export function LdDecayPlot({
  data,
  width = 460,
  height = 240,
  title = "Kurva Peluruhan Linkage Disequilibrium (LD Decay)",
}) {
  const canvasRef = useRef(null);
  const [hoveredBin, setHoveredBin] = useState(null);

  const bins = data?.bins || EMPTY_BINS;
  const cutoffR2 = data?.cutoff_r2 ?? 0.2;
  const chosenKb = data?.chosen_kb;
  const reason = data?.reason;

  const plotWidth = width - MARGIN.left - MARGIN.right;
  const plotHeight = height - MARGIN.top - MARGIN.bottom;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || bins.length === 0) return;

    const ctx = setupHiDpiCanvas(canvas, width, height);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    const maxKb = Math.max(...bins.map((b) => b.bin_kb), 100);
    const maxR2 = 1.0;

    const xScale = (kb) => MARGIN.left + (kb / maxKb) * plotWidth;
    const yScale = (r2) => MARGIN.top + plotHeight - (r2 / maxR2) * plotHeight;

    // Draw Grid & Ticks
    const xTicks = niceTicks(0, maxKb, 5);
    ctx.strokeStyle = "#f1f5f9";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#64748b";
    ctx.font = "10px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";

    xTicks.forEach((t) => {
      const cx = xScale(t);
      ctx.beginPath();
      ctx.moveTo(cx, MARGIN.top);
      ctx.lineTo(cx, MARGIN.top + plotHeight);
      ctx.stroke();
      ctx.fillText(`${t} kb`, cx, MARGIN.top + plotHeight + 6);
    });

    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    [0.0, 0.2, 0.4, 0.6, 0.8, 1.0].forEach((r2) => {
      const cy = yScale(r2);
      ctx.beginPath();
      ctx.moveTo(MARGIN.left, cy);
      ctx.lineTo(MARGIN.left + plotWidth, cy);
      ctx.stroke();
      ctx.fillText(r2.toFixed(1), MARGIN.left - 6, cy);
    });

    // Cutoff Threshold Line (Dashed red at r2 = 0.2)
    const cutY = yScale(cutoffR2);
    ctx.strokeStyle = "#ef4444";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(MARGIN.left, cutY);
    ctx.lineTo(MARGIN.left + plotWidth, cutY);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "#ef4444";
    ctx.font = "10px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(`Ambang r² = ${cutoffR2}`, MARGIN.left + 8, cutY - 6);

    // Chosen KB Vertical Line (Dashed green)
    if (chosenKb && chosenKb <= maxKb) {
      const chX = xScale(chosenKb);
      ctx.strokeStyle = "#10b981";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(chX, MARGIN.top);
      ctx.lineTo(chX, MARGIN.top + plotHeight);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = "#10b981";
      ctx.textAlign = "right";
      ctx.fillText(`Jarak Clumping: ${chosenKb} kb`, chX - 6, MARGIN.top + 16);
    }

    // Draw Smooth Area & Line for LD Decay
    ctx.beginPath();
    ctx.moveTo(MARGIN.left, MARGIN.top + plotHeight);
    bins.forEach((b, idx) => {
      const px = xScale(b.bin_kb);
      const py = yScale(b.mean_r2);
      if (idx === 0) ctx.lineTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.lineTo(xScale(bins[bins.length - 1].bin_kb), MARGIN.top + plotHeight);
    ctx.closePath();
    ctx.fillStyle = "rgba(37, 99, 235, 0.08)";
    ctx.fill();

    // Line
    ctx.beginPath();
    bins.forEach((b, idx) => {
      const px = xScale(b.bin_kb);
      const py = yScale(b.mean_r2);
      if (idx === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.strokeStyle = "#2563eb";
    ctx.lineWidth = 2.5;
    ctx.stroke();

    // Dots
    bins.forEach((b, idx) => {
      const px = xScale(b.bin_kb);
      const py = yScale(b.mean_r2);
      const isHovered = hoveredBin === idx;
      ctx.beginPath();
      ctx.arc(px, py, isHovered ? 5 : 3, 0, Math.PI * 2);
      ctx.fillStyle = isHovered ? "#1d4ed8" : "#3b82f6";
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1;
      ctx.stroke();
    });

    // Border
    ctx.strokeStyle = "#cbd5e1";
    ctx.lineWidth = 1;
    ctx.strokeRect(MARGIN.left, MARGIN.top, plotWidth, plotHeight);

    // Title & Labels
    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 12px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(title, MARGIN.left, 18);

    ctx.fillStyle = "#64748b";
    ctx.font = "11px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Jarak Fisik Pasangan Varian (kb)", MARGIN.left + plotWidth / 2, height - 8);
  }, [bins, cutoffR2, chosenKb, width, height, title, hoveredBin, plotWidth, plotHeight]);

  const handleMouseMove = (e) => {
    const canvas = canvasRef.current;
    if (!canvas || bins.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const maxKb = Math.max(...bins.map((b) => b.bin_kb), 100);
    const xScale = (kb) => MARGIN.left + (kb / maxKb) * plotWidth;
    const yScale = (r2) => MARGIN.top + plotHeight - r2 * plotHeight;

    let closestIdx = null;
    let closestDist = 12;
    for (let i = 0; i < bins.length; i++) {
      const px = xScale(bins[i].bin_kb);
      const py = yScale(bins[i].mean_r2);
      const d = Math.hypot(mx - px, my - py);
      if (d < closestDist) {
        closestDist = d;
        closestIdx = i;
      }
    }
    setHoveredBin(closestIdx);
  };

  return (
    <div style={{ display: "grid", gap: 6 }}>
      <div style={{ position: "relative", width, height }}>
        <canvas
          ref={canvasRef}
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHoveredBin(null)}
          style={{ borderRadius: 8, border: "1px solid #e2e8f0" }}
        />
        {hoveredBin !== null && bins[hoveredBin] && (
          <div
            style={{
              position: "absolute",
              top: 8,
              right: 12,
              padding: "4px 8px",
              background: "rgba(15, 23, 42, 0.85)",
              color: "#fff",
              borderRadius: 6,
              fontSize: 11,
              pointerEvents: "none",
            }}
          >
            Jarak: <strong>{bins[hoveredBin].bin_kb} kb</strong> | Rata-rata r²:{" "}
            <strong>{bins[hoveredBin].mean_r2?.toFixed(4)}</strong>
            {bins[hoveredBin].n_pairs > 0 && ` (${bins[hoveredBin].n_pairs} pasang)`}
          </div>
        )}
      </div>
      {reason && (
        <p style={{ margin: "2px 0 0", fontSize: 11, color: "#64748b" }}>
          ℹ️ {reason}
        </p>
      )}
    </div>
  );
}
