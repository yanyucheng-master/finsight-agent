"""标准化财务科目表（一般工商业企业）及其与东方财富 F10 字段的映射。

映射原则
* 一个标准科目可以有多个候选原始字段，按顺序取第一个非空值（兼容新旧会计准则下的字段变化）；
* sign = -1 表示原始字段与标准口径符号相反（如 2019 年后利润表“资产减值损失”以负数列示）；
* 合计类科目直接取报表原值而不是自行加总 —— 自行加总只用于完整性核验（见 analytics/checks.py）。

字段名来自对真实接口响应的逐一核对（data/snapshots 中 600519 等样本），不是猜测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Statement = Literal["bs", "is", "cf"]


@dataclass(frozen=True)
class Item:
    code: str
    label: str
    statement: Statement
    fields: tuple[tuple[str, int], ...]  # (原始字段, 符号)
    group: str = ""
    note: str = ""

    @property
    def kind(self) -> str:
        """bs = 时点数（存量），is/cf = 期间数（流量）。"""
        return "stock" if self.statement == "bs" else "flow"


def _i(code: str, label: str, *fields: str | tuple[str, int], group: str = "", note: str = "") -> Item:
    norm = tuple((f, 1) if isinstance(f, str) else f for f in fields)
    statement: Statement = code.split(".", 1)[0]  # type: ignore[assignment]
    return Item(code=code, label=label, statement=statement, fields=norm, group=group, note=note)


ITEMS: tuple[Item, ...] = (
    # ---------------- 资产负债表：流动资产 ----------------
    _i("bs.cash", "货币资金", "MONETARYFUNDS", group="流动资产"),
    _i("bs.settle_reserve", "结算备付金", "SETTLE_EXCESS_RESERVE", group="流动资产"),
    _i("bs.lend_fund", "拆出资金", "LEND_FUND", group="流动资产",
       note="通常来自集团财务公司等金融类子公司，属于类现金资产"),
    _i("bs.trading_fin_assets", "交易性金融资产", "TRADE_FINASSET_NOTFVTPL", "TRADE_FINASSET", "FVTPL_FINASSET",
       group="流动资产"),
    _i("bs.derivative_fin_assets", "衍生金融资产", "DERIVE_FINASSET", group="流动资产"),
    _i("bs.buy_resale", "买入返售金融资产", "BUY_RESALE_FINASSET", group="流动资产"),
    _i("bs.notes_receivable", "应收票据", "NOTE_RECE", group="流动资产"),
    _i("bs.accounts_receivable", "应收账款", "ACCOUNTS_RECE", group="流动资产"),
    _i("bs.receivables_financing", "应收款项融资", "FINANCE_RECE", group="流动资产"),
    _i("bs.prepayments", "预付款项", "PREPAYMENT", group="流动资产"),
    _i("bs.other_receivables", "其他应收款（合计）", "TOTAL_OTHER_RECE", "OTHER_RECE", group="流动资产"),
    _i("bs.inventory", "存货", "INVENTORY", group="流动资产"),
    _i("bs.contract_assets", "合同资产", "CONTRACT_ASSET", group="流动资产"),
    _i("bs.held_for_sale_assets", "持有待售资产", "HOLDSALE_ASSET", group="流动资产"),
    _i("bs.noncurrent_assets_due_1y", "一年内到期的非流动资产", "NONCURRENT_ASSET_1YEAR", group="流动资产"),
    _i("bs.other_current_assets", "其他流动资产", "OTHER_CURRENT_ASSET", group="流动资产"),
    _i("bs.current_assets", "流动资产合计", "TOTAL_CURRENT_ASSETS", group="流动资产"),
    # ---------------- 资产负债表：非流动资产 ----------------
    _i("bs.loans_advances", "发放贷款及垫款", "LOAN_ADVANCE", group="非流动资产"),
    _i("bs.debt_investments", "债权投资", "CREDITOR_INVEST", group="非流动资产"),
    _i("bs.other_debt_investments", "其他债权投资", "OTHER_CREDITOR_INVEST", group="非流动资产"),
    _i("bs.available_for_sale", "可供出售金融资产", "AVAILABLE_SALE_FINASSET", group="非流动资产",
       note="旧金融工具准则科目（2019 年前）"),
    _i("bs.held_to_maturity", "持有至到期投资", "HOLD_MATURITY_INVEST", group="非流动资产",
       note="旧金融工具准则科目（2019 年前）"),
    _i("bs.lt_receivables", "长期应收款", "LONG_RECE", group="非流动资产"),
    _i("bs.lt_equity_invest", "长期股权投资", "LONG_EQUITY_INVEST", group="非流动资产"),
    _i("bs.other_equity_invest", "其他权益工具投资", "OTHER_EQUITY_INVEST", group="非流动资产"),
    _i("bs.other_noncurrent_fin_assets", "其他非流动金融资产", "OTHER_NONCURRENT_FINASSET", group="非流动资产"),
    _i("bs.invest_property", "投资性房地产", "INVEST_REALESTATE", group="非流动资产"),
    _i("bs.fixed_assets", "固定资产", "FIXED_ASSET", group="非流动资产"),
    _i("bs.cip", "在建工程", "CIP", group="非流动资产"),
    _i("bs.productive_bio_assets", "生产性生物资产", "PRODUCTIVE_BIOLOGY_ASSET", group="非流动资产"),
    _i("bs.consumptive_bio_assets", "消耗性生物资产", "CONSUMPTIVE_BIOLOGICAL_ASSET", group="非流动资产"),
    _i("bs.right_of_use", "使用权资产", "USERIGHT_ASSET", group="非流动资产"),
    _i("bs.intangible_assets", "无形资产", "INTANGIBLE_ASSET", group="非流动资产"),
    _i("bs.dev_expenditure", "开发支出", "DEVELOP_EXPENSE", group="非流动资产"),
    _i("bs.goodwill", "商誉", "GOODWILL", group="非流动资产"),
    _i("bs.lt_prepaid_expense", "长期待摊费用", "LONG_PREPAID_EXPENSE", group="非流动资产"),
    _i("bs.deferred_tax_assets", "递延所得税资产", "DEFER_TAX_ASSET", group="非流动资产"),
    _i("bs.other_noncurrent_assets", "其他非流动资产", "OTHER_NONCURRENT_ASSET", group="非流动资产"),
    _i("bs.noncurrent_assets", "非流动资产合计", "TOTAL_NONCURRENT_ASSETS", group="非流动资产"),
    _i("bs.total_assets", "资产总计", "TOTAL_ASSETS", group="资产"),
    # ---------------- 资产负债表：负债 ----------------
    _i("bs.short_loans", "短期借款", "SHORT_LOAN", group="流动负债"),
    _i("bs.borrow_fund", "拆入资金", "BORROW_FUND", group="流动负债"),
    _i("bs.trading_fin_liab", "交易性金融负债", "TRADE_FINLIAB_NOTFVTPL", "TRADE_FINLIAB", "FVTPL_FINLIAB",
       group="流动负债"),
    _i("bs.notes_payable", "应付票据", "NOTE_PAYABLE", group="流动负债"),
    _i("bs.accounts_payable", "应付账款", "ACCOUNTS_PAYABLE", group="流动负债"),
    _i("bs.advance_receipts", "预收款项", "ADVANCE_RECEIVABLES", group="流动负债"),
    _i("bs.contract_liabilities", "合同负债", "CONTRACT_LIAB", group="流动负债"),
    _i("bs.deposits_taken", "吸收存款及同业存放", "ACCEPT_DEPOSIT_INTERBANK", group="流动负债",
       note="通常来自集团财务公司吸收的成员单位存款"),
    _i("bs.payroll_payable", "应付职工薪酬", "STAFF_SALARY_PAYABLE", group="流动负债"),
    _i("bs.taxes_payable", "应交税费", "TAX_PAYABLE", group="流动负债"),
    _i("bs.other_payables", "其他应付款（合计）", "TOTAL_OTHER_PAYABLE", "OTHER_PAYABLE", group="流动负债"),
    _i("bs.noncurrent_liab_due_1y", "一年内到期的非流动负债", "NONCURRENT_LIAB_1YEAR", group="流动负债"),
    _i("bs.short_bonds", "应付短期债券", "SHORT_BOND_PAYABLE", group="流动负债"),
    _i("bs.other_current_liab", "其他流动负债", "OTHER_CURRENT_LIAB", group="流动负债"),
    _i("bs.current_liabilities", "流动负债合计", "TOTAL_CURRENT_LIAB", group="流动负债"),
    _i("bs.long_loans", "长期借款", "LONG_LOAN", group="非流动负债"),
    _i("bs.bonds_payable", "应付债券", "BOND_PAYABLE", group="非流动负债"),
    _i("bs.lease_liabilities", "租赁负债", "LEASE_LIAB", group="非流动负债"),
    _i("bs.lt_payables", "长期应付款", "LONG_PAYABLE", group="非流动负债"),
    _i("bs.provisions", "预计负债", "PREDICT_LIAB", group="非流动负债"),
    _i("bs.deferred_income", "递延收益", "DEFER_INCOME", group="非流动负债"),
    _i("bs.deferred_tax_liab", "递延所得税负债", "DEFER_TAX_LIAB", group="非流动负债"),
    _i("bs.noncurrent_liabilities", "非流动负债合计", "TOTAL_NONCURRENT_LIAB", group="非流动负债"),
    _i("bs.total_liabilities", "负债合计", "TOTAL_LIABILITIES", group="负债"),
    # ---------------- 资产负债表：所有者权益 ----------------
    _i("bs.share_capital", "实收资本（或股本）", "SHARE_CAPITAL", group="所有者权益"),
    _i("bs.other_equity_tools", "其他权益工具", "OTHER_EQUITY_TOOL", group="所有者权益"),
    _i("bs.capital_reserve", "资本公积", "CAPITAL_RESERVE", group="所有者权益"),
    _i("bs.treasury_shares", "减：库存股", "TREASURY_SHARES", group="所有者权益"),
    _i("bs.oci", "其他综合收益", "OTHER_COMPRE_INCOME", group="所有者权益"),
    _i("bs.special_reserve", "专项储备", "SPECIAL_RESERVE", group="所有者权益"),
    _i("bs.surplus_reserve", "盈余公积", "SURPLUS_RESERVE", group="所有者权益"),
    _i("bs.general_risk_reserve", "一般风险准备", "GENERAL_RISK_RESERVE", group="所有者权益"),
    _i("bs.retained_earnings", "未分配利润", "UNASSIGN_RPOFIT", group="所有者权益"),
    _i("bs.equity_parent", "归属于母公司股东权益合计", "TOTAL_PARENT_EQUITY", group="所有者权益"),
    _i("bs.minority_equity", "少数股东权益", "MINORITY_EQUITY", group="所有者权益"),
    _i("bs.total_equity", "所有者权益合计", "TOTAL_EQUITY", group="所有者权益"),
    _i("bs.total_liab_equity", "负债和所有者权益总计", "TOTAL_LIAB_EQUITY", group="合计"),
    # 数据源平衡项（东方财富用于使合计相符的“未列示项目”差额）—— 仅用于核验
    _i("bs.asset_balance", "资产平衡项", "ASSET_BALANCE", group="平衡项"),
    _i("bs.liab_balance", "负债平衡项", "LIAB_BALANCE", group="平衡项"),
    _i("bs.equity_balance", "权益平衡项", "EQUITY_BALANCE", group="平衡项"),
    # ---------------- 利润表 ----------------
    _i("is.total_revenue", "营业总收入", "TOTAL_OPERATE_INCOME", group="收入"),
    _i("is.revenue", "营业收入", "OPERATE_INCOME", group="收入"),
    _i("is.interest_income_fin", "利息收入（金融业务）", "INTEREST_INCOME", group="收入",
       note="营业总收入中来自财务公司等金融业务的利息收入"),
    _i("is.total_cost", "营业总成本", "TOTAL_OPERATE_COST", group="成本费用"),
    _i("is.cogs", "营业成本", "OPERATE_COST", group="成本费用"),
    _i("is.interest_expense_fin", "利息支出（金融业务）", "INTEREST_EXPENSE", group="成本费用"),
    _i("is.taxes_surcharges", "税金及附加", "OPERATE_TAX_ADD", group="成本费用"),
    _i("is.selling_exp", "销售费用", "SALE_EXPENSE", group="成本费用"),
    _i("is.admin_exp", "管理费用", "MANAGE_EXPENSE", group="成本费用"),
    _i("is.rd_exp", "研发费用", "RESEARCH_EXPENSE", group="成本费用",
       note="2018 年起从管理费用中单独列示"),
    _i("is.finance_exp", "财务费用", "FINANCE_EXPENSE", group="成本费用"),
    _i("is.fe_interest_exp", "其中：利息费用", "FE_INTEREST_EXPENSE", group="成本费用"),
    _i("is.fe_interest_income", "其中：利息收入", "FE_INTEREST_INCOME", group="成本费用"),
    _i("is.other_income", "其他收益", "OTHER_INCOME", group="其他损益", note="主要为政府补助"),
    _i("is.invest_income", "投资收益", "INVEST_INCOME", group="其他损益"),
    _i("is.fv_change", "公允价值变动收益", "FAIRVALUE_CHANGE_INCOME", group="其他损益"),
    _i("is.credit_impairment", "信用减值损失（损失为正）", "CREDIT_IMPAIRMENT_LOSS", ("CREDIT_IMPAIRMENT_INCOME", -1),
       group="其他损益", note="2019 年后报表以负数列示损失，此处统一为损失取正"),
    _i("is.asset_impairment", "资产减值损失（损失为正）", "ASSET_IMPAIRMENT_LOSS", ("ASSET_IMPAIRMENT_INCOME", -1),
       group="其他损益", note="2019 年后报表以负数列示损失，此处统一为损失取正"),
    _i("is.asset_disposal", "资产处置收益", "ASSET_DISPOSAL_INCOME", group="其他损益"),
    _i("is.operating_profit", "营业利润", "OPERATE_PROFIT", group="利润"),
    _i("is.nonop_income", "营业外收入", "NONBUSINESS_INCOME", group="利润"),
    _i("is.nonop_expense", "营业外支出", "NONBUSINESS_EXPENSE", group="利润"),
    _i("is.total_profit", "利润总额", "TOTAL_PROFIT", group="利润"),
    _i("is.income_tax", "所得税费用", "INCOME_TAX", group="利润"),
    _i("is.net_profit", "净利润", "NETPROFIT", group="利润"),
    _i("is.np_continuing", "持续经营净利润", "CONTINUED_NETPROFIT", group="利润"),
    _i("is.np_parent", "归属于母公司股东的净利润", "PARENT_NETPROFIT", group="利润"),
    _i("is.minority_pl", "少数股东损益", "MINORITY_INTEREST", group="利润"),
    _i("is.np_deducted", "扣除非经常性损益后归母净利润", "DEDUCT_PARENT_NETPROFIT", group="利润"),
    _i("is.eps_basic", "基本每股收益", "BASIC_EPS", group="每股"),
    _i("is.eps_diluted", "稀释每股收益", "DILUTED_EPS", group="每股"),
    _i("is.operate_profit_balance", "营业利润平衡项", "OPERATE_PROFIT_BALANCE", group="平衡项"),
    _i("is.total_profit_balance", "利润总额平衡项", "TOTAL_PROFIT_BALANCE", group="平衡项"),
    # ---------------- 现金流量表 ----------------
    _i("cf.sales_cash", "销售商品、提供劳务收到的现金", "SALES_SERVICES", group="经营活动"),
    _i("cf.tax_refund", "收到的税费返还", "RECEIVE_TAX_REFUND", group="经营活动"),
    _i("cf.ocf_in", "经营活动现金流入小计", "TOTAL_OPERATE_INFLOW", group="经营活动"),
    _i("cf.purchase_cash", "购买商品、接受劳务支付的现金", "BUY_SERVICES", group="经营活动"),
    _i("cf.staff_cash", "支付给职工以及为职工支付的现金", "PAY_STAFF_CASH", group="经营活动"),
    _i("cf.taxes_paid", "支付的各项税费", "PAY_ALL_TAX", group="经营活动"),
    _i("cf.ocf_out", "经营活动现金流出小计", "TOTAL_OPERATE_OUTFLOW", group="经营活动"),
    _i("cf.ocf", "经营活动产生的现金流量净额", "NETCASH_OPERATE", group="经营活动"),
    _i("cf.invest_recovered", "收回投资收到的现金", "WITHDRAW_INVEST", group="投资活动"),
    _i("cf.disposal_lt_assets", "处置长期资产收回的现金净额", "DISPOSAL_LONG_ASSET", group="投资活动"),
    _i("cf.capex", "购建固定资产、无形资产和其他长期资产支付的现金", "CONSTRUCT_LONG_ASSET", group="投资活动"),
    _i("cf.invest_paid", "投资支付的现金", "INVEST_PAY_CASH", group="投资活动"),
    _i("cf.acquisition_cash", "取得子公司及其他营业单位支付的现金净额", "OBTAIN_SUBSIDIARY_OTHER", group="投资活动"),
    _i("cf.icf", "投资活动产生的现金流量净额", "NETCASH_INVEST", group="投资活动"),
    _i("cf.equity_raised", "吸收投资收到的现金", "ACCEPT_INVEST_CASH", group="筹资活动"),
    _i("cf.borrowing_received", "取得借款收到的现金", "RECEIVE_LOAN_CASH", group="筹资活动"),
    _i("cf.bonds_issued", "发行债券收到的现金", "ISSUE_BOND", group="筹资活动"),
    _i("cf.debt_repaid", "偿还债务支付的现金", "PAY_DEBT_CASH", group="筹资活动"),
    _i("cf.dividends_interest_paid", "分配股利、利润或偿付利息支付的现金", "ASSIGN_DIVIDEND_PORFIT", group="筹资活动"),
    _i("cf.fincf", "筹资活动产生的现金流量净额", "NETCASH_FINANCE", group="筹资活动"),
    _i("cf.fx_effect", "汇率变动对现金的影响", "RATE_CHANGE_EFFECT", group="现金汇总"),
    _i("cf.net_change", "现金及现金等价物净增加额", "CCE_ADD", group="现金汇总"),
    _i("cf.cash_begin", "期初现金及现金等价物余额", "BEGIN_CCE", group="现金汇总"),
    _i("cf.cash_end", "期末现金及现金等价物余额", "END_CCE", group="现金汇总"),
    # 补充资料（间接法）
    _i("cf.sup_net_profit", "补充资料：净利润", "NETPROFIT", group="补充资料"),
    _i("cf.sup_impairment", "补充资料：资产减值准备", "ASSET_IMPAIRMENT", group="补充资料"),
    _i("cf.depreciation", "固定资产折旧、油气资产折耗、生产性生物资产折旧", "FA_IR_DEPR", group="补充资料"),
    _i("cf.amort_intangible", "无形资产摊销", "IA_AMORTIZE", group="补充资料"),
    _i("cf.amort_lt_prepaid", "长期待摊费用摊销", "LPE_AMORTIZE", group="补充资料"),
    _i("cf.amort_rou", "使用权资产折旧", "USERIGHT_ASSET_AMORTIZE", group="补充资料"),
    _i("cf.sup_ocf", "补充资料：经营活动现金流量净额", "NETCASH_OPERATENOTE", group="补充资料"),
    _i("cf.operate_netcash_balance", "经营现金流平衡项", "OPERATE_NETCASH_BALANCE", group="平衡项"),
    _i("cf.cce_add_balance", "现金净增加额平衡项", "CCE_ADD_BALANCE", group="平衡项"),
)

ITEM_BY_CODE: dict[str, Item] = {it.code: it for it in ITEMS}

# 报表元数据字段（非数值）
META_FIELDS = ("REPORT_DATE", "REPORT_TYPE", "REPORT_DATE_NAME", "NOTICE_DATE", "UPDATE_DATE", "CURRENCY",
               "OPINION_TYPE", "SECURITY_NAME_ABBR", "ORG_TYPE")

REPORT_TYPE_TO_PERIOD = {"年报": "FY", "中报": "H1", "一季报": "Q1", "三季报": "Q3"}


def items_for(statement: Statement) -> list[Item]:
    return [it for it in ITEMS if it.statement == statement]


def label(code: str) -> str:
    item = ITEM_BY_CODE.get(code)
    return item.label if item else code
