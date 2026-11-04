import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { fmtValue, shortHash, year } from "../format";
import { pulseAt, useToast } from "../fx";
import type { Evidence, EvidenceDetail } from "../types";
import { Icon, Spinner } from "./ui";

// ---------------------------------------------------------------- 证据抽屉（全局）

interface OpenArgs {
  id: string;
  code: string;
  local?: Evidence; // 问答中的同业证据等本地对象
}
const Ctx = createContext<(a: OpenArgs) => void>(() => {});
export const useEvidence = () => useContext(Ctx);

const KIND: Record<string, string> = {
  fact: "原始数据", metric: "计算指标", check: "数据核验", signal: "风险信号", model: "模型",
  text: "文本证据", event: "公告事件", param: "阈值参数",
};
const KIND_ICON: Record<string, string> = {
  fact: "db", metric: "cpu", check: "check", signal: "alert", model: "layers", text: "doc", event: "doc", param: "hash",
};
const KIND_COLOR: Record<string, string> = {
  fact: "#0fa6c7", metric: "#2262f3", check: "#19b48a", signal: "#f59331", model: "#6b5cff", text: "#71809e", event: "#71809e", param: "#8a63d2",
};

export function EvidenceProvider({ children }: { children: ReactNode }) {
  const [args, setArgs] = useState<OpenArgs | null>(null);
  const [closing, setClosing] = useState(false);
  const [detail, setDetail] = useState<EvidenceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const open = useCallback((a: OpenArgs) => { window.clearTimeout(timer.current); setClosing(false); setArgs(a); }, []);
  const close = useCallback(() => {
    setClosing(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { setArgs(null); setClosing(false); }, 230);
  }, []);

  useEffect(() => {
    if (!args) return;
    setDetail(null);
    setError(null);
    // 同业证据 ID 形如 “000858|m:roe@2025-12-31”
    const [code, id] = args.id.includes("|") ? args.id.split("|", 2) : [args.code, args.id];
    api.evidence(code, id).then(setDetail).catch((e) => {
      if (args.local) setDetail({ evidence: args.local, chain: [args.local], snapshots: {} });
      else setError(String(e.message ?? e));
    });
  }, [args]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);

  return (
    <Ctx.Provider value={open}>
      {children}
      {args && (
        <div className={`fixed inset-0 z-50 flex justify-end ${closing ? "drawer-out" : ""}`} role="dialog" aria-modal="true" aria-label="证据溯源">
          <div className="drawer-backdrop absolute inset-0 bg-[#152445]/25 backdrop-blur-[3px]" onClick={close} />
          <aside className="drawer-panel thin-scroll relative h-full w-full max-w-xl overflow-y-auto border-l border-white/70 bg-[#f7faff]/95 shadow-[-24px_0_60px_rgba(30,50,100,0.18)] backdrop-blur-xl">
            <div className="sticky top-0 z-10 flex items-center justify-between border-b border-line/80 bg-white/80 px-5 py-3 backdrop-blur-xl">
              <div className="flex items-center gap-2 text-sm font-bold text-ink">
                <span className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-brand-500 to-cyan text-white"><Icon name="link" className="h-4 w-4" /></span>
                证据溯源
              </div>
              <button className="btn btn-ghost !rounded-xl !px-2.5 !py-1.5 text-xs" onClick={close}>关闭<Icon name="x" className="h-3.5 w-3.5" /><span className="kbd ml-1 hidden sm:inline">Esc</span></button>
            </div>
            <div key={args.id} className="space-y-4 p-5">
              {error && <div className="shake rounded-2xl bg-red-50 p-3 text-sm text-red-700">加载失败：{error}</div>}
              {!detail && !error && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-sm text-muted"><Spinner className="h-4 w-4 text-brand-500" />沿输入回溯证据链…</div>
                  <div className="skeleton h-24 rounded-2xl" /><div className="skeleton h-16 rounded-2xl" /><div className="skeleton h-28 rounded-2xl" />
                </div>
              )}
              {detail && <EvidenceBody detail={detail} />}
            </div>
          </aside>
        </div>
      )}
    </Ctx.Provider>
  );
}

