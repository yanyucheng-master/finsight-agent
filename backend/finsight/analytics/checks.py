"""数据完整性核验：在分析之前先验证“数据本身是否可信”。

三类核验
1. 会计恒等式（identity）：资产 = 负债 + 权益、净利润 = 归母 + 少数股东损益、现金流三项之和 = 净增加额……
2. 跨期连续性（continuity）：上年期末现金 = 本年期初现金（不一致通常意味着追溯调整或口径变化）
3. 第三方交叉核验（vendor）：本系统独立计算的毛利率、资产负债率等，与数据源（东方财富）发布的
   同口径指标比对 —— 两套独立计算一致，说明取数与公式都没有错位。

核验结果本身也登记为证据（kind="check"），报告可以引用“数据已通过 N 项核验”。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from finsight.analytics.engine import Engine
from finsight.analytics.ledger import Ledger
from finsight.domain import Evidence

CheckStatus = Literal["pass", "fail", "skip"]


class CheckResult(BaseModel):
    code: str
    title: str
    category: Literal["identity", "continuity", "vendor", "balance"]
    period: str
    status: CheckStatus
    lhs: float | None = None
    rhs: float | None = None
    diff: float | None = None
    tolerance: float | None = None
    severity: Literal["error", "warning", "info"] = "error"
    evidence_id: str
    inputs: list[str] = []
    note: str | None = None


def _tol(x: float) -> float:
    return max(1.0, abs(x) * 1e-6)


# (代码, 标题, 左侧科目[(科目, 符号)], 右侧科目)
IDENTITIES: tuple[tuple[str, str, tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]], ...] = (
    ("IC01", "资产总计 = 负债和所有者权益总计", (("bs.total_assets", 1),), (("bs.total_liab_equity", 1),)),
    ("IC02", "资产总计 = 负债合计 + 所有者权益合计", (("bs.total_assets", 1),),
     (("bs.total_liabilities", 1), ("bs.total_equity", 1))),
    ("IC03", "流动资产 + 非流动资产 = 资产总计", (("bs.current_assets", 1), ("bs.noncurrent_assets", 1)),
     (("bs.total_assets", 1),)),
    ("IC04", "流动负债 + 非流动负债 = 负债合计", (("bs.current_liabilities", 1), ("bs.noncurrent_liabilities", 1)),
     (("bs.total_liabilities", 1),)),
    ("IC05", "归母权益 + 少数股东权益 = 所有者权益合计", (("bs.equity_parent", 1), ("bs.minority_equity", 1)),
     (("bs.total_equity", 1),)),
    ("IC06", "归母净利润 + 少数股东损益 = 净利润", (("is.np_parent", 1), ("is.minority_pl", 1)),
     (("is.net_profit", 1),)),
    ("IC07", "利润总额 − 所得税费用 = 净利润", (("is.total_profit", 1), ("is.income_tax", -1)),
     (("is.net_profit", 1),)),
    ("IC08", "经营 + 投资 + 筹资 + 汇率影响 = 现金净增加额",
     (("cf.ocf", 1), ("cf.icf", 1), ("cf.fincf", 1), ("cf.fx_effect", 1)), (("cf.net_change", 1),)),
    ("IC09", "期初现金 + 现金净增加额 = 期末现金", (("cf.cash_begin", 1), ("cf.net_change", 1)),
     (("cf.cash_end", 1),)),
    ("IC10", "现金流量表补充资料净利润 = 利润表净利润", (("cf.sup_net_profit", 1),), (("is.net_profit", 1),)),
    ("IC11", "补充资料经营现金流 = 主表经营现金流", (("cf.sup_ocf", 1),), (("cf.ocf", 1),)),
)

# 可缺省科目：未列示视为 0
OPTIONAL_IN_IDENTITY = {"bs.minority_equity", "is.minority_pl", "cf.fx_effect"}

BALANCE_ITEMS = ("bs.asset_balance", "bs.liab_balance", "bs.equity_balance", "is.operate_profit_balance",
                 "is.total_profit_balance", "cf.operate_netcash_balance", "cf.cce_add_balance")

# 本系统指标代码 → (东方财富主要指标字段, 换算系数, 说明)
VENDOR_PAIRS: tuple[tuple[str, str, float, str], ...] = (
    ("gross_margin", "XSMLL", 100.0, "毛利率（%）"),
    ("net_margin", "XSJLL", 100.0, "销售净利率（%）"),
    ("debt_ratio", "ZCFZL", 100.0, "资产负债率（%）"),
    ("current_ratio", "LD", 1.0, "流动比率"),
    ("quick_ratio", "SD", 1.0, "速动比率"),
)


def _sum_side(ledger: Ledger, side, period: str) -> tuple[float | None, list[str], list[str]]:
    total, refs, missing = 0.0, [], []
    for item, sign in side:
        ev = ledger.fact_for(item, period)
        if ev is None or ev.value is None:
            if item in OPTIONAL_IN_IDENTITY:
                continue
            missing.append(item)
            continue
        total += sign * float(ev.value)  # type: ignore[arg-type]
        refs.append(ev.id)
    return (None if missing else total), refs, missing


def _register(ledger: Ledger, r: CheckResult) -> CheckResult:
    ledger.add(Evidence(
        id=r.evidence_id, kind="check", label=r.title, value={"pass": "通过", "fail": "未通过", "skip": "跳过"}[r.status],
        unit="text", period=r.period, inputs=r.inputs, note=r.note,
        expression=(f"左={r.lhs:,.2f}；右={r.rhs:,.2f}；差={r.diff:,.2f}" if r.diff is not None else None),
    ))
    return r


def run_checks(engine: Engine, periods: list[str]) -> list[CheckResult]:
    ledger, data = engine.ledger, engine.data
    results: list[CheckResult] = []
    for period in periods:
        # 1) 会计恒等式
        for code, title, lhs_side, rhs_side in IDENTITIES:
            lhs, lrefs, lmiss = _sum_side(ledger, lhs_side, period)
            rhs, rrefs, rmiss = _sum_side(ledger, rhs_side, period)
            eid = f"c:{code}@{period}"
            if lhs is None or rhs is None:
                results.append(_register(ledger, CheckResult(
                    code=code, title=title, category="identity", period=period, status="skip", evidence_id=eid,
                    inputs=lrefs + rrefs, note="缺少科目：" + "、".join(lmiss + rmiss))))
                continue
            diff = lhs - rhs
            tol = _tol(rhs)
            results.append(_register(ledger, CheckResult(
                code=code, title=title, category="identity", period=period,
                status="pass" if abs(diff) <= tol else "fail", lhs=lhs, rhs=rhs, diff=diff, tolerance=tol,
                evidence_id=eid, inputs=lrefs + rrefs)))
        # 2) 平衡项：数据源用于“凑平”的未列示项目，应为 0
        nonzero = []
        refs = []
        ta = ledger.fact_for("bs.total_assets", period)
        # 以千元/百元披露的报表在换算为元时会产生 ±100/±1000 元级的尾差，不视为未列示科目
        rounding_tol = max(1e4, abs(float(ta.value)) * 1e-7) if ta and ta.value is not None else 1e4  # type: ignore[arg-type]
        for item in BALANCE_ITEMS:
            ev = ledger.fact_for(item, period)
            if ev is not None and ev.value is not None and abs(float(ev.value)) > rounding_tol:  # type: ignore[arg-type]
                nonzero.append(f"{ev.label}={float(ev.value):,.2f}")  # type: ignore[arg-type]
                refs.append(ev.id)
        results.append(_register(ledger, CheckResult(
            code="IC12", title="数据源平衡项为零（尾差 ≤ 1 万元视为四舍五入）", category="balance", period=period,
            status="fail" if nonzero else "pass", severity="warning", evidence_id=f"c:IC12@{period}", inputs=refs,
            note=("存在未逐项列示的金额：" + "；".join(nonzero)) if nonzero else None)))
        # 3) 第三方交叉核验
        vendor_row = data.vendor_indicators.get(period) or {}
        for metric_code, field, scale, desc in VENDOR_PAIRS:
            ev = engine.compute(metric_code, period)
            vendor_val = vendor_row.get(field)
            eid = f"c:VC_{metric_code}@{period}"
            if ev is None or ev.value is None or vendor_val is None:
                results.append(_register(ledger, CheckResult(
                    code=f"VC_{metric_code}", title=f"交叉核验：{desc}", category="vendor", period=period,
                    status="skip", severity="warning", evidence_id=eid, inputs=[ev.id] if ev else [],
                    note="本系统指标或数据源指标缺失")))
                continue
            ours = float(ev.value) * scale  # type: ignore[arg-type]
            theirs = float(vendor_val)
            diff = ours - theirs
            tol = max(0.01, abs(theirs) * 1e-3)
            vendor_note = (f"数据源字段 {field}，来源快照 {data.vendor_source.snapshot_id[:8]}"
                           if data.vendor_source else f"数据源字段 {field}")
            results.append(_register(ledger, CheckResult(
                code=f"VC_{metric_code}", title=f"交叉核验：{desc}", category="vendor", period=period,
                status="pass" if abs(diff) <= tol else "fail", lhs=ours, rhs=theirs, diff=diff, tolerance=tol,
                severity="warning", evidence_id=eid, inputs=[ev.id], note=vendor_note)))
    # 4) 跨期连续性：上年期末现金 = 本年期初现金
    annual = engine.annual
    for period in periods:
        idx = annual.index(period) if period in annual else -1
        if idx < 0 or idx + 1 >= len(annual):
            continue
        prev = annual[idx + 1]
        if int(prev[:4]) != int(period[:4]) - 1:
            continue
        end_prev = ledger.fact_for("cf.cash_end", prev)
        begin_cur = ledger.fact_for("cf.cash_begin", period)
        eid = f"c:CC01@{period}"
        if end_prev is None or begin_cur is None:
            continue
        diff = float(begin_cur.value) - float(end_prev.value)  # type: ignore[arg-type]
        tol = _tol(float(end_prev.value))  # type: ignore[arg-type]
        results.append(_register(ledger, CheckResult(
            code="CC01", title="上年期末现金 = 本年期初现金", category="continuity", period=period,
            status="pass" if abs(diff) <= tol else "fail", lhs=float(begin_cur.value),  # type: ignore[arg-type]
            rhs=float(end_prev.value), diff=diff, tolerance=tol, severity="warning", evidence_id=eid,  # type: ignore[arg-type]
            inputs=[begin_cur.id, end_prev.id],
            note=None if abs(diff) <= tol else "不一致通常意味着上年数据被追溯调整（会计差错更正、同一控制下合并等）")))
    return results


def summarize(results: list[CheckResult]) -> dict[str, int]:
    out = {"pass": 0, "fail": 0, "skip": 0, "error_fail": 0}
    for r in results:
        out[r.status] += 1
        if r.status == "fail" and r.severity == "error":
            out["error_fail"] += 1
    return out
