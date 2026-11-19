import { useEffect, useMemo, useRef, useState } from "react";
import { buildQQPoints, getQQAxisMax } from "../utils/qqplot";

const COLOR_POINTS = "#2563eb";
const COLOR_REFERENCE_LINE = "#94a3b8";

const CANVAS_W = 680;
const CANVAS_H = 420;
const PAD_LEFT = 58;
const PAD_RIGHT = 24;
const PAD_TOP = 24;
const PAD_BOTTOM = 50;
const PLOT_W = CANVAS_W - PAD_LEFT - PAD_RIGHT;
const PLOT_H = CANVAS_H - PAD_TOP - PAD_BOTTOM;

function inverseNormalUpperTail(q) {
  // Persamaan Peter J. Acklam untuk kuantil distribusi Normal standar
  const pLow = 0.02425;
  const pHigh = 1 - pLow;
  const a = [-39.6968302866538, 220.946098424521, -275.928510446969, 138.357751867269, -30.6647980661472, 2.50662827745924];
  const b = [-54.4760987982241, 161.585836858041, -155.698979859887, 66.8013118877197, -13.2806815528857];
  const c = [-0.00778489400243029, -0.322396458041136, -2.40075827716184, -2.54973253934373, 4.37466414146497, 2.93816398269878];
  const d = [0.00778469570904146, 0.32246712907004, 2.445134137143, 3.75440866190742];
  if (q < pLow) {
    const r = Math.sqrt(-2 * Math.log(q));
    return (((((c[0] * r + c[1]) * r + c[2]) * r + c[3]) * r + c[4]) * r + c[5]) /
      ((((d[0] * r + d[1]) * r + d[2]) * r + d[3]) * r + 1);
  }
  if (q <= pHigh) {
    const r = q - 0.5;
    const s = r * r;
    return ((((((a[0] * s + a[1]) * s + a[2]) * s + a[3]) * s + a[4]) * s + a[5]) * r) /
      (((((b[0] * s + b[1]) * s + b[2]) * s + b[3]) * s + b[4]) * s + 1);
  }
  const r = Math.sqrt(-2 * Math.log(1 - q));
  return -(((((c[0] * r + c[1]) * r + c[2]) * r + c[3]) * r + c[4]) * r + c[5]) /
    ((((d[0] * r + d[1]) * r + d[2]) * r + d[3]) * r + 1);
}

function estimateLambdaGc(log10pValues) {
  if (!log10pValues || log10pValues.length === 0) return null;
  const sorted = Float32Array.from(log10pValues).sort();
  const mid = Math.floor(sorted.length / 2);
  const medianLogP =
    sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  const medianP = Math.min(Math.max(Math.pow(10, -medianLogP), 1e-300), 1);
  const z = inverseNormalUpperTail(1 - medianP / 2);
  return (z * z) / 0.4549364;
}

/**
 * High-performance Canvas QQ Plot component.
 * Renders observed vs expected -log10(p) on HTML5 Canvas without DOM node overhead.
 */
