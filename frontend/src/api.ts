import type { AgentAnswer, CompanyBrief, DiagnosisResult, EvidenceDetail, Report } from "./types";

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
};
