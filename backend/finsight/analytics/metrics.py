"""指标库（一般工商业企业）。每个指标给出口径公式与说明，所有计算在 engine.V 上进行以追踪来源。

口径说明（重要）
* 平均余额 = (期初 + 期末) / 2，期初取上一年年报期末数；
* 周转天数按 365 天计算（东方财富按 360 天，交叉核验时不比较天数类指标）；
* 有息负债 = 短期借款 + 一年内到期的非流动负债 + 应付短期债券 + 长期借款 + 应付债券 + 租赁负债；
  其中“一年内到期的非流动负债”可能含少量无息项目，属业内常用近似口径；
* 应收类合计 = 应收账款 + 应收票据 + 应收款项融资 + 合同资产，用于跨越 2019（新金融工具准则）
  与 2020（新收入准则）科目重分类的可比口径。
"""

from __future__ import annotations

from finsight.analytics.engine import Calc, V, metric, vsum

ALL = ("general", "bank", "insurance", "securities", "unknown")

# ------------------------------------------------------------------ 组合口径（内部辅助）


def receivables(c: Calc, lag: int = 0) -> V:
    return vsum(c.opt("bs.accounts_receivable", lag), c.opt("bs.notes_receivable", lag),
                c.opt("bs.receivables_financing", lag), c.opt("bs.contract_assets", lag), required=0) \
        if c._shift(lag) else V(None, missing=frozenset({f"应收类科目（{c.year(lag)}年）"}))


def interest_bearing_debt(c: Calc, lag: int = 0) -> V:
    if c._shift(lag) is None:
        return V(None, missing=frozenset({f"有息负债科目（{c.year(lag)}年）"}))
    return vsum(c.opt("bs.short_loans", lag), c.opt("bs.noncurrent_liab_due_1y", lag), c.opt("bs.short_bonds", lag),
                c.opt("bs.long_loans", lag), c.opt("bs.bonds_payable", lag), c.opt("bs.lease_liabilities", lag),
                required=0)


def short_term_debt(c: Calc, lag: int = 0) -> V:
    if c._shift(lag) is None:
        return V(None, missing=frozenset({f"短期有息负债科目（{c.year(lag)}年）"}))
    return vsum(c.opt("bs.short_loans", lag), c.opt("bs.noncurrent_liab_due_1y", lag), c.opt("bs.short_bonds", lag),
                required=0)


def sga(c: Calc, lag: int = 0) -> V:
    """销售 + 管理 + 研发费用：2018 年研发费用从管理费用拆出，合并后口径跨年可比。"""
    return c.f("is.selling_exp", lag) + c.f("is.admin_exp", lag) + vsum(c.opt("is.rd_exp", lag), required=0)


def growth(cur: V, prev: V) -> V:
    """同比增速；基期为负或为零时增速无经济含义 → 缺失。"""
    if prev.ok and prev.value <= 0:  # type: ignore[operator]
        return V(None, cur.refs | prev.refs, frozenset({"基期数值≤0，增速无意义"}))
    return cur / prev - 1


# ------------------------------------------------------------------ 盈利能力

@metric("gross_margin", "毛利率", "ratio", "profitability", "(营业收入 − 营业成本) ÷ 营业收入", higher_is_better=True)
def gross_margin(c: Calc) -> V:
    return (c.f("is.revenue") - c.f("is.cogs")) / c.f("is.revenue")


@metric("net_margin", "销售净利率", "ratio", "profitability", "净利润 ÷ 营业收入", higher_is_better=True)
def net_margin(c: Calc) -> V:
    return c.f("is.net_profit") / c.f("is.revenue")


@metric("roe", "净资产收益率（平均）", "ratio", "profitability", "归母净利润 ÷ 平均归母股东权益",
        description="使用期初期末平均归母权益；与公司公告的加权平均 ROE 口径不同，可能存在小幅差异。",
        higher_is_better=True)
def roe(c: Calc) -> V:
    return c.f("is.np_parent") / c.avg("bs.equity_parent")


