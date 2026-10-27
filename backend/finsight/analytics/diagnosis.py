"""财务风险体检：确定性主流程（数据 → 指标 → 核验 → 信号 → 结论）。

这是系统的“核心业务闭环”的确定性部分：不依赖大模型、结果可复现、每个数字可溯源。
大模型层（报告撰写、问答）只能在本结果之上做解释，不能产生新的数字。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

import finsight.analytics.metrics  # noqa: F401  注册指标
import finsight.analytics.models  # noqa: F401  注册模型
from finsight.analytics.checks import CheckResult, run_checks, summarize
from finsight.analytics.engine import REGISTRY, Engine
from finsight.analytics.rules import DIMENSION_LABELS, Signal, run_rules
from finsight.data.service import CompanyData
from finsight.domain import CompanyProfile, Evidence, Period

KEY_METRICS: dict[str, list[str]] = {
    "profitability": ["gross_margin", "net_margin", "roe", "roa", "sga_ratio"],
    "earnings_quality": ["ocf_to_np", "cash_to_revenue", "accruals_ratio", "nonrecurring_ratio", "fcf"],
    "solvency": ["debt_ratio", "current_ratio", "quick_ratio", "cash_to_st_debt", "interest_coverage", "ibd_ratio"],
    "asset_quality": ["goodwill_to_equity", "other_rec_to_assets", "prepay_to_assets", "implied_cash_yield",
                      "inventory_cogs_gap"],
    "efficiency": ["dso", "dio", "dpo", "ccc", "asset_turnover"],
    "growth": ["revenue_growth", "np_parent_growth", "ocf_growth", "receivables_revenue_gap"],
    "models": ["beneish_m", "altman_z2", "piotroski_f"],
}

MODEL_COMPONENTS = {
    "beneish_m": ["m_dsri", "m_gmi", "m_aqi", "m_sgi", "m_depi", "m_sgai", "m_lvgi", "m_tata"],
    "altman_z2": ["z_x1", "z_x2", "z_x3", "z_x4"],
    "piotroski_f": ["f_roa", "f_cfo", "f_droa", "f_accrual", "f_lever", "f_liquid", "f_eq_offer", "f_margin", "f_turn"],
}

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1, "info": 0}
CORE_DIMENSIONS = {"earnings_quality", "solvency", "governance", "models", "asset_quality"}


class MetricRow(BaseModel):
    code: str
    label: str
    unit: str
    dimension: str
    formula: str
    description: str = ""
    higher_is_better: bool | None = None
    values: dict[str, str | None]  # period -> evidence id（值在 evidence 中）


class DimensionSummary(BaseModel):
    code: str
    label: str
    level: str  # high / medium / low / none
    risk_count: int
    strength_count: int


class Overall(BaseModel):
    level: str  # 高 / 中 / 低
    level_code: str
    headline: str
    reasons: list[str]
    rule: str  # 结论规则的文字说明（可解释）


class DiagnosisResult(BaseModel):
    engine_version: str
    generated_at: str
    profile: CompanyProfile
    periods: list[Period]
    interim_periods: list[Period] = Field(default_factory=list)
    metrics: list[MetricRow]
    models: dict[str, dict[str, Any]]
    checks: list[CheckResult]
    checks_summary: dict[str, int]
    signals: list[Signal]
    dimensions: list[DimensionSummary]
    overall: Overall
    statements: dict[str, dict[str, dict[str, str]]]  # statement -> item -> period -> fact evidence id
    evidence: dict[str, Evidence]
    announcements: list[dict[str, Any]] = Field(default_factory=list)
    documents: list[dict[str, Any]] = Field(default_factory=list)
    data_notes: list[str] = Field(default_factory=list)


def _overall(signals: list[Signal], company_type: str) -> Overall:
    risks = [s for s in signals if s.polarity == "risk"]
    high = [s for s in risks if s.severity == "high"]
    medium = [s for s in risks if s.severity == "medium"]
    core_high = [s for s in high if s.dimension in CORE_DIMENSIONS]
    rule_text = ("高：核心维度（盈利质量/偿债/资产质量/治理/模型）出现高风险信号，或高风险信号≥2；"
                 "中：出现任一高风险信号，或中风险信号≥3；其余为低。")
    if core_high or len(high) >= 2:
        level, code = "高", "high"
    elif high or len(medium) >= 3:
        level, code = "中", "medium"
    else:
        level, code = "低", "low"
    reasons = [f"{s.title}（{DIMENSION_LABELS.get(s.dimension, s.dimension)}）" for s in high + medium][:6]
    if company_type not in ("general",):
        headline = "该公司为金融类企业，一般工商业指标体系仅部分适用，结论仅供参考。"
    elif code == "high":
        headline = f"发现 {len(high)} 项高风险、{len(medium)} 项中风险信号，建议重点核查。"
    elif code == "medium":
        headline = f"发现 {len(high)} 项高风险、{len(medium)} 项中风险信号，需持续关注。"
    else:
        headline = f"未发现高风险信号（中风险 {len(medium)} 项），财务状况整体稳健。"
    return Overall(level=level, level_code=code, headline=headline, reasons=reasons, rule=rule_text)


def diagnose(data: CompanyData, *, years: int = 6, engine_version: str = "0.1.0") -> DiagnosisResult:
    engine = Engine(data)
    periods = data.annual_ends()[:years]
    notes: list[str] = list(data.parse_issues)
    if data.profile.company_type != "general":
        notes.append(f"公司类型为 {data.profile.company_type}，其报表结构与一般工商业企业不同，部分指标不适用。")
    if len(periods) < 3:
        notes.append("可用年报少于 3 期，趋势类判断可靠性下降。")

    # 1) 指标（含模型分项）
    metric_rows: list[MetricRow] = []
    for dim, codes in KEY_METRICS.items():
        for code in codes:
            mdef = REGISTRY[code]
            values = {}
            for p in periods:
                ev = engine.compute(code, p)
                values[p] = ev.id if ev else None
            metric_rows.append(MetricRow(code=code, label=mdef.label, unit=mdef.unit, dimension=dim,
                                         formula=mdef.formula, description=mdef.description,
                                         higher_is_better=mdef.higher_is_better, values=values))
    models: dict[str, dict[str, Any]] = {}
    for code, parts in MODEL_COMPONENTS.items():
        mdef = REGISTRY[code]
        models[code] = {
            "label": mdef.label, "formula": mdef.formula, "description": mdef.description, "reference": mdef.reference,
            "values": {p: (engine.compute(code, p).id if engine.compute(code, p) else None) for p in periods},
            "components": [{"code": pc, "label": REGISTRY[pc].label, "formula": REGISTRY[pc].formula,
                            "values": {p: engine.compute(pc, p).id for p in periods}} for pc in parts],
        }

    # 2) 数据核验
    checks = run_checks(engine, periods)
    # 3) 风险信号
    signals = run_rules(engine, data, periods)
    signals.sort(key=lambda s: (s.polarity != "risk", -SEVERITY_ORDER[s.severity]))

    # 4) 维度汇总
    dims = []
    for code, label in DIMENSION_LABELS.items():
        ds = [s for s in signals if s.dimension == code]
        risk = [s for s in ds if s.polarity == "risk"]
        level = max((s.severity for s in risk), key=lambda x: SEVERITY_ORDER[x], default="none")
        dims.append(DimensionSummary(code=code, label=label, level=level, risk_count=len(risk),
                                     strength_count=len(ds) - len(risk)))

    # 5) 报表事实（供“财务数据”页与溯源）
    statements: dict[str, dict[str, dict[str, str]]] = {"bs": {}, "is": {}, "cf": {}}
    for p in periods:
        for item, fact in data.facts.get(p, {}).items():
            ev = engine.ledger.fact(fact)
            statements[item.split(".", 1)[0]].setdefault(item, {})[p] = ev.id

    period_objs = [p for p in data.annual_periods if p.end in periods]
    return DiagnosisResult(
        engine_version=engine_version,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        profile=data.profile,
        periods=period_objs,
        interim_periods=data.interim_periods[:4],
        metrics=metric_rows,
        models=models,
        checks=checks,
        checks_summary=summarize(checks),
        signals=signals,
        dimensions=dims,
        overall=_overall(signals, data.profile.company_type),
        statements=statements,
        evidence={ev.id: ev for ev in engine.ledger.all()},
        announcements=[a.model_dump() for a in data.announcements[:60]],
        documents=[d.model_dump() for d in data.documents],
        data_notes=notes,
    )
