import type { AgentAnswer, CompanyBrief, DemoStatus, DiagnosisResult, EvidenceDetail, JobStatus, Report, SampleFull, SampleMeta } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

const diagCache = new Map<string, Promise<DiagnosisResult>>();

export const api = {
  health: () => request<{ status: string; llm_enabled: boolean; llm_model: string | null; companies: number }>("/api/health"),
  available: () => request<CompanyBrief[]>("/api/companies/available"),
  search: (q: string) => request<{ code: string; name: string; available: boolean }[]>(`/api/companies?q=${encodeURIComponent(q)}&limit=12`),
  diagnosis: (code: string) => {
    if (!diagCache.has(code)) {
      const p = request<DiagnosisResult>(`/api/companies/${code}/diagnosis`);
      p.catch(() => diagCache.delete(code));
      diagCache.set(code, p);
    }
    return diagCache.get(code)!;
  },
  evidence: (code: string, id: string) =>
    request<EvidenceDetail>(`/api/companies/${code}/evidence/${encodeURIComponent(id)}`),
  report: (code: string, mode: "auto" | "llm" | "template") =>
    request<Report>(`/api/companies/${code}/report?mode=${mode}`, { method: "POST" }),
  ask: (code: string, question: string) =>
    request<AgentAnswer>(`/api/companies/${code}/ask`, { method: "POST", body: JSON.stringify({ question }) }),
  // 异步任务：先创建，再轮询阶段进度（推理模型一次要 0.5–2 分钟，同步等待体验很差）
  startReport: (code: string, mode: "auto" | "llm" | "template") =>
    request<JobStatus>(`/api/companies/${code}/report/jobs?mode=${mode}`, { method: "POST" }),
  startAsk: (code: string, question: string) =>
    request<JobStatus>(`/api/companies/${code}/ask/jobs`, { method: "POST", body: JSON.stringify({ question }) }),
  job: (id: string) => request<JobStatus>(`/api/jobs/${id}`),
  demo: () => request<DemoStatus>("/api/demo"),
  samples: () => request<SampleMeta[]>("/api/samples"),
  sample: <T,>(id: string) => request<SampleFull<T>>(`/api/samples/${id}`),
};

/** 轮询任务直到结束；失败时抛出服务端给出的原因。stop() 返回 true 时（如组件已卸载）静默退出。 */
export async function waitJob<T>(start: JobStatus, onUpdate: (j: JobStatus) => void, stop: () => boolean = () => false): Promise<T> {
  let job = start;
  onUpdate(job);
  while (job.status === "queued" || job.status === "running") {
    await new Promise((r) => setTimeout(r, 900));
    if (stop()) throw new Error("已取消");
    job = await api.job(job.id);
    onUpdate(job);
  }
  if (job.status === "failed") throw new Error(job.error ?? "生成失败");
  return job.result as T;
}
