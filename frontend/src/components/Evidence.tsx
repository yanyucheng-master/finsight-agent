import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import { fmtValue, shortHash, year } from "../format";
import type { Evidence, EvidenceDetail } from "../types";

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

export function EvidenceProvider({ children }: { children: ReactNode }) {
  const [args, setArgs] = useState<OpenArgs | null>(null);
  const [detail, setDetail] = useState<EvidenceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const open = useCallback((a: OpenArgs) => setArgs(a), []);

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
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setArgs(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <Ctx.Provider value={open}>
      {children}
      {args && (
        <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
          <div className="absolute inset-0 bg-slate-900/30" onClick={() => setArgs(null)} />
          <aside className="relative h-full w-full max-w-xl overflow-y-auto bg-white shadow-2xl">
            <div className="sticky top-0 flex items-center justify-between border-b bg-white/95 px-5 py-3 backdrop-blur">
              <div className="text-sm font-semibold text-slate-800">证据溯源</div>
              <button className="rounded px-2 py-1 text-slate-500 hover:bg-slate-100" onClick={() => setArgs(null)}>关闭 ✕</button>
            </div>
            <div className="space-y-4 p-5">
              {error && <div className="rounded bg-red-50 p-3 text-sm text-red-700">加载失败：{error}</div>}
              {!detail && !error && <div className="text-sm text-slate-400">加载中…</div>}
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
  return (
    <>
      <div>
        <div className="mb-1 flex items-center gap-2 text-xs text-slate-500">
          <span className="rounded bg-slate-100 px-1.5 py-0.5">{KIND[ev.kind] ?? ev.kind}</span>
          {ev.period && <span>{year(ev.period)} 年度</span>}
          <code className="truncate text-[11px] text-slate-400">{ev.id}</code>
        </div>
        <div className="text-lg font-semibold text-slate-900">{ev.label}</div>
        <div className="mt-1 font-mono text-2xl font-semibold text-brand-700 tabular-nums">{fmtValue(ev.value, ev.unit)}</div>
        {ev.note && <p className="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-800">{ev.note}</p>}
      </div>
      {ev.formula && (
        <section className="rounded-lg border p-3">
          <div className="text-xs font-medium text-slate-500">计算公式</div>
          <div className="mt-1 text-sm text-slate-800">{ev.formula}</div>
          {ev.expression && <div className="mt-2 text-xs leading-relaxed text-slate-500">代入：{ev.expression}</div>}
        </section>
      )}
      <section>
        <div className="mb-2 text-xs font-medium text-slate-500">证据链（{detail.chain.length} 个节点，向下追溯到原始响应）</div>
        <ol className="space-y-2">
          {detail.chain.map((node) => (
            <li key={node.id} className="rounded-lg border border-slate-200 p-3 text-sm">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-slate-700">{node.label}{node.period ? `（${year(node.period)}）` : ""}</span>
                <span className="font-mono tabular-nums text-slate-900">{fmtValue(node.value, node.unit)}</span>
              </div>
              {node.source && (
                <div className="mt-2 grid grid-cols-[72px_1fr] gap-x-2 gap-y-0.5 text-xs text-slate-500">
                  <span>数据源</span><span>{node.source.provider} · {node.source.dataset}</span>
                  <span>原始字段</span><code>{node.source.field}</code>
                  <span>抓取时间</span><span>{node.source.fetched_at}</span>
                  {node.source.notice_date && (<><span>公告日期</span><span>{node.source.notice_date}</span></>)}
                  <span>SHA-256</span><code title={node.source.sha256}>{shortHash(node.source.sha256)}</code>
                  <span>请求地址</span>
                  <a className="truncate text-brand-600 hover:underline" href={node.source.url} target="_blank" rel="noreferrer">{node.source.url}</a>
                </div>
              )}
            </li>
          ))}
        </ol>
      </section>
      {Object.keys(detail.snapshots).length > 0 && (
        <p className="text-xs leading-relaxed text-slate-400">
          原始响应按内容寻址保存，读取时重新计算 SHA-256 与采集记录比对；哈希一致即证明数据未被改动。
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
      onClick={() => open({ id, code, local })}
      className="mx-0.5 inline-flex items-center rounded bg-brand-50 px-1 align-baseline text-[11px] font-medium text-brand-700 ring-1 ring-brand-200 hover:bg-brand-100"
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
