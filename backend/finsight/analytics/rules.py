"""风险信号规则库。

每条规则：读取已登记的指标证据 → 按阈值给出信号（风险或亮点）→ 文本中以 [[证据ID]] 内联引用。
阈值分两类，并在 rationale 中如实标注：
* 文献阈值：来自原始论文（Beneish、Altman、Piotroski 等）；
* 经验阈值：业内常用的排查线索，不是监管标准，可在 RULE_PARAMS 中调整。
规则只产生“需要关注的线索”，最终判断留给研究员 —— 报告中会明确这一点。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from finsight.analytics.engine import Engine
from finsight.analytics.models import (
    ALTMAN_Z2_DISTRESS,
    ALTMAN_Z2_SAFE,
    BENEISH_COEF,
    BENEISH_THRESHOLD,
    BENEISH_WATCH,
)
from finsight.data.service import CompanyData
from finsight.domain import Evidence, Severity, fmt_value

Dimension = Literal["profitability", "earnings_quality", "solvency", "asset_quality", "efficiency", "growth",
                    "governance", "models"]

DIMENSION_LABELS: dict[str, str] = {
    "profitability": "盈利能力", "earnings_quality": "盈利质量", "solvency": "偿债与流动性",
    "asset_quality": "资产质量", "efficiency": "营运效率", "growth": "成长性", "governance": "治理与信息披露",
    "models": "综合预警模型",
}

# 可调参数（经验阈值）
RULE_PARAMS = {
    "ocf_to_np_low": 0.5, "ocf_to_np_warn": 0.8, "accruals_medium": 0.10, "accruals_high": 0.20,
    "rec_gap_medium": 0.20, "rec_gap_high": 0.50, "nonrec_medium": 0.30, "nonrec_high": 0.50,
    "cash_rev_low": 0.80, "debt_ratio_medium": 0.70, "debt_ratio_high": 0.85, "icr_high": 1.5, "icr_medium": 3.0,
    "cash_assets_double": 0.15, "ibd_assets_double": 0.15, "cash_yield_low": 0.01, "goodwill_medium": 0.15,
    "goodwill_high": 0.30, "other_rec_medium": 0.05, "other_rec_high": 0.10, "prepay_medium": 0.05,
    "inv_gap_medium": 0.30, "gm_drop": 0.05, "rev_drop": -0.20, "np_drop": -0.50, "rec_material_days": 18.0,
}


# 阈值的单位与说明（用于把阈值登记为可引用的 param 证据）
PARAM_META: dict[str, tuple[str, str]] = {
    "ocf_to_np_low": ("times", "净现比低位阈值"), "ocf_to_np_warn": ("times", "净现比关注阈值"),
    "accruals_medium": ("ratio", "应计比率中风险阈值"), "accruals_high": ("ratio", "应计比率高风险阈值"),
    "rec_gap_medium": ("ratio", "应收-营收增速差中风险阈值"), "rec_gap_high": ("ratio", "应收-营收增速差高风险阈值"),
    "nonrec_medium": ("ratio", "非经常性损益占比中风险阈值"), "nonrec_high": ("ratio", "非经常性损益占比高风险阈值"),
    "cash_rev_low": ("times", "收现比低位阈值"), "debt_ratio_medium": ("ratio", "资产负债率中风险阈值"),
    "debt_ratio_high": ("ratio", "资产负债率高风险阈值"), "icr_high": ("times", "利息保障倍数高风险阈值"),
    "icr_medium": ("times", "利息保障倍数中风险阈值"), "cash_assets_double": ("ratio", "存贷双高：货币资金占比阈值"),
    "ibd_assets_double": ("ratio", "存贷双高：有息负债率阈值"), "cash_yield_low": ("ratio", "货币资金隐含收益率低位阈值"),
    "goodwill_medium": ("ratio", "商誉占比中风险阈值"), "goodwill_high": ("ratio", "商誉占比高风险阈值"),
    "other_rec_medium": ("ratio", "其他应收款占比中风险阈值"), "other_rec_high": ("ratio", "其他应收款占比高风险阈值"),
    "prepay_medium": ("ratio", "预付款项占比阈值"), "inv_gap_medium": ("ratio", "存货-成本增速差阈值"),
    "gm_drop": ("ratio", "毛利率下滑阈值"), "rev_drop": ("ratio", "营收下滑阈值"), "np_drop": ("ratio", "利润下滑阈值"),
    "rec_material_days": ("days", "应收重要性门槛（周转天数）"),
}
LITERATURE_PARAMS: dict[str, tuple[float, str, str, str]] = {
    "beneish_threshold": (BENEISH_THRESHOLD, "score", "Beneish M-Score 判别阈值", "Beneish (1999)"),
    "beneish_watch": (BENEISH_WATCH, "score", "Beneish M-Score 保守警戒阈值", "Beneish et al. (2013) 常用阈值"),
    "altman_z2_safe": (ALTMAN_Z2_SAFE, "score", "Altman Z'' 安全区下限", "Altman, Hartzell & Peck (1995)"),
    "altman_z2_distress": (ALTMAN_Z2_DISTRESS, "score", "Altman Z'' 困境区上限", "Altman, Hartzell & Peck (1995)"),
    "piotroski_max": (9.0, "count", "Piotroski F-Score 满分", "Piotroski (2000)"),
}


def register_params(ledger) -> None:
    for name, value in RULE_PARAMS.items():
        unit, label = PARAM_META.get(name, ("ratio", name))
        ledger.add(Evidence(id=f"p:{name}", kind="param", label=label, value=value, unit=unit,  # type: ignore[arg-type]
                            note="经验阈值（可在 RULE_PARAMS 中调整）"))
    for name, (value, unit, label, ref) in LITERATURE_PARAMS.items():
        ledger.add(Evidence(id=f"p:{name}", kind="param", label=label, value=value, unit=unit,  # type: ignore[arg-type]
                            note=f"文献阈值：{ref}"))


def tp(r: RuleCtx, name: str) -> str:
    """阈值的展示文本 + 引用。"""
    ev = r.engine.ledger.get(f"p:{name}")
    return f"{fmt_value(ev.value, ev.unit)}[[p:{name}]]" if ev else name


class Signal(BaseModel):
    rule: str
    title: str
    dimension: str
    severity: Severity
    polarity: Literal["risk", "strength"] = "risk"
    period: str | None = None
    summary: str  # 含 [[证据ID]] 内联引用
    evidence: list[str] = Field(default_factory=list)
    rationale: str = ""
    threshold_type: Literal["literature", "heuristic", "fact"] = "heuristic"
    reference: str | None = None

    @property
    def id(self) -> str:
        return f"s:{self.rule}"


class RuleCtx:
    def __init__(self, engine: Engine, data: CompanyData, periods: list[str]):
        self.engine, self.data, self.periods = engine, data, periods
        self.latest = periods[0] if periods else None

    def ev(self, code: str, period: str | None = None) -> Evidence | None:
        period = period or self.latest
        if period is None:
            return None
        ev = self.engine.compute(code, period)
        return ev if ev is not None and ev.value is not None else None

    def series(self, code: str, n: int = 3) -> list[Evidence]:
        out = []
        for p in self.periods[:n]:
            ev = self.ev(code, p)
            if ev is not None:
                out.append(ev)
        return out


def cite(ev: Evidence) -> str:
    return f"{fmt_value(ev.value, ev.unit)}[[{ev.id}]]"


def yr(ev: Evidence) -> str:
    return f"{ev.period[:4]}年" if ev.period else ""


RULES: list[Callable[[RuleCtx], list[Signal]]] = []


def rule(fn: Callable[[RuleCtx], list[Signal]]) -> Callable[[RuleCtx], list[Signal]]:
    RULES.append(fn)
    return fn


P = RULE_PARAMS

# ------------------------------------------------------------------ 盈利质量


@rule
def eq01_cash_conversion(r: RuleCtx) -> list[Signal]:
    s = r.series("ocf_to_np", 3)
    if len(s) < 2:
        return []
    low = [e for e in s if float(e.value) < P["ocf_to_np_low"]]  # type: ignore[arg-type]
    text = "、".join(f"{yr(e)} {cite(e)}" for e in s)
    if len(low) >= 2:
        return [Signal(rule="EQ01", title="利润的现金含量持续偏低", dimension="earnings_quality", severity="high",
                       period=s[0].period, summary=f"近年净现比：{text}，其中 {len(low)} 年低于 {tp(r, 'ocf_to_np_low')}。",
                       evidence=[e.id for e in s], rationale="净利润长期没有转化为经营现金流，是盈余质量差和收入确认激进的常见特征（经验阈值）。")]
    avg = sum(float(e.value) for e in s) / len(s)  # type: ignore[arg-type]
    if float(s[0].value) < P["ocf_to_np_warn"] and avg < P["ocf_to_np_warn"]:  # type: ignore[arg-type]
        return [Signal(rule="EQ01", title="利润的现金含量偏低", dimension="earnings_quality", severity="medium",
                       period=s[0].period, summary=f"近年净现比：{text}，均值低于 {tp(r, 'ocf_to_np_warn')}。",
                       evidence=[e.id for e in s], rationale="经验阈值：净现比持续低于 0.8 需关注应收、存货对现金的占用。")]
    if avg >= 1.0 and all(float(e.value) >= 0.9 for e in s):  # type: ignore[arg-type]
        return [Signal(rule="EQ01", title="利润的现金含量充足", dimension="earnings_quality", severity="info",
                       polarity="strength", period=s[0].period, summary=f"近年净现比：{text}，经营现金流对利润覆盖充分。",
                       evidence=[e.id for e in s], rationale="经营现金流持续覆盖净利润，盈利质量较高。")]
    return []


@rule
def eq02_accruals(r: RuleCtx) -> list[Signal]:
    e = r.ev("accruals_ratio")
    if e is None:
        return []
    v = float(e.value)  # type: ignore[arg-type]
    if v > P["accruals_high"] or v > P["accruals_medium"]:
        sev: Severity = "high" if v > P["accruals_high"] else "medium"
        return [Signal(rule="EQ02", title="应计利润占比偏高", dimension="earnings_quality", severity=sev, period=e.period,
                       summary=f"{yr(e)}总应计比率为 {cite(e)}，利润中较大部分未体现为现金流入。",
                       evidence=[e.id], threshold_type="heuristic",
                       rationale="Sloan (1996) 发现高应计企业未来盈利持续性更差；10%/20% 为经验分档。",
                       reference="Sloan, R. G. (1996). The Accounting Review, 71(3), 289–315.")]
    return []


@rule
def eq03_receivables(r: RuleCtx) -> list[Signal]:
    gap = r.ev("receivables_revenue_gap")
    if gap is None:
        return []
    v = float(gap.value)  # type: ignore[arg-type]
    if v <= P["rec_gap_medium"]:
        return []
    dso = r.ev("dso")
    if dso is not None and float(dso.value) < P["rec_material_days"]:  # type: ignore[arg-type]
        return []  # 应收规模不重要（周转天数很短），增速差异没有经济意义
    rg, revg = r.ev("receivables_growth"), r.ev("revenue_growth")
    ev_ids = [gap.id] + [e.id for e in (rg, revg) if e]
    detail = f"应收类合计增速 {cite(rg)}，营业收入增速 {cite(revg)}" if rg and revg else f"差值 {cite(gap)}"
    return [Signal(rule="EQ03", title="应收增速显著快于收入", dimension="earnings_quality",
                   severity="high" if v > P["rec_gap_high"] else "medium", period=gap.period,
                   summary=f"{yr(gap)}{detail}，两者相差 {cite(gap)}。", evidence=ev_ids,
                   rationale="应收扩张快于收入，可能是放宽信用政策“以账期换收入”，也可能是收入确认提前（经验阈值 20pp/50pp）。")]


@rule
def eq04_nonrecurring(r: RuleCtx) -> list[Signal]:
    e, npp = r.ev("nonrecurring_ratio"), r.ev("roe")
    if e is None:
        return []
    v = float(e.value)  # type: ignore[arg-type]
    if v > P["nonrec_medium"]:
        return [Signal(rule="EQ04", title="利润依赖非经常性损益", dimension="earnings_quality",
                       severity="high" if v > P["nonrec_high"] else "medium", period=e.period,
                       summary=f"{yr(e)}非经常性损益占归母净利润 {cite(e)}。", evidence=[e.id],
                       rationale="政府补助、资产处置、公允价值变动等不可持续，扣非利润更能反映主业盈利（经验阈值 30%/50%）。")]
    _ = npp
    return []


@rule
def eq05_cash_collection(r: RuleCtx) -> list[Signal]:
    s = r.series("cash_to_revenue", 2)
    if len(s) < 2:
        return []
    if all(float(e.value) < P["cash_rev_low"] for e in s):  # type: ignore[arg-type]
        return [Signal(rule="EQ05", title="收入回款偏弱", dimension="earnings_quality", severity="medium",
                       period=s[0].period, summary=f"连续两年收现比低于 {tp(r, 'cash_rev_low')}：" + "、".join(f"{yr(e)} {cite(e)}" for e in s) + "。",
                       evidence=[e.id for e in s],
                       rationale="收现比含增值税，正常情况下接近或高于 1；持续偏低说明收入未能及时转化为现金（经验阈值）。")]
    return []


# ------------------------------------------------------------------ 偿债与流动性


@rule
def so01_leverage(r: RuleCtx) -> list[Signal]:
    e = r.ev("debt_ratio")
    if e is None:
        return []
    v = float(e.value)  # type: ignore[arg-type]
    if v > P["debt_ratio_medium"]:
        return [Signal(rule="SO01", title="资产负债率偏高", dimension="solvency",
                       severity="high" if v > P["debt_ratio_high"] else "medium", period=e.period,
                       summary=f"{yr(e)}资产负债率 {cite(e)}。", evidence=[e.id],
                       rationale="经验阈值 70%/85%；房地产、建筑等行业天然偏高，应结合行业比较与负债结构判断。")]
    if v < 0.3:
        return [Signal(rule="SO01", title="财务杠杆低", dimension="solvency", severity="info", polarity="strength",
                       period=e.period, summary=f"{yr(e)}资产负债率仅 {cite(e)}，财务结构稳健。", evidence=[e.id])]
    return []


@rule
def so02_short_term(r: RuleCtx) -> list[Signal]:
    cs, cr = r.ev("cash_to_st_debt"), r.ev("current_ratio")
    if cs is None:
        return []
    v = float(cs.value)  # type: ignore[arg-type]
    if v >= 1.0:
        return []
    weak_cr = cr is not None and float(cr.value) < 1.0  # type: ignore[arg-type]
    ev_ids = [cs.id] + ([cr.id] if cr else [])
    tail = f"，流动比率 {cite(cr)}" if cr else ""
    return [Signal(rule="SO02", title="短期偿债压力", dimension="solvency", severity="high" if weak_cr else "medium",
                   period=cs.period, summary=f"{yr(cs)}现金短债比 {cite(cs)}{tail}，账面现金不足以覆盖一年内到期的有息负债。",
                   evidence=ev_ids, rationale="现金短债比 < 1 意味着依赖再融资或经营回款偿还短债；叠加流动比率 < 1 风险更高（经验阈值）。")]


@rule
def so03_interest(r: RuleCtx) -> list[Signal]:
    e = r.ev("interest_coverage")
    if e is None:
        return []
    v = float(e.value)  # type: ignore[arg-type]
    if v < P["icr_medium"]:
        return [Signal(rule="SO03", title="利息保障倍数偏低", dimension="solvency",
                       severity="high" if v < P["icr_high"] else "medium", period=e.period,
                       summary=f"{yr(e)}利息保障倍数 {cite(e)}。", evidence=[e.id],
                       rationale="息税前利润对利息的覆盖不足 1.5/3 倍时，利润波动可能直接威胁付息能力（经验阈值）。")]
    return []


@rule
def so04_negative_ocf(r: RuleCtx) -> list[Signal]:
    s = [r.engine.ledger.fact_for("cf.ocf", p) for p in r.periods[:3]]
    s = [e for e in s if e is not None and e.value is not None]
    neg = [e for e in s if float(e.value) < 0]  # type: ignore[arg-type]
    if len(neg) >= 2:
        return [Signal(rule="SO04", title="经营现金流持续为负", dimension="solvency", severity="high", period=s[0].period,
                       summary="近三年经营活动现金流量净额：" + "、".join(f"{yr(e)} {cite(e)}" for e in s) + "。",
                       evidence=[e.id for e in s], threshold_type="fact",
                       rationale="主营业务不能自我造血，持续依赖筹资维持运转。")]
    if neg and neg[0].period == r.latest:
        return [Signal(rule="SO04", title="经营现金流为负", dimension="solvency", severity="medium", period=r.latest,
                       summary=f"{yr(neg[0])}经营活动现金流量净额为 {cite(neg[0])}。", evidence=[neg[0].id],
                       threshold_type="fact")]
    return []


# ------------------------------------------------------------------ 资产质量


@rule
def aq01_cash_and_debt(r: RuleCtx) -> list[Signal]:
    ca, ib, cy = r.ev("cash_to_assets"), r.ev("ibd_ratio"), r.ev("implied_cash_yield")
    if ca is None or ib is None:
        return []
    if float(ca.value) < P["cash_assets_double"] or float(ib.value) < P["ibd_assets_double"]:  # type: ignore[arg-type]
        return []
    ev_ids = [ca.id, ib.id] + ([cy.id] if cy else [])
    low_yield = cy is not None and float(cy.value) < P["cash_yield_low"]  # type: ignore[arg-type]
    tail = f"，而货币资金隐含收益率仅 {cite(cy)}" if cy else ""
    return [Signal(rule="AQ01", title="“存贷双高”" + ("且资金收益率异常偏低" if low_yield else ""),
                   dimension="asset_quality", severity="high" if low_yield else "medium", period=ca.period,
                   summary=f"{yr(ca)}货币资金占总资产 {cite(ca)}，有息负债率 {cite(ib)}{tail}。", evidence=ev_ids,
                   rationale="账上资金充裕却同时大量举债、且利息收入与资金规模不匹配，是 A 股财务造假案例中反复出现的资金类异常；"
                             "也可能由资金受限、集团资金归集等合理原因造成，需核对受限资金与关联方资金往来（经验阈值）。")]


@rule
def aq02_goodwill(r: RuleCtx) -> list[Signal]:
    e = r.ev("goodwill_to_equity")
    if e is None or float(e.value) <= P["goodwill_medium"]:  # type: ignore[arg-type]
        return []
    return [Signal(rule="AQ02", title="商誉减值风险", dimension="asset_quality",
                   severity="high" if float(e.value) > P["goodwill_high"] else "medium", period=e.period,  # type: ignore[arg-type]
                   summary=f"{yr(e)}商誉占归母净资产 {cite(e)}。", evidence=[e.id],
                   rationale="并购形成的大额商誉在被收购方业绩不达预期时集中减值，可能一次性侵蚀大量净资产（经验阈值 15%/30%）。")]


@rule
def aq03_other_receivables(r: RuleCtx) -> list[Signal]:
    e = r.ev("other_rec_to_assets")
    if e is None or float(e.value) <= P["other_rec_medium"]:  # type: ignore[arg-type]
        return []
    return [Signal(rule="AQ03", title="其他应收款占比偏高", dimension="asset_quality",
                   severity="high" if float(e.value) > P["other_rec_high"] else "medium", period=e.period,  # type: ignore[arg-type]
                   summary=f"{yr(e)}其他应收款占总资产 {cite(e)}。", evidence=[e.id],
                   rationale="其他应收款是关联方资金占用的常见“藏身处”，应查阅附注中的欠款方与账龄（经验阈值 5%/10%）。")]


@rule
def aq04_prepayments(r: RuleCtx) -> list[Signal]:
    e = r.ev("prepay_to_assets")
    if e is None or float(e.value) <= P["prepay_medium"]:  # type: ignore[arg-type]
        return []
    return [Signal(rule="AQ04", title="预付款项占比偏高", dimension="asset_quality", severity="medium", period=e.period,
                   summary=f"{yr(e)}预付款项占总资产 {cite(e)}。", evidence=[e.id],
                   rationale="大额预付款可能被用于体外资金循环，应关注预付对象是否为关联方及长期挂账情况（经验阈值）。")]


@rule
def aq05_inventory(r: RuleCtx) -> list[Signal]:
    e = r.ev("inventory_cogs_gap")
    if e is None or float(e.value) <= P["inv_gap_medium"]:  # type: ignore[arg-type]
        return []
    return [Signal(rule="AQ05", title="存货增速显著快于成本", dimension="asset_quality", severity="medium", period=e.period,
                   summary=f"{yr(e)}存货增速与营业成本增速相差 {cite(e)}。", evidence=[e.id],
                   rationale="存货积压提示滞销与跌价风险，极端情况下可能存在存货虚增（经验阈值 30pp）。")]


# ------------------------------------------------------------------ 盈利能力 / 成长


@rule
def pr01_margin_drop(r: RuleCtx) -> list[Signal]:
    s = r.series("gross_margin", 2)
    if len(s) < 2 or s[0].period != r.latest:
        return []
    drop = float(s[1].value) - float(s[0].value)  # type: ignore[arg-type]
    if drop > P["gm_drop"]:
        return [Signal(rule="PR01", title="毛利率明显下滑", dimension="profitability", severity="medium", period=r.latest,
                       summary=f"毛利率由 {yr(s[1])} {cite(s[1])} 降至 {yr(s[0])} {cite(s[0])}。",
                       evidence=[s[0].id, s[1].id], rationale="毛利率一年内下滑超过 5 个百分点，需分析价格、成本与产品结构变化（经验阈值）。")]
    return []


@rule
def gr01_decline(r: RuleCtx) -> list[Signal]:
    out = []
    rg, ng = r.ev("revenue_growth"), r.ev("np_parent_growth")
    if rg is not None and float(rg.value) < P["rev_drop"]:  # type: ignore[arg-type]
        out.append(Signal(rule="GR01", title="营业收入大幅下滑", dimension="growth", severity="medium", period=rg.period,
                          summary=f"{yr(rg)}营业收入同比 {cite(rg)}。", evidence=[rg.id], rationale="经验阈值：收入同比下降超过 20%。"))
    if ng is not None and float(ng.value) < P["np_drop"]:  # type: ignore[arg-type]
        out.append(Signal(rule="GR02", title="归母净利润大幅下滑", dimension="growth", severity="medium", period=ng.period,
                          summary=f"{yr(ng)}归母净利润同比 {cite(ng)}。", evidence=[ng.id], rationale="经验阈值：利润同比下降超过 50%。"))
    return out


# ------------------------------------------------------------------ 综合模型


@rule
def md01_beneish(r: RuleCtx) -> list[Signal]:
    e = r.ev("beneish_m")
    if e is None:
        return []
    v = float(e.value)  # type: ignore[arg-type]
    if v <= BENEISH_WATCH:
        return []
    # 驱动分解：各分项相对“中性值”（指数=1，TATA=0）的贡献
    contribs = []
    for code, coef in BENEISH_COEF.items():
        comp = r.ev(code)
        if comp is None:
            continue
        neutral = 0.0 if code == "m_tata" else 1.0
        contribs.append((coef * (float(comp.value) - neutral), comp))  # type: ignore[arg-type]
    contribs.sort(key=lambda x: -x[0])
    drivers = "、".join(f"{c.label} {cite(c)}" for d, c in contribs[:2] if d > 0)
    tail = f" 主要驱动：{drivers}。" if drivers else ""
    ev_ids = [e.id] + [c.id for d, c in contribs[:2] if d > 0]
    ref = "Beneish (1999), Financial Analysts Journal."
    if v > BENEISH_THRESHOLD:
        return [Signal(rule="MD01", title="Beneish 模型提示盈余操纵特征", dimension="models", severity="high", period=e.period,
                       summary=f"{yr(e)} Beneish M-Score 为 {cite(e)}，高于判别阈值 {tp(r, 'beneish_threshold')}。{tail}",
                       evidence=ev_ids, threshold_type="literature", reference=ref,
                       rationale="模型基于美国样本估计，在 A 股作为排查线索而非结论：应结合驱动分项回到报表附注核实。")]
    return [Signal(rule="MD01", title="Beneish M-Score 接近警戒阈值", dimension="models", severity="low", period=e.period,
                   summary=f"{yr(e)} Beneish M-Score 为 {cite(e)}，介于 {tp(r, 'beneish_watch')} 与 {tp(r, 'beneish_threshold')} 之间。{tail}",
                   evidence=ev_ids, threshold_type="literature", reference=ref,
                   rationale="未超过判别阈值，但已进入常用的保守警戒区间。")]


@rule
def md02_altman(r: RuleCtx) -> list[Signal]:
    e = r.ev("altman_z2")
    if e is None:
        return []
    v = float(e.value)  # type: ignore[arg-type]
    ref = "Altman (1968)；Z'' 见 Altman, Hartzell & Peck (1995)。"
    if v < ALTMAN_Z2_DISTRESS:
        return [Signal(rule="MD02", title="Altman Z'' 处于困境区", dimension="models", severity="high", period=e.period,
                       summary=f"{yr(e)} Z''-Score 为 {cite(e)}，低于 {tp(r, 'altman_z2_distress')}。", evidence=[e.id],
                       threshold_type="literature", reference=ref, rationale="困境区企业的财务失败概率显著更高。")]
    if v < ALTMAN_Z2_SAFE:
        return [Signal(rule="MD02", title="Altman Z'' 处于灰色区", dimension="models", severity="medium", period=e.period,
                       summary=f"{yr(e)} Z''-Score 为 {cite(e)}，介于 {tp(r, 'altman_z2_distress')} 与 {tp(r, 'altman_z2_safe')} 之间。",
                       evidence=[e.id], threshold_type="literature", reference=ref)]
    return [Signal(rule="MD02", title="Altman Z'' 处于安全区", dimension="models", severity="info", polarity="strength",
                   period=e.period, summary=f"{yr(e)} Z''-Score 为 {cite(e)}，高于 {tp(r, 'altman_z2_safe')}。", evidence=[e.id],
                   threshold_type="literature", reference=ref)]


@rule
def md03_piotroski(r: RuleCtx) -> list[Signal]:
    e = r.ev("piotroski_f")
    if e is None:
        return []
    v = float(e.value)  # type: ignore[arg-type]
    ref = "Piotroski (2000), Journal of Accounting Research."
    if v <= 3:
        return [Signal(rule="MD03", title="Piotroski F-Score 偏弱", dimension="models", severity="medium", period=e.period,
                       summary=f"{yr(e)} F-Score 为 {cite(e)}（满分 {tp(r, 'piotroski_max')}）。", evidence=[e.id], threshold_type="literature",
                       reference=ref, rationale="多项基本面信号同时恶化。")]
    if v >= 7:
        return [Signal(rule="MD03", title="Piotroski F-Score 强健", dimension="models", severity="info", polarity="strength",
                       period=e.period, summary=f"{yr(e)} F-Score 为 {cite(e)}（满分 {tp(r, 'piotroski_max')}）。", evidence=[e.id],
                       threshold_type="literature", reference=ref)]
    return []


# ------------------------------------------------------------------ 治理与信息披露


@rule
def gv01_audit(r: RuleCtx) -> list[Signal]:
    out = []
    for p in r.data.annual_periods[:3]:
        if p.audit_opinion and p.audit_opinion != "标准无保留意见":
            ev = r.engine.ledger.add(Evidence(
                id=f"t:audit@{p.end}", kind="text", label="审计意见", value=p.audit_opinion, unit="text", period=p.end,
                source=(r.data.fact("bs.total_assets", p.end).source if r.data.fact("bs.total_assets", p.end) else None)))
            out.append(Signal(rule="GV01", title=f"{p.fiscal_year}年度审计意见为非标准意见", dimension="governance",
                              severity="high" if p.end == r.latest else "medium", period=p.end,
                              summary=f"{p.fiscal_year}年度财务报告审计意见：{p.audit_opinion}[[{ev.id}]]。",
                              evidence=[ev.id], threshold_type="fact",
                              rationale="非标准审计意见意味着审计师对财务报表的公允性存在保留或无法获取充分证据。"))
    return out[:2]


EVENT_PATTERNS: tuple[tuple[str, Severity, str], ...] = (
    (r"立案|行政处罚|处罚决定|市场禁入", "high", "监管调查或处罚"),
    (r"无法表示意见|否定意见|保留意见", "high", "非标准审计意见相关公告"),
    (r"会计差错更正|前期差错|追溯调整|追溯重述", "medium", "会计差错更正 / 追溯调整"),
    (r"问询函|关注函|监管函|警示函|监管工作函", "medium", "交易所 / 监管问询"),
    (r"延期披露|无法按期披露|推迟披露", "medium", "定期报告延期披露"),
    (r"违规担保|资金占用|占用资金", "high", "违规担保或资金占用"),
    (r"变更会计师事务所|更换会计师事务所|改聘", "low", "更换审计机构"),
    (r"退市风险警示|其他风险警示|\*ST", "high", "风险警示 / 退市风险"),
)


# 年报例行披露的“资金占用专项说明”等不是违规事件；“不存在……”类声明也不计入
ROUTINE_TITLE = re.compile(
    r"专项(审计|审核)?(说明|报告)|审核报告|情况的说明|资金往来情况|汇总表|自查|不存在|核查意见|法律意见|制度|管理办法|工作细则")


@rule
def gv02_events(r: RuleCtx) -> list[Signal]:
    hits: dict[str, list] = {}
    for a in r.data.announcements:
        if ROUTINE_TITLE.search(a.title):
            continue
        for pattern, sev, label in EVENT_PATTERNS:
            if re.search(pattern, a.title):
                # 排除“回复/说明”类公告中的重复计数：仍记录，但标题保持原文
                hits.setdefault(label, []).append((a, sev))
                break
    out = []
    for label, items in hits.items():
        sev = items[0][1]
        ev_ids = []
        for a, _ in items[:5]:
            eid = f"e:{a.id}"
            r.engine.ledger.add(Evidence(id=eid, kind="event", label=a.title, value=a.date or "", unit="text",
                                         period=a.date, note=a.pdf_url, inputs=[]))
            ev_ids.append(eid)
        sample = "；".join(f"{a.date} 《{a.title}》[[e:{a.id}]]" for a, _ in items[:3])
        out.append(Signal(rule="GV02", title=f"公告事件：{label}（{len(items)} 条）", dimension="governance", severity=sev,
                          period=items[0][0].date, summary=f"近年公告中发现 {len(items)} 条与“{label}”相关的公告，例如：{sample}。",
                          evidence=ev_ids, threshold_type="fact",
                          rationale="基于公告标题的关键词识别，需阅读原文确认事件性质与影响（标题匹配可能包含“回复”“澄清”类公告）。"))
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return sorted(out, key=lambda s: order[s.severity])


def run_rules(engine: Engine, data: CompanyData, periods: list[str]) -> list[Signal]:
    register_params(engine.ledger)
    ctx = RuleCtx(engine, data, periods)
    signals: list[Signal] = []
    for fn in RULES:
        try:
            signals.extend(fn(ctx))
        except Exception as exc:  # 单条规则失败不影响整体，但要显式暴露
            signals.append(Signal(rule="SYS", title=f"规则执行异常：{fn.__name__}", dimension="governance",
                                  severity="info", summary=f"{type(exc).__name__}: {exc}", threshold_type="fact"))
    for s in signals:
        engine.ledger.add(Evidence(id=s.id + (f"@{s.period}" if s.period else ""), kind="signal", label=s.title,
                                   value=s.severity, unit="text", period=s.period, inputs=s.evidence,
                                   note=s.rationale or None))
    return signals
