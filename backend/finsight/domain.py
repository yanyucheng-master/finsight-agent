"""领域模型：公司、报告期、财务事实、证据。

约定
* 金额单位统一为“元”（float）；比率为小数（0.25 表示 25%）；展示层再换算为亿元 / 百分比。
* 每一个财务事实（Fact）都携带 SourceRef，指向某一次快照记录中的某个原始字段。
* 证据（Evidence）是系统内一切“可被引用的数字或判断”的统一载体：原始事实、派生指标、
  核验结果、风险信号。报告与问答中的每个数字都必须引用证据 ID。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

CompanyType = Literal["general", "bank", "insurance", "securities", "unknown"]
PeriodType = Literal["FY", "H1", "Q1", "Q3", "Q2", "Q4", "OTHER"]
Unit = Literal["CNY", "ratio", "times", "days", "score", "cny_per_share", "count", "text", "bool"]
EvidenceKind = Literal["fact", "metric", "check", "signal", "model", "text", "event", "param"]
Severity = Literal["high", "medium", "low", "info"]

# 东方财富 F10 的 companyType 编码
EM_COMPANY_TYPES: dict[str, CompanyType] = {"4": "general", "3": "bank", "2": "insurance", "1": "securities"}


class SourceRef(BaseModel):
    """原始数据来源：一次快照记录中的一个字段。"""

    provider: str
    dataset: str
    snapshot_id: str
    sha256: str
    url: str
    fetched_at: str
    field: str | None = None
    report_date: str | None = None
    notice_date: str | None = None  # 公告日期：回测时用于避免未来函数


class Period(BaseModel):
    end: str  # "2025-12-31"
    type: PeriodType
    label: str  # "2025年报"
    fiscal_year: int
    notice_date: str | None = None
    update_date: str | None = None
    audit_opinion: str | None = None

    @property
    def key(self) -> str:
        return self.end


class Fact(BaseModel):
    item: str
    period: str
    value: float
    source: SourceRef


class CompanyProfile(BaseModel):
    code: str
    exchange: str
    name: str
    full_name: str | None = None
    company_type: CompanyType = "unknown"
    industry_em: str | None = None  # 东财行业，如 “食品饮料-饮料-白酒”
    industry_csrc: str | None = None  # 证监会行业
    listing_date: str | None = None
    auditor: str | None = None
    province: str | None = None
    business_scope: str | None = None
    profile: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)


class Evidence(BaseModel):
    """可被引用的证据。

    id 规则（稳定、可读）：
      fact:   f:{item}@{period}            例 f:bs.cash@2025-12-31
      metric: m:{metric}@{period}          例 m:ocf_to_np@2025-12-31
      check:  c:{check}@{period}
      model:  md:{model}@{period}
      signal: s:{rule}
      event:  e:{announcement_id}
      param:  p:{name}                     阈值 / 模型参数（附文献或经验依据）
    """

    id: str
    kind: EvidenceKind
    label: str
    value: float | str | bool | None
    unit: Unit
    period: str | None = None
    formula: str | None = None  # 派生证据的计算公式（人类可读）
    expression: str | None = None  # 代入数值后的算式
    inputs: list[str] = Field(default_factory=list)  # 依赖的证据 ID
    source: SourceRef | None = None  # 原始事实的出处
    note: str | None = None
    status: Literal["ok", "missing", "not_applicable", "error"] = "ok"


def fmt_amount(value: float | None, digits: int = 2) -> str:
    """把“元”格式化为便于阅读的中文金额（亿元 / 万元 / 元）。"""
    if value is None:
        return "—"
    v = float(value)
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:,.{digits}f} 亿元"
    if a >= 1e4:
        return f"{v / 1e4:,.{digits}f} 万元"
    return f"{v:,.{digits}f} 元"


def fmt_value(value: Any, unit: str, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, str):
        return value
    if unit == "CNY":
        return fmt_amount(value, digits)
    if unit == "ratio":
        return f"{value * 100:.{digits}f}%"
    if unit == "times":
        return f"{value:.{digits}f} 倍"
    if unit == "days":
        return f"{value:.{max(digits - 1, 0)}f} 天"
    if unit == "cny_per_share":
        return f"{value:.{digits}f} 元/股"
    if unit == "count":
        return f"{value:.0f}"
    return f"{value:.{digits}f}"