@metric("roa", "总资产收益率（平均）", "ratio", "profitability", "净利润 ÷ 平均总资产", higher_is_better=True)
def roa(c: Calc) -> V:
    return c.f("is.net_profit") / c.avg("bs.total_assets")


@metric("sga_ratio", "期间费用率（销售+管理+研发）", "ratio", "profitability",
        "(销售费用 + 管理费用 + 研发费用) ÷ 营业收入", higher_is_better=False)
def sga_ratio(c: Calc) -> V:
    return sga(c) / c.f("is.revenue")


@metric("rd_ratio", "研发费用率", "ratio", "profitability", "研发费用 ÷ 营业收入")
def rd_ratio(c: Calc) -> V:
    return c.f("is.rd_exp") / c.f("is.revenue")


@metric("nonrecurring_ratio", "非经常性损益占比", "ratio", "earnings_quality",
        "(归母净利润 − 扣非归母净利润) ÷ |归母净利润|",
        description="衡量利润对政府补助、资产处置、投资收益等非经常性项目的依赖程度。", higher_is_better=False)
def nonrecurring_ratio(c: Calc) -> V:
    np_ = c.f("is.np_parent")
    return (np_ - c.f("is.np_deducted")) / abs(np_)


# ------------------------------------------------------------------ 盈利质量

@metric("ocf_to_np", "净现比", "times", "earnings_quality", "经营活动现金流量净额 ÷ 净利润",
        description="利润的现金含量。长期显著低于 1 说明利润主要停留在应收、存货等非现金资产上。",
        higher_is_better=True)
def ocf_to_np(c: Calc) -> V:
    np_ = c.f("is.net_profit")
    if np_.ok and np_.value <= 0:  # type: ignore[operator]
        return V(None, np_.refs, frozenset({"净利润≤0，净现比无意义"}))
    return c.f("cf.ocf") / np_


@metric("cash_to_revenue", "收现比", "times", "earnings_quality", "销售商品、提供劳务收到的现金 ÷ 营业收入",
        description="含增值税，正常经营的企业通常在 1 附近或略高；持续偏低提示收入回款不足。", higher_is_better=True)
def cash_to_revenue(c: Calc) -> V:
    return c.f("cf.sales_cash") / c.f("is.revenue")


@metric("accruals_ratio", "总应计比率", "ratio", "earnings_quality", "(净利润 − 经营活动现金流量净额) ÷ 平均总资产",
        description="Sloan (1996) 现金流量表口径的应计利润。应计越高，盈利的持续性越弱。",
        reference="Sloan, R. G. (1996). The Accounting Review, 71(3), 289–315.", higher_is_better=False)
def accruals_ratio(c: Calc) -> V:
    return (c.f("is.net_profit") - c.f("cf.ocf")) / c.avg("bs.total_assets")


@metric("fcf", "自由现金流（简化）", "CNY", "earnings_quality",
        "经营活动现金流量净额 − 购建固定资产、无形资产和其他长期资产支付的现金", higher_is_better=True)
def fcf(c: Calc) -> V:
    return c.f("cf.ocf") - c.f("cf.capex")


# ------------------------------------------------------------------ 成长性

@metric("revenue_growth", "营业收入增速", "ratio", "growth", "本期营业收入 ÷ 上期营业收入 − 1")
def revenue_growth(c: Calc) -> V:
    return growth(c.f("is.revenue"), c.f("is.revenue", 1))


@metric("np_parent_growth", "归母净利润增速", "ratio", "growth", "本期归母净利润 ÷ 上期归母净利润 − 1")
def np_parent_growth(c: Calc) -> V:
    return growth(c.f("is.np_parent"), c.f("is.np_parent", 1))


@metric("ocf_growth", "经营现金流增速", "ratio", "growth", "本期经营现金流净额 ÷ 上期 − 1")
def ocf_growth(c: Calc) -> V:
    return growth(c.f("cf.ocf"), c.f("cf.ocf", 1))


@metric("receivables_growth", "应收类合计增速", "ratio", "growth", "本期应收类合计 ÷ 上期 − 1",
        description="应收类合计 = 应收账款 + 应收票据 + 应收款项融资 + 合同资产。")
