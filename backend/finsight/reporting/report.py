"""可验证研究简报：证据上下文 → 大模型撰写 → 核验 → 必要时修订一次 → 定稿（或模板降级）。

大模型看到的只有“证据清单”（带短编号 E1..En 的数字与公式）和风险信号，
它的任务是组织与解读，而不是计算；任何数字都必须引用编号，由 verifier 逐一核对。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from finsight.analytics.diagnosis import DiagnosisResult
from finsight.analytics.rules import DIMENSION_LABELS
from finsight.domain import Evidence, fmt_value
from finsight.llm.gateway import LLMClient, LLMError, UsageMeter
from finsight.reporting.verifier import VerificationResult, annotate, verify

SEV_LABEL = {"high": "高", "medium": "中", "low": "低", "info": "提示"}
CORE_FACTS = ("is.revenue", "is.np_parent", "is.np_deducted", "cf.ocf", "bs.total_assets", "bs.total_liabilities",
              "bs.cash")


class ReportContext(BaseModel):
    refs: dict[str, Evidence]  # E# → 证据
    id_to_ref: dict[str, str]  # 证据 ID → E#
    evidence_block: str
    signals_block: str
    header: str


class Report(BaseModel):
    code: str
    name: str
    mode: Literal["llm", "template"]
    model: str | None = None
    markdown: str
    annotated_markdown: str
    refs: dict[str, str]  # E# → 证据 ID（前端据此渲染引用标签与溯源）
    verification: VerificationResult
    attempts: int = 1
    revised: bool = False
    usage: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    generated_at: str
    elapsed_ms: int = 0


# ------------------------------------------------------------------ 上下文

def build_context(result: DiagnosisResult, years: int = 3) -> ReportContext:
    ev = result.evidence
    order: list[str] = []

    def push(eid: str | None) -> None:
        if eid and eid in ev and eid not in order and ev[eid].value is not None and ev[eid].status == "ok":
            order.append(eid)

    periods = [p.end for p in result.periods[:years]]
    for p in periods[:2]:
        for item in CORE_FACTS:
            push(f"f:{item}@{p}")
    for row in result.metrics:
        for p in periods:
            push(row.values.get(p))
    if periods:
        for model in result.models.values():
            push(model["values"].get(periods[0]))
            for comp in model["components"]:
                push(comp["values"].get(periods[0]))
    for s in result.signals:
        for eid in s.evidence + re.findall(r"\[\[([^\]]+)\]\]", s.summary):
            push(eid)
    # 数据核验汇总（文本证据，供报告引用“通过 N 项核验”）
    cs = result.checks_summary
    summary_ev = Evidence(id="c:summary", kind="check", label="数据核验汇总", unit="text",
                          value=f"通过 {cs['pass']} 项，未通过 {cs['fail']} 项，跳过 {cs['skip']} 项")
    refs: dict[str, Evidence] = {}
    id_to_ref: dict[str, str] = {}
    for i, eid in enumerate(order + ["c:summary"], 1):
        item = ev.get(eid, summary_ev) if eid != "c:summary" else summary_ev
        refs[f"E{i}"] = item
        id_to_ref[eid] = f"E{i}"

    lines = []
    for ref, e in refs.items():
        year = f"{e.period[:4]}年 " if e.period and re.match(r"\d{4}", e.period) else ""
        formula = f"（公式：{e.formula}）" if e.formula else ""
        lines.append(f"[{ref}] {year}{e.label} = {fmt_value(e.value, e.unit)}{formula}")

    def cite(text: str) -> str:
        return re.sub(r"\[\[([^\]]+)\]\]", lambda m: f"[{id_to_ref[m.group(1)]}]" if m.group(1) in id_to_ref else "",
                      text)

    sig_lines = []
    for s in result.signals:
        kind = "亮点" if s.polarity == "strength" else f"{SEV_LABEL[s.severity]}风险"
        sig_lines.append(f"- [{kind}｜{DIMENSION_LABELS.get(s.dimension, s.dimension)}] {s.title}：{cite(s.summary)}"
                         f"（依据：{s.rationale or '—'}）")
    p = result.profile
    latest = result.periods[0].end[:4] if result.periods else "—"
    header = (f"公司：{p.name}（{p.code}.{p.exchange}），行业：{p.industry_em or '—'}，最新年报期：{latest}年；"
              f"系统综合风险评级：{result.overall.level}（{result.overall.headline}）。评级规则：{result.overall.rule}")
    return ReportContext(refs=refs, id_to_ref=id_to_ref, evidence_block="\n".join(lines),
                         signals_block="\n".join(sig_lines) or "（无）", header=header)


# ------------------------------------------------------------------ 模板报告（无需大模型）

def template_report(result: DiagnosisResult, ctx: ReportContext) -> str:
    ref = ctx.id_to_ref
    ev = result.evidence

    def c(eid: str | None) -> str:
        if not eid or eid not in ref:
            return "—"
        return f"{fmt_value(ev[eid].value, ev[eid].unit)}[{ref[eid]}]"

    def cite(text: str) -> str:
        return re.sub(r"\[\[([^\]]+)\]\]", lambda m: f"[{ref[m.group(1)]}]" if m.group(1) in ref else "", text)

    p = result.profile
    latest = result.periods[0].end if result.periods else None
    risks = [s for s in result.signals if s.polarity == "risk"]
    strengths = [s for s in result.signals if s.polarity == "strength"]
    out = ["## 核心结论",
           f"{p.name}最新年报期（{latest[:4] if latest else '—'}年）综合风险评级为“{result.overall.level}”。"
           f"{result.overall.headline}数据核验：{ctx.refs[ref['c:summary']].value}[{ref['c:summary']}]。"]
    if latest:
        out.append(f"关键数据：营业收入 {c(f'f:is.revenue@{latest}')}，归母净利润 {c(f'f:is.np_parent@{latest}')}，"
                   f"经营活动现金流量净额 {c(f'f:cf.ocf@{latest}')}，资产负债率 {c(f'm:debt_ratio@{latest}')}。")
    out.append("\n## 主要风险")
    out += [f"- **[{SEV_LABEL[s.severity]}] {s.title}**：{cite(s.summary)}" for s in risks] or ["- 未发现规则库定义的风险信号。"]
    out.append("\n## 积极因素")
    out += [f"- **{s.title}**：{cite(s.summary)}" for s in strengths] or ["- 规则库未识别到显著亮点。"]
    out.append("\n## 建议进一步核实的事项")
    todo = [f"- 结合年报附注核实“{s.title}”的成因与持续性。" for s in risks[:4]]
    out += todo or ["- 跟踪下一期定期报告，确认各项指标趋势是否延续。"]
    out.append("\n## 数据与方法说明")
    out.append("本简报由 FinSight 确定性引擎生成：数据取自公开披露渠道的原始快照，指标与模型按公开公式计算，"
               "每个数字均可通过引用编号溯源。模型结论为排查线索，不构成投资建议。")
    return "\n".join(out)


# ------------------------------------------------------------------ 大模型报告

SYSTEM_PROMPT = """你是一名严谨的证券研究分析师，为专业投研人员撰写上市公司财务风险研究简报。你只能使用用户提供的【证据清单】和【风险信号】中的信息。

