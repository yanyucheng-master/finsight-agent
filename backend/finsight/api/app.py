"""HTTP API（FastAPI）。前端构建产物放在 finsight/web/ 时一并托管。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from finsight import __version__
from finsight.analytics.diagnosis import DiagnosisResult, diagnose
from finsight.analytics.engine import metric_catalog
from finsight.config import LLM_PROVIDERS, Settings, llm_settings, load_settings
from finsight.data.service import CompanyNotFound, DataService, load_service
from finsight.llm.gateway import LLMClient, LLMError, make_client
from finsight.reporting.report import Report, generate_report

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(settings: Settings | None = None, service: DataService | None = None,
               llm_factory=None) -> FastAPI:
    settings = settings or load_settings()
    svc = service or load_service(settings.snapshot_dir)
    app = FastAPI(title="FinSight Agent API", version=__version__)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    @lru_cache(maxsize=64)
    def _diag(code: str, runs_key: str) -> DiagnosisResult:
        return diagnose(svc.company(code), engine_version=settings.engine_version)

    def get_diag(code: str) -> DiagnosisResult:
        try:
            data = svc.company(code)
        except CompanyNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return _diag(code, "|".join(data.snapshot_runs))

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "provider_mode": settings.provider_mode,
                "llm_enabled": settings.llm.enabled, "llm_model": settings.llm.model if settings.llm.enabled else None,
                "companies": len(svc.available_symbols())}

    @app.get("/api/companies")
    def companies(q: str = Query("", max_length=40), limit: int = Query(20, ge=1, le=100)) -> list[dict]:
        return svc.search(q, limit)

    @app.get("/api/companies/available")
    def available() -> list[dict]:
        out = []
        for code in svc.available_symbols():
            try:
                p = svc.company(code).profile
                out.append({"code": code, "name": p.name, "industry": p.industry_em, "company_type": p.company_type})
            except CompanyNotFound:
                continue
        return out

    @app.get("/api/companies/{code}/diagnosis", response_model=DiagnosisResult)
    def diagnosis(code: str) -> DiagnosisResult:
        return get_diag(code)

    @app.get("/api/companies/{code}/evidence/{evidence_id:path}")
    def evidence(code: str, evidence_id: str) -> dict:
        result = get_diag(code)
        ev = result.evidence.get(evidence_id)
        if ev is None:
            raise HTTPException(404, f"证据 {evidence_id} 不存在")
        # 溯源闭包：沿 inputs 回溯到原始事实
        chain, stack, seen = [], [evidence_id], set()
        while stack:
            eid = stack.pop()
            if eid in seen or eid not in result.evidence:
                continue
            seen.add(eid)
            item = result.evidence[eid]
            chain.append(item)
            stack.extend(item.inputs)
        snapshots = {}
        for item in chain:
            if item.source and item.source.snapshot_id not in snapshots:
                rec = svc.store.get(item.source.snapshot_id)
                if rec:
                    snapshots[rec.id] = rec.public()
        return {"evidence": ev, "chain": chain, "snapshots": snapshots}

    clients: dict[str, LLMClient] = {}

    def get_llm(provider: str | None) -> LLMClient | None:
        name = (provider or settings.llm.provider).lower()
        if name in clients:
            return clients[name]
        if llm_factory is not None:
            clients[name] = llm_factory(name)
            return clients[name]
        cfg = settings.llm if provider is None else llm_settings(name)
        if not cfg.enabled:
            return None
        try:
            clients[name] = make_client(cfg)
        except LLMError:
            return None
        return clients[name]

    @app.get("/api/llm")
    def llm_status() -> dict:
        return {"default": settings.llm.public(),
                "providers": {name: llm_settings(name).public() for name in LLM_PROVIDERS}}

    @app.post("/api/companies/{code}/report", response_model=Report)
    def report(code: str, mode: str = Query("auto", pattern="^(auto|llm|template)$"),
               provider: str | None = Query(None, pattern="^(deepseek|kimi|intern)$")) -> Report:
        result = get_diag(code)
        llm = get_llm(provider) if mode != "template" else None
        return generate_report(result, llm=llm, mode=mode)

    @app.get("/api/metrics/catalog")
    def catalog() -> list[dict]:
        return metric_catalog()

    if WEB_DIR.exists():
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = WEB_DIR / path
            if path and target.is_file():
                return FileResponse(target)
            return FileResponse(WEB_DIR / "index.html")

    return app
