/**
 * Shared canvas utilities for high-DPI scaling, tick calculations, and color palettes.
 */

export const COLOR_PALETTE = [
  "#0072B2", // Blue
  "#E69F00", // Orange
  "#009E73", // Green
  "#D55E00", // Red-orange
  "#CC79A7", // Purple-pink
  "#56B4E9", // Sky blue
  "#F0E442", // Yellow
];

/**
 * Configure canvas for Retina / HiDPI screens so lines and text remain crisp.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {number} cssWidth
 * @param {number} cssHeight
 * @returns {CanvasRenderingContext2D}
 */
export function setupHiDpiCanvas(canvas, cssWidth, cssHeight) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(cssWidth * dpr);
  canvas.height = Math.round(cssHeight * dpr);
  canvas.style.width = `${cssWidth}px`;
  canvas.style.height = `${cssHeight}px`;

  const ctx = canvas.getContext("2d");
  ctx.resetTransform?.();
  ctx.scale(dpr, dpr);
  return ctx;
}

/**
 * Generate human-friendly tick values between min and max.
 *
 * @param {number} min
 * @param {number} max
 * @param {number} targetTicks
 * @returns {number[]}
 */
export function niceTicks(min, max, targetTicks = 5) {
  if (min === max) return [min];
  if (min > max) [min, max] = [max, min];

  const range = max - min;
  const rawStep = range / Math.max(targetTicks, 1);
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const magFraction = rawStep / mag;

  let niceStep = mag;
  if (magFraction > 5) {
    niceStep = 10 * mag;
  } else if (magFraction > 2) {
    niceStep = 5 * mag;
  } else if (magFraction > 1) {
    niceStep = 2 * mag;
  }

  const start = Math.floor(min / niceStep) * niceStep;
  const end = Math.ceil(max / niceStep) * niceStep;
  const ticks = [];
  for (let val = start; val <= end + niceStep * 0.5; val += niceStep) {
    ticks.push(Number(val.toFixed(6)));
  }
  return ticks;
}
