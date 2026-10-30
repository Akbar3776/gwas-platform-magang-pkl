/**
 * Perhitungan QQ Plot: membandingkan -log10(p) yang diamati (observed)
 * terhadap -log10(p) yang diharapkan di bawah hipotesis nol (expected),
 * dengan asumsi p-value terdistribusi Uniform(0,1) jika tidak ada asosiasi
 * genetik nyata.
 *
 * Agar tidak membebani memori/browser pada dataset besar, titik pada ekor
 * atas (nilai -log10(p) tertinggi) dipertahankan 100%, sementara titik yang
 * menumpuk rapat di dekat nol didownsample secara merata hingga `maxPoints`.
 */

/**
 * @param {number[]} log10pValues - nilai -log10(p) hasil GWAS
 * @param {number} [maxPoints=2500] - batas maksimum titik QQ yang dirender
 * @returns {Array<{x:number, y:number}>} pasangan {expected, observed}
 */
export function buildQQPoints(log10pValues, maxPoints = 2500) {
  const n = log10pValues ? log10pValues.length : 0;
  if (n === 0) return [];

  // Urutkan menurun (descending): indeks 0 = nilai -log10(p) terbesar (p-value terkecil)
  const observedDesc = Float32Array.from(log10pValues).sort().reverse();

  if (n <= maxPoints) {
    const points = new Array(n);
    for (let i = 0; i < n; i++) {
      const rank = i + 1;
      const expected = -Math.log10((rank - 0.5) / n);
      points[i] = { x: expected, y: Number(observedDesc[i]) };
    }
    return points;
  }

  // Pertahankan 100% 500 SNP paling signifikan di ekor atas, dan sampling merata sisanya
  const topKeep = Math.min(500, Math.floor(maxPoints / 2));
  const tailBudget = maxPoints - topKeep;
  const points = [];

  for (let i = 0; i < topKeep; i++) {
    const rank = i + 1;
    const expected = -Math.log10((rank - 0.5) / n);
    points.push({ x: expected, y: Number(observedDesc[i]) });
  }

  const remaining = n - topKeep;
  const step = remaining / tailBudget;
  for (let j = 0; j < tailBudget; j++) {
    const i = topKeep + Math.min(Math.floor(j * step), remaining - 1);
    const rank = i + 1;
    const expected = -Math.log10((rank - 0.5) / n);
    points.push({ x: expected, y: Number(observedDesc[i]) });
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
