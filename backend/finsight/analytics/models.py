"""经典财务预警模型：Beneish M-Score、Altman Z''-Score、Piotroski F-Score。

所有模型的分项都登记为独立指标，因此报告里的模型结论可以逐项展开、逐项溯源。

参考文献
* Beneish, M. D. (1999). The Detection of Earnings Manipulation. Financial Analysts Journal, 55(5), 24–36.
* Altman, E. I. (1968). Financial Ratios, Discriminant Analysis and the Prediction of Corporate Bankruptcy.
  Journal of Finance, 23(4), 589–609；Z'' 版本见 Altman, Hartzell & Peck (1995)，适用于非制造业与新兴市场，
  只使用账面数据。
* Piotroski, J. D. (2000). Value Investing: The Use of Historical Financial Statement Information to Separate
  Winners from Losers. Journal of Accounting Research, 38(Supplement), 1–41.

本地化口径（在报告中如实披露）
* M-Score 的 SGA 使用 销售+管理+研发费用（2018 年研发费用拆出，合并后跨年可比）；
  应收使用“应收类合计”以跨越 2019/2020 年准则重分类；长期负债 = 长期借款 + 应付债券；
* Z'' 的留存收益 = 盈余公积 + 未分配利润；EBIT = 利润总额 + 利息费用；
* 这些模型基于美国样本估计，在 A 股上作为“排查线索”而非定罪依据。
"""

from __future__ import annotations

from finsight.analytics.engine import Calc, V, metric, vsum
from finsight.analytics.metrics import receivables, sga

BENEISH_REF = "Beneish, M. D. (1999). Financial Analysts Journal, 55(5), 24–36."
ALTMAN_REF = "Altman, E. I. (1968). Journal of Finance, 23(4); Z'' 见 Altman, Hartzell & Peck (1995)."
PIOTROSKI_REF = "Piotroski, J. D. (2000). Journal of Accounting Research, 38, 1–41."

BENEISH_THRESHOLD = -1.78  # Beneish (1999) 8 变量模型的判别阈值
BENEISH_WATCH = -2.22  # 常用的更保守阈值，介于两者之间视为“接近阈值”
DSRI_MATERIALITY = 0.05  # 应收类合计 / 营业收入 低于该值视为不重要（经验处理）
BENEISH_COEF = {"m_dsri": 0.920, "m_gmi": 0.528, "m_aqi": 0.404, "m_sgi": 0.892, "m_depi": 0.115,
                "m_sgai": -0.172, "m_tata": 4.679, "m_lvgi": -0.327}
ALTMAN_Z2_SAFE = 2.60
ALTMAN_Z2_DISTRESS = 1.10


def _long_term_debt(c: Calc, lag: int = 0) -> V:
    if c._shift(lag) is None:
        return V(None, missing=frozenset({f"长期负债（{c.year(lag)}年）"}))
    return vsum(c.opt("bs.long_loans", lag), c.opt("bs.bonds_payable", lag), required=0)


def _lt_investments(c: Calc, lag: int = 0) -> V:
    return vsum(*(c.opt(code, lag) for code in (
        "bs.lt_equity_invest", "bs.other_equity_invest", "bs.other_noncurrent_fin_assets", "bs.debt_investments",
        "bs.other_debt_investments", "bs.available_for_sale", "bs.held_to_maturity")), required=0)


def _gm(c: Calc, lag: int = 0) -> V:
    rev = c.f("is.revenue", lag)
    return (rev - c.f("is.cogs", lag)) / rev


def _aq(c: Calc, lag: int = 0) -> V:
    ta = c.f("bs.total_assets", lag)
    return 1 - (c.f("bs.current_assets", lag) + c.opt_zero("bs.fixed_assets", lag) + _lt_investments(c, lag)) / ta


def _dep_rate(c: Calc, lag: int = 0) -> V:
    dep = c.f("cf.depreciation", lag)
    return dep / (dep + c.f("bs.fixed_assets", lag))


# ------------------------------------------------------------------ Beneish 8 个分项

@metric("m_dsri", "DSRI 应收周转指数", "times", "models", "(应收类_t ÷ 营收_t) ÷ (应收类_t-1 ÷ 营收_t-1)",
        reference=BENEISH_REF)
def m_dsri(c: Calc) -> V:
    cur = receivables(c) / c.f("is.revenue")
    prev = receivables(c, 1) / c.f("is.revenue", 1)
    if cur.ok and prev.ok and cur.value < DSRI_MATERIALITY and prev.value < DSRI_MATERIALITY:  # type: ignore[operator]
        # 应收规模不重要时，比值的比值会被极小的分母放大（如票据在“应收票据/应收款项融资”间重分类），
        # 取中性值 1 并在证据中披露该处理
        neutral = V(1.0, cur.refs | prev.refs)
        neutral.notes = frozenset({f"两期应收类合计均低于营收的 {DSRI_MATERIALITY:.0%}，DSRI 取中性值 1"})
        return neutral
    return cur / prev


