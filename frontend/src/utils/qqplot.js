/**
 * Perhitungan QQ Plot: membandingkan -log10(p) yang diamati (observed)
 * terhadap -log10(p) yang diharapkan di bawah hipotesis nol (expected),
 * dengan asumsi p-value terdistribusi Uniform(0,1) jika tidak ada asosiasi
 * genetik nyata.
 *
 * Rumus expected quantile standar:
 *   expected_i = -log10( (i - 0.5) / n )   untuk i = 1..n, setelah nilai
 *   observed diurutkan menaik (ascending).
 */

/**
 * @param {number[]} log10pValues - nilai -log10(p) hasil GWAS (sudah didownsample dari backend)
 * @returns {Array<{x:number, y:number}>} pasangan {expected, observed}
 */
export function buildQQPoints(log10pValues) {
  const n = log10pValues.length;
  if (n === 0) return [];

  const observedSorted = [...log10pValues].sort((a, b) => a - b);
  const points = new Array(n);

  for (let i = 0; i < n; i++) {
    const rank = i + 1; // 1-indexed
    const expected = -Math.log10((rank - 0.5) / n);
    points[i] = { x: expected, y: observedSorted[i] };
  }

  return points;
}

/** Nilai maksimum expected & observed, dipakai untuk skala sumbu dan garis referensi y = x. */
export function getQQAxisMax(qqPoints) {
  let max = 0;
  for (const p of qqPoints) {
    if (p.x > max) max = p.x;
    if (p.y > max) max = p.y;
  }
  return Math.ceil(max) + 1;
}
