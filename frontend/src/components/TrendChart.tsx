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

const COLORS = ["#2262f3", "#f59331", "#19b48a", "#6b5cff"];

/** 指标趋势图：数值按证据单位格式化，期间升序显示。切换指标时重新绘制入场动画。 */
export function TrendChart({ series, height = 220 }: { series: Series[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const periods = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p.period)))).sort();
    const scale = (u: Unit) => (u === "ratio" ? 100 : u === "CNY" ? 1e-8 : 1);
    chart.setOption({
      color: COLORS,
      animation: !reduce,
      animationDuration: 900,
      animationEasing: "cubicOut",
      grid: { left: 56, right: 18, top: 30, bottom: 28 },
      legend: { top: 0, right: 0, icon: "roundRect", itemWidth: 12, itemHeight: 4, textStyle: { fontSize: 11, color: "#71809e" } },
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(255,255,255,0.96)",
        borderColor: "#dce6f7",
        borderWidth: 1,
        padding: [8, 12],
        textStyle: { color: "#152445", fontSize: 12 },
        extraCssText: "border-radius:12px;box-shadow:0 12px 30px rgba(46,86,170,.16);backdrop-filter:blur(8px);",
        axisPointer: { type: "line", lineStyle: { color: "#8fb4ff", type: "dashed" } },
        valueFormatter: (v: number) => (v == null ? "—" : String(v)),
        formatter: (params: { seriesIndex: number; axisValue: string; data: number | null; marker: string }[]) =>
          [`<b>${params[0]?.axisValue}</b>`, ...params.map((p) => {
            const s = series[p.seriesIndex];
            const raw = p.data == null ? null : p.data / scale(s.unit);
            return `${p.marker}${s.name}：<b>${fmtValue(raw, s.unit)}</b>`;
          })].join("<br/>"),
      },
      xAxis: { type: "category", data: periods.map((p) => p.slice(0, 4)), axisTick: { show: false },
        axisLine: { lineStyle: { color: "#dce6f7" } }, axisLabel: { color: "#71809e" } },
      yAxis: { type: "value", scale: true, splitLine: { lineStyle: { color: "#eef2f9", type: "dashed" } },
        axisLabel: { color: "#a3afc6", formatter: (v: number) => `${v}${series[0]?.unit === "ratio" ? "%" : series[0]?.unit === "CNY" ? "亿" : ""}` } },
      series: series.map((s, i) => ({
        name: s.name, type: "line", smooth: false, symbol: "circle", symbolSize: 8, connectNulls: false,
        lineStyle: { width: 3, color: COLORS[i], shadowColor: `${COLORS[i]}66`, shadowBlur: 10, shadowOffsetY: 4 },
        itemStyle: { color: "#fff", borderColor: COLORS[i], borderWidth: 2.5 },
        emphasis: { scale: 1.6, itemStyle: { color: COLORS[i], borderColor: "#fff" } },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: `${COLORS[i]}38` }, { offset: 1, color: `${COLORS[i]}00` }]) },
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