@metric("m_gmi", "GMI 毛利率指数", "times", "models", "毛利率_t-1 ÷ 毛利率_t", reference=BENEISH_REF)
def m_gmi(c: Calc) -> V:
    return _gm(c, 1) / _gm(c)


@metric("m_aqi", "AQI 资产质量指数", "times", "models",
        "[1 − (流动资产 + 固定资产 + 长期投资)/总资产]_t ÷ [同口径]_t-1", reference=BENEISH_REF)
def m_aqi(c: Calc) -> V:
    return _aq(c) / _aq(c, 1)


@metric("m_sgi", "SGI 营收增长指数", "times", "models", "营收_t ÷ 营收_t-1", reference=BENEISH_REF)
def m_sgi(c: Calc) -> V:
    return c.f("is.revenue") / c.f("is.revenue", 1)


@metric("m_depi", "DEPI 折旧率指数", "times", "models",
        "[折旧/(折旧+固定资产)]_t-1 ÷ [折旧/(折旧+固定资产)]_t", reference=BENEISH_REF)
def m_depi(c: Calc) -> V:
    return _dep_rate(c, 1) / _dep_rate(c)


@metric("m_sgai", "SGAI 期间费用指数", "times", "models", "(期间费用_t ÷ 营收_t) ÷ (期间费用_t-1 ÷ 营收_t-1)",
        reference=BENEISH_REF)
def m_sgai(c: Calc) -> V:
    return (sga(c) / c.f("is.revenue")) / (sga(c, 1) / c.f("is.revenue", 1))


@metric("m_lvgi", "LVGI 杠杆指数", "times", "models",
        "[(流动负债+长期负债)/总资产]_t ÷ [同口径]_t-1", reference=BENEISH_REF)
def m_lvgi(c: Calc) -> V:
    cur = (c.f("bs.current_liabilities") + _long_term_debt(c)) / c.f("bs.total_assets")
    prev = (c.f("bs.current_liabilities", 1) + _long_term_debt(c, 1)) / c.f("bs.total_assets", 1)
    return cur / prev


@metric("m_tata", "TATA 总应计/总资产", "ratio", "models", "(净利润 − 经营活动现金流量净额) ÷ 总资产",
        reference=BENEISH_REF)
def m_tata(c: Calc) -> V:
    return (c.f("is.net_profit") - c.f("cf.ocf")) / c.f("bs.total_assets")


@metric("beneish_m", "Beneish M-Score", "score", "models",
        "−4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI + 0.115·DEPI − 0.172·SGAI + 4.679·TATA − 0.327·LVGI",
        description=f"高于 {BENEISH_THRESHOLD} 提示存在盈余操纵特征（Beneish 1999 判别阈值）。", reference=BENEISH_REF,
        higher_is_better=False)
def beneish_m(c: Calc) -> V:
    return (-4.84 + 0.920 * c.m("m_dsri") + 0.528 * c.m("m_gmi") + 0.404 * c.m("m_aqi") + 0.892 * c.m("m_sgi")
            + 0.115 * c.m("m_depi") - 0.172 * c.m("m_sgai") + 4.679 * c.m("m_tata") - 0.327 * c.m("m_lvgi"))


# ------------------------------------------------------------------ Altman Z''（账面版）

@metric("z_x1", "Z''·X1 营运资本/总资产", "ratio", "models", "(流动资产 − 流动负债) ÷ 总资产", reference=ALTMAN_REF)
def z_x1(c: Calc) -> V:
    return (c.f("bs.current_assets") - c.f("bs.current_liabilities")) / c.f("bs.total_assets")


@metric("z_x2", "Z''·X2 留存收益/总资产", "ratio", "models", "(盈余公积 + 未分配利润) ÷ 总资产", reference=ALTMAN_REF)
def z_x2(c: Calc) -> V:
    return (c.opt_zero("bs.surplus_reserve") + c.f("bs.retained_earnings")) / c.f("bs.total_assets")


@metric("z_x3", "Z''·X3 息税前利润/总资产", "ratio", "models", "(利润总额 + 利息费用) ÷ 总资产", reference=ALTMAN_REF)
def z_x3(c: Calc) -> V:
    return (c.f("is.total_profit") + c.opt_zero("is.fe_interest_exp")) / c.f("bs.total_assets")


@metric("z_x4", "Z''·X4 账面权益/总负债", "times", "models", "所有者权益合计 ÷ 负债合计", reference=ALTMAN_REF)
def z_x4(c: Calc) -> V:
    return c.f("bs.total_equity") / c.f("bs.total_liabilities")


