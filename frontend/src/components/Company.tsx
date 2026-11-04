import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, BALANCE_ERROR_MESSAGE, callErrorMessage, waitJob } from "../api";
import { fmtValue, LEVEL_STYLE, sevOf, THRESHOLD_TYPE, year } from "../format";
import { burst, CountUp, pulseAt, useToast } from "../fx";
import type { AgentAnswer, DemoStatus, DiagnosisResult, DimensionSummary, JobStatus, MetricRow, Report, SampleMeta, Signal } from "../types";
import { Cite, CitedText, useEvidence } from "./Evidence";
import { CachedBadge, JobProgress, LiveQuota, ArticleSkeleton } from "./Progress";
import { Icon, RiskArc, Ring, SectionTitle, SlidingTabs, Spinner } from "./ui";
import { VerificationBar, VerifiedMarkdown } from "./Verified";

// ECharts 体积大（约 600 KB），只有“指标趋势”页用到：按需加载，首页与简报页更快打开
const TrendChart = lazy(() => import("./TrendChart").then((m) => ({ default: m.TrendChart })));

const TABS = [
  { key: "risk", label: "风险体检", icon: "shield" },
  { key: "brief", label: "研究简报", icon: "doc" },
  { key: "ask", label: "智能问答", icon: "chat" },
  { key: "metrics", label: "指标趋势", icon: "chart" },
  { key: "statements", label: "财务报表", icon: "table" },
  { key: "checks", label: "数据核验", icon: "check" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

const SEV_COLOR: Record<string, string> = { high: "#ef4462", medium: "#f59331", low: "#8fa2c4", info: "#19b48a", none: "#25c99a" };

// ---------------------------------------------------------------- 加载中：体检流程示意（不是进度条，只表示正在运行）

function DiagnosisLoading({ code }: { code: string }) {
  const STEPS = ["读取快照", "数据核验", "指标计算", "风险规则", "综合评级"];
  return (
    <div className="mx-auto max-w-6xl px-4 pb-16">
      <section className="glass relative mt-6 overflow-hidden rounded-[26px] p-6">
        <div className="scanline" />
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-2xl bg-brand-50 text-brand-500"><Spinner className="h-5 w-5" /></span>
          <div>
            <div className="text-base font-bold text-ink">正在运行财务风险体检…</div>
            <div className="num text-xs text-muted">{code} · 全部计算在本地快照上完成，不调用大模型</div>
          </div>
        </div>
        <div className="mt-5 flex flex-wrap gap-2">
          {STEPS.map((s, i) => <span key={s} className="chip pop border border-line bg-white/80 text-muted" style={{ ["--i" as string]: i }}>{s}</span>)}
        </div>
        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-20 rounded-2xl" />)}
        </div>
      </section>
    </div>
  );
}

export function CompanyPage({ code }: { code: string }) {
  const [d, setD] = useState<DiagnosisResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("risk");
  const toast = useToast();
  useEffect(() => {
    setD(null);
    setErr(null);
    setTab("risk");
    api.diagnosis(code).then(setD).catch((e) => setErr(String(e.message ?? e)));
  }, [code]);

  if (err) return (
    <div className="mx-auto max-w-6xl px-4 pt-6">
      <div className="shake glass rounded-2xl border-red-200 p-5 text-red-700">无法加载 {code}：{err}
        <a href="#/" className="btn btn-ghost ml-3">返回首页</a></div>
    </div>
  );
  if (!d) return <DiagnosisLoading code={code} />;
  const p = d.profile;
  const latest = d.periods[0];
  const cs = d.checks_summary;
  const risks = d.signals.filter((s) => s.polarity === "risk");
  const strengths = d.signals.filter((s) => s.polarity === "strength");
  const lv = d.overall.level_code;
  const copy = (e: React.MouseEvent) => {
    void navigator.clipboard?.writeText(p.code).then(() => toast(`已复制代码 ${p.code}`, "ok"), () => toast("浏览器未允许复制", "warn"));
    pulseAt(e.currentTarget);
  };
  return (
    <div className="mx-auto max-w-6xl px-4 pb-20">
      <a href="#/" className="mt-5 inline-flex items-center gap-1.5 text-xs font-semibold text-muted transition hover:-translate-x-0.5 hover:text-brand-600">
        <Icon name="back" className="h-3.5 w-3.5" />样本公司
      </a>
      <section className="mt-2 grid gap-4 md:grid-cols-[1fr_300px]">
        <div className="glass fx-spot rise rounded-[26px] p-5 sm:p-6">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <h1 className="text-[26px] font-extrabold tracking-tight text-ink">{p.name}</h1>
            <button onClick={copy} title="复制股票代码" className="num inline-flex items-center gap-1 rounded-lg border border-line bg-white/80 px-2 py-0.5 text-sm text-muted transition hover:border-brand-200 hover:text-brand-600">
              {p.code}.{p.exchange}<Icon name="copy" className="h-3.5 w-3.5" />
            </button>
            {p.industry_em && <span className="chip bg-brand-50 text-brand-700">{p.industry_em}</span>}
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-4">
            {[["最新年报", latest?.label], ["审计意见", latest?.audit_opinion], ["审计机构", p.auditor], ["上市日期", p.listing_date]].map(([k, v]) => (
              <div key={k} className="min-w-0"><dt className="text-faint">{k}</dt><dd className="truncate font-medium text-ink-2" title={v ?? ""}>{v ?? "—"}</dd></div>
            ))}
          </dl>
          <div className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            <button onClick={() => setTab("checks")} className="group flex items-center gap-3 rounded-2xl border border-line/80 bg-white/70 p-3 text-left transition hover:-translate-y-0.5 hover:border-brand-200 hover:bg-white">
              <Ring value={cs.pass} total={cs.pass + cs.fail} size={44} stroke={5} color={cs.fail ? "#f59331" : "#19b48a"}>
                <Icon name="check" className={`h-4 w-4 ${cs.fail ? "text-risk-mid" : "text-risk-low"}`} />
              </Ring>
              <div className="min-w-0"><div className="text-[11px] text-muted">数据核验</div>
                <div className="whitespace-nowrap text-sm font-bold text-ink"><CountUp value={cs.pass} /><span className="text-faint">/{cs.pass + cs.fail}</span> 通过</div></div>
            </button>
            <div className="rounded-2xl border border-line/80 bg-white/70 p-3">
              <div className="text-[11px] text-muted">证据条目</div>
              <div className="mt-0.5 text-xl font-extrabold text-ink"><CountUp value={Object.keys(d.evidence).length} duration={1100} /></div>
            </div>
            <div className="rounded-2xl border border-line/80 bg-white/70 p-3">
              <div className="text-[11px] text-muted">风险信号</div>
              <div className={`mt-0.5 text-xl font-extrabold ${risks.length ? "text-risk-mid" : "text-ink"}`}><CountUp value={risks.length} duration={600} /></div>
            </div>
            <div className="rounded-2xl border border-line/80 bg-white/70 p-3">
              <div className="text-[11px] text-muted">积极因素</div>
              <div className="mt-0.5 text-xl font-extrabold text-risk-low"><CountUp value={strengths.length} duration={600} /></div>
            </div>
          </div>
          {d.data_notes.length > 0 && <p className="mt-3 flex items-start gap-1.5 rounded-xl bg-[#fff6ea] px-3 py-2 text-xs text-[#9a5a0c]"><Icon name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0" />{d.data_notes.join("；")}</p>}
          <div className="mt-3 text-[10.5px] text-faint">引擎 v{d.engine_version}</div>
        </div>
        <div className={`rise relative overflow-hidden rounded-[26px] bg-gradient-to-br p-5 text-white shadow-[0_18px_40px_rgba(30,60,140,0.22)] ${LEVEL_STYLE[lv] ?? "from-slate-600 to-slate-500"}`} style={{ ["--i" as string]: 1 }}>
          <div aria-hidden className="pointer-events-none absolute -right-10 -top-12 h-40 w-40 rounded-full bg-white/15 blur-2xl" />
          <div className="flex items-center justify-between text-xs">
            <span className="font-semibold opacity-90">综合风险评级</span>
            <span className="rounded-full bg-white/20 px-2 py-0.5 text-[10.5px] font-semibold">{latest?.label}</span>
          </div>
          <div className="mt-1 flex items-end justify-between gap-2">
            <div className="pop text-[52px] font-black leading-none" style={{ ["--i" as string]: 6 }}>{d.overall.level}<span className="ml-1 text-lg font-bold opacity-90">风险</span></div>
            <RiskArc level={lv} size={132} />
          </div>
          <div className="mt-2 text-sm leading-snug opacity-95">{d.overall.headline}</div>
          <details className="group mt-2 text-[11px] opacity-90">
            <summary className="cursor-pointer select-none list-none font-semibold"><span className="inline-block transition-transform group-open:rotate-90">›</span> 评级规则</summary>
            <div className="mt-1 leading-relaxed">{d.overall.rule}</div>
          </details>
        </div>
      </section>

      <nav className="glass sticky top-[4.5rem] z-20 mt-5 overflow-x-auto rounded-2xl p-1 backdrop-blur-xl thin-scroll">
        <SlidingTabs items={TABS.map((t) => ({
          ...t,
          badge: t.key === "risk" && risks.length > 0 ? <span className={`ml-0.5 grid h-[18px] min-w-[18px] place-items-center rounded-full px-1 text-[10.5px] ${tab === "risk" ? "bg-white/25 text-white" : "bg-[#fff1e2] text-risk-mid"}`}>{risks.length}</span> : undefined,
        }))} value={tab} onChange={setTab} />
      </nav>
      <div key={tab} className="rise mt-5">
        {tab === "risk" && <RiskTab d={d} />}
        {tab === "brief" && <BriefTab d={d} />}
        {tab === "ask" && <AskTab d={d} />}
        {tab === "metrics" && <MetricsTab d={d} />}
        {tab === "statements" && <StatementsTab d={d} />}
        {tab === "checks" && <ChecksTab d={d} />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 风险体检

function SignalCard({ s, code, i }: { s: Signal; code: string; i: number }) {
  const strength = s.polarity === "strength";
  const sev = strength ? sevOf("info") : sevOf(s.severity);
  const color = strength ? SEV_COLOR.info : SEV_COLOR[s.severity];
  return (
    <li className="glass fx-spot lift rise relative overflow-hidden rounded-2xl p-4 pl-5" style={{ ["--i" as string]: i }}>
      <span aria-hidden className="absolute bottom-3 left-0 top-3 w-1 rounded-r-full" style={{ background: color, boxShadow: `0 0 12px ${color}88` }} />
      <div className="flex flex-wrap items-center gap-2">
        <span className="chip font-bold ring-1" style={{ color, background: `${color}14`, ["--tw-ring-color" as string]: `${color}40` }}>
          <Icon name={strength ? "star" : "alert"} className="h-3.5 w-3.5" />{strength ? "亮点" : `${sev.label}风险`}
        </span>
        <span className="font-bold text-ink">{s.title}</span>
        <span className="num ml-auto text-[11px] text-faint">{s.rule} · {THRESHOLD_TYPE[s.threshold_type]}</span>
      </div>
      <p className="mt-2 text-[13.5px] leading-relaxed text-ink-2"><CitedText text={s.summary} code={code} /></p>
      {s.rationale && <p className="mt-2 rounded-xl bg-[#f4f7fd] px-3 py-2 text-xs leading-relaxed text-muted"><b className="font-semibold text-ink-2">依据</b>　{s.rationale}{s.reference ? `（${s.reference}）` : ""}</p>}
    </li>
  );
}

const DIM_SHORT: Record<string, string> = { profitability: "盈利能力", earnings_quality: "盈利质量", solvency: "偿债", asset_quality: "资产质量",
  efficiency: "营运效率", growth: "成长性", governance: "治理", models: "预警模型" };
const LEVEL_R: Record<string, number> = { none: 0.22, info: 0.22, low: 0.48, medium: 0.74, high: 1 };

/** 风险雷达：各维度的分类风险等级（无 / 低 / 中 / 高），不是连续分数。 */
function RiskRadar({ dims }: { dims: DimensionSummary[] }) {
  const S = 250, c = S / 2, R = 80;
  const n = dims.length;
  const pt = (i: number, k: number) => {
    const a = -Math.PI / 2 + (i / n) * Math.PI * 2;
    return [c + Math.cos(a) * R * k, c + Math.sin(a) * R * k] as const;
  };
  const poly = dims.map((dm, i) => pt(i, LEVEL_R[dm.level] ?? 0.22).join(",")).join(" ");
  const [on, setOn] = useState(false);
  useEffect(() => { const t = requestAnimationFrame(() => setOn(true)); return () => cancelAnimationFrame(t); }, []);
  const worst = dims.some((x) => x.level === "high") ? "#ef4462" : dims.some((x) => x.level === "medium") ? "#f59331" : "#2262f3";
  return (
    <svg viewBox={`0 0 ${S} ${S}`} className="mx-auto w-full max-w-[260px] overflow-visible" role="img" aria-label="分维度风险雷达">
      {[0.25, 0.5, 0.75, 1].map((k) => (
        <polygon key={k} points={dims.map((_, i) => pt(i, k).join(",")).join(" ")} fill={k === 1 ? "#f6f9ff" : "none"} stroke="#dce6f7" strokeWidth="1" />
      ))}
      {dims.map((_, i) => { const [x, y] = pt(i, 1); return <line key={i} x1={c} y1={c} x2={x} y2={y} stroke="#e6edf9" />; })}
      <g style={{ transform: `scale(${on ? 1 : 0.05})`, transformOrigin: `${c}px ${c}px`, transition: "transform .9s cubic-bezier(.34,1.4,.5,1)" }}>
        <polygon points={poly} fill={`${worst}22`} stroke={worst} strokeWidth="2" strokeLinejoin="round" />
        {dims.map((dm, i) => { const [x, y] = pt(i, LEVEL_R[dm.level] ?? 0.22); return <circle key={dm.code} cx={x} cy={y} r={dm.level === "none" ? 3 : 4.5} fill="#fff" stroke={SEV_COLOR[dm.level] ?? "#25c99a"} strokeWidth="2.2" />; })}
      </g>
      {dims.map((dm, i) => {
        const [x, y] = pt(i, 1.24);
        return <text key={dm.code} x={x} y={y + 4} textAnchor={Math.abs(x - c) < 8 ? "middle" : x > c ? "start" : "end"} fontSize="10.5"
          fill={dm.level === "none" ? "#71809e" : SEV_COLOR[dm.level]} fontWeight={dm.level === "none" ? 500 : 700}>{DIM_SHORT[dm.code] ?? dm.label}</text>;
      })}
    </svg>
  );
}

function RiskTab({ d }: { d: DiagnosisResult }) {
  const code = d.profile.code;
  const open = useEvidence();
  const risks = d.signals.filter((s) => s.polarity === "risk");
  const strengths = d.signals.filter((s) => s.polarity === "strength");
  const latest = d.periods[0]?.end;
  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_330px]">
      <div className="min-w-0 space-y-6">
        <section>
          <SectionTitle icon="alert">风险信号（{risks.length}）</SectionTitle>
          {risks.length === 0 ? <p className="glass rounded-2xl p-4 text-sm text-muted">规则库未识别到风险信号。</p>
            : <ul className="space-y-2.5">{risks.map((s, i) => <SignalCard key={i} s={s} code={code} i={i} />)}</ul>}
        </section>
        {strengths.length > 0 && (
          <section>
            <SectionTitle icon="star">积极因素（{strengths.length}）</SectionTitle>
            <ul className="space-y-2.5">{strengths.map((s, i) => <SignalCard key={i} s={s} code={code} i={i + risks.length} />)}</ul>
          </section>
        )}
      </div>
      <aside className="space-y-4">
        <section className="glass rounded-[22px] p-4">
          <SectionTitle icon="layers">分维度结论</SectionTitle>
          <RiskRadar dims={d.dimensions} />
          <ul className="mt-2 space-y-1">
            {d.dimensions.map((dim, i) => {
              const sev = sevOf(dim.level === "none" ? "none" : (dim.level as never));
              const color = dim.level === "none" ? "#25c99a" : SEV_COLOR[dim.level];
              return (
                <li key={dim.code} className="rise flex items-center gap-2 rounded-lg px-1.5 py-1 text-[13px] transition-colors hover:bg-brand-50/60" style={{ ["--i" as string]: i }}>
                  <span className={`h-2 w-2 shrink-0 rounded-full ${dim.level === "high" || dim.level === "medium" ? "dot-alert" : ""}`} style={{ background: color, color }} />
                  <span className="text-ink-2">{dim.label}</span>
                  <span className="ml-auto text-xs font-medium" style={{ color: dim.level === "none" ? "#71809e" : color }}>
                    {dim.level === "none" ? "未见风险" : `${sev.label}风险 ${dim.risk_count} 项`}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
        {latest && Object.entries(d.models).map(([key, m], mi) => {
          const ev = m.values[latest] ? d.evidence[m.values[latest]!] : null;
          return (
            <section key={key} className="glass fx-spot rise rounded-[22px] p-4" style={{ ["--i" as string]: mi + 2 }}>
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="text-sm font-bold text-ink">{m.label}</h3>
                {ev && <span className="num text-xl font-extrabold text-ink">
                  {fmtValue(ev.value, ev.unit)}<Cite id={ev.id} code={code} label="源" /></span>}
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-muted">{m.description}</p>
              <ul className="mt-2.5 space-y-0.5 text-xs">
                {m.components.map((c) => {
                  const ce = d.evidence[c.values[latest]];
                  return (
                    <li key={c.code}>
                      <button disabled={!ce} onClick={() => ce && open({ id: ce.id, code })} data-noripple title={c.formula}
                        className="flex w-full justify-between gap-2 rounded-lg px-1.5 py-1 text-left text-ink-2 transition-colors hover:bg-brand-50 hover:text-brand-700 disabled:cursor-default disabled:hover:bg-transparent">
                        <span className="truncate">{c.label}</span>
                        <span className="num font-semibold">{ce ? fmtValue(ce.value, ce.unit) : "—"}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------- 在线演示：额度与缓存样例

function useDemo() {
  const [demo, setDemo] = useState<DemoStatus | null>(null);
  const refresh = useCallback(() => { api.demo().then(setDemo).catch(() => undefined); }, []);
  useEffect(refresh, [refresh]);
  const quota = demo?.live;
  const canLive = !!demo?.llm_enabled && quota?.per_ip_remaining !== 0 && quota?.daily_remaining !== 0;
  return { demo, refresh, canLive };
}

function useSamples(code: string, kind: "report" | "ask") {
  const [all, setAll] = useState<SampleMeta[]>([]);
  useEffect(() => {
    api.samples().then((xs) => setAll(xs.filter((x) => x.kind === kind))).catch(() => setAll([]));
  }, [kind]);
  const mine = useMemo(() => all.filter((x) => x.code === code), [all, code]);
  return { mine, all };
}

/** 同一公司有多个样例时，按钮上带生成日期区分；只有一个时保持简洁。 */
function sampleLabel(s: SampleMeta, many: boolean): string {
  return many && s.generated_at ? `查看 AI 样例（${s.generated_at.slice(0, 10)}）` : "查看 AI 样例（秒开）";
}

function useAlive() {
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  return alive;
}

const verifyOk = (r: Report | AgentAnswer) => {
  const v = r.verification;
  return v.mismatch + v.unverified + v.invalid_ref === 0 && v.compliance_flags.length === 0 && (v.consistency_flags ?? []).length === 0;
};

// ---------------------------------------------------------------- 研究简报

function BriefTab({ d }: { d: DiagnosisResult }) {
  const code = d.profile.code;
  const alive = useAlive();
  const toast = useToast();
  const { demo, refresh, canLive } = useDemo();
  const { mine: samples } = useSamples(code, "report");
  const [rep, setRep] = useState<Report | null>(null);
  const [cached, setCached] = useState<SampleMeta | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [celebrate, setCelebrate] = useState(0); // 用户主动操作得到的结果才放庆祝特效；自动打开的样例只做入场
  const autoOpened = useRef(false);
  const touched = useRef(false); // 用户已经自己点过按钮：不要再用样例覆盖他的操作

  const run = async (mode: "llm" | "template") => {
    touched.current = true;
    // 开始新的生成前先清掉旧结果：否则缓存样例会在进度条下面冒充“正在生成的结果”
    setBusy(true); setErr(null); setCached(null); setRep(null); setJob(null);
    try {
      const start = await api.startReport(code, mode);
      const out = await waitJob<Report>(start, (j) => { if (alive.current) setJob(j); }, () => !alive.current);
      if (alive.current) {
        setRep(out); setCached(null); setCelebrate((x) => x + 1);
        const v = out.verification;
        toast(verifyOk(out) ? `简报已生成：${v.verified + v.unattributed}/${v.total} 个数字与证据一致` : "简报已生成，存在未通过核验的内容，已在文中标出", verifyOk(out) ? "ok" : "warn");
      }
    } catch (e) {
      if (alive.current) { setErr(String((e as Error).message ?? e)); toast("生成失败", "err"); }
    } finally {
      if (alive.current) { setBusy(false); setJob(null); refresh(); }
    }
  };
  const open = useCallback(async (s: SampleMeta, manual: boolean) => {
    touched.current = manual || touched.current;
    setBusy(true); setErr(null); setJob(null);
    try {
      const full = await api.sample<Report>(s.id);
      if (alive.current) {
        setRep(full.result); setCached(s);
        if (manual) { setCelebrate((x) => x + 1); toast("已打开缓存样例", "info"); }
      }
    } catch (e) {
      if (alive.current) setErr(String((e as Error).message ?? e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }, [alive, toast]);
  // 有缓存样例时直接打开：评委进来就能看到完整的、核验过的简报，不用等
  useEffect(() => {
    if (samples.length && !autoOpened.current && !touched.current) { autoOpened.current = true; void open(samples[0], false); }
  }, [samples, open]);

  return (
    <div className="space-y-4">
      <div className="glass rounded-[22px] p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-3">
          <div className="mr-auto flex min-w-[16rem] flex-1 items-start gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-brand-500 to-cyan text-white shadow-[var(--shadow-glow)]"><Icon name="sparkle" className="h-5 w-5" /></span>
            <p className="text-[13px] leading-relaxed text-ink-2">
              大模型依据带编号的证据撰写简报，核验器逐个比对文中数字：不一致或缺少出处的打回修订，修订后仍不符的在文中标红。
            </p>
          </div>
          {samples.map((s) => (
            <button key={s.id} disabled={busy} onClick={() => open(s, true)} className="btn btn-amber">
              <Icon name="bolt" className="h-4 w-4" />{sampleLabel(s, samples.length > 1)}
            </button>
          ))}
          <button disabled={busy || !canLive} onClick={() => run("llm")}
            title={canLive ? "调用大模型现场生成，约 0.5–2 分钟" : "实时生成不可用：未配置 Key 或今日额度已用完"}
            className="btn btn-primary">
            {busy && job?.live ? <Spinner /> : <Icon name="sparkle" className="h-4 w-4" />}
            {busy && job?.live ? "生成与核验中…" : "AI 实时生成简报"}
          </button>
          <button disabled={busy} onClick={() => run("template")} className="btn btn-ghost">
            {busy && job && !job.live ? <Spinner /> : <Icon name="doc" className="h-4 w-4" />}模板简报（无需大模型）
          </button>
        </div>
        <div className="mt-2 text-right">
          <LiveQuota enabled={!!demo?.llm_enabled} limited={!!demo?.live.limited}
            remaining={demo?.live.per_ip_remaining ?? demo?.live.daily_remaining} />
        </div>
      </div>
      {job && <JobProgress job={job} />}
      {busy && !rep && <ArticleSkeleton />}
      {err && <div className="shake rounded-2xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      {rep && (
        <div key={(cached?.id ?? "") + rep.generated_at} className="space-y-3">
          {cached && <CachedBadge s={cached} />}
          <VerificationBar v={rep.verification} celebrate={celebrate} />
          <div className="rise flex flex-wrap gap-2 text-xs text-muted" style={{ ["--i" as string]: 2 }}>
            <span className="chip border border-line bg-white/80"><Icon name="cpu" className="h-3.5 w-3.5 text-brand-500" />模式：{rep.mode === "llm" ? `大模型（${rep.model}）` : "确定性模板"}</span>
            {rep.mode === "llm" && <span className="chip border border-line bg-white/80">尝试 {rep.attempts} 次{rep.revised ? "（经核验器打回修订）" : ""}</span>}
            <span className="chip border border-line bg-white/80"><Icon name="clock" className="h-3.5 w-3.5 text-brand-500" />耗时 {(rep.elapsed_ms / 1000).toFixed(1)} 秒</span>
            {rep.usage.prompt_tokens != null && <span className="chip border border-line bg-white/80">Token：{rep.usage.prompt_tokens} 输入 / {rep.usage.completion_tokens} 输出</span>}
          </div>
          {[...new Set(rep.warnings.map(callErrorMessage))].map((w) => <div key={w} className="rounded-xl bg-[#fff6ea] p-2.5 text-xs text-[#9a5a0c]">{w}</div>)}
          <article className="glass glass-strong rise rounded-[24px] p-6 sm:p-8" style={{ ["--i" as string]: 3 }}>
            <VerifiedMarkdown text={annotatedOf(rep)} refs={rep.refs} code={d.profile.code} />
          </article>
        </div>
      )}
    </div>
  );
}

function annotatedOf(rep: Report | AgentAnswer): string {
  const a = (rep as Report & { annotated_markdown?: string; annotated_answer?: string });
  const text = a.annotated_markdown ?? a.annotated_answer ?? (rep as Report).markdown;
  return "question" in rep && rep.ok === false ? callErrorMessage(text) : text;
}

// ---------------------------------------------------------------- 智能问答

const SUGGESTIONS = [
  "近三年利润的现金含量如何？原因可能是什么？",
  "目前最主要的偿债风险是什么？请给出依据。",
  "Beneish 模型的结论由哪些分项驱动？",
  "和同业相比，毛利率和净现比处于什么水平？",
];

const TOOL_LABEL: Record<string, string> = {
  list_metrics: "指标目录", get_metrics: "取指标", get_statement_items: "取报表科目", get_risk_signals: "查风险信号",
  get_model_breakdown: "拆解模型", search_announcements: "检索公告", get_data_checks: "查数据核验", compare_peers: "同业比较",
};

function AskTab({ d }: { d: DiagnosisResult }) {
  const code = d.profile.code;
  const alive = useAlive();
  const toast = useToast();
  const { demo, refresh, canLive } = useDemo();
  const { mine: samples, all: allSamples } = useSamples(code, "ask");
  const otherSampleCos = useMemo(() => {
    const seen = new Map<string, string>();
    allSamples.forEach((x) => { if (x.code !== code && !seen.has(x.code)) seen.set(x.code, x.name); });
    return [...seen.entries()];
  }, [allSamples, code]);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<{ a: AgentAnswer; cached?: SampleMeta; key: number }[]>([]);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [focus, setFocus] = useState(false);
  const seq = useRef(0);
  const ask = async (question: string) => {
    if (!question.trim() || !canLive || busy) return; // busy：防止连按回车重复提交、重复消耗额度
    setBusy(true); setErr(null); setJob(null);
    try {
      const start = await api.startAsk(code, question);
      const a = await waitJob<AgentAnswer>(start, (j) => { if (alive.current) setJob(j); }, () => !alive.current);
      if (alive.current) {
        setItems((x) => [{ a, key: ++seq.current }, ...x]); setQ("");
        toast(a.ok === false ? "没有得到有效回答" : `回答完成：调用 ${a.steps.length} 个工具`, a.ok === false ? "warn" : "ok");
      }
    } catch (e) {
      if (alive.current) { setErr(String((e as Error).message ?? e)); toast("提问失败", "err"); }
    } finally {
      if (alive.current) { setBusy(false); setJob(null); refresh(); }
    }
  };
  const openSample = async (s: SampleMeta) => {
    setErr(null);
    try {
      const full = await api.sample<AgentAnswer>(s.id);
      if (alive.current) {
        setItems((x) => [{ a: full.result, cached: s, key: ++seq.current }, ...x.filter((i) => i.cached?.id !== s.id)]);
        toast("已打开问答样例", "info");
      }
    } catch (e) {
      if (alive.current) setErr(String((e as Error).message ?? e));
    }
  };
  return (
    <div className="space-y-4">
      <div className="glass rounded-[22px] p-4 sm:p-5">
        <div className={`flex items-center gap-2 rounded-2xl border bg-white/90 p-1.5 pl-3.5 transition-all duration-300 ${focus ? "border-brand-300 shadow-[0_0_0_4px_rgba(34,98,243,0.12)]" : "border-line"}`}>
          <Icon name="chat" className={`h-5 w-5 shrink-0 transition-colors ${focus ? "text-brand-500" : "text-faint"}`} />
          <input value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) void ask(q); }}
            placeholder={`向研究智能体提问，例如：${SUGGESTIONS[0]}`}
            className="min-w-0 flex-1 bg-transparent py-2 text-sm text-ink placeholder-faint outline-none" />
          <button disabled={busy || !canLive} onClick={() => ask(q)} className="btn btn-primary">
            {busy ? <Spinner /> : <Icon name="send" className="h-4 w-4" />}
            {busy ? "取证与作答中…" : "提问"}
          </button>
        </div>
        {samples.length > 0 && (
          <div className="mt-3">
            <div className="mb-1.5 text-xs text-muted">问答样例（kimi-k3.1-pro 生成，已核验）：</div>
            <div className="flex flex-wrap gap-2">
              {samples.map((s) => (
                <button key={s.id} onClick={() => openSample(s)}
                  className="btn-amber inline-flex items-center gap-1.5 rounded-full border border-amber-300 px-3 py-1.5 text-xs font-semibold transition hover:-translate-y-0.5 active:scale-95">
                  <Icon name="bolt" className="h-3.5 w-3.5" />{s.title}</button>
              ))}
            </div>
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {SUGGESTIONS.map((s) => (
            <button key={s} onClick={() => ask(s)} disabled={busy || !canLive}
              className="rounded-full border border-line bg-white/80 px-3 py-1.5 text-xs text-ink-2 transition hover:-translate-y-0.5 hover:border-brand-200 hover:text-brand-700 active:scale-95 disabled:opacity-50 disabled:hover:translate-y-0">{s}</button>
          ))}
          <span className="ml-auto">
            <LiveQuota enabled={!!demo?.llm_enabled} limited={!!demo?.live.limited}
              remaining={demo?.live.per_ip_remaining ?? demo?.live.daily_remaining} />
          </span>
        </div>
      </div>
      {job && <JobProgress job={job} />}
      {err && <div className="shake rounded-2xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      {items.length === 0 && !job && !canLive && samples.length === 0 && (
        <div className="glass rounded-[22px] border-dashed p-5 text-sm leading-relaxed text-muted">
          <div className="mb-2 flex items-center gap-2 font-bold text-ink-2"><Icon name="chat" className="h-4 w-4 text-brand-500" />这家公司暂无问答样例</div>
          实时提问当前不可用（{demo?.llm_enabled ? "今日实时生成额度已用完" : "服务端未配置大模型 Key"}）。
          {otherSampleCos.length > 0 && (
            <>可查看其他公司的问答样例：
              {otherSampleCos.map(([c, name]) => (
                <a key={c} href={`#/c/${c}`} className="ml-2 inline-flex items-center gap-1 font-semibold text-brand-600 hover:underline">{name}<Icon name="arrow" className="h-3 w-3" /></a>
              ))}
              。</>
          )}
          风险体检、模板简报、指标趋势等功能不依赖大模型，可直接使用。
        </div>
      )}
      {items.length === 0 && !job && samples.length > 0 && (
        <div className="glass rounded-[22px] p-5 text-center text-sm text-muted">点击上方问答样例秒开，或输入问题让智能体现场取证作答。</div>
      )}
      {items.map(({ a, cached, key }) => (
        <article key={key} className="glass glass-strong rise space-y-4 rounded-[24px] p-5 sm:p-6">
          {cached && <CachedBadge s={cached} />}
          <div className="flex items-start gap-3">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-ink text-xs font-bold text-white">问</span>
            <div className="pt-1 text-[15px] font-bold text-ink">问：{a.question}</div>
          </div>
          <div className="rounded-2xl bg-[#f4f7fd] p-3">
            <div className="mb-2 flex items-center gap-1.5 text-[11px] font-bold tracking-wide text-muted"><Icon name="tool" className="h-3.5 w-3.5" />工具调用轨迹（{a.steps.length}）</div>
            <ol className="relative space-y-1.5 pl-5">
              <span aria-hidden className="absolute bottom-2 left-[7px] top-2 w-px bg-brand-200" />
              {a.steps.map((s, j) => (
                <li key={j} className="rise relative text-xs leading-relaxed text-ink-2" style={{ ["--i" as string]: j + 1 }}>
                  <span aria-hidden className={`absolute -left-[17px] top-[5px] h-2.5 w-2.5 rounded-full border-2 border-white ${s.error ? "bg-risk-high" : "bg-brand-500"} shadow`} />
                  <span className="mr-1 rounded-md bg-white px-1.5 py-0.5 font-mono text-[11px] font-semibold text-brand-700 shadow-[inset_0_0_0_1px_var(--color-brand-100)]">{s.tool}</span>
                  {TOOL_LABEL[s.tool] && <span className="mr-1 text-faint">{TOOL_LABEL[s.tool]}</span>}
                  <span className="text-muted">（{Object.entries(s.arguments).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join("，")}）</span>→ {s.summary}
                </li>
              ))}
              {a.steps.length === 0 && <li className="text-xs text-muted">未调用工具</li>}
            </ol>
          </div>
          <div className="flex gap-3">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-brand-500 to-cyan text-white"><Icon name="sparkle" className="h-4 w-4" /></span>
            <div className="min-w-0 flex-1"><VerifiedMarkdown text={annotatedOf(a)} refs={a.refs} code={d.profile.code} local={a.evidence} /></div>
          </div>
          {a.ok === false
            ? annotatedOf(a) !== BALANCE_ERROR_MESSAGE && <div className="shake rounded-xl bg-red-50 p-2.5 text-xs text-red-700">模型调用失败或回答为空，本次没有可核验的内容。</div>
            : <VerificationBar v={a.verification} celebrate={cached ? 0 : key} />}
          {[...new Set(a.warnings.map(callErrorMessage))].filter((w) => w !== annotatedOf(a)).map((w) => <div key={w} className="text-xs text-[#9a5a0c]">{w}</div>)}
        </article>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- 指标趋势

const DIM: Record<string, string> = { profitability: "盈利能力", earnings_quality: "盈利质量", solvency: "偿债与流动性",
  asset_quality: "资产质量", efficiency: "营运效率", growth: "成长性", models: "综合模型" };

function MetricsTab({ d }: { d: DiagnosisResult }) {
  const code = d.profile.code;
  const periods = d.periods.map((p) => p.end);
  const groups = useMemo(() => {
    const g = new Map<string, MetricRow[]>();
    d.metrics.forEach((m) => g.set(m.dimension, [...(g.get(m.dimension) ?? []), m]));
    return g;
  }, [d]);
  const [sel, setSel] = useState<MetricRow>(d.metrics[0]);
  const series = useMemo(() => [{
    name: sel.label, unit: sel.unit,
    points: periods.map((p) => {
      const id = sel.values[p];
      const v = id ? d.evidence[id]?.value : null;
      return { period: p, value: typeof v === "number" ? v : null };
    }),
  }], [sel, d, periods.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="space-y-4">
      <section className="glass rounded-[22px] p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div key={sel.code} className="fade-in min-w-0">
            <div className="flex items-center gap-2">
              <span className="chip bg-brand-50 text-brand-700">{DIM[sel.dimension] ?? sel.dimension}</span>
              <span className="text-base font-bold text-ink">{sel.label}</span>
            </div>
            <div className="mt-1 text-xs text-muted">公式：{sel.formula}{sel.description ? `。${sel.description}` : ""}</div>
          </div>
          <span className="text-[11px] text-faint">点击下表指标名切换曲线 · 点击数值查看计算过程</span>
        </div>
        <Suspense fallback={<div className="skeleton mt-3 h-[240px] rounded-2xl" />}>
          <TrendChart series={series} height={240} />
        </Suspense>
      </section>
      <div className="glass overflow-x-auto rounded-[22px] thin-scroll">
        <table className="table-fin">
          <thead>
            <tr><th className="text-left">指标</th>{periods.map((p) => <th key={p} className="text-right">{year(p)}</th>)}</tr>
          </thead>
          <tbody>
            {[...groups.entries()].map(([dim, rows]) => [
              <tr key={dim}><td colSpan={periods.length + 1} className="!bg-[#f3f7fe] text-[11px] font-bold tracking-wide text-brand-700">{DIM[dim] ?? dim}</td></tr>,
              ...rows.map((m) => (
                <tr key={m.code} className={sel.code === m.code ? "!bg-brand-50" : ""}>
                  <td className={`cursor-pointer whitespace-nowrap font-medium transition-colors ${sel.code === m.code ? "!bg-brand-50 text-brand-700 shadow-[inset_3px_0_0_var(--color-brand-500)]" : "text-ink-2 hover:text-brand-700"}`}
                    onClick={() => setSel(m)} title={m.formula}>
                    {sel.code === m.code && <Icon name="chart" className="mr-1.5 inline h-3.5 w-3.5 align-[-2px]" />}{m.label}
                  </td>
                  {periods.map((p) => <ValueCell key={p} id={m.values[p]} d={d} code={code} />)}
                </tr>
              )),
            ])}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ValueCell({ id, d, code }: { id?: string | null; d: DiagnosisResult; code: string }) {
  const open = useEvidence();
  const ev = id ? d.evidence[id] : null;
  if (!ev) return <td className="text-right text-faint">—</td>;
  const na = ev.value === null;
  return (
    <td className={`font-mono num cell-val whitespace-nowrap text-right ${na ? "text-faint" : "text-ink hover:text-brand-700"}`}
      title={na ? ev.note ?? ev.status : "点击查看出处与计算过程"}
      onClick={(e) => {
        const el = e.currentTarget;
        el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
        open({ id: ev.id, code });
      }}>
      {na ? "—" : fmtValue(ev.value, ev.unit)}
    </td>
  );
}

// ---------------------------------------------------------------- 财务报表

const STATEMENTS = [{ key: "bs", label: "资产负债表", icon: "layers" }, { key: "is", label: "利润表", icon: "chart" }, { key: "cf", label: "现金流量表", icon: "db" }];

function StatementsTab({ d }: { d: DiagnosisResult }) {
  const [st, setSt] = useState("bs");
  const periods = d.periods.map((p) => p.end);
  const rows = Object.entries(d.statements[st] ?? {});
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <SlidingTabs variant="segment" items={STATEMENTS} value={st} onChange={setSt} />
        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted"><Icon name="link" className="h-3.5 w-3.5 text-brand-500" />点击任意数字查看原始字段、抓取时间与 SHA-256</span>
      </div>
      <div key={st} className="glass fade-in max-h-[70vh] overflow-auto rounded-[22px] thin-scroll">
        <table className="table-fin">
          <thead>
            <tr><th className="text-left">科目</th>{periods.map((p) => <th key={p} className="text-right">{year(p)}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map(([item, byPeriod]) => {
              const first = d.evidence[Object.values(byPeriod)[0]];
              return (
                <tr key={item}>
                  <td className="whitespace-nowrap text-ink-2">{first?.label ?? item}</td>
                  {periods.map((p) => <ValueCell key={p} id={byPeriod[p]} d={d} code={d.profile.code} />)}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 数据核验

function ChecksTab({ d }: { d: DiagnosisResult }) {
  const CAT: Record<string, { t: string; icon: string; d: string }> = {
    identity: { t: "会计恒等式", icon: "layers", d: "资产 = 负债 + 所有者权益等勾稽关系" },
    balance: { t: "平衡项", icon: "hash", d: "数据源报表中的平衡项应为零" },
    vendor: { t: "第三方交叉核验", icon: "link", d: "本系统独立计算的指标与数据源公布值比对" },
    continuity: { t: "跨期连续性", icon: "clock", d: "期初数与上期期末数一致" },
  };
  const byCat = new Map<string, typeof d.checks>();
  d.checks.forEach((c) => byCat.set(c.category, [...(byCat.get(c.category) ?? []), c]));
  const cs = d.checks_summary;
  const open = useEvidence();
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (cs.fail === 0 && cs.pass > 0) { const t = setTimeout(() => burst(host.current, { count: 16, spread: 60 }), 900); return () => clearTimeout(t); }
  }, [cs.fail, cs.pass]);
  return (
    <div className="space-y-4">
      <section className="glass flex flex-wrap items-center gap-5 rounded-[22px] p-5">
        <div ref={host}>
          <Ring value={cs.pass} total={cs.pass + cs.fail} size={86} stroke={9} color={cs.fail ? "#f59331" : "#19b48a"}>
            <span className="text-center leading-tight"><span className="block text-lg font-extrabold text-ink"><CountUp value={cs.pass} /></span><span className="num text-[10px] text-faint">/ {cs.pass + cs.fail}</span></span>
          </Ring>
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-base font-bold text-ink">{cs.fail === 0 ? "数据核验全部通过" : `${cs.fail} 项未通过`}<span className="ml-2 text-sm font-medium text-muted">通过 {cs.pass} · 未通过 {cs.fail}{cs.skip ? ` · 跳过 ${cs.skip}` : ""}</span></div>
          <p className="mt-1 text-sm text-muted">分析之前先核验数据本身：会计恒等式、数据源平衡项、跨期连续性，以及本系统独立计算的指标与数据源公布值的交叉比对。</p>
        </div>
      </section>
      <div className="grid gap-4 md:grid-cols-2">
        {[...byCat.entries()].map(([cat, items], ci) => {
          const pass = items.filter((c) => c.status === "pass").length;
          const meta = CAT[cat] ?? { t: cat, icon: "check", d: "" };
          return (
            <section key={cat} className="glass fx-spot rise rounded-[22px] p-4" style={{ ["--i" as string]: ci }}>
              <div className="mb-3 flex items-center gap-3">
                <Ring value={pass} total={items.length} size={40} stroke={4.5} color={pass === items.length ? "#19b48a" : "#f59331"} delay={ci * 120}>
                  <Icon name={meta.icon} className="h-4 w-4 text-ink-2" />
                </Ring>
                <div className="min-w-0">
                  <h3 className="text-sm font-bold text-ink">{meta.t}<span className="ml-2 text-xs font-medium text-muted">通过 {pass} / {items.length}</span></h3>
                  <div className="truncate text-[11px] text-faint">{meta.d}</div>
                </div>
              </div>
              <ul className="grid max-h-72 gap-0.5 overflow-y-auto pr-1 text-xs thin-scroll sm:grid-cols-2">
                {items.map((c, i) => (
                  <li key={c.evidence_id}>
                    <button data-noripple onClick={() => open({ id: c.evidence_id, code: d.profile.code })}
                      className="flex w-full items-start gap-1.5 rounded-lg px-1.5 py-1 text-left transition-colors hover:bg-brand-50">
                      <span className={`pop mt-[1px] grid h-4 w-4 shrink-0 place-items-center rounded-full text-[10px] font-bold text-white ${c.status === "pass" ? "bg-risk-low" : c.status === "fail" ? "bg-risk-high" : "bg-faint"}`} style={{ ["--i" as string]: Math.min(i, 24) }}>
                        {c.status === "pass" ? "✓" : c.status === "fail" ? "✕" : "–"}
                      </span>
                      <span className="text-ink-2"><span className="num text-faint">{year(c.period)}</span> {c.title}{c.note && c.status !== "pass" ? `（${c.note}）` : ""}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>
    </div>
  );
}
