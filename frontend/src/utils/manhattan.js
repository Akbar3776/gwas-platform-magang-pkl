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
 * @param {{chr:number[], bp:number[], snp:string[], log10_p:number[]}} rawData
 * @returns {{
 *   points: Array<{x:number, y:number, chr:number, bp:number, snp:string}>,
 *   chromosomeTicks: Array<{chr:number, x:number}>,
 *   totalGenomeLength: number
 * }}
 */
export function buildManhattanLayout(rawData) {
  const { chr, bp, snp, log10_p: log10p } = rawData;
  const n = chr.length;

  // Hitung BP maksimum per kromosom dalam satu pass (O(n), bukan nested loop)
  const maxBpByChr = new Map();
  for (let i = 0; i < n; i++) {
    const c = chr[i];
    const current = maxBpByChr.get(c) ?? 0;
    if (bp[i] > current) maxBpByChr.set(c, bp[i]);
  }

  const chromosomes = Array.from(maxBpByChr.keys()).sort((a, b) => a - b);

  const offsetByChr = new Map();
  const chromosomeTicks = [];
  let cumulativeOffset = 0;

  for (const c of chromosomes) {
    const width = maxBpByChr.get(c);
    offsetByChr.set(c, cumulativeOffset);
    chromosomeTicks.push({ chr: c, x: cumulativeOffset + width / 2 });
    cumulativeOffset += width;
  }

  const totalGenomeLength = cumulativeOffset;

  const points = new Array(n);
  for (let i = 0; i < n; i++) {
    points[i] = {
      x: offsetByChr.get(chr[i]) + bp[i],
      y: log10p[i],
      chr: chr[i],
      bp: bp[i],
      snp: snp[i],
    };
  }

  return { points, chromosomeTicks, totalGenomeLength };
}

/** Konversi -log10(p) kembali menjadi p-value untuk ditampilkan di tooltip. */
export function log10ToPValue(log10p) {
  const p = Math.pow(10, -log10p);
  if (p < 0.0001) return p.toExponential(2);
  return p.toFixed(4);
}
