import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, waitJob } from "../api";
import { fmtValue, LEVEL_STYLE, sevOf, THRESHOLD_TYPE, year } from "../format";
import type { AgentAnswer, DemoStatus, DiagnosisResult, JobStatus, MetricRow, Report, SampleMeta, Signal } from "../types";
import { Cite, CitedText, useEvidence } from "./Evidence";
import { CachedBadge, JobProgress, LiveQuota } from "./Progress";
import { VerificationBar, VerifiedMarkdown } from "./Verified";

// ECharts 体积大（约 600 KB），只有“指标趋势”页用到：按需加载，首页与简报页更快打开
const TrendChart = lazy(() => import("./TrendChart").then((m) => ({ default: m.TrendChart })));

const TABS = [
  { key: "risk", label: "风险体检" },
  { key: "brief", label: "研究简报" },
  { key: "ask", label: "智能问答" },
  { key: "metrics", label: "指标趋势" },
  { key: "statements", label: "财务报表" },
  { key: "checks", label: "数据核验" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

export function CompanyPage({ code }: { code: string }) {
  const [d, setD] = useState<DiagnosisResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("risk");
  useEffect(() => {
    setD(null);
    setErr(null);
    api.diagnosis(code).then(setD).catch((e) => setErr(String(e.message ?? e)));
  }, [code]);

  if (err) return <div className="mx-auto max-w-6xl p-6 text-red-700">无法加载 {code}：{err}</div>;
  if (!d) return <div className="mx-auto max-w-6xl p-6 text-slate-400">正在运行财务风险体检…</div>;
  const p = d.profile;
  const latest = d.periods[0];
  const cs = d.checks_summary;
  return (
    <div className="mx-auto max-w-6xl px-4 pb-16">
      <section className="mt-6 grid gap-4 md:grid-cols-[1fr_280px]">
        <div className="rounded-2xl border bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="text-2xl font-semibold text-slate-900">{p.name}</h1>
            <span className="font-mono text-slate-500">{p.code}.{p.exchange}</span>
            {p.industry_em && <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{p.industry_em}</span>}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500 sm:grid-cols-4">
            <span>最新年报：{latest?.label ?? "—"}</span>
            <span>审计意见：{latest?.audit_opinion ?? "—"}</span>
            <span className="truncate" title={p.auditor ?? ""}>审计机构：{p.auditor ?? "—"}</span>
            <span>上市日期：{p.listing_date ?? "—"}</span>
          </div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700 ring-1 ring-emerald-200">
              数据核验 {cs.pass} 项通过 · {cs.fail} 项未通过
            </span>
            <span className="rounded-full bg-slate-50 px-2.5 py-1 text-slate-600 ring-1 ring-slate-200">
              证据条目 {Object.keys(d.evidence).length}
            </span>
            <span className="rounded-full bg-slate-50 px-2.5 py-1 text-slate-600 ring-1 ring-slate-200">引擎 v{d.engine_version}</span>
          </div>
          {d.data_notes.length > 0 && <p className="mt-2 text-xs text-amber-700">{d.data_notes.join("；")}</p>}
        </div>
        <div className={`rounded-2xl bg-gradient-to-br p-5 text-white shadow-sm ${LEVEL_STYLE[d.overall.level_code] ?? "from-slate-600 to-slate-500"}`}>
          <div className="text-xs opacity-90">综合风险评级</div>
          <div className="mt-1 text-4xl font-bold">{d.overall.level}</div>
          <div className="mt-2 text-sm leading-snug opacity-95">{d.overall.headline}</div>
          <details className="mt-2 text-[11px] opacity-90"><summary className="cursor-pointer">评级规则</summary>{d.overall.rule}</details>
        </div>
      </section>

      <nav className="sticky top-14 z-10 mt-5 flex gap-1 overflow-x-auto border-b bg-slate-50/95 backdrop-blur">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`whitespace-nowrap border-b-2 px-4 py-2.5 text-sm ${tab === t.key ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}>
            {t.label}
          </button>
        ))}
      </nav>
      <div className="mt-5">
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

function SignalCard({ s, code }: { s: Signal; code: string }) {
  const sev = s.polarity === "strength" ? sevOf("info") : sevOf(s.severity);
  return (
    <li className="rounded-xl border bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-md px-2 py-0.5 text-xs font-medium ring-1 ${sev.cls}`}>
          {s.polarity === "strength" ? "亮点" : `${sev.label}风险`}
        </span>
        <span className="font-medium text-slate-900">{s.title}</span>
        <span className="ml-auto text-[11px] text-slate-400">{s.rule} · {THRESHOLD_TYPE[s.threshold_type]}</span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-slate-700"><CitedText text={s.summary} code={code} /></p>
      {s.rationale && <p className="mt-1.5 text-xs leading-relaxed text-slate-500">依据：{s.rationale}{s.reference ? `（${s.reference}）` : ""}</p>}
    </li>
  );
}

function RiskTab({ d }: { d: DiagnosisResult }) {
  const code = d.profile.code;
  const risks = d.signals.filter((s) => s.polarity === "risk");
  const strengths = d.signals.filter((s) => s.polarity === "strength");
  const latest = d.periods[0]?.end;
  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
      <div className="space-y-5">
        <section>
          <h2 className="mb-2 text-sm font-semibold text-slate-700">风险信号（{risks.length}）</h2>
          {risks.length === 0 ? <p className="rounded-xl border bg-white p-4 text-sm text-slate-500">规则库未识别到风险信号。</p>
            : <ul className="space-y-2">{risks.map((s, i) => <SignalCard key={i} s={s} code={code} />)}</ul>}
        </section>
        {strengths.length > 0 && (
          <section>
            <h2 className="mb-2 text-sm font-semibold text-slate-700">积极因素（{strengths.length}）</h2>
            <ul className="space-y-2">{strengths.map((s, i) => <SignalCard key={i} s={s} code={code} />)}</ul>
          </section>
        )}
      </div>
      <aside className="space-y-5">
        <section className="rounded-xl border bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-slate-700">分维度结论</h2>
          <ul className="space-y-1.5">
            {d.dimensions.map((dim) => {
              const sev = sevOf(dim.level === "none" ? "none" : (dim.level as never));
              return (
                <li key={dim.code} className="flex items-center gap-2 text-sm">
                  <span className={`h-2 w-2 rounded-full ${dim.level === "none" ? "bg-emerald-400" : sev.dot}`} />
                  <span className="text-slate-700">{dim.label}</span>
                  <span className="ml-auto text-xs text-slate-500">
                    {dim.level === "none" ? "未见风险" : `${sev.label}风险 ${dim.risk_count} 项`}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
        {latest && Object.entries(d.models).map(([key, m]) => {
          const ev = m.values[latest] ? d.evidence[m.values[latest]!] : null;
          return (
            <section key={key} className="rounded-xl border bg-white p-4 shadow-sm">
              <div className="flex items-baseline justify-between">
                <h3 className="text-sm font-semibold text-slate-700">{m.label}</h3>
                {ev && <span className="font-mono text-lg font-semibold tabular-nums text-slate-900">
                  {fmtValue(ev.value, ev.unit)}<Cite id={ev.id} code={code} label="源" /></span>}
              </div>
              <p className="mt-1 text-[11px] text-slate-500">{m.description}</p>
              <ul className="mt-2 space-y-0.5 text-xs">
                {m.components.map((c) => {
                  const ce = d.evidence[c.values[latest]];
                  return (
                    <li key={c.code} className="flex justify-between gap-2 text-slate-600">
                      <span className="truncate" title={c.formula}>{c.label}</span>
                      <span className="font-mono tabular-nums">{ce ? fmtValue(ce.value, ce.unit) : "—"}</span>
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

// ---------------------------------------------------------------- 研究简报

function BriefTab({ d }: { d: DiagnosisResult }) {
  const code = d.profile.code;
  const alive = useAlive();
  const { demo, refresh, canLive } = useDemo();
  const { mine: samples } = useSamples(code, "report");
  const [rep, setRep] = useState<Report | null>(null);
  const [cached, setCached] = useState<SampleMeta | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const autoOpened = useRef(false);
  const touched = useRef(false); // 用户已经自己点过按钮：不要再用样例覆盖他的操作

  const run = async (mode: "llm" | "template") => {
    touched.current = true;
    // 开始新的生成前先清掉旧结果：否则缓存样例会在进度条下面冒充“正在生成的结果”
    setBusy(true); setErr(null); setCached(null); setRep(null); setJob(null);
    try {
      const start = await api.startReport(code, mode);
      const out = await waitJob<Report>(start, (j) => { if (alive.current) setJob(j); }, () => !alive.current);
      if (alive.current) { setRep(out); setCached(null); }
    } catch (e) {
      if (alive.current) setErr(String((e as Error).message ?? e));
    } finally {
      if (alive.current) { setBusy(false); setJob(null); refresh(); }
    }
  };
  const open = useCallback(async (s: SampleMeta) => {
    touched.current = true;
    setBusy(true); setErr(null); setJob(null);
    try {
      const full = await api.sample<Report>(s.id);
      if (alive.current) { setRep(full.result); setCached(s); }
    } catch (e) {
      if (alive.current) setErr(String((e as Error).message ?? e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }, [alive]);
  // 有缓存样例时直接打开：评委进来就能看到完整的、核验过的简报，不用等
  useEffect(() => {
    if (samples.length && !autoOpened.current && !touched.current) { autoOpened.current = true; void open(samples[0]); }
  }, [samples, open]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-white p-4 shadow-sm">
        <div className="mr-auto min-w-[16rem] flex-1 text-sm text-slate-600">
          大模型只能使用带编号的证据撰写简报；核验器逐个比对其中的数字，不一致或无出处的会被打回修订，仍不合格的在文中标红。
        </div>
        {samples.map((s) => (
          <button key={s.id} disabled={busy} onClick={() => open(s)}
            className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900 hover:bg-amber-100 disabled:opacity-60">
            {sampleLabel(s, samples.length > 1)}
          </button>
        ))}
        <button disabled={busy || !canLive} onClick={() => run("llm")}
          title={canLive ? "调用大模型现场生成，约 0.5–2 分钟" : "实时生成不可用：未配置 Key 或今日额度已用完"}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
          {busy && job?.live ? "生成与核验中…" : "AI 实时生成简报"}
        </button>
        <button disabled={busy} onClick={() => run("template")}
          className="rounded-lg border px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-60">
          模板简报（无需大模型）
        </button>
        <div className="basis-full text-right">
          <LiveQuota enabled={!!demo?.llm_enabled} limited={!!demo?.live.limited}
            remaining={demo?.live.per_ip_remaining ?? demo?.live.daily_remaining} />
        </div>
      </div>
      {job && <JobProgress job={job} />}
      {err && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      {rep && (
        <>
          {cached && <CachedBadge s={cached} />}
          <VerificationBar v={rep.verification} />
          <div className="flex flex-wrap gap-3 text-xs text-slate-500">
            <span>模式：{rep.mode === "llm" ? `大模型（${rep.model}）` : "确定性模板"}</span>
            {rep.mode === "llm" && <span>尝试 {rep.attempts} 次{rep.revised ? "（经核验器打回修订）" : ""}</span>}
            <span>耗时 {(rep.elapsed_ms / 1000).toFixed(1)} 秒</span>
            {rep.usage.prompt_tokens != null && <span>Token：{rep.usage.prompt_tokens} 输入 / {rep.usage.completion_tokens} 输出</span>}
          </div>
          {rep.warnings.map((w) => <div key={w} className="rounded bg-amber-50 p-2 text-xs text-amber-800">{w}</div>)}
          <article className="rounded-2xl border bg-white p-6 shadow-sm">
            <VerifiedMarkdown text={annotatedOf(rep)} refs={rep.refs} code={d.profile.code} />
          </article>
        </>
      )}
    </div>
  );
}

function annotatedOf(rep: Report | AgentAnswer): string {
  const a = (rep as Report & { annotated_markdown?: string; annotated_answer?: string });
  return a.annotated_markdown ?? a.annotated_answer ?? (rep as Report).markdown;
}

// ---------------------------------------------------------------- 智能问答

const SUGGESTIONS = [
  "近三年利润的现金含量如何？原因可能是什么？",
  "目前最主要的偿债风险是什么？请给出依据。",
  "Beneish 模型的结论由哪些分项驱动？",
  "和同业相比，毛利率和净现比处于什么水平？",
];

function AskTab({ d }: { d: DiagnosisResult }) {
  const code = d.profile.code;
  const alive = useAlive();
  const { demo, refresh, canLive } = useDemo();
  const { mine: samples, all: allSamples } = useSamples(code, "ask");
  const otherSampleCos = useMemo(() => {
    const seen = new Map<string, string>();
    allSamples.forEach((x) => { if (x.code !== code && !seen.has(x.code)) seen.set(x.code, x.name); });
    return [...seen.entries()];
  }, [allSamples, code]);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<{ a: AgentAnswer; cached?: SampleMeta }[]>([]);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ask = async (question: string) => {
    if (!question.trim() || !canLive || busy) return; // busy：防止连按回车重复提交、重复消耗额度
    setBusy(true); setErr(null); setJob(null);
    try {
      const start = await api.startAsk(code, question);
      const a = await waitJob<AgentAnswer>(start, (j) => { if (alive.current) setJob(j); }, () => !alive.current);
      if (alive.current) { setItems((x) => [{ a }, ...x]); setQ(""); }
    } catch (e) {
      if (alive.current) setErr(String((e as Error).message ?? e));
    } finally {
      if (alive.current) { setBusy(false); setJob(null); refresh(); }
    }
  };
  const openSample = async (s: SampleMeta) => {
    setErr(null);
    try {
      const full = await api.sample<AgentAnswer>(s.id);
      if (alive.current) setItems((x) => [{ a: full.result, cached: s }, ...x.filter((i) => i.cached?.id !== s.id)]);
    } catch (e) {
      if (alive.current) setErr(String((e as Error).message ?? e));
    }
  };
  return (
    <div className="space-y-4">
      <div className="rounded-xl border bg-white p-4 shadow-sm">
        <div className="flex gap-2">
          <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) void ask(q); }}
            placeholder={`向研究智能体提问，例如：${SUGGESTIONS[0]}`}
            className="flex-1 rounded-lg border px-3 py-2 text-sm outline-none focus:border-brand-500" />
          <button disabled={busy || !canLive} onClick={() => ask(q)}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
            {busy ? "取证与作答中…" : "提问"}
          </button>
        </div>
        {samples.length > 0 && (
          <div className="mt-3">
            <div className="mb-1 text-xs text-slate-500">缓存样例问答（真实模型已生成并核验，点击秒开）：</div>
            <div className="flex flex-wrap gap-2">
              {samples.map((s) => (
                <button key={s.id} onClick={() => openSample(s)}
                  className="rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs text-amber-900 hover:bg-amber-100">{s.title}</button>
              ))}
            </div>
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {SUGGESTIONS.map((s) => (
            <button key={s} onClick={() => ask(s)} disabled={busy || !canLive}
              className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600 hover:bg-slate-200 disabled:opacity-50">{s}</button>
          ))}
          <span className="ml-auto">
            <LiveQuota enabled={!!demo?.llm_enabled} limited={!!demo?.live.limited}
              remaining={demo?.live.per_ip_remaining ?? demo?.live.daily_remaining} />
          </span>
        </div>
      </div>
      {job && <JobProgress job={job} />}
      {err && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      {items.length === 0 && !job && !canLive && samples.length === 0 && (
        <div className="rounded-xl border border-dashed bg-white p-4 text-sm leading-relaxed text-slate-500">
          这家公司还没有缓存的问答样例，当前也不能实时提问（{demo?.llm_enabled ? "今日实时生成额度已用完" : "服务端未配置大模型 Key"}）。
          {otherSampleCos.length > 0 && (
            <>可以先看看其他公司的真实问答样例：
              {otherSampleCos.map(([c, name]) => (
                <a key={c} href={`#/c/${c}`} className="ml-2 text-brand-700 underline">{name}</a>
              ))}
              。</>
          )}
          “风险体检”“研究简报（模板）”“指标趋势”等功能不调用大模型，随时可用。
        </div>
      )}
      {items.map(({ a, cached }, i) => (
        <article key={i} className="space-y-3 rounded-2xl border bg-white p-5 shadow-sm">
          {cached && <CachedBadge s={cached} />}
          <div className="text-sm font-medium text-slate-900">问：{a.question}</div>
          <ol className="space-y-1 border-l-2 border-brand-100 pl-3 text-xs text-slate-500">
            {a.steps.map((s, j) => (
              <li key={j}>
                <span className="font-mono text-brand-700">{s.tool}</span>（{Object.entries(s.arguments).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join("，")}）→ {s.summary}
              </li>
            ))}
            {a.steps.length === 0 && <li>未调用工具</li>}
          </ol>
          <VerifiedMarkdown text={annotatedOf(a)} refs={a.refs} code={d.profile.code} local={a.evidence} />
          {a.ok === false
            ? <div className="rounded bg-red-50 p-2 text-xs text-red-700">本次没有得到有效回答（模型调用失败或回答为空），不进行核验统计。</div>
            : <VerificationBar v={a.verification} />}
          {a.warnings.map((w) => <div key={w} className="text-xs text-amber-700">{w}</div>)}
        </article>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- 指标趋势

function MetricsTab({ d }: { d: DiagnosisResult }) {
  const code = d.profile.code;
  const periods = d.periods.map((p) => p.end);
  const groups = useMemo(() => {
    const g = new Map<string, MetricRow[]>();
    d.metrics.forEach((m) => g.set(m.dimension, [...(g.get(m.dimension) ?? []), m]));
    return g;
  }, [d]);
  const [sel, setSel] = useState<MetricRow>(d.metrics[0]);
  const series = [{
    name: sel.label, unit: sel.unit,
    points: periods.map((p) => {
      const id = sel.values[p];
      const v = id ? d.evidence[id]?.value : null;
      return { period: p, value: typeof v === "number" ? v : null };
    }),
  }];
  const DIM: Record<string, string> = { profitability: "盈利能力", earnings_quality: "盈利质量", solvency: "偿债与流动性",
    asset_quality: "资产质量", efficiency: "营运效率", growth: "成长性", models: "综合模型" };
  return (
    <div className="space-y-4">
      <section className="rounded-xl border bg-white p-4 shadow-sm">
        <div className="mb-1 text-sm font-semibold text-slate-700">{sel.label}</div>
        <div className="text-xs text-slate-500">公式：{sel.formula}{sel.description ? `。${sel.description}` : ""}</div>
        <Suspense fallback={<div className="flex h-[220px] items-center justify-center text-xs text-slate-400">图表加载中…</div>}>
          <TrendChart series={series} />
        </Suspense>
      </section>
      <div className="overflow-x-auto rounded-xl border bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr><th className="px-3 py-2 text-left">指标</th>{periods.map((p) => <th key={p} className="px-3 py-2 text-right">{year(p)}</th>)}</tr>
          </thead>
          <tbody>
            {[...groups.entries()].map(([dim, rows]) => [
              <tr key={dim} className="bg-slate-50/60"><td colSpan={periods.length + 1} className="px-3 py-1.5 text-xs font-semibold text-slate-600">{DIM[dim] ?? dim}</td></tr>,
              ...rows.map((m) => (
                <tr key={m.code} className={`border-t hover:bg-brand-50/40 ${sel.code === m.code ? "bg-brand-50/60" : ""}`}>
                  <td className="cursor-pointer px-3 py-1.5 text-slate-700" onClick={() => setSel(m)} title={m.formula}>{m.label}</td>
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
  if (!ev) return <td className="px-3 py-1.5 text-right text-slate-300">—</td>;
  const na = ev.value === null;
  return (
    <td className={`cursor-pointer px-3 py-1.5 text-right font-mono tabular-nums ${na ? "text-slate-300" : "text-slate-800 hover:text-brand-700"}`}
      title={na ? ev.note ?? ev.status : "点击查看出处与计算过程"} onClick={() => open({ id: ev.id, code })}>
      {na ? "—" : fmtValue(ev.value, ev.unit)}
    </td>
  );
}

// ---------------------------------------------------------------- 财务报表

const STATEMENTS = [{ key: "bs", label: "资产负债表" }, { key: "is", label: "利润表" }, { key: "cf", label: "现金流量表" }];

function StatementsTab({ d }: { d: DiagnosisResult }) {
  const [st, setSt] = useState("bs");
  const periods = d.periods.map((p) => p.end);
  const rows = Object.entries(d.statements[st] ?? {});
  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        {STATEMENTS.map((s) => (
          <button key={s.key} onClick={() => setSt(s.key)}
            className={`rounded-lg px-3 py-1.5 text-sm ${st === s.key ? "bg-brand-600 text-white" : "border bg-white text-slate-600"}`}>{s.label}</button>
        ))}
        <span className="ml-auto self-center text-xs text-slate-400">点击任意数字查看原始字段、抓取时间与 SHA-256</span>
      </div>
      <div className="overflow-x-auto rounded-xl border bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr><th className="px-3 py-2 text-left">科目</th>{periods.map((p) => <th key={p} className="px-3 py-2 text-right">{year(p)}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map(([item, byPeriod]) => {
              const first = d.evidence[Object.values(byPeriod)[0]];
              return (
                <tr key={item} className="border-t hover:bg-brand-50/30">
                  <td className="px-3 py-1.5 text-slate-700">{first?.label ?? item}</td>
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
  const CAT: Record<string, string> = { identity: "会计恒等式", balance: "平衡项", vendor: "第三方交叉核验", continuity: "跨期连续性" };
  const byCat = new Map<string, typeof d.checks>();
  d.checks.forEach((c) => byCat.set(c.category, [...(byCat.get(c.category) ?? []), c]));
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">分析之前先核验数据本身：会计恒等式、数据源平衡项、跨期连续性，以及本系统独立计算的指标与数据源公布值的交叉比对。</p>
      {[...byCat.entries()].map(([cat, items]) => (
        <section key={cat} className="rounded-xl border bg-white p-4 shadow-sm">
          <h3 className="mb-2 text-sm font-semibold text-slate-700">
            {CAT[cat] ?? cat}
            <span className="ml-2 text-xs font-normal text-slate-500">
              通过 {items.filter((c) => c.status === "pass").length} / {items.length}
            </span>
          </h3>
          <ul className="grid gap-1 text-xs sm:grid-cols-2">
            {items.map((c) => (
              <li key={c.evidence_id} className="flex items-start gap-2">
                <span className={c.status === "pass" ? "text-emerald-600" : c.status === "fail" ? "text-red-600" : "text-slate-400"}>
                  {c.status === "pass" ? "✓" : c.status === "fail" ? "✕" : "–"}
                </span>
                <span className="text-slate-600">{year(c.period)} {c.title}{c.note && c.status !== "pass" ? `（${c.note}）` : ""}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
