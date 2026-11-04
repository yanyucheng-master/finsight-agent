import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { CompanyPage } from "./components/Company";
import { EvidenceProvider } from "./components/Evidence";
import { BrandMark, Icon } from "./components/ui";
import { fmtValue, LEVEL_STYLE } from "./format";
import { CountUp, installGlobalFx, ToastProvider } from "./fx";
import type { CompanyBrief, DiagnosisResult, SampleMeta } from "./types";

installGlobalFx();

// 极简哈希路由：#/  首页；#/c/600519  公司工作台
function useRoute(): string {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const on = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}
const go = (code: string) => { window.location.hash = `#/c/${code}`; };

// ---------------------------------------------------------------- 搜索（↑↓ 选择，回车进入，Esc 收起；页首搜索框按 / 聚焦）

function Search({ big = false, hotkey = false }: { big?: boolean; hotkey?: boolean }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<{ code: string; name: string; available: boolean }[]>([]);
  const [sel, setSel] = useState(0);
  const [focus, setFocus] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!q.trim()) { setRes([]); return; }
    const t = setTimeout(() => api.search(q).then((r) => { setRes(r); setSel(Math.max(0, r.findIndex((x) => x.available))); }).catch(() => setRes([])), 160);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    if (!hotkey) return;
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key === "/" && !/INPUT|TEXTAREA/.test(t.tagName)) { e.preventDefault(); input.current?.focus(); }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [hotkey]);
  const pick = (r?: { code: string; available: boolean }) => { if (r?.available) { setQ(""); setRes([]); input.current?.blur(); go(r.code); } };
  const open = focus && res.length > 0;
  return (
    <div className={`relative w-full ${big ? "max-w-xl" : "max-w-xs"}`}>
      <div className={`flex items-center gap-2 rounded-2xl border bg-white/90 transition-all duration-300 ${big ? "px-4 py-3 shadow-[var(--shadow-card)]" : "px-3 py-1.5"} ${focus ? "border-brand-300 shadow-[0_0_0_4px_rgba(34,98,243,0.12)]" : "border-line"}`}>
        <Icon name="search" className={`${big ? "h-5 w-5" : "h-4 w-4"} shrink-0 transition-colors ${focus ? "text-brand-500" : "text-faint"}`} />
        <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => setFocus(true)} onBlur={() => setTimeout(() => setFocus(false), 150)}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return;
            if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(res.length - 1, s + 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
            else if (e.key === "Enter") pick(res[sel]);
            else if (e.key === "Escape") { setQ(""); input.current?.blur(); }
          }}
          placeholder={big ? "输入股票代码、名称或拼音首字母，例如 600519 / 茅台 / gzmt" : "搜索代码 / 名称 / 拼音"}
          className={`min-w-0 flex-1 bg-transparent text-ink placeholder-faint outline-none ${big ? "text-[15px]" : "text-sm"}`} />
        {hotkey && !focus && <span className="kbd hidden sm:inline">/</span>}
      </div>
      {open && (
        <ul className="glass glass-strong fade-in absolute left-0 right-0 top-[calc(100%+8px)] z-40 overflow-hidden rounded-2xl p-1.5 text-sm shadow-[var(--shadow-lift)]">
          {res.map((r, i) => (
            <li key={r.code}>
              <button disabled={!r.available} onMouseEnter={() => setSel(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(r)}
                className={`flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${i === sel && r.available ? "bg-brand-50" : ""}`}>
                <span className="truncate text-ink">{r.name} <span className="num ml-1 text-faint">{r.code}</span></span>
                <span className={`chip shrink-0 ${r.available ? "bg-[#e6f8f1] text-risk-low" : "bg-slate-100 text-faint"}`}>{r.available ? "可分析" : "暂无快照"}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 首页：证据链示意（数字来自真实快照：贵州茅台 2025 年报）

function EvidenceFlow({ d }: { d: DiagnosisResult | null }) {
  const end = d?.periods[0]?.end;
  const rev = end ? d?.evidence[`f:is.revenue@${end}`] : undefined;
  const gm = end ? d?.evidence[`m:gross_margin@${end}`] : undefined;
  const raw = rev?.source?.field ?? "OPERATE_INCOME";
  const NODES = [
    { icon: "db", t: "原始快照", s: "东方财富 / 巨潮原始响应，SHA-256 内容寻址", chip: rev ? `${raw} = ${fmtValue(rev.value, rev.unit)}` : raw, c: "#22c7e8" },
    { icon: "cpu", t: "确定性引擎", s: "公式与输入全程登记，缺失值不以 0 填充", chip: gm ? `毛利率 ${fmtValue(gm.value, gm.unit)}` : "指标 · 模型 · 规则", c: "#2262f3" },
    { icon: "doc", t: "AI 简报 + 核验器", s: "按编号引用证据写作，逐个比对文中数字", chip: gm ? `毛利率为${fmtValue(gm.value, gm.unit)}[E1] ✓` : "[E1] ✓ 已核验", c: "#25c99a" },
  ];
  return (
    <div className="glass fx-spot relative overflow-hidden rounded-[28px] p-5 sm:p-6">
      <div className="mb-4 flex items-center justify-between">
        <div className="eyebrow"><span className="dot-live" />EVIDENCE CHAIN</div>
        <span className="text-[11px] text-faint">{d ? `${d.profile.name} · ${d.periods[0]?.label}` : "证据链"}</span>
      </div>
      <ol className="relative space-y-3">
        <svg className="pointer-events-none absolute left-[22px] top-8 h-[calc(100%-4rem)] w-2 overflow-visible" aria-hidden>
          <line x1="4" y1="0" x2="4" y2="100%" stroke="#dce6f7" strokeWidth="2" />
          <line x1="4" y1="0" x2="4" y2="100%" stroke="url(#flowg)" strokeWidth="2.4" className="flow" strokeLinecap="round" />
          <defs><linearGradient id="flowg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#22c7e8" /><stop offset=".5" stopColor="#2262f3" /><stop offset="1" stopColor="#25c99a" /></linearGradient></defs>
        </svg>
        {NODES.map((n, i) => (
          <li key={n.t} className="rise relative flex gap-3.5" style={{ ["--i" as string]: i + 2 }}>
            <span className="relative z-[1] grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-white shadow-[0_6px_16px_rgba(60,89,146,0.14)]" style={{ color: n.c }}>
              <Icon name={n.icon} className="h-5 w-5" />
            </span>
            <div className="min-w-0 flex-1 rounded-2xl border border-line/80 bg-white/70 px-3.5 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-bold text-ink">{n.t}</span>
                <span className="text-[10px] font-bold tracking-wider text-faint">0{i + 1}</span>
              </div>
              <div className="mt-0.5 text-xs text-muted">{n.s}</div>
              <div className="num mt-2 inline-flex max-w-full items-center gap-1.5 truncate rounded-lg px-2 py-1 text-[11.5px] font-semibold" style={{ color: n.c, background: `${n.c}14` }}>
                {n.chip}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------- 首页：公司卡片

const DIM_DOT: Record<string, string> = { none: "#cfe9df", low: "#9fb3d6", medium: "#f59331", high: "#ef4462", info: "#25c99a" };

function CompanyCard({ c, i, d, hasSample }: { c: CompanyBrief; i: number; d?: DiagnosisResult; hasSample: boolean }) {
  const risks = d?.signals.filter((s) => s.polarity === "risk").length ?? 0;
  const lv = d?.overall.level_code;
  return (
    <button onClick={() => go(c.code)} data-tilt
      className="glass fx-spot rise group flex flex-col rounded-[22px] p-4 text-left transition-[box-shadow,border-color] duration-300 hover:border-brand-200 hover:shadow-[var(--shadow-lift)]"
      style={{ ["--i" as string]: i, transition: "transform .25s var(--ease-out), box-shadow .3s, border-color .3s" }}>
      <div className="flex w-full items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-[15px] font-bold text-ink transition-colors group-hover:text-brand-700">{c.name}</span>
            {hasSample && <span className="chip bg-[#fff4e3] px-1.5 py-0 text-[10px] text-[#b26205]" title="有已核验的大模型简报样例，进入后秒开">AI 样例</span>}
          </div>
          <div className="num text-xs text-faint">{c.code}</div>
        </div>
        {d ? (
          <span className={`chip shrink-0 bg-gradient-to-br font-bold text-white shadow-sm ${LEVEL_STYLE[lv ?? ""] ?? "from-slate-500 to-slate-400"}`}>
            <span className="h-1.5 w-1.5 rounded-full bg-white/90" />{d.overall.level}风险
          </span>
        ) : <span className="skeleton h-6 w-16 rounded-full" />}
      </div>
      <div className="mt-2 truncate text-xs text-muted">{c.industry ?? (c.company_type === "bank" ? "银行" : "—")}</div>
      <div className="mt-3 flex w-full items-end justify-between gap-2 border-t border-line/70 pt-3">
        {d ? (
          <>
            <div className="text-[11px] leading-5 text-muted">
              <div>核验 <b className="num text-ink">{d.checks_summary.pass}</b>/<span className="num">{d.checks_summary.pass + d.checks_summary.fail}</span> 通过</div>
              <div>风险信号 <b className={`num ${risks ? "text-risk-mid" : "text-ink"}`}>{risks}</b> 条</div>
            </div>
            <div className="flex items-center gap-[3px]" title="八个分析维度的风险等级">
              {d.dimensions.map((dim) => <span key={dim.code} className="h-4 w-1.5 rounded-full" style={{ background: DIM_DOT[dim.level] }} title={`${dim.label}：${dim.level === "none" ? "未见风险" : dim.level}`} />)}
            </div>
          </>
        ) : <span className="skeleton h-9 w-full" />}
        <Icon name="arrow" className="h-4 w-4 shrink-0 -translate-x-1 text-brand-500 opacity-0 transition-all duration-300 group-hover:translate-x-0 group-hover:opacity-100" />
      </div>
    </button>
  );
}

function Home() {
  const [list, setList] = useState<CompanyBrief[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [diags, setDiags] = useState<Record<string, DiagnosisResult>>({});
  const [samples, setSamples] = useState<SampleMeta[]>([]);
  useEffect(() => {
    api.available().then((xs) => {
      setList(xs);
      xs.forEach((c) => api.diagnosis(c.code).then((d) => setDiags((m) => ({ ...m, [c.code]: d }))).catch(() => undefined));
    }).catch((e) => setErr(String(e.message ?? e)));
    api.samples().then(setSamples).catch(() => undefined);
  }, []);
  const loaded = Object.values(diags);
  const all = list && loaded.length === list.length && list.length > 0;
  const stats = useMemo(() => ({
    checks: loaded.reduce((s, d) => s + d.checks_summary.pass, 0),
    checksTotal: loaded.reduce((s, d) => s + d.checks_summary.pass + d.checks_summary.fail, 0),
    evidence: loaded.reduce((s, d) => s + Object.keys(d.evidence).length, 0),
  }), [loaded]);
  const sampleCos = useMemo(() => new Set(samples.filter((s) => s.kind === "report").map((s) => s.code)), [samples]);
  const PILLARS = [
    { icon: "hash", t: "每个数字都有出处", d: "原始响应快照带 URL、抓取时间与 SHA-256，任意数字一键回溯到原始字段。" },
    { icon: "shield", t: "先核验数据，再做分析", d: "会计恒等式、跨期连续性与数据源公布指标交叉比对，通过后再进入指标计算。" },
    { icon: "sparkle", t: "AI 结论逐条核验", d: "大模型按编号引用证据写作，核验器逐个比对文中数字，不一致的打回修订或标红。" },
  ];
  const flowD = diags["600519"] ?? null;
  return (
    <div className="mx-auto max-w-6xl px-4 pb-20">
      <section className="mt-8 grid items-start gap-6 lg:grid-cols-[1.25fr_1fr]">
        <div className="rise pt-2" style={{ ["--i" as string]: 0 }}>
          <div className="eyebrow"><span className="dot-live" />AI+金融赋能 · 投研分析场景</div>
          <h1 className="mt-3 text-[34px] font-extrabold leading-tight tracking-tight text-ink sm:text-[42px]">
            可验证的<span className="bg-gradient-to-r from-brand-500 via-[#3a8bff] to-cyan bg-clip-text text-transparent">上市公司投研</span>智能体
          </h1>
          <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-muted">
            智能体完成取数、核验、指标计算、风险排查与简报撰写，研究员可以从任意数字追溯到原始字段和快照。
          </p>
          <div className="mt-6"><Search big /></div>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
            <span>试试：</span>
            {(list ?? []).slice(0, 4).map((c) => (
              <button key={c.code} onClick={() => go(c.code)} className="chip border border-line bg-white/80 text-ink-2 transition hover:-translate-y-0.5 hover:border-brand-200 hover:text-brand-700">
                {c.name}
              </button>
            ))}
          </div>
          <div className="mt-7 grid gap-3 sm:grid-cols-3">
            {PILLARS.map((p, i) => (
              <div key={p.t} className="glass fx-spot rise rounded-[20px] p-4" style={{ ["--i" as string]: i + 1 }}>
                <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-brand-50 to-white text-brand-600 shadow-[inset_0_0_0_1px_var(--color-brand-100)]"><Icon name={p.icon} className="h-[18px] w-[18px]" /></span>
                <div className="mt-2.5 text-sm font-bold text-ink">{p.t}</div>
                <div className="mt-1 text-xs leading-relaxed text-muted">{p.d}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="rise" style={{ ["--i" as string]: 2 }}><EvidenceFlow d={flowD} /></div>
      </section>

      <section className="glass rise mt-8 grid grid-cols-2 gap-px overflow-hidden rounded-[22px] bg-line/60 p-0 sm:grid-cols-4" style={{ ["--i" as string]: 3 }}>
        {[
          { k: "样本公司", v: list?.length ?? 0, u: "家", ready: !!list },
          { k: "数据核验通过", v: stats.checks, u: `/ ${stats.checksTotal} 项`, ready: !!all },
          { k: "证据条目", v: stats.evidence, u: "条", ready: !!all },
          { k: "已核验 AI 样例", v: samples.length, u: "份", ready: samples.length > 0 },
        ].map((s) => (
          <div key={s.k} className="bg-white/85 px-5 py-4">
            <div className="text-[11px] font-semibold text-muted">{s.k}</div>
            <div className="mt-1 flex items-baseline gap-1.5">
              {s.ready ? <span className="text-2xl font-extrabold text-ink"><CountUp value={s.v} /></span> : <span className="skeleton inline-block h-7 w-16" />}
              <span className="text-xs text-faint">{s.u}</span>
            </div>
          </div>
        ))}
      </section>

      <div className="mb-3 mt-10 flex items-end justify-between gap-3">
        <h2 className="text-base font-bold text-ink">样本公司（基于真实公开数据快照）</h2>
        <span className="hidden text-xs text-faint sm:inline">点击卡片进入工作台 · 竖条为八个分析维度的风险等级</span>
      </div>
      {err && <div className="shake rounded-2xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">后端不可用：{err}</div>}
      {!list && !err && <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-36 rounded-[22px]" />)}</div>}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {list?.map((c, i) => <CompanyCard key={c.code} c={c} i={i} d={diags[c.code]} hasSample={sampleCos.has(c.code)} />)}
      </div>
      <p className="mt-10 text-xs leading-relaxed text-faint">
        数据来源：东方财富 F10 公开财务数据、巨潮资讯（证监会指定信息披露平台）。分析结果供研究参考，不构成投资建议。
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- 页首

function HealthPill() {
  const [h, setH] = useState<{ llm_enabled: boolean; llm_model: string | null; companies: number } | null>(null);
  useEffect(() => { api.health().then(setH).catch(() => undefined); }, []);
  if (!h) return null;
  return (
    <span className="chip hidden border border-line bg-white/80 text-muted md:inline-flex" title={h.llm_enabled ? `大模型：${h.llm_model}` : "未配置大模型 Key：可查看已核验样例、模板简报与全部确定性分析"}>
      <span className={`h-1.5 w-1.5 rounded-full ${h.llm_enabled ? "bg-mint" : "bg-faint"}`} />
      {h.companies} 家公司 · {h.llm_enabled ? "实时生成可用" : "样例模式"}
    </span>
  );
}

function useScrolled(px = 8) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const f = () => setOn(window.scrollY > px);
    f();
    window.addEventListener("scroll", f, { passive: true });
    return () => window.removeEventListener("scroll", f);
  }, [px]);
  return on;
}

export default function App() {
  const route = useRoute();
  const m = route.match(/^#\/c\/(\d{6})/);
  const scrolled = useScrolled();
  useEffect(() => { window.scrollTo({ top: 0 }); }, [m?.[1]]);
  return (
    <ToastProvider>
      <EvidenceProvider>
        <div className="ambient" aria-hidden><i /><i /><i /></div>
        <header className={`sticky top-0 z-30 border-b backdrop-blur-xl transition-[background-color,box-shadow,border-color] duration-300 ${scrolled ? "border-line/80 bg-white/85 shadow-[0_8px_30px_rgba(60,89,146,0.10)]" : "border-transparent bg-white/50"}`}>
          <div className="mx-auto flex h-16 max-w-6xl items-center gap-3 px-4">
            <a href="#/" className="flex items-center gap-2.5" aria-label="FinSight 首页">
              <BrandMark size={36} />
              <span className="leading-tight">
                <span className="block text-[17px] font-extrabold tracking-tight"><span className="text-[#124a59]">Fin</span><span className="text-[#596f8e]">Sight</span></span>
                <span className="hidden text-[10.5px] text-muted sm:block">每一个数字都有出处，每一个结论都可核验</span>
              </span>
            </a>
            <div className="ml-auto flex min-w-0 flex-1 items-center justify-end gap-3">
              <HealthPill />
              {m && <Search hotkey />}
            </div>
          </div>
        </header>
        <main key={m ? m[1] : "home"} className="fade-in">
          {m ? <CompanyPage code={m[1]} /> : <Home />}
        </main>
      </EvidenceProvider>
    </ToastProvider>
  );
}
