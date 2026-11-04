import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import brandMarkUrl from "../assets/finsight-mark.png";
import { useCountUp } from "../fx";

// ---------------------------------------------------------------- 图标（线性、1.8 描边，与大师实验室一致）

const PATHS: Record<string, ReactNode> = {
  shield: <><path d="M12 3l7 3v5.5c0 4.3-2.9 8.1-7 9.5-4.1-1.4-7-5.2-7-9.5V6z" /><path d="M9 12l2.2 2.2L15.5 10" /></>,
  doc: <><path d="M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></>,
  chat: <><path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-7l-4 3.5V16H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" /><path d="M8.5 10.5h.01M12 10.5h.01M15.5 10.5h.01" /></>,
  chart: <><path d="M4 19h16" /><path d="M6 15l4-4 3 3 5-6" /><path d="M18 8h-3M18 8v3" /></>,
  table: <><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M4 10h16M10 10v9" /></>,
  check: <><circle cx="12" cy="12" r="8.5" /><path d="M8.5 12.2l2.4 2.4 4.6-4.8" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></>,
  copy: <><rect x="8" y="8" width="11" height="11" rx="2" /><path d="M5 15V6a1 1 0 0 1 1-1h9" /></>,
  sparkle: <><path d="M12 4l1.6 4.4L18 10l-4.4 1.6L12 16l-1.6-4.4L6 10l4.4-1.6z" /><path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" /></>,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  back: <path d="M19 12H5M11 6l-6 6 6 6" />,
  send: <><path d="M4 12l16-8-6 16-2.5-6.5z" /><path d="M11.5 13.5L20 4" /></>,
  bolt: <path d="M13 3L5 14h6l-1 7 8-11h-6z" />,
  db: <><ellipse cx="12" cy="6" rx="7" ry="2.8" /><path d="M5 6v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6M5 12v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8v-6" /></>,
  cpu: <><rect x="7" y="7" width="10" height="10" rx="2" /><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4" /></>,
  link: <><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  alert: <><path d="M12 4l9 16H3z" /><path d="M12 10v4M12 17h.01" /></>,
  hash: <path d="M9 4L7 20M17 4l-2 16M4 9h16M3 15h16" />,
  layers: <><path d="M12 4l8 4-8 4-8-4z" /><path d="M4 12l8 4 8-4M4 16l8 4 8-4" /></>,
  tool: <path d="M14.5 6.5a4 4 0 0 0-5.3 5.3L4 17l3 3 5.2-5.2a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.6-.4-.4-2.6z" />,
  star: <path d="M12 4l2.4 5 5.4.6-4 3.7 1.1 5.4L12 16l-4.9 2.7 1.1-5.4-4-3.7 5.4-.6z" />,
  plus: <path d="M12 5v14M5 12h14" />,
};

export function Icon({ name, className = "h-4 w-4", strokeWidth = 1.8 }: { name: keyof typeof PATHS | string; className?: string; strokeWidth?: number }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {PATHS[name]}
    </svg>
  );
}

// ---------------------------------------------------------------- 品牌标志：镜片 + 上行折线（FinSight = 看清财务）

export function BrandMark({ size = 36 }: { size?: number }) {
  return (
    <span className="brand-mark relative grid shrink-0 place-items-center" style={{ width: size, height: size }}>
      <img src={brandMarkUrl} alt="" width={size} height={size} draggable={false} className="h-full w-full select-none object-contain" />
    </span>
  );
}

// ---------------------------------------------------------------- 环形进度（数值来自真实计数）

export function Ring({ value, total, size = 64, stroke = 7, color = "#19b48a", track = "#e6eefb", children, delay = 0 }: {
  value: number; total: number; size?: number; stroke?: number; color?: string; track?: string; children?: ReactNode; delay?: number;
}) {
  const frac = total > 0 ? value / total : 0;
  const f = useCountUp(frac, 1000, delay);
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <span className="relative inline-grid shrink-0 place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} stroke={track} strokeWidth={stroke} fill="none" />
        <circle cx={size / 2} cy={size / 2} r={r} stroke={color} strokeWidth={stroke} fill="none" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - f)} style={{ filter: `drop-shadow(0 0 4px ${color}55)` }} />
      </svg>
      <span className="absolute inset-0 grid place-items-center">{children}</span>
    </span>
  );
}

// ---------------------------------------------------------------- 滑动指示条的标签栏 / 分段控件

