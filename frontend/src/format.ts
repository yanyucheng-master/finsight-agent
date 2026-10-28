import type { Evidence, Severity, Unit } from "./types";

// 与后端 finsight.domain.fmt_value 保持一致，保证页面显示与核验器使用同一套格式
export function fmtValue(value: Evidence["value"], unit: Unit, digits = 2): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") return value;
  const v = value;
  const n = (x: number, d = digits) => x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  switch (unit) {
    case "CNY": {
      const a = Math.abs(v);
      if (a >= 1e8) return `${n(v / 1e8)} 亿元`;
      if (a >= 1e4) return `${n(v / 1e4)} 万元`;
      return `${n(v)} 元`;
    }
    case "ratio":
      return `${(v * 100).toFixed(digits)}%`;
    case "times":
      return `${n(v)} 倍`;
    case "days":
      return `${v.toFixed(Math.max(digits - 1, 0))} 天`;
    case "cny_per_share":
      return `${v.toFixed(digits)} 元/股`;
    case "count":
      return v.toFixed(0);
    default:
      return v.toFixed(digits);
  }
}

export const SEVERITY: Record<string, { label: string; cls: string; dot: string }> = {
  high: { label: "高", cls: "bg-red-50 text-red-700 ring-red-200", dot: "bg-red-500" },
  medium: { label: "中", cls: "bg-amber-50 text-amber-700 ring-amber-200", dot: "bg-amber-500" },
  low: { label: "低", cls: "bg-slate-100 text-slate-600 ring-slate-200", dot: "bg-slate-400" },
  info: { label: "亮点", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200", dot: "bg-emerald-500" },
  none: { label: "无", cls: "bg-slate-50 text-slate-400 ring-slate-200", dot: "bg-slate-300" },
};

export const LEVEL_STYLE: Record<string, string> = {
  high: "from-red-600 to-rose-500",
  medium: "from-amber-500 to-orange-400",
  low: "from-emerald-600 to-teal-500",
};

export function sevOf(s: Severity | "none"): { label: string; cls: string; dot: string } {
  return SEVERITY[s] ?? SEVERITY.none;
}

export const THRESHOLD_TYPE: Record<string, string> = { literature: "文献阈值", heuristic: "经验阈值", fact: "事实" };

export const CLAIM_STATUS: Record<string, { label: string; cls: string }> = {
  verified: { label: "已核验", cls: "text-emerald-700 bg-emerald-50" },
  unattributed: { label: "未标引用", cls: "text-sky-700 bg-sky-50" },
  mismatch: { label: "不一致", cls: "text-red-700 bg-red-50" },
  invalid_ref: { label: "引用无效", cls: "text-red-700 bg-red-50" },
  unverified: { label: "无证据", cls: "text-red-700 bg-red-50" },
};

export const year = (end?: string | null) => (end ? end.slice(0, 4) : "");
export const shortHash = (h?: string) => (h ? `${h.slice(0, 10)}…${h.slice(-6)}` : "—");
