import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CLAIM_STATUS } from "../format";
import { burst, CountUp } from "../fx";
import type { Evidence, Verification } from "../types";
import { Cite } from "./Evidence";
import { Icon, Ring } from "./ui";

const FLAG = "⚠️〔未核验〕";

/** 渲染大模型 / 模板输出：[E#] → 可点击的证据标签；未通过核验的数字 → 红色标记。 */
export function VerifiedMarkdown({ text, refs, code, local }: {
  text: string; refs: Record<string, string>; code: string; local?: Record<string, Evidence>;
}) {
  const md = text
    .replace(/[[【]\s*(E\d+(?:\s*[,，、]\s*E\d+)*)\s*[\]】]/g, (_, ids: string) =>
      ids.split(/[,，、]/).map((x) => `[${x.trim()}](#cite-${x.trim()})`).join(""))
    .split(FLAG).join("[未核验](#flag)")
    // CommonMark 不把中文标点旁的 ** 认作加粗（如“压力。**以下”会原样显示星号）。
    // 同一行内成对、且不含引用或其他标记的 **…**，先转成加粗链接再渲染；含引用的仍交给 Markdown 处理。
    .replace(/\*\*([^*\n[\]]+?)\*\*/g, "[$1](#b)");
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
                : <span className="rounded-md bg-red-50 px-1 text-[11px] font-semibold text-risk-high ring-1 ring-red-200">{ref}?</span>;
            }
            if (href === "#b") return <strong>{children}</strong>;
            if (href === "#flag") return <span className="ml-0.5 animate-pulse rounded-md bg-risk-high px-1.5 text-[11px] font-bold text-white shadow-[0_0_10px_rgba(239,68,98,.5)]">未核验</span>;
            return <a href={href} target="_blank" rel="noreferrer" className="text-brand-600 underline">{children}</a>;
          },
        }}
      >
        {md}
      </ReactMarkdown>
    </div>
  );
}

