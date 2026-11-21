import { buildManhattanLayout, formatChromosomeLabel, toLog10Threshold } from "./manhattan";

describe("buildManhattanLayout", () => {
  it("prevents small chromosomes (Chr 9..22) from piling up at the corner after gigabase chromosomes (Chr 1..8)", () => {
    const chr = [];
    const bp = [];
    const snp = [];
    const log10_p = [];

    // Chromosomes 1..8 with ~2 Gb length
    for (let c = 1; c <= 8; c += 1) {
      chr.push(c, c);
      bp.push(10000, 2000000000);
      snp.push(`chr${c}_start`, `chr${c}_end`);
      log10_p.push(1.2, 4.5);
    }

    // Chromosomes 9..22 with only 50 kb - 200 kb length
    for (let c = 9; c <= 22; c += 1) {
      chr.push(c, c);
      bp.push(1000, 80000);
      snp.push(`chr${c}_start`, `chr${c}_end`);
      log10_p.push(0.8, 3.1);
    }

    const layout = buildManhattanLayout({ chr, bp, snp, log10_p });
    expect(layout.chromosomeTicks).toHaveLength(22);

    // Every adjacent chromosome tick must be separated by at least 2.5% of the total genome width
    for (let i = 1; i < layout.chromosomeTicks.length; i += 1) {
      const gapRatio =
        (layout.chromosomeTicks[i].x - layout.chromosomeTicks[i - 1].x) /
        layout.totalGenomeLength;
      expect(gapRatio).toBeGreaterThan(0.025);
    }

    // Chromosome 8 should end well before the right edge (leaving >35% of the plot width for Chr 9..22)
    const chr8Band = layout.chromosomeBands.find((b) => b.chr === 8);
    expect(chr8Band.endX / layout.totalGenomeLength).toBeLessThan(0.65);
  });

  it("converts p-value thresholds and log10 thresholds accurately", () => {
    expect(toLog10Threshold(1e-6)).toBeCloseTo(6, 5);
    expect(toLog10Threshold(7.3)).toBeCloseTo(7.3, 5);
  });

  it("formats non-numeric chromosome labels when chr_labels map is provided", () => {
    const labels = { 1: "Gm01", 2: "Gm02", 3: "scaffold_1" };
    expect(formatChromosomeLabel(1, labels)).toBe("Gm01");
    expect(formatChromosomeLabel("3", labels)).toBe("scaffold_1");
    expect(formatChromosomeLabel(4, labels)).toBe("4");
    expect(formatChromosomeLabel(23, null)).toBe("23");
  });

  it("preserves exact >2^31 integer BP positions and monotonic cumulative coordinates", () => {
    const layout = buildManhattanLayout({
      chr: [7, 7, 8, 8],
      bp: [500, 2160284371, 1000, 500000],
      snp: ["chr7:500", "chr7:2160284371", "chr8:1000", "chr8:500000"],
      log10_p: [1.5, 9.2, 2.0, 3.5],
    });

    expect(layout.chromosomeTicks.map((t) => t.chr)).toEqual([7, 8]);
    expect(layout.points[1].bp).toBe(2160284371);
    expect(layout.points[0].x).toBeLessThan(layout.points[1].x);
    expect(layout.points[1].x).toBeLessThan(layout.points[2].x);
    expect(layout.points[2].x).toBeLessThan(layout.points[3].x);
  });
});
