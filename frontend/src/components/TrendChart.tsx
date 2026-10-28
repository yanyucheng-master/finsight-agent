import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { fmtValue } from "../format";
import type { Unit } from "../types";

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

export interface Series {
  name: string;
  unit: Unit;
  points: { period: string; value: number | null }[];
}

const COLORS = ["#0b3b8c", "#d97706", "#0f766e", "#7c3aed"];

/** 指标趋势图：数值按证据单位格式化，期间升序显示。 */
export function TrendChart({ series, height = 220 }: { series: Series[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const periods = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p.period)))).sort();
    const scale = (u: Unit) => (u === "ratio" ? 100 : u === "CNY" ? 1e-8 : 1);
    chart.setOption({
      color: COLORS,
      grid: { left: 56, right: 16, top: 28, bottom: 28 },
      legend: { top: 0, textStyle: { fontSize: 11 } },
      tooltip: {
        trigger: "axis",
        valueFormatter: (v: number) => (v == null ? "—" : String(v)),
        formatter: (params: { seriesIndex: number; axisValue: string; data: number | null; marker: string }[]) =>
          [params[0]?.axisValue, ...params.map((p) => {
            const s = series[p.seriesIndex];
            const raw = p.data == null ? null : p.data / scale(s.unit);
            return `${p.marker}${s.name}：${fmtValue(raw, s.unit)}`;
          })].join("<br/>"),
      },
      xAxis: { type: "category", data: periods.map((p) => p.slice(0, 4)), axisTick: { show: false } },
      yAxis: { type: "value", scale: true, splitLine: { lineStyle: { color: "#eef2f7" } },
        axisLabel: { formatter: (v: number) => `${v}${series[0]?.unit === "ratio" ? "%" : series[0]?.unit === "CNY" ? "亿" : ""}` } },
      series: series.map((s) => ({
        name: s.name, type: "line", smooth: false, symbolSize: 6, connectNulls: false,
        data: periods.map((p) => {
          const v = s.points.find((x) => x.period === p)?.value;
          return v == null ? null : Number((v * scale(s.unit)).toFixed(4));
        }),
      })),
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [series]);
  return <div ref={ref} style={{ height }} className="w-full" />;
}