function EvidenceBody({ detail }: { detail: EvidenceDetail }) {
  const ev = detail.evidence;
  const toast = useToast();
  const color = KIND_COLOR[ev.kind] ?? "#2262f3";
  const copyHash = (h: string, el: Element) => {
    void navigator.clipboard?.writeText(h).then(() => toast("已复制 SHA-256", "ok"), () => toast("浏览器未允许复制", "warn"));
    pulseAt(el, "#19b48a");
  };
  return (
    <>
      <div className="glass glass-strong rise relative overflow-hidden rounded-[22px] p-5">
        <div aria-hidden className="pointer-events-none absolute -right-8 -top-10 h-32 w-32 rounded-full blur-2xl" style={{ background: `${color}22` }} />
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="chip font-bold" style={{ color, background: `${color}14` }}><Icon name={KIND_ICON[ev.kind] ?? "doc"} className="h-3.5 w-3.5" />{KIND[ev.kind] ?? ev.kind}</span>
          {ev.period && <span className="chip bg-white text-ink-2 ring-1 ring-line">{year(ev.period)} 年度</span>}
          <code className="truncate text-[11px] text-faint">{ev.id}</code>
        </div>
        <div className="text-lg font-bold text-ink">{ev.label}</div>
        <div className="num pop mt-1 text-[32px] font-extrabold leading-tight" style={{ color, ["--i" as string]: 3 }}>{fmtValue(ev.value, ev.unit)}</div>
        {ev.note && <p className="mt-2 rounded-xl bg-[#fff6ea] p-2.5 text-xs text-[#9a5a0c]">{ev.note}</p>}
      </div>
      {ev.formula && (
        <section className="rise rounded-[20px] border border-brand-100 bg-gradient-to-br from-white to-brand-50/60 p-4" style={{ ["--i" as string]: 1 }}>
          <div className="flex items-center gap-1.5 text-xs font-bold text-brand-700"><Icon name="cpu" className="h-3.5 w-3.5" />计算公式</div>
          <div className="mt-1.5 text-[15px] font-semibold text-ink">{ev.formula}</div>
          {ev.expression && <div className="mt-2 rounded-xl bg-white/80 px-3 py-2 text-xs leading-relaxed text-ink-2 ring-1 ring-line">代入：{ev.expression}</div>}
        </section>
      )}
      <section>
        <div className="mb-2.5 flex items-center gap-1.5 text-xs font-bold text-muted"><Icon name="layers" className="h-3.5 w-3.5 text-brand-500" />证据链（{detail.chain.length} 个节点，向下追溯到原始响应）</div>
        <ol className="relative space-y-2.5 pl-7">
          <span aria-hidden className="chain-line absolute bottom-4 left-[11px] top-4 w-[2px] origin-top rounded-full bg-gradient-to-b from-brand-400 via-cyan to-mint" />
          {detail.chain.map((node, i) => {
            const c = KIND_COLOR[node.kind] ?? "#2262f3";
            return (
              <li key={node.id} className="rise relative" style={{ ["--i" as string]: i + 2 }}>
                <span className="pop absolute -left-7 top-3 grid h-6 w-6 place-items-center rounded-full border-2 border-white text-white shadow-[0_4px_10px_rgba(30,60,140,.2)]" style={{ background: c, ["--i" as string]: i + 4 }}>
                  <Icon name={KIND_ICON[node.kind] ?? "doc"} className="h-3 w-3" strokeWidth={2.4} />
                </span>
                <div className="glass rounded-2xl p-3 text-sm transition hover:border-brand-200 hover:shadow-[var(--shadow-lift)]">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-ink-2">{node.label}{node.period ? `（${year(node.period)}）` : ""}</span>
                    <span className="num font-bold text-ink">{fmtValue(node.value, node.unit)}</span>
                  </div>
                  {node.source && (
                    <div className="mt-2 grid grid-cols-[72px_1fr] gap-x-2 gap-y-1 text-xs text-muted">
                      <span>数据源</span><span className="text-ink-2">{node.source.provider} · {node.source.dataset}</span>
                      <span>原始字段</span><code className="text-ink-2">{node.source.field}</code>
                      <span>抓取时间</span><span className="num">{node.source.fetched_at}</span>
                      {node.source.notice_date && (<><span>公告日期</span><span className="num">{node.source.notice_date}</span></>)}
                      <span>SHA-256</span>
                      <button data-noripple title={`${node.source.sha256}（点击复制）`} onClick={(e) => copyHash(node.source!.sha256, e.currentTarget)}
                        className="inline-flex w-fit items-center gap-1 rounded-md bg-[#e9f8f2] px-1.5 py-0.5 font-mono text-[11px] text-[#0d8a66] transition hover:bg-[#d5f2e6]">
                        <Icon name="hash" className="h-3 w-3" />{shortHash(node.source.sha256)}
                      </button>
                      <span>请求地址</span>
                      <a className="truncate text-brand-600 hover:underline" href={node.source.url} target="_blank" rel="noreferrer">{node.source.url}</a>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </section>
      {Object.keys(detail.snapshots).length > 0 && (
        <p className="rise flex items-start gap-2 rounded-2xl bg-white/70 p-3 text-xs leading-relaxed text-muted ring-1 ring-line" style={{ ["--i" as string]: detail.chain.length + 3 }}>
          <Icon name="shield" className="mt-0.5 h-4 w-4 shrink-0 text-risk-low" />
          原始响应按内容寻址保存，读取时重新计算 SHA-256 并与采集记录比对，哈希一致说明快照自采集后没有改动。
        </p>
      )}
    </>
  );
}

// ---------------------------------------------------------------- 引用标签与富文本

export function Cite({ id, code, label, local }: { id: string; code: string; label?: string; local?: Evidence }) {
  const open = useEvidence();
  return (
    <button
      type="button"
      data-noripple
      onClick={(e) => { pulseAt(e.currentTarget); open({ id, code, local }); }}
      className="cite"
      title="查看证据与出处"
    >
      {label ?? "证据"}
    </button>
  );
}

/** 渲染带 [[证据ID]] 引用的文本（规则信号摘要）。 */
export function CitedText({ text, code }: { text: string; code: string }) {
  const parts = text.split(/(\[\[[^\]]+\]\])/g);
  return (
    <>
      {parts.map((p, i) => {
        const m = p.match(/^\[\[([^\]]+)\]\]$/);
        return m ? <Cite key={i} id={m[1]} code={code} label="源" /> : <span key={i}>{p}</span>;
      })}
    </>
  );
}