@metric("altman_z2", "Altman Z''-Score", "score", "models", "6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4",
        description=f"> {ALTMAN_Z2_SAFE} 安全区；{ALTMAN_Z2_DISTRESS}–{ALTMAN_Z2_SAFE} 灰色区；"
                    f"< {ALTMAN_Z2_DISTRESS} 困境区。", reference=ALTMAN_REF, higher_is_better=True)
def altman_z2(c: Calc) -> V:
    return 6.56 * c.m("z_x1") + 3.26 * c.m("z_x2") + 6.72 * c.m("z_x3") + 1.05 * c.m("z_x4")


# ------------------------------------------------------------------ Piotroski F-Score（9 个二元信号）

def _flag(cond: V) -> V:
    """把一个“>0 即满足”的差值转为 1/0。"""
    if not cond.ok:
        return cond
    return V(1.0 if cond.value > 0 else 0.0, cond.refs)  # type: ignore[operator]


def _roa_begin(c: Calc, lag: int = 0) -> V:
    return c.f("is.net_profit", lag) / c.f("bs.total_assets", lag + 1)


@metric("f_roa", "F1 ROA>0", "count", "models", "净利润 ÷ 期初总资产 > 0", reference=PIOTROSKI_REF)
def f_roa(c: Calc) -> V:
    return _flag(_roa_begin(c))


@metric("f_cfo", "F2 经营现金流>0", "count", "models", "经营活动现金流量净额 > 0", reference=PIOTROSKI_REF)
def f_cfo(c: Calc) -> V:
    return _flag(c.f("cf.ocf"))


@metric("f_droa", "F3 ROA 改善", "count", "models", "ROA_t − ROA_t-1 > 0", reference=PIOTROSKI_REF)
def f_droa(c: Calc) -> V:
    return _flag(_roa_begin(c) - _roa_begin(c, 1))


@metric("f_accrual", "F4 现金流优于利润", "count", "models", "经营现金流/期初总资产 − ROA > 0", reference=PIOTROSKI_REF)
def f_accrual(c: Calc) -> V:
    return _flag(c.f("cf.ocf") / c.f("bs.total_assets", 1) - _roa_begin(c))


@metric("f_lever", "F5 长期杠杆下降", "count", "models", "长期负债/平均总资产 同比下降", reference=PIOTROSKI_REF)
def f_lever(c: Calc) -> V:
    cur = _long_term_debt(c) / c.avg("bs.total_assets")
    prev_avg = (c.f("bs.total_assets", 1) + c.f("bs.total_assets", 2)) / 2
    prev = _long_term_debt(c, 1) / prev_avg
    return _flag(prev - cur)


@metric("f_liquid", "F6 流动比率提升", "count", "models", "流动比率_t − 流动比率_t-1 > 0", reference=PIOTROSKI_REF)
def f_liquid(c: Calc) -> V:
    return _flag(c.m("current_ratio") - c.m("current_ratio", 1))


@metric("f_eq_offer", "F7 未增发股本", "count", "models", "实收资本（股本）未增加",
        description="送转股也会使股本增加，可能造成误判，报告中会提示。", reference=PIOTROSKI_REF)
def f_eq_offer(c: Calc) -> V:
    diff = c.f("bs.share_capital", 1) - c.f("bs.share_capital")
    if not diff.ok:
        return diff
    return V(1.0 if diff.value >= 0 else 0.0, diff.refs)  # type: ignore[operator]


@metric("f_margin", "F8 毛利率提升", "count", "models", "毛利率_t − 毛利率_t-1 > 0", reference=PIOTROSKI_REF)
def f_margin(c: Calc) -> V:
    return _flag(c.m("gross_margin") - c.m("gross_margin", 1))


@metric("f_turn", "F9 资产周转率提升", "count", "models", "营收/期初总资产 同比提升", reference=PIOTROSKI_REF)
def f_turn(c: Calc) -> V:
    cur = c.f("is.revenue") / c.f("bs.total_assets", 1)
    prev = c.f("is.revenue", 1) / c.f("bs.total_assets", 2)
    return _flag(cur - prev)


F_COMPONENTS = ("f_roa", "f_cfo", "f_droa", "f_accrual", "f_lever", "f_liquid", "f_eq_offer", "f_margin", "f_turn")


@metric("piotroski_f", "Piotroski F-Score", "score", "models", "9 个二元信号之和（0–9）",
        description="≥7 基本面强健；≤3 基本面偏弱。", reference=PIOTROSKI_REF, higher_is_better=True)
def piotroski_f(c: Calc) -> V:
    total = V(0.0)
    for code in F_COMPONENTS:
        total = total + c.m(code)
    return total