def receivables_growth(c: Calc) -> V:
    return growth(receivables(c), receivables(c, 1))


@metric("inventory_growth", "存货增速", "ratio", "growth", "本期存货 ÷ 上期存货 − 1")
def inventory_growth(c: Calc) -> V:
    return growth(c.f("bs.inventory"), c.f("bs.inventory", 1))


@metric("cogs_growth", "营业成本增速", "ratio", "growth", "本期营业成本 ÷ 上期营业成本 − 1")
def cogs_growth(c: Calc) -> V:
    return growth(c.f("is.cogs"), c.f("is.cogs", 1))


@metric("assets_growth", "总资产增速", "ratio", "growth", "本期总资产 ÷ 上期总资产 − 1")
def assets_growth(c: Calc) -> V:
    return growth(c.f("bs.total_assets"), c.f("bs.total_assets", 1))


@metric("receivables_revenue_gap", "应收增速 − 营收增速", "ratio", "earnings_quality",
        "应收类合计增速 − 营业收入增速",
        description="应收显著快于收入增长，可能意味着放宽信用政策或收入确认激进。", higher_is_better=False)
def receivables_revenue_gap(c: Calc) -> V:
    return c.m("receivables_growth") - c.m("revenue_growth")


@metric("inventory_cogs_gap", "存货增速 − 成本增速", "ratio", "asset_quality", "存货增速 − 营业成本增速",
        description="存货显著快于成本增长，提示滞销、跌价或存货虚增风险。", higher_is_better=False)
def inventory_cogs_gap(c: Calc) -> V:
    return c.m("inventory_growth") - c.m("cogs_growth")


# ------------------------------------------------------------------ 偿债能力

@metric("debt_ratio", "资产负债率", "ratio", "solvency", "负债合计 ÷ 资产总计", higher_is_better=False)
def debt_ratio(c: Calc) -> V:
    return c.f("bs.total_liabilities") / c.f("bs.total_assets")


@metric("current_ratio", "流动比率", "times", "solvency", "流动资产合计 ÷ 流动负债合计", higher_is_better=True)
def current_ratio(c: Calc) -> V:
    return c.f("bs.current_assets") / c.f("bs.current_liabilities")


@metric("quick_ratio", "速动比率", "times", "solvency", "(流动资产合计 − 存货) ÷ 流动负债合计", higher_is_better=True)
def quick_ratio(c: Calc) -> V:
    return (c.f("bs.current_assets") - c.opt_zero("bs.inventory")) / c.f("bs.current_liabilities")


@metric("ibd", "有息负债", "CNY", "solvency",
        "短期借款 + 一年内到期的非流动负债 + 应付短期债券 + 长期借款 + 应付债券 + 租赁负债")
def ibd(c: Calc) -> V:
    return interest_bearing_debt(c)


@metric("ibd_ratio", "有息负债率", "ratio", "solvency", "有息负债 ÷ 资产总计", higher_is_better=False)
def ibd_ratio(c: Calc) -> V:
    return c.m("ibd") / c.f("bs.total_assets")


@metric("cash_to_st_debt", "现金短债比", "times", "solvency",
        "(货币资金 + 交易性金融资产) ÷ (短期借款 + 一年内到期的非流动负债 + 应付短期债券)",
        description="短期有息负债为 0 时该指标无意义（记为缺失并说明）。", higher_is_better=True)
def cash_to_st_debt(c: Calc) -> V:
    st = short_term_debt(c)
    if st.ok and st.value == 0:
        return V(None, st.refs, frozenset({"无短期有息负债"}))
    return vsum(c.f("bs.cash"), c.opt("bs.trading_fin_assets")) / st


@metric("interest_coverage", "利息保障倍数", "times", "solvency", "(利润总额 + 利息费用) ÷ 利息费用",
        description="利息费用取财务费用附注中的“利息费用”；利息费用为 0 时记为缺失。", higher_is_better=True)
def interest_coverage(c: Calc) -> V:
    interest = c.f("is.fe_interest_exp")
    if interest.ok and interest.value <= 0:  # type: ignore[operator]
        return V(None, interest.refs, frozenset({"利息费用为 0"}))
    return (c.f("is.total_profit") + interest) / interest


