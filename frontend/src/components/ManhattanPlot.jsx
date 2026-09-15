import React, { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { buildManhattanLayout, log10ToPValue, GENOME_WIDE_SIGNIFICANCE } from "../utils/manhattan";

const COLOR_ODD = "#2f6fed";
const COLOR_EVEN = "#94a9c9";
const COLOR_THRESHOLD = "#e63946";

/**
 * Manhattan Plot interaktif menggunakan Apache ECharts.
 *
 * ECharts dipilih karena mode `large` bawaannya dioptimasi khusus untuk
 * scatter plot berisi puluhan-ratusan ribu titik tanpa membuat browser lag,
 * sekaligus tetap mendukung tooltip per titik dan dataZoom (pan/zoom).
 *
 * @param {object} props
 * @param {{chr:number[], bp:number[], snp:string[], log10_p:number[]}} props.data
 * @param {number} [props.significanceThreshold] default 7.3 (-log10(5e-8))
 * @param {number} [props.height]
 */
export default function ManhattanPlot({ data, significanceThreshold = GENOME_WIDE_SIGNIFICANCE, height = 460 }) {
  const option = useMemo(() => {
    const { points, chromosomeTicks, totalGenomeLength } = buildManhattanLayout(data);

    const oddPoints = [];
    const evenPoints = [];
    let maxY = significanceThreshold;

    for (const p of points) {
      const item = { value: [p.x, p.y], snp: p.snp, chr: p.chr, bp: p.bp };
      if (p.chr % 2 === 1) {
        oddPoints.push(item);
      } else {
        evenPoints.push(item);
      }
      if (p.y > maxY) maxY = p.y;
    }

    // Toleransi pencocokan label kromosom dibuat proporsional terhadap
    // panjang genom total (bukan angka tetap), supaya tetap akurat untuk
    // dataset dengan jumlah/ukuran kromosom yang berbeda-beda.
    const tickTolerance = totalGenomeLength * 0.01;

    return {
      animation: false, // matikan animasi -- penting untuk performa dataset besar
      grid: { left: 56, right: 24, top: 24, bottom: 56 },
      tooltip: {
        trigger: "item",
        formatter: (params) => {
          const { snp, chr, bp } = params.data;
          const log10p = params.data.value[1];
          return [
            `<strong>${snp}</strong>`,
            `Kromosom ${chr} &middot; posisi ${bp.toLocaleString("id-ID")} bp`,
            `-log10(p) = ${log10p.toFixed(3)}`,
            `p-value &asymp; ${log10ToPValue(log10p)}`,
          ].join("<br/>");
        },
      },
      xAxis: {
        type: "value",
        min: 0,
        max: totalGenomeLength,
        splitLine: { show: false },
        axisTick: { show: false },
        name: "Kromosom",
        nameLocation: "middle",
        nameGap: 32,
        axisLabel: {
          formatter: (value) => {
            let nearest = chromosomeTicks[0];
            for (const tick of chromosomeTicks) {
              if (Math.abs(tick.x - value) < Math.abs(nearest.x - value)) nearest = tick;
            }
            return Math.abs(nearest.x - value) < tickTolerance ? `${nearest.chr}` : "";
          },
        },
      },
      yAxis: {
        type: "value",
        name: "-log10(p)",
        min: 0,
        max: Math.ceil(maxY) + 1,
        splitLine: { lineStyle: { color: "#eef1f6" } },
      },
      dataZoom: [
        { type: "inside", xAxisIndex: 0 },
        { type: "slider", xAxisIndex: 0, height: 14, bottom: 6 },
      ],
      series: [
        {
          name: "Kromosom ganjil",
          type: "scatter",
          data: oddPoints,
          symbolSize: 4,
          itemStyle: { color: COLOR_ODD },
          large: true,
          largeThreshold: 2000,
        },
        {
          name: "Kromosom genap",
          type: "scatter",
          data: evenPoints,
          symbolSize: 4,
          itemStyle: { color: COLOR_EVEN },
          large: true,
          largeThreshold: 2000,
        },
        {
          name: "Genome-wide significance",
          type: "line",
          data: [
            [0, significanceThreshold],
            [totalGenomeLength, significanceThreshold],
          ],
          showSymbol: false,
          silent: true,
          tooltip: { show: false },
          lineStyle: { type: "dashed", color: COLOR_THRESHOLD, width: 1.5 },
        },
      ],
    };
  }, [data, significanceThreshold]);

  return <ReactECharts option={option} style={{ height, width: "100%" }} notMerge lazyUpdate />;
}
