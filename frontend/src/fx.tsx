// 交互反馈与动效工具：点击波纹、跟随光斑、粒子迸发、数字滚动、轻提示。
// 原则：动效只表达真实发生的事情（点击、核验结果、数据到达），不做假进度；
// 系统开启“减少动态效果”时全部降级为直接显示。
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// ---------------------------------------------------------------- 全局：波纹 + 光斑（事件委托，只装一次）

let installed = false;
export function installGlobalFx() {
  if (installed || typeof document === "undefined") return;
  installed = true;

  document.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || reducedMotion()) return;
    const el = (e.target as HTMLElement | null)?.closest<HTMLElement>("button, .fx-press");
    if (!el || (el as HTMLButtonElement).disabled || el.dataset.noripple !== undefined) return;
    // 波纹层放在 body 上、按按钮的位置和圆角裁切：不往 React 管理的节点里插 DOM
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const host = document.createElement("span");
    host.className = "fx-ripple-host";
    Object.assign(host.style, {
      position: "fixed", left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`,
      borderRadius: cs.borderRadius, color: cs.color, zIndex: "95",
    });
    const dot = document.createElement("span");
    dot.className = "fx-ripple-dot";
    dot.style.left = `${e.clientX - rect.left}px`;
    dot.style.top = `${e.clientY - rect.top}px`;
    dot.style.setProperty("--r", String(Math.ceil((Math.hypot(rect.width, rect.height) * 2) / 12)));
    host.appendChild(dot);
    document.body.appendChild(host);
    setTimeout(() => host.remove(), 700);
  }, { passive: true });

  let raf = 0;
  let last: PointerEvent | null = null;
  document.addEventListener("pointermove", (e) => {
    last = e;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const ev = last;
      if (!ev) return;
      const el = (ev.target as HTMLElement | null)?.closest<HTMLElement>(".fx-spot");
      if (!el) return;
      const r = el.getBoundingClientRect();
      el.style.setProperty("--mx", `${ev.clientX - r.left}px`);
      el.style.setProperty("--my", `${ev.clientY - r.top}px`);
      if (el.dataset.tilt !== undefined && !reducedMotion()) {
        const rx = ((ev.clientY - r.top) / r.height - 0.5) * -5;
        const ry = ((ev.clientX - r.left) / r.width - 0.5) * 6;
        el.style.transform = `perspective(900px) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg) translateY(-3px)`;
      }
    });
  }, { passive: true });
  document.addEventListener("pointerout", (e) => {
    const el = (e.target as HTMLElement | null)?.closest<HTMLElement>(".fx-spot[data-tilt]");
    if (el && !el.contains(e.relatedTarget as Node | null)) el.style.transform = "";
  }, { passive: true });
}

// ---------------------------------------------------------------- 粒子迸发 / 光环

const BRAND = ["#2262f3", "#22c7e8", "#25c99a", "#5b8dfa", "#ffb648"];

/** 在元素中心（或给定坐标）放出一圈粒子。用于“核验通过”“样例已打开”等真实完成事件。 */
export function burst(target: Element | { x: number; y: number } | null, opts: { colors?: string[]; count?: number; spread?: number } = {}) {
  if (!target || reducedMotion()) return;
  const p = "getBoundingClientRect" in target
    ? (() => { const r = target.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()
    : target;
  const host = document.createElement("div");
  host.className = "fx-burst";
  host.style.left = `${p.x}px`;
  host.style.top = `${p.y}px`;
  const colors = opts.colors ?? BRAND;
  const n = opts.count ?? 18;
  const spread = opts.spread ?? 70;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 + Math.random() * 0.4;
    const d = spread * (0.6 + Math.random() * 0.6);
    const s = document.createElement("i");
    s.style.setProperty("--tx", `${Math.cos(a) * d}px`);
    s.style.setProperty("--ty", `${Math.sin(a) * d}px`);
    s.style.setProperty("--c", colors[i % colors.length]);
    s.style.setProperty("--s", `${4 + Math.random() * 5}px`);
    s.style.setProperty("--br", i % 3 === 0 ? "2px" : "50%");
    s.style.setProperty("--rot", `${Math.round(Math.random() * 360)}deg`);
    s.style.setProperty("--d", `${Math.random() * 60}ms`);
    host.appendChild(s);
  }
  document.body.appendChild(host);
  setTimeout(() => host.remove(), 1100);
}

/** 在元素位置放出一圈光环（点击引用标签时的“已接收”反馈）。 */
export function pulseAt(el: Element | null, color = "#2262f3") {
  if (!el || reducedMotion()) return;
  const r = el.getBoundingClientRect();
  const ring = document.createElement("div");
  ring.className = "fx-pulse";
  Object.assign(ring.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
  ring.style.setProperty("--c", color);
  document.body.appendChild(ring);
  setTimeout(() => ring.remove(), 650);
}

// ---------------------------------------------------------------- 数字滚动

/** 从 0 缓动到目标值，结束时严格等于目标值。只用于汇总计数，不用于表格里的财务数字。 */
export function useCountUp(target: number, duration = 900, delay = 0): number {
  const [v, setV] = useState(() => (reducedMotion() ? target : 0));
  const from = useRef(0);
  useEffect(() => {
    if (reducedMotion() || !Number.isFinite(target)) { setV(target); return; }
    let raf = 0;
    let t0 = 0;
    const start = from.current;
    const tick = (t: number) => {
      if (!t0) t0 = t;
      const k = Math.min(1, Math.max(0, (t - t0 - delay) / duration));
      const e = 1 - Math.pow(1 - k, 3);
      const val = start + (target - start) * e;
      setV(k >= 1 ? target : val);
      if (k < 1) raf = requestAnimationFrame(tick);
      else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration, delay]);
  return v;
}

export function CountUp({ value, digits = 0, suffix = "", duration, delay }: { value: number; digits?: number; suffix?: string; duration?: number; delay?: number }) {
  const v = useCountUp(value, duration, delay);
  return <span className="num">{v.toFixed(digits)}{suffix}</span>;
}

// ---------------------------------------------------------------- 轻提示

type ToastKind = "ok" | "info" | "warn" | "err";
interface Toast { id: number; kind: ToastKind; text: string; out?: boolean }
const ToastCtx = createContext<(text: string, kind?: ToastKind) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

const TOAST_STYLE: Record<ToastKind, { ring: string; icon: ReactNode }> = {
  ok: { ring: "text-risk-low", icon: <path d="M5 12.5l4.2 4.2L19 7" /> },
  info: { ring: "text-brand-500", icon: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8h.01" /></> },
  warn: { ring: "text-risk-mid", icon: <><path d="M12 4l9 16H3z" /><path d="M12 10v4M12 17h.01" /></> },
  err: { ring: "text-risk-high", icon: <><circle cx="12" cy="12" r="8.5" /><path d="M9 9l6 6M15 9l-6 6" /></> },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((text: string, kind: ToastKind = "info") => {
    const id = ++seq.current;
    setItems((xs) => [...xs.slice(-2), { id, kind, text }]);
    setTimeout(() => setItems((xs) => xs.map((t) => (t.id === id ? { ...t, out: true } : t))), 2600);
    setTimeout(() => setItems((xs) => xs.filter((t) => t.id !== id)), 2900);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-5 z-[90] flex flex-col items-center gap-2 px-4" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`glass glass-strong flex max-w-md items-center gap-2.5 rounded-2xl px-4 py-2.5 text-sm text-ink shadow-[var(--shadow-lift)] ${t.out ? "toast-out" : "toast-in"}`}>
            <svg viewBox="0 0 24 24" className={`h-5 w-5 shrink-0 ${TOAST_STYLE[t.kind].ring}`} fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">{TOAST_STYLE[t.kind].icon}</svg>
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