/** 核验条：环形图是“可核验率”；celebrate 变化时（用户主动得到新结果）通过则迸发粒子，未通过则抖动提示。 */
export function VerificationBar({ v, celebrate = 0 }: { v: Verification; celebrate?: number }) {
  const [open, setOpen] = useState(false);
  const [shake, setShake] = useState(false);
  const ring = useRef<HTMLDivElement>(null);
  const consistency = v.consistency_flags ?? [];
  const bench = v.benchmark ?? 0;
  const numBad = v.mismatch + v.unverified + v.invalid_ref;
  // 与后端 VerificationResult.passed 保持一致：数值问题、合规提示、口径提示任一存在都不算通过
  const ok = numBad === 0 && v.compliance_flags.length === 0 && consistency.length === 0;
  const headline = !ok
    ? (numBad > 0 ? "⚠ 存在未通过核验的数字" : "⚠ 数字一致，但存在口径或合规提示")
    : v.unattributed > 0
      ? `✓ 数字均与证据一致（其中 ${v.unattributed} 个未标引用，归属为系统推断）`
      : "✓ 抽取到的数字均已按引用核验";
  useEffect(() => {
    if (!celebrate) return;
    if (ok) { const t = setTimeout(() => burst(ring.current, { count: 22, spread: 80 }), 950); return () => clearTimeout(t); }
    setShake(true);
    const t = setTimeout(() => setShake(false), 600);
    return () => clearTimeout(t);
  }, [celebrate, ok]);
  const color = ok ? "#19b48a" : numBad > 0 ? "#ef4462" : "#f59331";
  const stats: { k: string; v: number; c: string; show: boolean; title?: string }[] = [
    { k: "数字断言", v: v.total, c: "text-ink", show: true },
    { k: "已核验", v: v.verified, c: "text-risk-low", show: true },
    { k: "未标引用但一致", v: v.unattributed, c: "text-sky-700", show: v.unattributed > 0 },
    { k: "不一致", v: v.mismatch, c: "text-risk-high", show: v.mismatch > 0 },
    { k: "无证据", v: v.unverified, c: "text-risk-high", show: v.unverified > 0 },
    { k: "引用无效", v: v.invalid_ref, c: "text-risk-high", show: v.invalid_ref > 0 },
    { k: "比较基准", v: bench, c: "text-muted", show: bench > 0, title: "如“低于1倍”中的 1，属于判断标尺，不计入数字断言" },
  ];
  return (
    <div className={`rise overflow-hidden rounded-[22px] border p-4 ${ok ? "border-[#bfead9] bg-gradient-to-r from-[#effaf5] to-white/90" : "border-red-200 bg-gradient-to-r from-[#fff1f3] to-white/90"} ${shake ? "shake" : ""}`}>
      <div className="flex flex-wrap items-center gap-4">
        <div ref={ring}>
          <Ring value={v.verifiable_rate} total={1} size={62} stroke={6.5} color={color} track={ok ? "#d6f2e7" : "#fadbe0"}>
            <span className="num text-[13px] font-extrabold" style={{ color }}><CountUp value={v.verifiable_rate * 100} digits={0} />%</span>
          </Ring>
        </div>
        <div className="min-w-0 flex-1">
          <div className={`text-[15px] font-bold ${ok ? "text-[#0d8a66]" : numBad > 0 ? "text-risk-high" : "text-[#b4620a]"}`}>{headline}</div>
          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-muted">
            {stats.filter((s) => s.show).map((s) => (
              <span key={s.k} title={s.title}>{s.k} <b className={`text-[15px] ${s.c}`}><CountUp value={s.v} duration={700} /></b></span>
            ))}
            <span>可核验率 <b className="text-[15px] text-ink">{(v.verifiable_rate * 100).toFixed(1)}%</b></span>
          </div>
        </div>
        {v.total > 0 && (
          <button className="btn btn-ghost !px-3 !py-1.5 text-xs" onClick={() => setOpen(!open)} aria-expanded={open}>
            <Icon name="layers" className="h-3.5 w-3.5" />{open ? "收起明细" : "查看逐条核验"}
          </button>
        )}
      </div>
      {v.compliance_flags.map((f) => <div key={f} className="mt-2 flex items-start gap-1.5 text-xs text-risk-high"><Icon name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0" />合规提示：{f}</div>)}
      {consistency.map((f) => <div key={f} className="mt-2 flex items-start gap-1.5 rounded-xl bg-[#fff6ea] px-2.5 py-1.5 text-xs text-[#9a5a0c]"><Icon name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0" />{f}</div>)}
      <div className="mt-2 text-[11px] text-faint">核验范围：文中抽取到的数字与所引证据的一致性。</div>
      <div className="grid transition-[grid-template-rows] duration-500 ease-[cubic-bezier(.2,.8,.2,1)]" style={{ gridTemplateRows: open ? "1fr" : "0fr" }}>
        <div className="min-h-0 overflow-hidden">
          {open && (
            <div className="mt-3 max-h-[420px] overflow-auto rounded-xl border border-line bg-white/80 thin-scroll">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-[#f6f9ff] text-left text-muted">
                  <tr><th className="px-3 py-2">数字</th><th className="px-2">引用</th><th className="px-2">状态</th><th className="px-2">说明</th></tr>
                </thead>
                <tbody>
                  {v.claims.map((c, i) => (
                    <tr key={i} className="rise border-t border-line/70 align-top transition-colors hover:bg-brand-50/50" style={{ ["--i" as string]: Math.min(i, 16) }}>
                      <td className="num px-3 py-1.5 font-semibold text-ink">{c.text}</td>
                      <td className="px-2 py-1.5">{c.refs.join("、") || "—"}</td>
                      <td className="px-2 py-1.5"><span className={`rounded-md px-1.5 py-0.5 font-semibold ${CLAIM_STATUS[c.status].cls}`}>{CLAIM_STATUS[c.status].label}</span></td>
                      <td className="px-2 py-1.5 text-muted">{c.message || (c.expected ? `证据值 ${c.expected}` : "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
