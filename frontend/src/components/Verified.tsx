import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CLAIM_STATUS } from "../format";
import type { Evidence, Verification } from "../types";
import { Cite } from "./Evidence";

const FLAG = "⚠️〔未核验〕";

/** 渲染大模型 / 模板输出：[E#] → 可点击的证据标签；未通过核验的数字 → 红色标记。 */
export function VerifiedMarkdown({ text, refs, code, local }: {
  text: string; refs: Record<string, string>; code: string; local?: Record<string, Evidence>;
}) {
  const md = text
    .replace(/[[【]\s*(E\d+(?:\s*[,，、]\s*E\d+)*)\s*[\]】]/g, (_, ids: string) =>
      ids.split(/[,，、]/).map((x) => `[${x.trim()}](#cite-${x.trim()})`).join(""))
    .split(FLAG).join("[未核验](#flag)");
  return (
    <div className="prose-fin">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("#cite-")) {
              const ref = href.slice(6);
              const id = refs[ref];
              return id ? <Cite id={id} code={code} label={ref} local={local?.[id]} />
                : <span className="rounded bg-red-50 px-1 text-[11px] text-red-700">{ref}?</span>;
            }
            if (href === "#flag") return <span className="ml-0.5 rounded bg-red-600 px-1 text-[11px] font-medium text-white">未核验</span>;
            return <a href={href} target="_blank" rel="noreferrer" className="text-brand-600 underline">{children}</a>;
          },
        }}
      >
        {md}
      </ReactMarkdown>
    </div>
  );
}

export function VerificationBar({ v }: { v: Verification }) {
  const [open, setOpen] = useState(false);
  const ok = v.mismatch + v.unverified + v.invalid_ref === 0 && v.compliance_flags.length === 0;
  return (
    <div className={`rounded-xl border p-3 ${ok ? "border-emerald-200 bg-emerald-50/60" : "border-red-200 bg-red-50/60"}`}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <span className={`font-semibold ${ok ? "text-emerald-700" : "text-red-700"}`}>
          {ok ? "✓ 全部数字已通过核验" : "⚠ 存在未通过核验的数字"}
        </span>
        <span className="text-slate-600">数字断言 <b>{v.total}</b></span>
        <span className="text-emerald-700">已核验 <b>{v.verified}</b></span>
        {v.unattributed > 0 && <span className="text-sky-700">未标引用但一致 <b>{v.unattributed}</b></span>}
        {v.mismatch > 0 && <span className="text-red-700">不一致 <b>{v.mismatch}</b></span>}
        {v.unverified > 0 && <span className="text-red-700">无证据 <b>{v.unverified}</b></span>}
        {v.invalid_ref > 0 && <span className="text-red-700">引用无效 <b>{v.invalid_ref}</b></span>}
        <span className="text-slate-600">可核验率 <b>{(v.verifiable_rate * 100).toFixed(1)}%</b></span>
        {v.total > 0 && (
          <button className="ml-auto text-xs text-brand-700 hover:underline" onClick={() => setOpen(!open)}>
            {open ? "收起明细" : "查看逐条核验"}
          </button>
        )}
      </div>
      {v.compliance_flags.map((f) => <div key={f} className="mt-1 text-xs text-red-700">合规提示：{f}</div>)}
      {open && (
        <table className="mt-3 w-full text-xs">
          <thead className="text-left text-slate-500">
            <tr><th className="py-1 pr-2">数字</th><th className="pr-2">引用</th><th className="pr-2">状态</th><th>说明</th></tr>
          </thead>
          <tbody>
            {v.claims.map((c, i) => (
              <tr key={i} className="border-t border-slate-200/70 align-top">
                <td className="py-1 pr-2 font-mono">{c.text}</td>
                <td className="pr-2">{c.refs.join("、") || "—"}</td>
                <td className="pr-2"><span className={`rounded px-1.5 py-0.5 ${CLAIM_STATUS[c.status].cls}`}>{CLAIM_STATUS[c.status].label}</span></td>
                <td className="text-slate-500">{c.message || (c.expected ? `证据值 ${c.expected}` : "")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
