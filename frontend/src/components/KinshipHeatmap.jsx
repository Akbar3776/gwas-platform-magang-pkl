import React, { useEffect, useRef, useState } from "react";
import { setupHiDpiCanvas } from "../utils/canvas";

const EMPTY_MATRIX = [];
const EMPTY_ORDER = [];

export function KinshipHeatmap({
  data,
  width = 380,
  height = 340,
  title = "Matriks Kekerabatan (Kinship Heatmap)",
}) {
  const canvasRef = useRef(null);
  const [hoveredCell, setHoveredCell] = useState(null);

  const matrix = data?.matrix || EMPTY_MATRIX;
  const order = data?.order || EMPTY_ORDER;
  const n = matrix.length;

  const margin = { top: 32, right: 20, bottom: 20, left: 20 };
  const heatSize = Math.min(width - margin.left - margin.right, height - margin.top - margin.bottom);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || n === 0) return;

    const ctx = setupHiDpiCanvas(canvas, width, height);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    // Title
    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 12px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(title, margin.left, 18);

    const cellSize = heatSize / n;

    // Draw Heatmap Cells
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const val = matrix[i][j];
        // Colormap: val from -0.1 to 1.0
        // Normalized t between 0 and 1
        const t = Math.max(0, Math.min(1, (val + 0.1) / 1.1));

        // Color interpolation: Dark Blue (low) -> White (medium) -> Dark Red (high)
        let r, g, b;
        if (t < 0.5) {
          const subT = t / 0.5;
          r = Math.round(30 + subT * 225);
          g = Math.round(60 + subT * 195);
          b = Math.round(180 + subT * 75);
        } else {
          const subT = (t - 0.5) / 0.5;
          r = Math.round(255 - subT * 35);
          g = Math.round(255 - subT * 220);
          b = Math.round(255 - subT * 220);
        }

        ctx.fillStyle = `rgb(${r},${g},${b})`;
        ctx.fillRect(margin.left + j * cellSize, margin.top + i * cellSize, cellSize + 0.5, cellSize + 0.5);
      }
    }

    // Border
    ctx.strokeStyle = "#cbd5e1";
    ctx.lineWidth = 1;
    ctx.strokeRect(margin.left, margin.top, heatSize, heatSize);
  }, [matrix, order, n, width, height, title, heatSize, margin.left, margin.top]);

  const handleMouseMove = (e) => {
    const canvas = canvasRef.current;
    if (!canvas || n === 0) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left - margin.left;
    const my = e.clientY - rect.top - margin.top;

    if (mx >= 0 && mx <= heatSize && my >= 0 && my <= heatSize) {
      const col = Math.floor((mx / heatSize) * n);
      const row = Math.floor((my / heatSize) * n);
      if (row < n && col < n) {
        setHoveredCell({
          row,
          col,
          id1: order[row] || `S${row + 1}`,
          id2: order[col] || `S${col + 1}`,
          value: matrix[row][col],
        });
        return;
      }
    }
    setHoveredCell(null);
  };

  return (
    <div style={{ position: "relative", width, height }}>
      <canvas
        ref={canvasRef}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoveredCell(null)}
        style={{ borderRadius: 8, border: "1px solid #e2e8f0" }}
      />
      {hoveredCell && (
        <div
          style={{
            position: "absolute",
            bottom: 8,
            left: 12,
            padding: "4px 8px",
            background: "rgba(15, 23, 42, 0.85)",
            color: "#fff",
            borderRadius: 6,
            fontSize: 11,
            pointerEvents: "none",
          }}
        >
          {hoveredCell.id1} × {hoveredCell.id2} | Kinship:{" "}
          <strong>{hoveredCell.value?.toFixed(4)}</strong>
        </div>
      )}
    </div>
  );
}
