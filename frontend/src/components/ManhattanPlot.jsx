import { useEffect, useMemo, useRef, useState } from "react";
import {
  buildManhattanLayout,
  GENOME_WIDE_SIGNIFICANCE,
  log10ToPValue,
  toLog10Threshold,
} from "../utils/manhattan";

const COLOR_ODD = "#2563eb";
const COLOR_EVEN = "#475569";
const COLOR_SIGNIFICANT = "#dc2626";
const COLOR_THRESHOLD = "#e11d48";

const BASE_CANVAS_W = 1320;
const CANVAS_H = 480;
const PAD_LEFT = 58;
const PAD_RIGHT = 28;
const PAD_TOP = 24;
const PAD_BOTTOM = 54;
const PLOT_H = CANVAS_H - PAD_TOP - PAD_BOTTOM;

/**
 * High-performance Canvas Manhattan Plot component.
 * Uses 2D pixel-bucket decimation + HTML5 Canvas so datasets with hundreds of
 * thousands of SNPs render in < 5ms without freezing the browser DOM.
 */
export default function ManhattanPlot({
  data,
  significanceThreshold = GENOME_WIDE_SIGNIFICANCE,
}) {
  const canvasRef = useRef(null);
  const [selectedChr, setSelectedChr] = useState("ALL");
  const [threshold, setThreshold] = useState(() => toLog10Threshold(significanceThreshold));
  const [zoomLevel, setZoomLevel] = useState(1);
  const [hoveredPoint, setHoveredPoint] = useState(null);

  const canvasW = Math.round(BASE_CANVAS_W * zoomLevel);
  const plotW = canvasW - PAD_LEFT - PAD_RIGHT;

  // Sinkronkan threshold ketika nilai ambang pasca-GWAS dari parent berubah
  useEffect(() => {
    setThreshold(toLog10Threshold(significanceThreshold));
  }, [significanceThreshold]);

  const availableChromosomes = useMemo(() => {
    return Array.from(new Set(data?.chr || [])).sort((a, b) => {
      const numA = Number(a);
      const numB = Number(b);
      if (!isNaN(numA) && !isNaN(numB)) return numA - numB;
      return String(a).localeCompare(String(b), undefined, { numeric: true });
    });
  }, [data?.chr]);

  const filteredData = useMemo(() => {
    if (!data?.chr) return { chr: [], bp: [], snp: [], log10_p: [] };
    if (selectedChr === "ALL") return data;
    const targetStr = String(selectedChr);
    const chr = [];
    const bp = [];
    const snp = [];
    const log10_p = [];
    for (let i = 0; i < data.chr.length; i += 1) {
      if (String(data.chr[i]) === targetStr) {
        chr.push(data.chr[i]);
        bp.push(data.bp[i]);
        snp.push(data.snp[i]);
        log10_p.push(data.log10_p[i]);
      }
    }
    return { chr, bp, snp, log10_p };
  }, [data, selectedChr]);

  const layout = useMemo(() => {
    const { points, chromosomeTicks, totalGenomeLength, chromosomeBands = [] } =
      buildManhattanLayout(filteredData);
    const safeLen = Math.max(totalGenomeLength, 1);

    const chrOrderMap = new Map();
    for (let i = 0; i < chromosomeBands.length; i += 1) {
      chrOrderMap.set(chromosomeBands[i].chr, i);
    }

    let maxY = threshold;
    for (let i = 0; i < points.length; i += 1) {
      if (points[i].y > maxY) maxY = points[i].y;
    }
    const yAxisMax = Math.max(Math.ceil(maxY) + 1, 8);

    // 2D pixel-bucket deduplication: eliminates redundant overlapping points
    // that map to the exact same 2x2 pixel cell while preserving 100% of peaks
    const seenBuckets = new Set();
    const decimated = [];
    for (let i = 0; i < points.length; i += 1) {
      const p = points[i];
      const cx = PAD_LEFT + (p.x / safeLen) * plotW;
      const cy =
        PAD_TOP +
        PLOT_H -
        (Math.min(Math.max(p.y, 0), yAxisMax) / yAxisMax) * PLOT_H;
      const isSig = p.y >= threshold;

      if (!isSig && points.length > 6000) {
        const bx = Math.round(cx / 2);
        const by = Math.round(cy / 2);
        const bucketKey = (bx << 16) | by;
        if (seenBuckets.has(bucketKey)) {
          continue;
        }
        seenBuckets.add(bucketKey);
      }

      decimated.push({
        ...p,
        cx,
        cy,
        isSig,
        chrIdx: chrOrderMap.get(p.chr) ?? (Number(p.chr) || 0),
      });
    }

    return {
      points: decimated,
      rawCount: points.length,
      chromosomeTicks,
      chromosomeBands,
      totalGenomeLength: safeLen,
      yAxisMax,
    };
  }, [filteredData, threshold, plotW]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = canvasW * dpr;
    canvas.height = CANVAS_H * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.save();
    ctx.scale(dpr, dpr);

    // Background
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvasW, CANVAS_H);

    const toX = (xVal) => PAD_LEFT + (xVal / layout.totalGenomeLength) * plotW;
    const toY = (yVal) =>
      PAD_TOP +
      PLOT_H -
      (Math.min(Math.max(yVal, 0), layout.yAxisMax) / layout.yAxisMax) * PLOT_H;

    // Alternating vertical chromosome background bands for visual clarity
    if (layout.chromosomeBands.length > 1) {
      for (let i = 0; i < layout.chromosomeBands.length; i += 1) {
        const band = layout.chromosomeBands[i];
        if (i % 2 === 1) {
          const x0 = toX(band.startX);
          const x1 = toX(band.endX);
          ctx.fillStyle = "#f8fafc";
          ctx.fillRect(x0, PAD_TOP, Math.max(x1 - x0, 1), PLOT_H);
        }
      }
    }

    // Grid & Y-axis ticks
    const step = layout.yAxisMax <= 10 ? 2 : Math.ceil(layout.yAxisMax / 5);
    ctx.font = "11px system-ui, -apple-system, sans-serif";
    ctx.textAlign = "right";
    for (let v = 0; v <= layout.yAxisMax; v += step) {
      const y = toY(v);
      ctx.strokeStyle = "#f1f5f9";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(PAD_LEFT, y);
      ctx.lineTo(canvasW - PAD_RIGHT, y);
      ctx.stroke();

      ctx.fillStyle = "#64748b";
      ctx.fillText(String(v), PAD_LEFT - 8, y + 4);
    }

    // Axes
    ctx.strokeStyle = "#cbd5e1";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(PAD_LEFT, PAD_TOP);
    ctx.lineTo(PAD_LEFT, PAD_TOP + PLOT_H);
    ctx.lineTo(canvasW - PAD_RIGHT, PAD_TOP + PLOT_H);
    ctx.stroke();

    // Significance threshold line
    const threshY = toY(threshold);
    ctx.save();
    ctx.strokeStyle = COLOR_THRESHOLD;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(PAD_LEFT, threshY);
    ctx.lineTo(canvasW - PAD_RIGHT, threshY);
    ctx.stroke();
    ctx.restore();

    // X-axis chromosome ticks
    ctx.textAlign = "center";
    ctx.fillStyle = "#334155";
    ctx.font = "600 10.5px system-ui, -apple-system, sans-serif";
    const ticksToDraw =
      layout.chromosomeTicks.length <= 36
        ? layout.chromosomeTicks
        : layout.chromosomeTicks.filter(
            (_, idx) => idx % Math.ceil(layout.chromosomeTicks.length / 30) === 0
          );
    for (const tick of ticksToDraw) {
      const tx = toX(tick.x);
      ctx.strokeStyle = "#cbd5e1";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(tx, PAD_TOP + PLOT_H);
      ctx.lineTo(tx, PAD_TOP + PLOT_H + 5);
      ctx.stroke();
      ctx.fillText(String(tick.chr), tx, PAD_TOP + PLOT_H + 18);
    }

    // Axis titles
    ctx.fillStyle = "#334155";
    ctx.font = "600 12px system-ui, -apple-system, sans-serif";
    ctx.fillText("Kromosom", PAD_LEFT + plotW / 2, CANVAS_H - 10);

    ctx.save();
    ctx.translate(16, PAD_TOP + PLOT_H / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("-log10(p)", 0, 0);
    ctx.restore();

    // Batch-render points by color for maximum Canvas GPU throughput
    const groups = [
      { color: COLOR_ODD, radius: 2.9, alpha: 0.82, items: [] },
      { color: COLOR_EVEN, radius: 2.9, alpha: 0.82, items: [] },
      { color: COLOR_SIGNIFICANT, radius: 4.3, alpha: 0.96, items: [] },
    ];

    for (let i = 0; i < layout.points.length; i += 1) {
      const pt = layout.points[i];
      if (pt.isSig) {
        groups[2].items.push(pt);
      } else if (pt.chrIdx % 2 === 0) {
        groups[0].items.push(pt);
      } else {
        groups[1].items.push(pt);
      }
    }

    for (const g of groups) {
      if (g.items.length === 0) continue;
      ctx.fillStyle = g.color;
      ctx.globalAlpha = g.alpha;
      ctx.beginPath();
      for (let i = 0; i < g.items.length; i += 1) {
        const pt = g.items[i];
        ctx.moveTo(pt.cx + g.radius, pt.cy);
        ctx.arc(pt.cx, pt.cy, g.radius, 0, Math.PI * 2);
      }
      ctx.fill();
    }

    ctx.restore();
  }, [layout, threshold, canvasW, plotW]);

  const handleMouseMove = (event) => {
    const canvas = canvasRef.current;
    if (!canvas || layout.points.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvasW / rect.width;
    const scaleY = CANVAS_H / rect.height;
    const mx = (event.clientX - rect.left) * scaleX;
    const my = (event.clientY - rect.top) * scaleY;

    let best = null;
    let bestDistSq = 64; // 8px hover radius
    for (let i = 0; i < layout.points.length; i += 1) {
      const pt = layout.points[i];
      const dx = pt.cx - mx;
      const dy = pt.cy - my;
      const d2 = dx * dx + dy * dy;
      if (d2 < bestDistSq) {
        bestDistSq = d2;
        best = pt;
      }
    }
    setHoveredPoint((prev) => (prev?.snp === best?.snp ? prev : best));
  };

  return (
    <div style={{ position: "relative" }}>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 16,
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
          fontSize: 13,
          color: "#334155",
        }}
      >
        <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center" }}>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <strong>Filter Kromosom:</strong>
            <select
              value={selectedChr}
              onChange={(e) => setSelectedChr(e.target.value)}
              style={{
                padding: "4px 8px",
                borderRadius: 6,
                border: "1px solid #cbd5e1",
                background: "#fff",
              }}
            >
              <option value="ALL">Semua Kromosom ({availableChromosomes.length})</option>
              {availableChromosomes.map((c) => (
                <option key={c} value={String(c)}>
                  Kromosom {c}
                </option>
              ))}
            </select>
          </label>

          <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <strong>Lebar Plot:</strong>
            {[
              { val: 1, label: "1x (Penuh)" },
              { val: 1.5, label: "1.5x (Lebar)" },
              { val: 2, label: "2x (Detail)" },
            ].map((opt) => (
              <button
                key={opt.val}
                type="button"
                onClick={() => setZoomLevel(opt.val)}
                style={{
                  padding: "3px 8px",
                  fontSize: 12,
                  borderRadius: 5,
                  border: "1px solid",
                  borderColor: zoomLevel === opt.val ? "#2563eb" : "#cbd5e1",
                  background: zoomLevel === opt.val ? "#eff6ff" : "#fff",
                  color: zoomLevel === opt.val ? "#1d4ed8" : "#475569",
                  fontWeight: zoomLevel === opt.val ? 700 : 500,
                  cursor: "pointer",
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <label style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <strong>
            Ambang -log10(p): {Number(threshold).toFixed(2)}{" "}
            <span style={{ fontWeight: 400, color: "#64748b" }}>
              (p ≈ {Math.pow(10, -threshold) < 0.001 ? Math.pow(10, -threshold).toExponential(2) : Math.pow(10, -threshold).toFixed(3)})
            </span>
          </strong>
          <input
            type="range"
            min="2"
            max="15"
            step="0.1"
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
          />
        </label>
      </div>

      <div
        style={{
          width: "100%",
          overflowX: zoomLevel > 1 ? "auto" : "hidden",
          borderRadius: 8,
          border: "1px solid #e2e8f0",
          background: "#ffffff",
        }}
      >
        <canvas
          ref={canvasRef}
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHoveredPoint(null)}
          style={{
            width: zoomLevel > 1 ? `${Math.round(zoomLevel * 100)}%` : "100%",
            minWidth: zoomLevel > 1 ? `${canvasW}px` : undefined,
            height: "auto",
            display: "block",
            background: "#ffffff",
            cursor: hoveredPoint ? "pointer" : "crosshair",
          }}
          role="img"
          aria-label="Manhattan Plot"
        />
      </div>

      {hoveredPoint && (
        <div
          style={{
            marginTop: 8,
            padding: "8px 12px",
            borderRadius: 8,
            background: "#0f172a",
            color: "#f8fafc",
            fontSize: 12,
            display: "inline-flex",
            flexWrap: "wrap",
            gap: 16,
          }}
        >
          <span>
            <strong>SNP:</strong> {hoveredPoint.snp}
          </span>
          <span>
            <strong>Kromosom:</strong> {hoveredPoint.chr} (
            {hoveredPoint.bp.toLocaleString("id-ID")} bp)
          </span>
          <span>
            <strong>-log10(p):</strong> {hoveredPoint.y.toFixed(3)}
          </span>
          <span>
            <strong>p-value:</strong> {log10ToPValue(hoveredPoint.y)}
          </span>
        </div>
      )}
    </div>
  );
}
