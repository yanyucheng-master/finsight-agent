import type { JobStatus, SampleMeta } from "../types";
import { Icon } from "./ui";

const STAGE_LABEL: Record<string, string> = {
  queued: "排队中",
  evidence: "整理证据",
  drafting: "撰写",
  thinking: "模型思考",
  tool: "工具取证",
  verifying: "逐个核验数字",
  revising: "打回修订",
  done: "完成",
};
const STAGE_ICON: Record<string, string> = {
  evidence: "db", drafting: "doc", thinking: "sparkle", tool: "tool", verifying: "shield", revising: "back",
};

/** 生成进度：阶段条 + 当前说明 + 已用时间。进度只来自服务端回报的真实阶段，不做假进度。 */
export function JobProgress({ job }: { job: JobStatus }) {
  const stages = (job.stages.length ? job.stages : ["done"]).filter((s) => s !== "done");
  const idx = stages.indexOf(job.stage);
  const last = job.events.slice(-4);
  return (
    <div className="glass rise relative overflow-hidden rounded-[22px] p-4 sm:p-5" role="status" aria-live="polite">
      <div aria-hidden className="scanline opacity-60" />
      <div className="relative flex flex-wrap items-center gap-y-3">
        {stages.map((s, i) => {
          const state = job.status === "queued" ? "todo" : i < idx ? "done" : i === idx ? "now" : "todo";
          return (
            <div key={s} className="flex items-center">
              {i > 0 && (
                <span className="relative mx-1.5 h-[3px] w-6 overflow-hidden rounded-full bg-line sm:w-10">
                  <span className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-mint to-brand-500 transition-[width] duration-700 ease-out" style={{ width: state === "todo" ? "0%" : "100%" }} />
                </span>
              )}
              <span className={`relative flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition-all duration-500 ${
                state === "now" ? "stage-now bg-brand-500 text-white shadow-[var(--shadow-glow)]" :
                state === "done" ? "bg-[#e3f7ef] text-risk-low" : "bg-white/80 text-faint ring-1 ring-line"}`}>
                {state === "done"
                  ? <span key="ok" className="pop grid h-3.5 w-3.5 place-items-center rounded-full bg-risk-low text-[9px] text-white">✓</span>
                  : <Icon name={STAGE_ICON[s] ?? "clock"} className="h-3.5 w-3.5" />}
                {STAGE_LABEL[s] ?? s}
              </span>
            </div>
          );
        })}
        <span className="num ml-auto flex items-center gap-1 rounded-full bg-white/80 px-2.5 py-1 text-xs font-semibold text-muted ring-1 ring-line">
          <Icon name="clock" className="h-3.5 w-3.5 text-brand-500" />{job.elapsed_s.toFixed(0)} 秒
        </span>
      </div>
      <div key={job.detail} className="fade-in relative mt-3 flex items-center gap-2 text-sm text-ink-2">
        <span className="dot-live shrink-0 !bg-brand-500" />
        {job.status === "queued" ? "排队中，前面还有任务在生成…" : job.detail || "处理中…"}
      </div>
      {last.length > 1 && (
        <ul className="relative mt-2 space-y-0.5 border-l-2 border-brand-100 pl-3 text-xs text-muted">
          {last.map((e) => <li key={`${e.t}-${e.stage}`} className="fade-in"><span className="num">{e.t.toFixed(0)}s</span> · {STAGE_LABEL[e.stage] ?? e.stage}：{e.detail}</li>)}
        </ul>
      )}
      {job.live && job.stage === "drafting" && (
        <div className="relative mt-2 text-[11px] text-faint">推理模型先思考再作答，这一步耗时最长；正文完成后立即进入核验。</div>
      )}
    </div>
  );
}

/** 生成中正文位置的骨架：只表示“内容会出现在这里”。 */
export function ArticleSkeleton() {
  return (
    <div className="glass fade-in space-y-3 rounded-[24px] p-6 sm:p-8" aria-hidden>
      <div className="skeleton h-5 w-40" />
      {[92, 100, 84, 96, 70].map((w, i) => <div key={i} className="skeleton h-3.5" style={{ width: `${w}%` }} />)}
      <div className="skeleton mt-4 h-5 w-32" />
      {[88, 94, 60].map((w, i) => <div key={i} className="skeleton h-3.5" style={{ width: `${w}%` }} />)}
    </div>
  );
}

export function CachedBadge({ s }: { s: SampleMeta }) {
  const v = s.verification;
  const bad = v.mismatch + v.invalid_ref + v.unverified;
  return (
    <div className="rise flex items-start gap-3 rounded-2xl border border-[#fbd49c] bg-gradient-to-r from-[#fff8ec] to-[#fffdf8] p-3 text-xs leading-relaxed text-[#7a4306]">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-xl bg-[#ffe7c2] text-[#b26205]"><Icon name="bolt" className="h-4 w-4" /></span>
      <div>
        <b>缓存样例</b>：{s.model ?? "大模型"} {s.generated_at ? `于 ${s.generated_at.replace("T", " ").replace("Z", " UTC")} ` : ""}生成，耗时 {(s.elapsed_ms / 1000).toFixed(0)} 秒。
        正文保持原样，核验结果由<b>当前版本核验器</b>重新计算：{v.total} 个数字，{bad} 个有问题。
      </div>
    </div>
  );
}

export function LiveQuota({ remaining, limited, enabled }: { remaining: number | null | undefined; limited: boolean; enabled: boolean }) {
  if (!enabled) return <span className="inline-flex items-center gap-1.5 text-xs text-faint"><span className="h-1.5 w-1.5 rounded-full bg-faint" />服务端未配置大模型 Key，实时生成不可用</span>;
  if (!limited) return null;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted">
      <span className={`h-1.5 w-1.5 rounded-full ${remaining === 0 ? "bg-risk-high" : "bg-mint"}`} />
      {/* 文字放在同一个子元素里：flex 子项边界的空格会被折叠，innerText 会变成“剩余0次” */}
      <span>实时生成今日剩余 <b className={remaining === 0 ? "text-risk-high" : "text-ink"}>{remaining ?? "—"}</b> 次</span>
    </span>
  );
}