@metric("cash_to_assets", "货币资金占总资产比", "ratio", "solvency", "货币资金 ÷ 资产总计")
def cash_to_assets(c: Calc) -> V:
    return c.f("bs.cash") / c.f("bs.total_assets")


@metric("implied_cash_yield", "货币资金隐含收益率", "ratio", "asset_quality",
        "财务费用中的利息收入 ÷ 平均货币资金",
        description="“存贷双高”识别的关键辅助指标：账面资金充裕却利息收入极低，"
                    "可能意味着资金受限、被占用或不真实。存在财务公司等金融子公司时需结合拆出资金等科目判断。",
        higher_is_better=True)
def implied_cash_yield(c: Calc) -> V:
    return c.f("is.fe_interest_income") / c.avg("bs.cash")


# ------------------------------------------------------------------ 资产质量

@metric("goodwill_to_equity", "商誉占归母净资产比", "ratio", "asset_quality", "商誉 ÷ 归母股东权益",
        higher_is_better=False)
def goodwill_to_equity(c: Calc) -> V:
    return c.opt_zero("bs.goodwill") / c.f("bs.equity_parent")


@metric("other_rec_to_assets", "其他应收款占总资产比", "ratio", "asset_quality", "其他应收款（合计） ÷ 资产总计",
        description="其他应收款异常偏高可能反映资金被关联方占用。", higher_is_better=False)
def other_rec_to_assets(c: Calc) -> V:
    return c.opt_zero("bs.other_receivables") / c.f("bs.total_assets")


@metric("prepay_to_assets", "预付款项占总资产比", "ratio", "asset_quality", "预付款项 ÷ 资产总计",
        higher_is_better=False)
def prepay_to_assets(c: Calc) -> V:
    return c.opt_zero("bs.prepayments") / c.f("bs.total_assets")


@metric("cip_to_fixed", "在建工程 ÷ 固定资产", "ratio", "asset_quality", "在建工程 ÷ 固定资产",
        description="在建工程长期高企且迟迟不转固，可能存在资本化操纵或项目停滞。", higher_is_better=False)
def cip_to_fixed(c: Calc) -> V:
    return c.opt_zero("bs.cip") / c.f("bs.fixed_assets")


@metric("impairment_to_revenue", "减值损失占营收比", "ratio", "asset_quality",
        "(资产减值损失 + 信用减值损失) ÷ 营业收入（损失为正）", higher_is_better=False)
def impairment_to_revenue(c: Calc) -> V:
    return vsum(c.opt("is.asset_impairment"), c.opt("is.credit_impairment"), required=0) / c.f("is.revenue")


# ------------------------------------------------------------------ 营运效率

@metric("dso", "应收类周转天数", "days", "efficiency", "365 × 平均应收类合计 ÷ 营业收入", higher_is_better=False)
def dso(c: Calc) -> V:
    return 365 * (receivables(c) + receivables(c, 1)) / 2 / c.f("is.revenue")


@metric("dio", "存货周转天数", "days", "efficiency", "365 × 平均存货 ÷ 营业成本", higher_is_better=False)
def dio(c: Calc) -> V:
    return 365 * c.avg("bs.inventory") / c.f("is.cogs")


@metric("dpo", "应付账款周转天数", "days", "efficiency", "365 × 平均应付账款 ÷ 营业成本")
def dpo(c: Calc) -> V:
    return 365 * c.avg("bs.accounts_payable") / c.f("is.cogs")


@metric("ccc", "现金转换周期", "days", "efficiency", "应收类周转天数 + 存货周转天数 − 应付账款周转天数",
        higher_is_better=False)
def ccc(c: Calc) -> V:
    return c.m("dso") + c.m("dio") - c.m("dpo")


@metric("asset_turnover", "总资产周转率", "times", "efficiency", "营业收入 ÷ 平均总资产", higher_is_better=True)
def asset_turnover(c: Calc) -> V:
    return c.f("is.revenue") / c.avg("bs.total_assets")