export default function QQPlot({ data }) {
  const canvasRef = useRef(null);
  const [hovered, setHovered] = useState(null);

  const { plottedPoints, axisMax, ticks, summaryLabel } = useMemo(() => {
    const rawValues = Array.isArray(data?.log10_p) ? data.log10_p : [];
    const rawPoints = buildQQPoints(rawValues, 2500);
    const maxVal = Math.max(getQQAxisMax(rawPoints), 4);
    const step = maxVal <= 8 ? 1 : Math.ceil(maxVal / 5);
    const tickList = [];
    for (let v = 0; v <= maxVal; v += step) {
      tickList.push(v);
    }

    const mapped = rawPoints.map((p) => ({
      ...p,
      cx: PAD_LEFT + (Math.min(Math.max(p.x, 0), maxVal) / maxVal) * PLOT_W,
      cy:
        PAD_TOP +
        PLOT_H -
        (Math.min(Math.max(p.y, 0), maxVal) / maxVal) * PLOT_H,
    }));

    const maxObs = rawPoints.length > 0 ? Math.max(...rawPoints.map((p) => p.y)) : 0;
    const lambdaVal =
      typeof data?.lambda_gc === "number" ? data.lambda_gc : estimateLambdaGc(rawValues);
    const lambdaStr =
      typeof lambdaVal === "number" && Number.isFinite(lambdaVal)
        ? lambdaVal.toFixed(2)
        : "N/A";
    const label = `QQ Plot GWAS (${rawValues.length.toLocaleString("id-ID")} SNP, lambda ${lambdaStr}, maks -log10(p) ${maxObs.toFixed(2)})`;

    return { plottedPoints: mapped, axisMax: maxVal, ticks: tickList, summaryLabel: label };
  }, [data]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = CANVAS_W * dpr;
    canvas.height = CANVAS_H * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.save();
    ctx.scale(dpr, dpr);

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);

    const toX = (xVal) =>
      PAD_LEFT + (Math.min(Math.max(xVal, 0), axisMax) / axisMax) * PLOT_W;
    const toY = (yVal) =>
      PAD_TOP +
      PLOT_H -
      (Math.min(Math.max(yVal, 0), axisMax) / axisMax) * PLOT_H;

    // Grid & ticks
    ctx.font = "11px system-ui, -apple-system, sans-serif";
    for (const t of ticks) {
      const x = toX(t);
      const y = toY(t);
      ctx.strokeStyle = "#f1f5f9";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(PAD_LEFT, y);
      ctx.lineTo(CANVAS_W - PAD_RIGHT, y);
      ctx.moveTo(x, PAD_TOP);
      ctx.lineTo(x, PAD_TOP + PLOT_H);
      ctx.stroke();

      ctx.fillStyle = "#64748b";
      ctx.textAlign = "right";
      ctx.fillText(String(t), PAD_LEFT - 8, y + 4);
      ctx.textAlign = "center";
      ctx.fillText(String(t), x, PAD_TOP + PLOT_H + 18);
    }

    // Axes
    ctx.strokeStyle = "#cbd5e1";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(PAD_LEFT, PAD_TOP);
    ctx.lineTo(PAD_LEFT, PAD_TOP + PLOT_H);
    ctx.lineTo(CANVAS_W - PAD_RIGHT, PAD_TOP + PLOT_H);
    ctx.stroke();

    // Diagonal y = x line
    ctx.save();
    ctx.strokeStyle = COLOR_REFERENCE_LINE;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(toX(0), toY(0));
    ctx.lineTo(toX(axisMax), toY(axisMax));
    ctx.stroke();
    ctx.restore();

    // Axis titles
    ctx.fillStyle = "#334155";
    ctx.font = "600 12px system-ui, -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Expected -log10(p)", PAD_LEFT + PLOT_W / 2, CANVAS_H - 10);

    ctx.save();
    ctx.translate(16, PAD_TOP + PLOT_H / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("Observed -log10(p)", 0, 0);
    ctx.restore();

    // Scatter points in single path batch
    ctx.fillStyle = COLOR_POINTS;
    ctx.globalAlpha = 0.82;
    ctx.beginPath();
    for (let i = 0; i < plottedPoints.length; i += 1) {
      const pt = plottedPoints[i];
      ctx.moveTo(pt.cx + 3, pt.cy);
      ctx.arc(pt.cx, pt.cy, 3, 0, Math.PI * 2);
    }
    ctx.fill();

    ctx.restore();
  }, [plottedPoints, axisMax, ticks]);

  const handleMouseMove = (event) => {
    const canvas = canvasRef.current;
    if (!canvas || plottedPoints.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const scaleX = CANVAS_W / rect.width;
    const scaleY = CANVAS_H / rect.height;
    const mx = (event.clientX - rect.left) * scaleX;
    const my = (event.clientY - rect.top) * scaleY;

    let best = null;
    let bestDistSq = 64;
    for (let i = 0; i < plottedPoints.length; i += 1) {
      const pt = plottedPoints[i];
      const dx = pt.cx - mx;
      const dy = pt.cy - my;
      const d2 = dx * dx + dy * dy;
      if (d2 < bestDistSq) {
        bestDistSq = d2;
        best = pt;
      }
    }
    setHovered(best);
  };

  return (
    <div>
      <canvas
        ref={canvasRef}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHovered(null)}
        style={{
          width: "100%",
          maxWidth: 680,
          height: "auto",
          display: "block",
          background: "#ffffff",
          borderRadius: 8,
          border: "1px solid #e2e8f0",
          cursor: hovered ? "pointer" : "crosshair",
        }}
        role="img"
        aria-label={summaryLabel}
      />

      {hovered && (
        <div
          style={{
            marginTop: 8,
            padding: "8px 12px",
            borderRadius: 8,
            background: "#0f172a",
            color: "#f8fafc",
            fontSize: 12,
            display: "inline-flex",
            gap: 16,
          }}
        >
          <span>
            <strong>Expected -log10(p):</strong> {hovered.x.toFixed(3)}
          </span>
          <span>
            <strong>Observed -log10(p):</strong> {hovered.y.toFixed(3)}
          </span>
        </div>
      )}
    </div>
  );
}
