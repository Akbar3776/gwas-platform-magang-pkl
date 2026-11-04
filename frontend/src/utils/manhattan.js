/**
 * Utilities untuk mengubah data mentah (CHR, BP, SNP, LOG10_P) menjadi
 * bentuk yang siap dirender sebagai Manhattan Plot.
 *
 * Kunci dari Manhattan Plot yang benar: posisi sumbu-X BUKAN nilai BP
 * mentah, karena setiap kromosom punya rentang BP yang mulai dari 0/1
 * lagi. Kita perlu "genomic cumulative position" -- menjumlahkan offset
 * dari kromosom-kromosom sebelumnya -- supaya titik tersusun berurutan
 * dari kiri (kromosom 1) ke kanan (kromosom terakhir) tanpa tumpang tindih.
 */

export const GENOME_WIDE_SIGNIFICANCE = 7.3; // -log10(5e-8), ambang umum GWAS

/**
 * Mengubah nilai ambang signifikansi dari p-value biasa (mis. 5e-8) ke skala -log10(p) (mis. 7.3).
 * Bila nilai sudah >= 1, dianggap sudah dalam skala -log10(p).
 */
export function toLog10Threshold(val) {
  if (val === null || val === undefined) return GENOME_WIDE_SIGNIFICANCE;
  const num = Number(val);
  if (isNaN(num) || num <= 0) return GENOME_WIDE_SIGNIFICANCE;
  if (num < 1) {
    return Math.min(Math.max(-Math.log10(num), 1), 50);
  }
  return num;
}

/**
 * @param {{chr:number[], bp:number[], snp:string[], log10_p:number[]}} rawData
 * @returns {{
 *   points: Array<{x:number, y:number, chr:number|string, bp:number, snp:string}>,
 *   chromosomeTicks: Array<{chr:number|string, x:number}>,
 *   totalGenomeLength: number
 * }}
 */
export function buildManhattanLayout(rawData) {
  const { chr, bp, snp, log10_p: log10p } = rawData || {};
  if (!chr || !bp || !snp || !log10p || chr.length === 0) {
    return { points: [], chromosomeTicks: [], totalGenomeLength: 1, chromosomeBands: [] };
  }
  const n = chr.length;

  // Hitung BP minimum dan maksimum per kromosom dalam satu pass (O(n))
  const maxBpByChr = new Map();
  const minBpByChr = new Map();
  for (let i = 0; i < n; i++) {
    const c = chr[i];
    const pos = Number(bp[i]) || 0;
    const curMax = maxBpByChr.get(c);
    if (curMax === undefined || pos > curMax) maxBpByChr.set(c, pos);
    const curMin = minBpByChr.get(c);
    if (curMin === undefined || pos < curMin) minBpByChr.set(c, pos);
  }

  const chromosomes = Array.from(maxBpByChr.keys()).sort((a, b) => {
    const numA = Number(a);
    const numB = Number(b);
    if (!isNaN(numA) && !isNaN(numB)) return numA - numB;
    return String(a).localeCompare(String(b), undefined, { numeric: true });
  });

  const numChrs = chromosomes.length;
  let totalRawSpan = 0;
  for (const c of chromosomes) {
    totalRawSpan += Math.max(maxBpByChr.get(c) || 0, 1);
  }
  totalRawSpan = Math.max(totalRawSpan, 1);

  // Pastikan setiap kromosom mendapatkan lebar visual minimum (mis. minimal ~2.8% lebar plot)
  // agar kromosom/scaffold berukuran kecil (mis. Chr 9..22 pada genom tanaman besar)
  // tidak menumpuk di satu titik di pojok kanan setelah kromosom besar (Chr 1..8).
  const minChrShare = numChrs > 1 ? Math.min(0.032, 0.62 / numChrs) : 1.0;
  const rawShares = chromosomes.map((c) => Math.max(maxBpByChr.get(c) || 0, 1) / totalRawSpan);

  let smallCount = 0;
  let largeRawSum = 0;
  for (let i = 0; i < numChrs; i++) {
    if (rawShares[i] < minChrShare) {
      smallCount += 1;
    } else {
      largeRawSum += rawShares[i];
    }
  }

  const reservedForSmall = smallCount * minChrShare;
  const remainingForLarge = Math.max(1.0 - reservedForSmall, 0.1);

  const visualWidthByChr = new Map();
  for (let i = 0; i < numChrs; i++) {
    const c = chromosomes[i];
    let share;
    if (numChrs === 1) {
      share = 1.0;
    } else if (rawShares[i] < minChrShare) {
      share = minChrShare;
    } else {
      share = largeRawSum > 0 ? (rawShares[i] / largeRawSum) * remainingForLarge : 1.0 / numChrs;
    }
    visualWidthByChr.set(c, share * totalRawSpan);
  }

  const gapWidth = numChrs > 1 ? totalRawSpan * 0.0035 : 0;
  const offsetByChr = new Map();
  const chromosomeTicks = [];
  const chromosomeBands = [];
  let cumulativeOffset = 0;

  for (let idx = 0; idx < numChrs; idx++) {
    const c = chromosomes[idx];
    const width = visualWidthByChr.get(c) || 1;
    offsetByChr.set(c, cumulativeOffset);
    chromosomeTicks.push({ chr: c, x: cumulativeOffset + width / 2 });
    chromosomeBands.push({
      chr: c,
      index: idx,
      startX: cumulativeOffset,
      endX: cumulativeOffset + width,
    });
    cumulativeOffset += width + (idx < numChrs - 1 ? gapWidth : 0);
  }

  const totalGenomeLength = Math.max(cumulativeOffset, 1);

  const points = new Array(n);
  for (let i = 0; i < n; i++) {
    const c = chr[i];
    const pos = Number(bp[i]) || 0;
    const offset = offsetByChr.get(c) ?? 0;
    const vWidth = visualWidthByChr.get(c) || 1;
    const maxBp = Math.max(maxBpByChr.get(c) || 0, 1);
    const minBp = minBpByChr.get(c) ?? 0;

    let relPos;
    if (maxBp <= minBp) {
      relPos = 0.5;
    } else if (numChrs > 1) {
      // Beri margin dalam 6% kiri-kanan pada tiap kolom kromosom agar titik antar kromosom tidak saling menempel
      relPos = 0.06 + 0.88 * Math.min(Math.max(pos / maxBp, 0), 1);
    } else {
      relPos = Math.min(Math.max(pos / maxBp, 0), 1);
    }

    points[i] = {
      x: offset + relPos * vWidth,
      y: Number(log10p[i]) || 0,
      chr: c,
      bp: pos,
      snp: snp[i] || "",
    };
  }

  return { points, chromosomeTicks, totalGenomeLength, chromosomeBands };
}

/** Konversi -log10(p) kembali menjadi p-value untuk ditampilkan di tooltip. */
export function log10ToPValue(log10p) {
  const p = Math.pow(10, -log10p);
  if (p < 0.0001) return p.toExponential(2);
  return p.toFixed(4);
}
