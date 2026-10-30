import React, { useEffect, useRef } from "react";
import { setupHiDpiCanvas } from "../utils/canvas";

const MARGIN = { top: 32, right: 44, bottom: 44, left: 44 };

export function ScreePlot({
  pve = [],
  cumulativePve = [],
  width = 380,
  height = 240,
  title = "Scree Plot (Proporsi Varians)",
}) {
  const canvasRef = useRef(null);

  const plotWidth = width - MARGIN.left - MARGIN.right;
  const plotHeight = height - MARGIN.top - MARGIN.bottom;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || pve.length === 0) return;

    const ctx = setupHiDpiCanvas(canvas, width, height);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    const n = Math.min(pve.length, 10);
    const maxPve = Math.max(...pve.slice(0, n), 0.1);

    const barW = Math.max((plotWidth / n) * 0.65, 8);
    const gap = plotWidth / n;

    // Y-Axis Left (Individual PVE)
    ctx.strokeStyle = "#f1f5f9";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#64748b";
    ctx.font = "10px Inter, system-ui, sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";

    [0, 0.25, 0.5, 0.75, 1.0].forEach((frac) => {
      const yVal = frac * maxPve;
      const yPos = MARGIN.top + plotHeight - frac * plotHeight;
      ctx.beginPath();
      ctx.moveTo(MARGIN.left, yPos);
      ctx.lineTo(MARGIN.left + plotWidth, yPos);
      ctx.stroke();
      ctx.fillText(`${(yVal * 100).toFixed(0)}%`, MARGIN.left - 6, yPos);
    });

    // Y-Axis Right (Cumulative PVE: 0 - 100%)
    ctx.textAlign = "left";
    [0, 25, 50, 75, 100].forEach((pct) => {
      const yPos = MARGIN.top + plotHeight - (pct / 100) * plotHeight;
      ctx.fillText(`${pct}%`, MARGIN.left + plotWidth + 6, yPos);
    });

    // Draw Bars (Individual PVE)
    for (let i = 0; i < n; i++) {
      const x = MARGIN.left + i * gap + (gap - barW) / 2;
      const h = (pve[i] / maxPve) * plotHeight;
      const y = MARGIN.top + plotHeight - h;

      ctx.fillStyle = "#3b82f6";
      ctx.fillRect(x, y, barW, h);
      ctx.strokeStyle = "#1d4ed8";
      ctx.lineWidth = 1;
      ctx.strokeRect(x, y, barW, h);

      // X Label
      ctx.fillStyle = "#334155";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(`PC${i + 1}`, x + barW / 2, MARGIN.top + plotHeight + 6);
    }

    // Draw Line (Cumulative PVE)
    if (cumulativePve.length > 0) {
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        const cx = MARGIN.left + i * gap + gap / 2;
        const cy = MARGIN.top + plotHeight - (cumulativePve[i] || 0) * plotHeight;
        if (i === 0) ctx.moveTo(cx, cy);
        else ctx.lineTo(cx, cy);
      }
      ctx.strokeStyle = "#ef4444";
      ctx.lineWidth = 2;
      ctx.stroke();

      // Dots
      for (let i = 0; i < n; i++) {
        const cx = MARGIN.left + i * gap + gap / 2;
        const cy = MARGIN.top + plotHeight - (cumulativePve[i] || 0) * plotHeight;
        ctx.beginPath();
        ctx.arc(cx, cy, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = "#ef4444";
        ctx.fill();
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    }

    // Border
    ctx.strokeStyle = "#cbd5e1";
    ctx.lineWidth = 1;
    ctx.strokeRect(MARGIN.left, MARGIN.top, plotWidth, plotHeight);

    // Title & Legend
    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 12px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(title, MARGIN.left, 18);

    // Legend
    ctx.fillStyle = "#3b82f6";
    ctx.fillRect(width - 130, 10, 10, 10);
    ctx.fillStyle = "#64748b";
    ctx.font = "10px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("PVE", width - 116, 18);

    ctx.strokeStyle = "#ef4444";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(width - 70, 15);
    ctx.lineTo(width - 55, 15);
    ctx.stroke();
    ctx.fillStyle = "#64748b";
    ctx.fillText("Kumulatif", width - 50, 18);
  }, [pve, cumulativePve, width, height, title, plotWidth, plotHeight]);

  return (
    <div style={{ position: "relative", width, height }}>
      <canvas ref={canvasRef} style={{ borderRadius: 8, border: "1px solid #e2e8f0" }} />
    </div>
  );
}