硬性规则：
1. 报告中出现的每一个数字（金额、比例、倍数、天数、分值）都必须直接取自证据清单，并在数字后紧跟其编号，例如“净现比为 0.72 倍[E12]”。数字的写法与证据清单保持一致。
2. 禁止自行计算、推算或估计任何新数字（包括差值、增速、平均值、合计）。证据清单里没有的数字一律不写，改用定性描述。
3. 年份写作“2025年”，不需要引用。不要写证据清单以外的公司事实、行业数据、新闻或股价。
4. 区分事实与判断：判断要给出依据（引用证据）；预警模型的结论只能表述为“提示/线索”，不能写成定论。
5. 不提供任何买入、卖出、增持、减持、持有或目标价等投资建议。

输出 Markdown，结构固定为五节：
## 核心结论（不超过 3 句）
## 主要风险（按严重程度排序，每条 1–3 句）
## 积极因素
## 建议进一步核实的事项（给研究员的待办，3–5 条，说明要查阅的资料）
## 数据与方法说明（1–2 句）"""


def _user_prompt(ctx: ReportContext) -> str:
    return (f"{ctx.header}\n\n【风险信号】（规则引擎输出，括号内为依据）\n{ctx.signals_block}\n\n"
            f"【证据清单】\n{ctx.evidence_block}\n\n请按要求撰写研究简报。")


