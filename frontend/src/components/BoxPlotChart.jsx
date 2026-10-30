import React, { useEffect, useRef, useState } from "react";
import { setupHiDpiCanvas, niceTicks } from "../utils/canvas";

const MARGIN = { top: 32, right: 24, bottom: 44, left: 24 };

export function BoxPlotChart({
  data = {},
  title = "Boxplot & Outlier Fenotipe",
  xLabel = "Nilai Fenotipe",
  width = 380,
  height = 240,
}) {
  const canvasRef = useRef(null);
  const [hoveredOutlier, setHoveredOutlier] = useState(null);

  const {
    q1 = 0,
    median = 0,
    q3 = 0,
    whisker_low = 0,
    whisker_high = 0,
    outliers = [],
  } = data || {};

  const plotWidth = width - MARGIN.left - MARGIN.right;
  const plotHeight = height - MARGIN.top - MARGIN.bottom;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const allVals = [whisker_low, q1, median, q3, whisker_high, ...outliers.map((o) => o.value)];
    if (allVals.length === 0) return;
    const minVal = Math.min(...allVals);
    const maxVal = Math.max(...allVals);
    if (isNaN(minVal)) return;

    const ctx = setupHiDpiCanvas(canvas, width, height);

    // Clear background
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    // Coordinate mapping along horizontal X axis
    const padding = (maxVal - minVal || 1) * 0.08;
    const xMin = minVal - padding;
    const xMax = maxVal + padding;
    const xScale = (val) => MARGIN.left + ((val - xMin) / (xMax - xMin || 1)) * plotWidth;

    const centerY = MARGIN.top + plotHeight / 2;
    const boxHeight = 52;
    const topY = centerY - boxHeight / 2;
    const bottomY = centerY + boxHeight / 2;

    // Draw X-Ticks & Grid
    const xTicks = niceTicks(xMin, xMax, 5);
    ctx.strokeStyle = "#f1f5f9";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#64748b";
    ctx.font = "11px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";

    xTicks.forEach((tick) => {
      const x = xScale(tick);
      ctx.beginPath();
      ctx.moveTo(x, MARGIN.top);
      ctx.lineTo(x, MARGIN.top + plotHeight);
      ctx.stroke();
      ctx.fillText(tick.toFixed(2), x, MARGIN.top + plotHeight + 8);
    });

    // Whiskers (horizontal line from whisker_low to whisker_high)
    ctx.strokeStyle = "#334155";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(xScale(whisker_low), centerY);
    ctx.lineTo(xScale(q1), centerY);
    ctx.moveTo(xScale(q3), centerY);
    ctx.lineTo(xScale(whisker_high), centerY);
    ctx.stroke();

    // Whisker end caps
    const capH = 16;
    ctx.beginPath();
    ctx.moveTo(xScale(whisker_low), centerY - capH / 2);
    ctx.lineTo(xScale(whisker_low), centerY + capH / 2);
    ctx.moveTo(xScale(whisker_high), centerY - capH / 2);
    ctx.lineTo(xScale(whisker_high), centerY + capH / 2);
    ctx.stroke();

    // Box (IQR from Q1 to Q3)
    const xQ1 = xScale(q1);
    const xQ3 = xScale(q3);
    const boxW = Math.max(xQ3 - xQ1, 2);

    ctx.fillStyle = "#e0f2fe";
    ctx.fillRect(xQ1, topY, boxW, boxHeight);
    ctx.strokeStyle = "#0284c7";
    ctx.lineWidth = 2;
    ctx.strokeRect(xQ1, topY, boxW, boxHeight);

    // Median vertical line
    const xMedian = xScale(median);
    ctx.strokeStyle = "#0369a1";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(xMedian, topY);
    ctx.lineTo(xMedian, bottomY);
    ctx.stroke();

    // Draw Outliers
    outliers.forEach((outlier, idx) => {
      const ox = xScale(outlier.value);
      const isHovered = hoveredOutlier === idx;
      ctx.beginPath();
      ctx.arc(ox, centerY, isHovered ? 6 : 4, 0, Math.PI * 2);
      ctx.fillStyle = isHovered ? "#ef4444" : "#dc2626";
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    });

    // Title & Axis
    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 12px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(title, MARGIN.left, 16);

    ctx.fillStyle = "#64748b";
    ctx.font = "11px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(xLabel, MARGIN.left + plotWidth / 2, height - 8);
  }, [
    data,
    width,
    height,
    hoveredOutlier,
    title,
    xLabel,
    median,
    q1,
    q3,
    whisker_high,
    whisker_low,
    outliers,
    plotHeight,
    plotWidth,
  ]);

  const handleMouseMove = (e) => {
    const canvas = canvasRef.current;
    if (!canvas || outliers.length === 0) return;
    const allVals = [whisker_low, q1, median, q3, whisker_high, ...outliers.map((o) => o.value)];
    const minVal = Math.min(...allVals);
    const maxVal = Math.max(...allVals);

    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const padding = (maxVal - minVal || 1) * 0.08;
    const xMin = minVal - padding;
    const xMax = maxVal + padding;
    const xScale = (val) => MARGIN.left + ((val - xMin) / (xMax - xMin || 1)) * plotWidth;
    const centerY = MARGIN.top + plotHeight / 2;

    let matchedIdx = null;
    for (let i = 0; i < outliers.length; i++) {
      const ox = xScale(outliers[i].value);
      const dist = Math.hypot(mx - ox, my - centerY);
      if (dist <= 8) {
        matchedIdx = i;
        break;
      }
    }
    setHoveredOutlier(matchedIdx);
  };

  return (
    <div style={{ position: "relative", width, height }}>
      <canvas
        ref={canvasRef}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoveredOutlier(null)}
        style={{ borderRadius: 8, border: "1px solid #e2e8f0" }}
      />
      {hoveredOutlier !== null && outliers[hoveredOutlier] && (
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
          Outlier: <strong>{outliers[hoveredOutlier].iid}</strong> (Nilai:{" "}
          {outliers[hoveredOutlier].value})
        </div>
      )}
    </div>
  );
}