export function SlidingTabs<K extends string>({ items, value, onChange, variant = "tabs", className = "" }: {
  items: { key: K; label: string; icon?: string; badge?: ReactNode }[];
  value: K; onChange: (k: K) => void; variant?: "tabs" | "segment"; className?: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [ind, setInd] = useState<{ left: number; width: number } | null>(null);
  useLayoutEffect(() => {
    const measure = () => {
      const el = wrap.current?.querySelector<HTMLElement>(`[data-k="${value}"]`);
      if (el) setInd({ left: el.offsetLeft, width: el.offsetWidth });
    };
    measure();
    const ro = new ResizeObserver(measure);
    if (wrap.current) ro.observe(wrap.current);
    return () => ro.disconnect();
  }, [value, items.length]);
  const seg = variant === "segment";
  return (
    <div ref={wrap} className={`relative flex ${seg ? "gap-1 rounded-2xl bg-[#eaf1fd] p-1" : "gap-1"} ${className}`}>
      {ind && (
        <span aria-hidden className={`absolute transition-[left,width] duration-500 ease-[cubic-bezier(.34,1.4,.5,1)] ${seg
          ? "top-1 bottom-1 rounded-xl bg-white shadow-[0_4px_12px_rgba(60,89,146,0.14)]"
          : "top-1 bottom-1 rounded-xl bg-gradient-to-br from-[#3a7bff] to-[#1b4fe0] shadow-[0_8px_18px_rgba(34,98,243,0.3)]"}`}
          style={{ left: ind.left, width: ind.width }} />
      )}
      {items.map((t) => {
        const on = t.key === value;
        return (
          <button key={t.key} data-k={t.key} data-noripple onClick={() => onChange(t.key)} aria-pressed={on}
            className={`relative z-[1] flex items-center gap-1.5 whitespace-nowrap rounded-xl px-3.5 py-2 text-sm font-semibold transition-colors duration-300 ${seg
              ? on ? "text-brand-700" : "text-muted hover:text-ink"
              : on ? "text-white" : "text-muted hover:bg-white/70 hover:text-ink"}`}>
            {t.icon && <Icon name={t.icon} className={`h-[17px] w-[17px] transition-transform duration-300 ${on ? "scale-110" : ""}`} />}
            {t.label}
            {t.badge}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- 风险评级：三段弧（分类评级，不伪装成连续分数）

const LEVELS = [
  { code: "low", label: "低", color: "#19b48a" },
  { code: "medium", label: "中", color: "#f59331" },
  { code: "high", label: "高", color: "#ef4462" },
];

export function RiskArc({ level, size = 150 }: { level: string; size?: number }) {
  const idx = Math.max(0, LEVELS.findIndex((l) => l.code === level));
  const cx = size / 2, cy = size * 0.58, r = size * 0.4, w = size * 0.085;
  const seg = (i: number) => {
    const gap = 0.06;
    const a0 = Math.PI + (i / 3) * Math.PI + gap, a1 = Math.PI + ((i + 1) / 3) * Math.PI - gap;
    const p = (a: number) => `${(cx + r * Math.cos(a)).toFixed(2)} ${(cy + r * Math.sin(a)).toFixed(2)}`;
    return `M${p(a0)} A${r} ${r} 0 0 1 ${p(a1)}`;
  };
  const mid = Math.PI + ((idx + 0.5) / 3) * Math.PI;
  const mx = cx + r * Math.cos(mid), my = cy + r * Math.sin(mid);
  return (
    <svg width={size} height={size * 0.68} viewBox={`0 0 ${size} ${size * 0.68}`} aria-hidden className="overflow-visible">
      {LEVELS.map((l, i) => (
        <path key={l.code} d={seg(i)} stroke={i === idx ? "#fff" : "rgba(255,255,255,0.28)"} strokeWidth={w} strokeLinecap="round" fill="none"
          className="draw" style={{ ["--len" as string]: 200, ["--d" as string]: `${i * 0.12}s`, filter: i === idx ? "drop-shadow(0 0 6px rgba(255,255,255,.8))" : undefined }} />
      ))}
      <g className="pop" style={{ ["--i" as string]: 8, transformOrigin: `${mx}px ${my}px` }}>
        {idx > 0 && <circle cx={mx} cy={my} r={w * 1.25} fill="#fff" opacity=".35" className="halo" style={{ transformOrigin: `${mx}px ${my}px` }} />}
        <circle cx={mx} cy={my} r={w * 0.78} fill="#fff" />
        <circle cx={mx} cy={my} r={w * 0.38} fill={LEVELS[idx].color} />
      </g>
      {LEVELS.map((l, i) => {
        const a = Math.PI + ((i + 0.5) / 3) * Math.PI;
        const lr = r - w * 1.9;
        return <text key={l.code} x={cx + lr * Math.cos(a)} y={cy + lr * Math.sin(a) + 4} textAnchor="middle" fontSize={size * 0.075}
          fill={i === idx ? "#fff" : "rgba(255,255,255,.55)"} fontWeight={i === idx ? 700 : 500}>{l.label}</text>;
      })}
    </svg>
  );
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={`spin ${className}`} fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity=".2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function SectionTitle({ icon, children, extra }: { icon?: string; children: ReactNode; extra?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      {icon && <span className="grid h-7 w-7 place-items-center rounded-lg bg-brand-50 text-brand-600"><Icon name={icon} className="h-4 w-4" /></span>}
      <h2 className="text-[15px] font-bold text-ink">{children}</h2>
      {extra && <div className="ml-auto">{extra}</div>}
    </div>
  );
}
