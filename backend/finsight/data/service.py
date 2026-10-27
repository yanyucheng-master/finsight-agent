"""数据服务：从快照仓库组装一家公司的完整数据视图（CompanyData）。

CompanyData 是分析引擎的唯一输入；它只包含“从原始响应中解析出的事实”与其出处，
不包含任何推断或计算结果。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from finsight.data.eastmoney import (
    company_type_from_index,
    parse_announcements,
    parse_main_indicators,
    parse_statement_rows,
    parse_survey,
    source_ref,
)
from finsight.data.snapshots import SnapshotRecord, SnapshotStore
from finsight.domain import CompanyProfile, Fact, Period, SourceRef

STATEMENT_DATASETS = {"bs": "balance", "is": "income", "cf": "cashflow"}
ORG_TYPES = {"通用": "general", "银行": "bank", "保险": "insurance", "证券": "securities"}


class Announcement(BaseModel):
    id: str
    title: str
    date: str | None
    type: str | None = None
    pdf_url: str | None = None
    snapshot_id: str


class DocumentRef(BaseModel):
    """本地保存的原始文档（如年报 PDF）。"""

    title: str
    snapshot_id: str
    sha256: str
    url: str
    fetched_at: str
    size: int
    announcement_id: str | None = None


class CompanyData(BaseModel):
    profile: CompanyProfile
    annual_periods: list[Period]  # 年报，按报告期降序
    interim_periods: list[Period]  # 最近的季报/半年报（不含年报）
    facts: dict[str, dict[str, Fact]]  # period_end -> item -> Fact
    vendor_indicators: dict[str, dict[str, Any]] = Field(default_factory=dict)  # period_end -> 原始字段
    vendor_source: SourceRef | None = None
    announcements: list[Announcement] = Field(default_factory=list)
    documents: list[DocumentRef] = Field(default_factory=list)
    parse_issues: list[str] = Field(default_factory=list)
    snapshot_runs: list[str] = Field(default_factory=list)

    def fact(self, item: str, period: str) -> Fact | None:
        return self.facts.get(period, {}).get(item)

    def value(self, item: str, period: str) -> float | None:
        f = self.fact(item, period)
        return f.value if f else None

    def annual_ends(self) -> list[str]:
        return [p.end for p in self.annual_periods]

    def period(self, end: str) -> Period | None:
        for p in self.annual_periods + self.interim_periods:
            if p.end == end:
                return p
        return None


class CompanyNotFound(LookupError):
    pass


class DataService:
    """线程安全的只读数据服务；按公司缓存解析结果，快照变化时自动失效。"""

    def __init__(self, store: SnapshotStore):
        self.store = store
        self._cache: dict[str, CompanyData] = {}
        self._lock = threading.Lock()
        self._stock_list: list[dict[str, Any]] | None = None

    # -- 公司列表与搜索 ------------------------------------------------------------
    def stock_list(self) -> list[dict[str, Any]]:
        """巨潮证券代码表（含拼音简称），用于搜索。"""
        if self._stock_list is None:
            records = self.store.latest_run(provider="cninfo", dataset="stock_list")
            rows: list[dict[str, Any]] = []
            if records:
                data = self.store.read_json(records[-1])
                for it in data.get("stockList", []):
                    if it.get("category") in (None, "A股") and it.get("code"):
                        rows.append({"code": it["code"], "name": it.get("zwjc"), "pinyin": it.get("pinyin"),
                                     "org_id": it.get("orgId")})
            self._stock_list = rows
        return self._stock_list

    def available_symbols(self) -> list[str]:
        """拥有财务报表快照、可以分析的公司。"""
        return sorted({r.symbol for r in self.store.iter_records()
                       if r.ok and r.symbol and r.dataset == "balance_annual"})

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = query.strip().lower()
        available = set(self.available_symbols())
        names = {row["code"]: row for row in self.stock_list()}
        results: list[dict[str, Any]] = []
        pool = names.values() if names else [{"code": c, "name": c, "pinyin": ""} for c in available]
        for row in pool:
            code, name, pinyin = row["code"], row.get("name") or "", (row.get("pinyin") or "").lower()
            if not q or q in code or q in name.lower() or (pinyin and pinyin.startswith(q)):
                results.append({"code": code, "name": name, "available": code in available})
        results.sort(key=lambda r: (not r["available"], r["code"]))
        return results[:limit]

    # -- 公司数据 ------------------------------------------------------------------
    def company(self, code: str) -> CompanyData:
        self.store.refresh_if_changed()
        with self._lock:
            cached = self._cache.get(code)
            runs = sorted({r.run_id for r in self.store.iter_records() if r.symbol == code})
            if cached is not None and cached.snapshot_runs == runs:
                return cached
            data = self._build(code)
            data.snapshot_runs = runs
            self._cache[code] = data
            return data

    def _latest(self, dataset: str, code: str, provider: str = "eastmoney") -> list[SnapshotRecord]:
        return self.store.latest_run(provider=provider, dataset=dataset, symbol=code)

    def _build(self, code: str) -> CompanyData:
        if not (self._latest("balance_annual", code) or self._latest("income_annual", code)):
            raise CompanyNotFound(f"没有 {code} 的财务报表快照，请先运行采集器")
        issues: list[str] = []
        sources: list[SourceRef] = []

        # 公司类型：优先取报表/主要指标行中的 ORG_TYPE（通用/银行/保险/证券），其次取 F10 页面标记
        company_type = "unknown"
        for dataset in ("main_indicators", "income_annual", "balance_annual"):
            recs = self._latest(dataset, code)
            if not recs:
                continue
            payload = self.store.read_json(recs[-1])
            rows = payload.get("data") if isinstance(payload, dict) and "data" in payload else \
                ((payload or {}).get("result") or {}).get("data")
            org_type = (rows or [{}])[0].get("ORG_TYPE") if rows else None
            if org_type in ORG_TYPES:
                company_type = ORG_TYPES[org_type]
                sources.append(source_ref(recs[-1], field="ORG_TYPE"))
                break
        idx = self._latest("f10_index", code)
        if company_type == "unknown" and idx:
            company_type = company_type_from_index(self.store.read_bytes(idx[-1]))
            sources.append(source_ref(idx[-1]))

        # 公司概况
        survey: dict[str, Any] = {}
        sv = self._latest("company_survey", code)
        if sv:
            survey = parse_survey(self.store.read_json(sv[-1]))
            sources.append(source_ref(sv[-1]))
        listed = {row["code"]: row for row in self.stock_list()}.get(code, {})

        # 三大报表
        facts: dict[str, dict[str, Fact]] = {}
        annual: dict[str, Period] = {}
        interim: dict[str, Period] = {}
        for statement, dataset in STATEMENT_DATASETS.items():
            for suffix, bucket in (("annual", annual), ("report", interim)):
                for rec in self._latest(f"{dataset}_{suffix}", code):
                    payload = self.store.read_json(rec)
                    periods, parsed, problems = parse_statement_rows(rec, payload, statement)
                    issues.extend(problems)
                    for p in periods:
                        if suffix == "report" and p.type == "FY":
                            continue  # 年报以“按年度”数据为准
                        prev = bucket.get(p.end)
                        # 审计意见只出现在年报行；合并多张报表的元数据
                        if prev is None or (not prev.audit_opinion and p.audit_opinion):
                            bucket[p.end] = p
                    for f in parsed:
                        if suffix == "report" and f.period in annual:
                            continue
                        facts.setdefault(f.period, {})[f.item] = f

        # 东方财富主要指标（交叉核验用）
        vendor: dict[str, dict[str, Any]] = {}
        vendor_src = None
        mi = self._latest("main_indicators", code)
        if mi:
            vendor = parse_main_indicators(mi[-1], self.store.read_json(mi[-1]))
            vendor_src = source_ref(mi[-1])

        # 公告
        anns: list[Announcement] = []
        seen: set[str] = set()
        for dataset in ("announcements", "annual_reports"):
            for rec in self._latest(dataset, code, provider="cninfo"):
                for a in parse_announcements(rec, self.store.read_json(rec)):
                    if a["id"] and a["id"] not in seen:
                        seen.add(a["id"])
                        anns.append(Announcement(**{k: a[k] for k in ("id", "title", "date", "type", "pdf_url", "snapshot_id")}))
        anns.sort(key=lambda a: a.date or "", reverse=True)

        docs = [DocumentRef(title=str(r.meta.get("title") or "年度报告"), snapshot_id=r.id, sha256=r.sha256 or "",
                            url=r.url, fetched_at=r.fetched_at, size=r.size,
                            announcement_id=str(r.meta.get("announcementId") or "") or None)
                for r in self._latest("annual_report_pdf", code, provider="cninfo")]

        name = survey.get("name") or listed.get("name") or code
        exchange = "SH" if code.startswith(("60", "68", "90")) else ("BJ" if code.startswith(("43", "83", "87", "88", "92")) else "SZ")
        profile = CompanyProfile(
            code=code, exchange=exchange, name=name, full_name=survey.get("full_name"),
            company_type=company_type,  # type: ignore[arg-type]
            industry_em=survey.get("industry_em"), industry_csrc=survey.get("industry_csrc"),
            listing_date=survey.get("listing_date"), auditor=survey.get("auditor"),
            province=survey.get("province"), business_scope=survey.get("business_scope"),
            profile=survey.get("profile"), sources=sources,
        )
        annual_periods = sorted((p for p in annual.values() if p.type == "FY"), key=lambda p: p.end, reverse=True)
        interim_periods = sorted(interim.values(), key=lambda p: p.end, reverse=True)
        return CompanyData(profile=profile, annual_periods=annual_periods, interim_periods=interim_periods,
                           facts=facts, vendor_indicators=vendor, vendor_source=vendor_src, announcements=anns,
                           documents=docs, parse_issues=issues)


def load_service(snapshot_dir: Path) -> DataService:
    return DataService(SnapshotStore(snapshot_dir))


def dump_company(data: CompanyData) -> str:  # 调试辅助
    return json.dumps(data.model_dump(), ensure_ascii=False, indent=1)[:2000]
