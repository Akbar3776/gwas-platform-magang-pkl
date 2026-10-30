import React, { useEffect, useRef, useState } from "react";
import { setupHiDpiCanvas, niceTicks, COLOR_PALETTE } from "../utils/canvas";

const MARGIN = { top: 32, right: 24, bottom: 44, left: 52 };
const EMPTY_SAMPLES = [];
const EMPTY_PVE = [];

export function PcaScatterPlot({
  data,
  pcX = 1,
  pcY = 2,
  width = 460,
  height = 320,
  title = "Analisis Komponen Utama (PCA)",
}) {
  const canvasRef = useRef(null);
  const [hoveredSample, setHoveredSample] = useState(null);

  const samples = data?.samples || EMPTY_SAMPLES;
  const pve = data?.pve || EMPTY_PVE;
  const pveX = pve[pcX - 1] ? (pve[pcX - 1] * 100).toFixed(1) : null;
  const pveY = pve[pcY - 1] ? (pve[pcY - 1] * 100).toFixed(1) : null;

  const plotWidth = width - MARGIN.left - MARGIN.right;
  const plotHeight = height - MARGIN.top - MARGIN.bottom;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || samples.length === 0) return;

    const ctx = setupHiDpiCanvas(canvas, width, height);

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    const xs = samples.map((s) => s.pcs[pcX - 1] || 0);
    const ys = samples.map((s) => s.pcs[pcY - 1] || 0);

    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);

    const padX = (maxX - minX || 1) * 0.08;
    const padY = (maxY - minY || 1) * 0.08;

    const x0 = minX - padX;
    const x1 = maxX + padX;
    const y0 = minY - padY;
    const y1 = maxY + padY;

    const xScale = (val) => MARGIN.left + ((val - x0) / (x1 - x0 || 1)) * plotWidth;
    const yScale = (val) => MARGIN.top + plotHeight - ((val - y0) / (y1 - y0 || 1)) * plotHeight;

    // Draw Grid & Ticks
    const xTicks = niceTicks(x0, x1, 5);
    const yTicks = niceTicks(y0, y1, 5);

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
      ctx.fillText(t.toFixed(2), cx, MARGIN.top + plotHeight + 6);
    });

    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    yTicks.forEach((t) => {
      const cy = yScale(t);
      ctx.beginPath();
      ctx.moveTo(MARGIN.left, cy);
      ctx.lineTo(MARGIN.left + plotWidth, cy);
      ctx.stroke();
      ctx.fillText(t.toFixed(2), MARGIN.left - 6, cy);
    });

    // Zero axes
    if (x0 <= 0 && x1 >= 0) {
      const zx = xScale(0);
      ctx.strokeStyle = "#cbd5e1";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(zx, MARGIN.top);
      ctx.lineTo(zx, MARGIN.top + plotHeight);
      ctx.stroke();
    }
    if (y0 <= 0 && y1 >= 0) {
      const zy = yScale(0);
      ctx.strokeStyle = "#cbd5e1";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(MARGIN.left, zy);
      ctx.lineTo(MARGIN.left + plotWidth, zy);
      ctx.stroke();
    }

    // Draw Sample Points
    samples.forEach((s, idx) => {
      const px = xScale(s.pcs[pcX - 1] || 0);
      const py = yScale(s.pcs[pcY - 1] || 0);
      const color = COLOR_PALETTE[(s.cluster || 0) % COLOR_PALETTE.length];
      const isHovered = hoveredSample === idx;

      ctx.beginPath();
      ctx.arc(px, py, isHovered ? 6 : 4, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = isHovered ? "#0f172a" : "#ffffff";
      ctx.lineWidth = isHovered ? 2 : 1;
      ctx.stroke();
    });

    // Outer border
    ctx.strokeStyle = "#cbd5e1";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(MARGIN.left, MARGIN.top, plotWidth, plotHeight);

    // Title & Axis Labels
    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 12px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(title, MARGIN.left, 18);

    ctx.fillStyle = "#64748b";
    ctx.font = "11px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(
      `PC${pcX}${pveX ? ` (${pveX}% varians)` : ""}`,
      MARGIN.left + plotWidth / 2,
      height - 8
    );

    // Y Axis Label (Rotated)
    ctx.save();
    ctx.translate(14, MARGIN.top + plotHeight / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = "center";
    ctx.fillText(`PC${pcY}${pveY ? ` (${pveY}% varians)` : ""}`, 0, 0);
    ctx.restore();
  }, [samples, pve, pcX, pcY, width, height, title, hoveredSample, plotWidth, plotHeight, pveX, pveY]);

  const handleMouseMove = (e) => {
    const canvas = canvasRef.current;
    if (!canvas || samples.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const xs = samples.map((s) => s.pcs[pcX - 1] || 0);
    const ys = samples.map((s) => s.pcs[pcY - 1] || 0);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);

    const padX = (maxX - minX || 1) * 0.08;
    const padY = (maxY - minY || 1) * 0.08;
    const x0 = minX - padX;
    const x1 = maxX + padX;
    const y0 = minY - padY;
    const y1 = maxY + padY;

    const xScale = (val) => MARGIN.left + ((val - x0) / (x1 - x0 || 1)) * plotWidth;
    const yScale = (val) => MARGIN.top + plotHeight - ((val - y0) / (y1 - y0 || 1)) * plotHeight;

    let closestIdx = null;
    let closestDist = 10;
    for (let i = 0; i < samples.length; i++) {
      const px = xScale(samples[i].pcs[pcX - 1] || 0);
      const py = yScale(samples[i].pcs[pcY - 1] || 0);
      const d = Math.hypot(mx - px, my - py);
      if (d < closestDist) {
        closestDist = d;
        closestIdx = i;
      }
    }
    setHoveredSample(closestIdx);
  };

  return (
    <div style={{ position: "relative", width, height }}>
      <canvas
        ref={canvasRef}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoveredSample(null)}
        style={{ borderRadius: 8, border: "1px solid #e2e8f0" }}
      />
      {hoveredSample !== null && samples[hoveredSample] && (
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
          <strong>{samples[hoveredSample].iid}</strong> (Cluster {samples[hoveredSample].cluster + 1})
          <br />
          PC{pcX}: {samples[hoveredSample].pcs[pcX - 1]?.toFixed(3)} | PC{pcY}:{" "}
          {samples[hoveredSample].pcs[pcY - 1]?.toFixed(3)}
        </div>
      )}
    </div>
  );
}
