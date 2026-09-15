import React, { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { buildQQPoints, getQQAxisMax } from "../utils/qqplot";

const COLOR_POINTS = "#2f6fed";
const COLOR_REFERENCE_LINE = "#94a9c9";

/**
 * QQ Plot: membandingkan -log10(p) observed vs expected di bawah H0.
 * Penyimpangan yang konsisten dari garis diagonal y = x pada ekor
 * kanan-atas mengindikasikan adanya sinyal asosiasi asli (bukan sekadar
 * inflasi statistik akibat population stratification, dsb).
 *
 * @param {object} props
 * @param {{log10_p:number[]}} props.data
 * @param {number} [props.height]
 */
export default function QQPlot({ data, height = 420 }) {
  const option = useMemo(() => {
    const qqPoints = buildQQPoints(data.log10_p);
    const axisMax = getQQAxisMax(qqPoints);
    const scatterData = qqPoints.map((p) => [p.x, p.y]);

    return {
      animation: false,
      grid: { left: 56, right: 24, top: 24, bottom: 48 },
      tooltip: {
        trigger: "item",
        formatter: (params) => {
          const [expected, observed] = params.value;
          return `Expected: ${expected.toFixed(2)}<br/>Observed: ${observed.toFixed(2)}`;
        },
      },
      xAxis: {
        type: "value",
        name: "Expected -log10(p)",
        nameLocation: "middle",
        nameGap: 28,
        min: 0,
        max: axisMax,
      },
      yAxis: {
        type: "value",
        name: "Observed -log10(p)",
        nameLocation: "middle",
        nameGap: 40,
        min: 0,
        max: axisMax,
      },
      series: [
        {
          name: "Garis referensi (y = x)",
          type: "line",
          data: [
            [0, 0],
            [axisMax, axisMax],
          ],
          showSymbol: false,
          silent: true,
          tooltip: { show: false },
          lineStyle: { type: "dashed", color: COLOR_REFERENCE_LINE, width: 1.5 },
        },
        {
          name: "SNP",
          type: "scatter",
          data: scatterData,
          symbolSize: 4,
          itemStyle: { color: COLOR_POINTS },
          large: true,
          largeThreshold: 2000,
        },
      ],
    };
  }, [data]);

  return <ReactECharts option={option} style={{ height, width: "100%" }} notMerge lazyUpdate />;
}