def _revision_prompt(v: VerificationResult) -> str:
    lines = []
    for i, c in enumerate(v.problems[:15], 1):
        where = c.sentence[:60]
        lines.append(f"{i}. “{c.text}”（所在句：{where}）：{c.message}")
    for f in v.compliance_flags:
        lines.append(f"- 合规：{f}，请删除。")
    return ("核验器发现以下问题。请输出修改后的完整报告：数字要么改为证据清单中的数值并引用编号，要么删除；"
            "其余内容保持不变，并继续遵守全部规则。\n" + "\n".join(lines))


def generate_report(result: DiagnosisResult, *, llm: LLMClient | None = None, mode: str = "auto",
                    max_revisions: int = 1) -> Report:
    started = time.monotonic()
    ctx = build_context(result)
    refs_public = {r: e.id for r, e in ctx.refs.items()}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    warnings: list[str] = []
    use_llm = mode == "llm" or (mode == "auto" and llm is not None)
    if use_llm and llm is None:
        warnings.append("未配置大模型，已降级为模板报告")
        use_llm = False

    if use_llm:
        meter = UsageMeter()
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": _user_prompt(ctx)}]
        try:
            resp = llm.chat(messages)  # type: ignore[union-attr]
            meter.add(resp)
            text = resp.content.strip()
            if not text:
                raise LLMError("模型返回了空报告")
            if resp.truncated:
                warnings.append("模型输出达到长度上限，报告可能不完整")
            v = verify(text, ctx.refs)
            attempts, revised = 1, False
            while not v.passed and attempts <= max_revisions:
                # 推理模型要求回传完整的 assistant 消息（含 reasoning_content）
                messages += [resp.as_message(), {"role": "user", "content": _revision_prompt(v)}]
                resp = llm.chat(messages)  # type: ignore[union-attr]
                meter.add(resp)
                attempts += 1
                new_text = resp.content.strip()
                new_v = verify(new_text, ctx.refs)
                # 只有修订确实减少了问题、且没有变成空文本才采用（防止越改越差）
                if new_text and (len(new_v.problems) + len(new_v.compliance_flags)
                                 <= len(v.problems) + len(v.compliance_flags)):
                    text, v, revised = new_text, new_v, True
            if v.problems:
                warnings.append(f"仍有 {len(v.problems)} 个数字未通过核验，已在文中标注")
            return Report(code=result.profile.code, name=result.profile.name, mode="llm",
                          model=getattr(llm, "model", None), markdown=text, annotated_markdown=annotate(text, v),
                          refs=refs_public, verification=v, attempts=attempts, revised=revised,
                          usage=meter.as_dict(), warnings=warnings, generated_at=now,
                          elapsed_ms=int((time.monotonic() - started) * 1000))
        except LLMError as exc:
            warnings.append(f"大模型调用失败，已降级为模板报告：{exc}")

    text = template_report(result, ctx)
    v = verify(text, ctx.refs)
    return Report(code=result.profile.code, name=result.profile.name, mode="template", markdown=text,
                  annotated_markdown=annotate(text, v), refs=refs_public, verification=v, warnings=warnings,
                  generated_at=now, elapsed_ms=int((time.monotonic() - started) * 1000))
