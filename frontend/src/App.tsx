import { useEffect, useState } from "react";
import { api } from "./api";
import { CompanyPage } from "./components/Company";
import { EvidenceProvider } from "./components/Evidence";
import { LEVEL_STYLE } from "./format";
import type { CompanyBrief, DiagnosisResult } from "./types";

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

function Search() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<{ code: string; name: string; available: boolean }[]>([]);
  useEffect(() => {
    if (!q.trim()) { setRes([]); return; }
    const t = setTimeout(() => api.search(q).then(setRes).catch(() => setRes([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <div className="relative w-full max-w-sm">
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="输入代码 / 名称 / 拼音首字母"
        className="w-full rounded-lg border border-white/20 bg-white/10 px-3 py-1.5 text-sm text-white placeholder-white/60 outline-none focus:bg-white/20" />
      {res.length > 0 && (
        <ul className="absolute right-0 top-10 z-30 w-full overflow-hidden rounded-lg border bg-white text-sm shadow-xl">
          {res.map((r) => (
            <li key={r.code}>
              <button disabled={!r.available} onClick={() => { setQ(""); go(r.code); }}
                className="flex w-full justify-between px-3 py-2 text-left hover:bg-brand-50 disabled:cursor-not-allowed disabled:opacity-50">
                <span className="text-slate-800">{r.name} <span className="font-mono text-slate-400">{r.code}</span></span>
                <span className="text-xs text-slate-400">{r.available ? "可分析" : "暂无快照"}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CompanyCard({ c }: { c: CompanyBrief }) {
  const [d, setD] = useState<DiagnosisResult | null>(null);
  useEffect(() => { api.diagnosis(c.code).then(setD).catch(() => undefined); }, [c.code]);
  return (
    <button onClick={() => go(c.code)} className="group rounded-xl border bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-medium text-slate-900 group-hover:text-brand-700">{c.name}</div>
          <div className="font-mono text-xs text-slate-400">{c.code}</div>
        </div>
        {d ? (
          <span className={`rounded-md bg-gradient-to-br px-2 py-0.5 text-xs font-semibold text-white ${LEVEL_STYLE[d.overall.level_code] ?? "from-slate-500 to-slate-400"}`}>
            {d.overall.level}风险
          </span>
        ) : <span className="text-xs text-slate-300">体检中…</span>}
      </div>
      <div className="mt-2 truncate text-xs text-slate-500">{c.industry ?? (c.company_type === "bank" ? "银行" : "—")}</div>
      {d && <div className="mt-1 text-[11px] text-slate-400">核验 {d.checks_summary.pass}/{d.checks_summary.pass + d.checks_summary.fail} 通过 · 信号 {d.signals.filter((s) => s.polarity === "risk").length}</div>}
    </button>
  );
}

function Home() {
  const [list, setList] = useState<CompanyBrief[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.available().then(setList).catch((e) => setErr(String(e.message ?? e))); }, []);
  const PILLARS = [
    { t: "每个数字都有出处", d: "原始响应快照带 URL、抓取时间与 SHA-256，任意数字一键回溯到原始字段。" },
    { t: "先核验数据，再做分析", d: "会计恒等式、跨期连续性、与数据源公布指标交叉比对，数据可信才进入分析。" },
    { t: "AI 结论逐条核验", d: "大模型只能引用证据作答；核验器逐个比对数字，编造与不一致会被打回或标红。" },
  ];
  return (
    <div className="mx-auto max-w-6xl px-4 pb-16">
      <section className="mt-8 rounded-2xl bg-gradient-to-br from-brand-700 to-brand-500 p-8 text-white shadow">
        <div className="text-sm opacity-80">AI+金融赋能 · 投研分析场景</div>
        <h1 className="mt-2 text-3xl font-semibold">可验证的上市公司投研智能体</h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed opacity-90">
          把“取数—核验—指标—风险排查—撰写—复核”的投研流程交给智能体自动完成，同时把每一个数字的证据链交还给研究员。
        </p>
        <div className="mt-6 grid gap-3 md:grid-cols-3">
          {PILLARS.map((p) => (
            <div key={p.t} className="rounded-xl bg-white/10 p-4 ring-1 ring-white/15">
              <div className="font-medium">{p.t}</div>
              <div className="mt-1 text-xs leading-relaxed opacity-85">{p.d}</div>
            </div>
          ))}
        </div>
      </section>
      <h2 className="mb-3 mt-8 text-sm font-semibold text-slate-700">样本公司（基于真实公开数据快照）</h2>
      {err && <div className="rounded bg-red-50 p-3 text-sm text-red-700">后端不可用：{err}</div>}
      {!list && !err && <div className="text-sm text-slate-400">加载中…</div>}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {list?.map((c) => <CompanyCard key={c.code} c={c} />)}
      </div>
      <p className="mt-8 text-xs leading-relaxed text-slate-400">
        数据来源：东方财富 F10 公开财务数据、巨潮资讯（证监会指定信息披露平台）。本系统输出为财务风险排查线索，不构成投资建议。
      </p>
    </div>
  );
}

export default function App() {
  const route = useRoute();
  const m = route.match(/^#\/c\/(\d{6})/);
  return (
    <EvidenceProvider>
      <header className="sticky top-0 z-20 bg-brand-700 text-white shadow">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
          <a href="#/" className="flex items-center gap-2 font-semibold">
            <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="7" fill="#fff" fillOpacity=".15" /><path d="M9 22l5-6 4 3 6-8" stroke="#fff" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
            FinSight
          </a>
          <span className="hidden text-xs opacity-70 sm:inline">每一个数字都有出处，每一个结论都可核验</span>
          <div className="ml-auto flex w-full max-w-sm justify-end"><Search /></div>
        </div>
      </header>
      {m ? <CompanyPage code={m[1]} /> : <Home />}
    </EvidenceProvider>
  );
}
