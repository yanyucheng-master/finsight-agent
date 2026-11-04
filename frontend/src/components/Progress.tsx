import type { JobStatus, SampleMeta } from "../types";

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

/** 生成进度：阶段条 + 当前说明 + 已用时间。进度只来自服务端回报的真实阶段，不做假进度。 */
export function JobProgress({ job }: { job: JobStatus }) {
  const stages = job.stages.length ? job.stages : ["done"];
  const idx = stages.indexOf(job.stage);
  const last = job.events.slice(-4);
  return (
    <div className="rounded-xl border border-brand-100 bg-brand-50/50 p-4" role="status" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {stages.filter((s) => s !== "done").map((s, i) => {
          const state = job.status === "queued" ? "todo" : i < idx ? "done" : i === idx ? "now" : "todo";
          return (
            <span key={s} className={`rounded-full px-2.5 py-1 ${
              state === "now" ? "bg-brand-600 font-medium text-white" :
              state === "done" ? "bg-emerald-100 text-emerald-700" : "bg-white text-slate-400 ring-1 ring-slate-200"}`}>
              {state === "done" ? "✓ " : ""}{STAGE_LABEL[s] ?? s}
            </span>
          );
        })}
        <span className="ml-auto font-mono text-slate-500">{job.elapsed_s.toFixed(0)} 秒</span>
      </div>
      <div className="mt-2 text-sm text-slate-700">
        {job.status === "queued" ? "排队中，前面还有任务在生成…" : job.detail || "处理中…"}
      </div>
      {last.length > 1 && (
        <ul className="mt-2 space-y-0.5 border-l-2 border-brand-100 pl-3 text-xs text-slate-500">
          {last.map((e, i) => <li key={i}>{e.t.toFixed(0)}s · {STAGE_LABEL[e.stage] ?? e.stage}：{e.detail}</li>)}
        </ul>
      )}
      {job.live && job.stage === "drafting" && (
        <div className="mt-2 text-[11px] text-slate-400">推理模型先思考再作答，这一步耗时最长；正文完成后立即进入核验。</div>
      )}
    </div>
  );
}

export function CachedBadge({ s }: { s: SampleMeta }) {
  const v = s.verification;
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-relaxed text-amber-900">
      <b>缓存样例</b>：{s.model ?? "大模型"} {s.generated_at ? `于 ${s.generated_at.replace("T", " ").replace("Z", " UTC")} ` : ""}生成，耗时 {(s.elapsed_ms / 1000).toFixed(0)} 秒。
      正文保持原样，核验结果由<b>当前版本核验器</b>重新计算：{v.total} 个数字，{v.mismatch + v.invalid_ref + v.unverified} 个有问题。
    </div>
  );
}

export function LiveQuota({ remaining, limited, enabled }: { remaining: number | null | undefined; limited: boolean; enabled: boolean }) {
  if (!enabled) return <span className="text-xs text-slate-400">服务端未配置大模型 Key，实时生成不可用</span>;
  if (!limited) return null;
  return <span className="text-xs text-slate-500">实时生成今日剩余 <b className={remaining === 0 ? "text-red-600" : "text-slate-700"}>{remaining ?? "—"}</b> 次</span>;
}
