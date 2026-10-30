import React, { useEffect, useRef, useState } from "react";
import { setupHiDpiCanvas, niceTicks } from "../utils/canvas";

const MARGIN = { top: 32, right: 20, bottom: 44, left: 48 };

export function HistogramChart({
  edges = [],
  counts = [],
  title = "Distribusi Frekuensi Fenotipe",
  xLabel = "Nilai Fenotipe",
  _yLabel = "Frekuensi",
  width = 440,
  height = 240,
}) {
  const canvasRef = useRef(null);
  const [hoveredBin, setHoveredBin] = useState(null);

  const plotWidth = width - MARGIN.left - MARGIN.right;
  const plotHeight = height - MARGIN.top - MARGIN.bottom;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || edges.length < 2 || counts.length === 0) return;

    const ctx = setupHiDpiCanvas(canvas, width, height);

    // Clear background
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    const minX = edges[0];
    const maxX = edges[edges.length - 1];
    const maxCount = Math.max(...counts, 1);

    // Coordinate mapping
    const xScale = (val) => MARGIN.left + ((val - minX) / (maxX - minX || 1)) * plotWidth;
    const yScale = (count) => MARGIN.top + plotHeight - (count / maxCount) * plotHeight;

    // Draw Grid & Y-Ticks
    const yTicks = niceTicks(0, maxCount, 4);
    ctx.strokeStyle = "#f1f5f9";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#64748b";
    ctx.font = "11px Inter, system-ui, sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";

    yTicks.forEach((tick) => {
      if (tick > maxCount * 1.1) return;
      const y = yScale(tick);
      ctx.beginPath();
      ctx.moveTo(MARGIN.left, y);
      ctx.lineTo(MARGIN.left + plotWidth, y);
      ctx.stroke();
      ctx.fillText(String(Math.round(tick)), MARGIN.left - 8, y);
    });

    // Draw X-Ticks
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const xTicks = niceTicks(minX, maxX, 5);
    xTicks.forEach((tick) => {
      if (tick < minX || tick > maxX) return;
      const x = xScale(tick);
      ctx.fillText(tick.toFixed(2), x, MARGIN.top + plotHeight + 8);
    });

    // Draw Histogram Bars
    counts.forEach((count, i) => {
      const x0 = xScale(edges[i]);
      const x1 = xScale(edges[i + 1]);
      const barW = Math.max(x1 - x0 - 1.5, 1);
      const y = yScale(count);
      const barH = MARGIN.top + plotHeight - y;

      const isHovered = hoveredBin === i;
      ctx.fillStyle = isHovered ? "#1d4ed8" : "#3b82f6";
      ctx.fillRect(x0, y, barW, barH);

      ctx.strokeStyle = isHovered ? "#1e40af" : "#2563eb";
      ctx.lineWidth = 1;
      ctx.strokeRect(x0, y, barW, barH);
    });

    // Draw Axes
    ctx.strokeStyle = "#cbd5e1";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(MARGIN.left, MARGIN.top);
    ctx.lineTo(MARGIN.left, MARGIN.top + plotHeight);
    ctx.lineTo(MARGIN.left + plotWidth, MARGIN.top + plotHeight);
    ctx.stroke();

    // Title & Labels
    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 12px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(title, MARGIN.left, 16);

    ctx.fillStyle = "#64748b";
    ctx.font = "11px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(xLabel, MARGIN.left + plotWidth / 2, height - 8);
  }, [edges, counts, width, height, hoveredBin, title, xLabel, plotWidth, plotHeight]);

  const handleMouseMove = (e) => {
    const canvas = canvasRef.current;
    if (!canvas || edges.length < 2) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    if (
      mx < MARGIN.left ||
      mx > MARGIN.left + plotWidth ||
      my < MARGIN.top ||
      my > MARGIN.top + plotHeight
    ) {
      setHoveredBin(null);
      return;
    }

    const minX = edges[0];
    const maxX = edges[edges.length - 1];
    const val = minX + ((mx - MARGIN.left) / plotWidth) * (maxX - minX);

    for (let i = 0; i < counts.length; i++) {
      if (val >= edges[i] && val <= edges[i + 1]) {
        setHoveredBin(i);
        return;
      }
    }
    setHoveredBin(null);
  };

  return (
    <div style={{ position: "relative", width, height }}>
      <canvas
        ref={canvasRef}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoveredBin(null)}
        style={{ borderRadius: 8, border: "1px solid #e2e8f0" }}
      />
      {hoveredBin !== null && edges[hoveredBin] !== undefined && (
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
          Rentang: [{edges[hoveredBin].toFixed(2)} - {edges[hoveredBin + 1].toFixed(2)}] | Frekuensi:{" "}
          <strong>{counts[hoveredBin]}</strong>
        </div>
      )}
    </div>
  );
}
