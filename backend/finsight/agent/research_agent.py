"""研究问答智能体：在有限步数内调用确定性工具取证，回答必须引用证据并通过核验。

为什么是“单智能体 + 工具”而不是多智能体：问答任务的子步骤（取数、比较、检索公告）都是确定性工具，
不需要多个 LLM 互相协商；单智能体更可控、更便宜，失败面更小。可靠性由“工具只返回登记过的证据 +
答案必须引用 + 核验器复核”三道机制保证，而不是靠模型自觉。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from finsight.analytics.diagnosis import KEY_METRICS, MODEL_COMPONENTS, DiagnosisResult
from finsight.analytics.engine import REGISTRY
from finsight.analytics.rules import DIMENSION_LABELS
from finsight.data.schema import ITEMS
from finsight.domain import Evidence, fmt_value
from finsight.llm.gateway import LLMClient, LLMError, UsageMeter
from finsight.progress import Progress, emit
from finsight.reporting.report import _issues, data_asof
from finsight.reporting.verifier import VerificationResult, annotate, verify

SYSTEM_PROMPT = """你是 FinSight 研究助理，帮助专业投研人员分析一家 A 股上市公司的财务状况与风险。

工作方式：
1. 先用工具获取证据，再回答。工具返回的每个数字都带有编号（如 E7），这是唯一可信的数据来源。
2. 回答中的每一个数字都必须来自工具结果，并在数字后紧跟编号，例如“净现比为 0.72 倍[E7]”。
3. 不要自行计算新数字（差值、增速、均值等）。需要的话先查找对应指标（增速、差值类指标都已提供）；查不到就用定性描述。
4. 数据不足以回答时，直接说明缺少什么数据，不要猜测。不要使用工具结果以外的公司信息、行业数据或新闻。
5. 不提供买入、卖出、增持、减持、持有或目标价等投资建议。
6. 回答要简洁、结构清楚：先给结论，再列依据。
7. 口径一致：引用比率时，若同一句还写了它的分子或分母，必须用该指标公式里的科目（例如净现比的分母是净利润，不是归母净利润）。
8. 时效：数据只到最新年报期。用户问“目前/现在”时，要说明回答依据的是哪一期年报，不能代表此后的最新状况。"""


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties,
                                                            "required": required or []}}}


YEARS_PROP = {"type": "integer", "description": "最近几个年度，默认 3，最多 6", "minimum": 1, "maximum": 6}

TOOLS = [
    _tool("list_metrics", "列出可查询的财务指标（代码、名称、维度、公式）。不确定指标代码时先调用它。",
          {"dimension": {"type": "string", "description": "可选维度：" + "、".join(DIMENSION_LABELS)}}),
    _tool("get_metrics", "查询指标的多年取值（含公式）。",
          {"codes": {"type": "array", "items": {"type": "string"}, "description": "指标代码列表，如 ocf_to_np、roe"},
           "years": YEARS_PROP}, ["codes"]),
    _tool("get_statement_items", "按中文关键词查询报表原始科目（资产负债表、利润表、现金流量表）的多年数值。",
          {"keywords": {"type": "array", "items": {"type": "string"}, "description": "如 [\"货币资金\", \"短期借款\"]"},
           "years": YEARS_PROP}, ["keywords"]),
    _tool("get_risk_signals", "获取规则引擎识别的风险信号与亮点（含严重程度和依据）。",
          {"dimension": {"type": "string", "description": "可选维度代码，如 earnings_quality、solvency"}}),
    _tool("get_model_breakdown", "获取预警模型的总分与各分项。",
          {"model": {"type": "string", "enum": list(MODEL_COMPONENTS)}}, ["model"]),
    _tool("search_announcements", "按关键词检索近年公告标题（巨潮资讯）。",
          {"keywords": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer", "maximum": 20}},
          ["keywords"]),
    _tool("get_data_checks", "获取数据核验结果（会计恒等式、跨期连续性、与数据源指标交叉核验）。", {}),
    _tool("compare_peers", "与同业公司比较指标（仅限系统中有数据的公司）。",
          {"codes": {"type": "array", "items": {"type": "string"}, "description": "同业公司 6 位代码"},
           "metrics": {"type": "array", "items": {"type": "string"}, "description": "指标代码"}},
          ["codes", "metrics"]),
]


class AgentStep(BaseModel):
    tool: str
    arguments: dict[str, Any]
    summary: str
    refs: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None


class AgentAnswer(BaseModel):
    question: str
    answer: str
    annotated_answer: str
    refs: dict[str, str]  # E# → 证据 ID
    evidence: dict[str, Evidence]  # 证据 ID → 证据（含同业公司证据）
    verification: VerificationResult
    steps: list[AgentStep]
    attempts: int = 1
    model: str | None = None
    usage: dict[str, int | float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0
    # 是否得到了有效回答：模型调用失败或回答为空时为 False。注意空文本的核验结果 passed=True 没有意义，不能代表成功。
    ok: bool = True


class _Session:
    """一次问答会话的证据注册表：证据 ID ↔ 短编号。"""

    def __init__(self):
        self.ref_of: dict[str, str] = {}
        self.items: dict[str, Evidence] = {}

    def ref(self, ev: Evidence) -> str:
        if ev.id not in self.ref_of:
            self.ref_of[ev.id] = f"E{len(self.ref_of) + 1}"
            self.items[ev.id] = ev
        return self.ref_of[ev.id]

    def show(self, ev: Evidence) -> dict[str, Any]:
        return {"ref": self.ref(ev), "label": ev.label, "period": (ev.period or "")[:10],
                "value": fmt_value(ev.value, ev.unit)}

    def refs_map(self) -> dict[str, Evidence]:
        return {r: self.items[eid] for eid, r in self.ref_of.items()}


class ResearchAgent:
    def __init__(self, result: DiagnosisResult, llm: LLMClient, *, max_steps: int = 6,
                 peer_loader: Callable[[str], DiagnosisResult | None] | None = None):
        self.r = result
        self.llm = llm
        self.max_steps = max_steps
        self.peer_loader = peer_loader
        self.s = _Session()
        self.periods = [p.end for p in result.periods]

    # ------------------------------------------------------------------ 工具实现
    def _years(self, args: dict[str, Any]) -> list[str]:
        n = max(1, min(int(args.get("years") or 3), len(self.periods) or 1))
        return self.periods[:n]

    def t_list_metrics(self, args):
        dim = args.get("dimension")
        rows = [{"code": m.code, "label": m.label, "dimension": DIMENSION_LABELS.get(m.dimension, m.dimension),
                 "formula": m.formula} for m in REGISTRY.values()
                if (not dim or m.dimension == dim) and not m.code.startswith(("m_", "z_", "f_"))]
        return {"metrics": rows}, []

    def t_get_metrics(self, args):
        out, refs, unknown = [], [], []
        for code in args.get("codes") or []:
            if code not in REGISTRY:
                unknown.append(code)
                continue
            for p in self._years(args):
                ev = self.r.evidence.get(f"m:{code}@{p}")
                if ev is None:
                    continue
                if ev.value is None:
                    out.append({"label": ev.label, "period": p, "value": None, "note": ev.note or ev.status})
                    continue
                item = self.s.show(ev) | {"formula": ev.formula}
                out.append(item)
                refs.append(item["ref"])
        res: dict[str, Any] = {"values": out}
        if unknown:
            res["unknown_codes"] = unknown
            res["hint"] = "可先调用 list_metrics 查看可用指标代码"
        return res, refs

    def t_get_statement_items(self, args):
        kws = [k for k in (args.get("keywords") or []) if k]
        items = [it for it in ITEMS if any(k in it.label for k in kws) and it.group != "平衡项"]
        out, refs = [], []
        for it in items[:12]:
            for p in self._years(args):
                ev = self.r.evidence.get(f"f:{it.code}@{p}")
                if ev is not None and ev.value is not None:
                    item = self.s.show(ev)
                    out.append(item)
                    refs.append(item["ref"])
        return {"values": out, "matched_items": [it.label for it in items[:12]]}, refs

    def _cite(self, text: str) -> str:
        def rep(m):
            ev = self.r.evidence.get(m.group(1))
            return f"[{self.s.ref(ev)}]" if ev is not None else ""
        return re.sub(r"\[\[([^\]]+)\]\]", rep, text)

    def t_get_risk_signals(self, args):
        dim = args.get("dimension")
        out = []
        for sg in self.r.signals:
            if dim and sg.dimension != dim:
                continue
            out.append({"title": sg.title, "severity": sg.severity, "type": "亮点" if sg.polarity == "strength" else "风险",
                        "dimension": DIMENSION_LABELS.get(sg.dimension, sg.dimension),
                        "detail": self._cite(sg.summary), "rationale": sg.rationale})
        return {"overall_level": self.r.overall.level, "signals": out}, []

    def t_get_model_breakdown(self, args):
        name = args.get("model")
        model = self.r.models.get(name)
        if model is None:
            return {"error": f"未知模型 {name}"}, []
        p = self.periods[0]
        total = self.r.evidence.get(model["values"].get(p) or "")
        comps = []
        for c in model["components"]:
            ev = self.r.evidence.get(c["values"].get(p) or "")
            if ev is not None and ev.value is not None:
                comps.append(self.s.show(ev) | {"formula": c["formula"]})
        res = {"model": model["label"], "formula": model["formula"], "description": model["description"],
               "total": self.s.show(total) if total is not None and total.value is not None else None,
               "components": comps}
        return res, [c["ref"] for c in comps]

    def t_search_announcements(self, args):
        kws = [k for k in (args.get("keywords") or []) if k]
        limit = min(int(args.get("limit") or 10), 20)
        hits = [a for a in self.r.announcements if any(k in a["title"] for k in kws)][:limit]
        return {"announcements": [{"date": a["date"], "title": a["title"], "url": a.get("pdf_url")} for a in hits],
                "total_found": len(hits)}, []

    def t_get_data_checks(self, args):
        fails = [{"check": c.title, "period": c.period, "note": c.note} for c in self.r.checks if c.status == "fail"]
        cs = self.r.checks_summary
        ev = Evidence(id="c:summary", kind="check", label="数据核验汇总", unit="text",
                      value=f"通过 {cs['pass']} 项，未通过 {cs['fail']} 项，跳过 {cs['skip']} 项")
        return {"summary": self.s.show(ev), "failures": fails}, []

    def t_compare_peers(self, args):
        if self.peer_loader is None:
            return {"error": "同业数据不可用"}, []
        out, refs = [], []
        codes = [self.r.profile.code] + [c for c in (args.get("codes") or []) if c != self.r.profile.code][:5]
        for code in codes:
            res = self.r if code == self.r.profile.code else self.peer_loader(code)
            if res is None or not res.periods:
                out.append({"company": code, "error": "系统中没有该公司的数据"})
                continue
            p = res.periods[0].end
            row: dict[str, Any] = {"company": f"{res.profile.name}（{code}）", "period": p}
            for m in args.get("metrics") or []:
                ev = res.evidence.get(f"m:{m}@{p}")
                if ev is None or ev.value is None:
                    row[m] = None
                    continue
                scoped = ev.model_copy(update={"id": f"{code}|{ev.id}", "label": f"{res.profile.name}{ev.label}"})
                shown = self.s.show(scoped)
                row[m] = f"{shown['value']}（{shown['ref']}）"
                refs.append(shown["ref"])
            out.append(row)
        return {"comparison": out}, refs

    # ------------------------------------------------------------------ 主循环
    def _execute(self, name: str, args: dict[str, Any]) -> tuple[str, AgentStep]:
        t0 = time.monotonic()
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            payload, refs, err = {"error": f"未知工具 {name}"}, [], f"未知工具 {name}"
        else:
            try:
                payload, refs = fn(args)
                err = payload.get("error") if isinstance(payload, dict) else None
            except Exception as exc:  # 工具异常反馈给模型，而不是让整个会话崩溃
                payload, refs, err = {"error": f"{type(exc).__name__}: {exc}"}, [], str(exc)
        text = json.dumps(payload, ensure_ascii=False)
        summary = f"返回 {len(refs)} 条带编号的证据" if refs else (err or "完成")
        step = AgentStep(tool=name, arguments=args, summary=summary, refs=refs,
                         latency_ms=int((time.monotonic() - t0) * 1000), error=err)
        return text, step

    def ask(self, question: str, *, max_revisions: int = 1, progress: Progress | None = None) -> AgentAnswer:
        started = time.monotonic()
        meter = UsageMeter()
        p = self.r.profile
        context = (f"当前公司：{p.name}（{p.code}.{p.exchange}），行业：{p.industry_em or '—'}，"
                   f"可用年报期：{'、'.join(x[:4] for x in self.periods)}；{data_asof(self.r)}。")
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT},
                                          {"role": "user", "content": f"{context}\n\n问题：{question}"}]
        steps: list[AgentStep] = []
        warnings: list[str] = []
        answer = ""
        try:
            # 说明：后续请求始终带同一份 TOOLS（不需要工具时用 tool_choice="none"），
            # 既符合“历史中有工具调用”的接口约束，也让提示前缀保持不变、提高缓存命中。
            # 每条 assistant 消息都用 resp.as_message() 原样回传（推理模型要求带 reasoning_content）。
            for step_no in range(1, self.max_steps + 1):
                emit(progress, "thinking", f"第 {step_no} 步：模型思考中")
                resp = self.llm.chat(messages, tools=TOOLS)
                meter.add(resp)
                if not resp.tool_calls:
                    answer = resp.content.strip()
                    break
                messages.append(resp.as_message())
                for tc in resp.tool_calls:
                    text, step = self._execute(tc.name, tc.arguments)
                    steps.append(step)
                    emit(progress, "tool", f"调用工具 {tc.name}：{step.summary}")
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
            else:
                warnings.append(f"达到最大工具调用步数 {self.max_steps}，已要求模型直接作答")
                messages.append({"role": "user", "content": "请基于已获取的证据直接给出回答，不再调用工具。"})
                resp = self.llm.chat(messages, tools=TOOLS, tool_choice="none")
                meter.add(resp)
                answer = resp.content.strip()
            if not answer:
                warnings.append("模型没有给出文字回答")
            if resp.truncated:
                warnings.append("模型输出达到长度上限，回答可能不完整")
            refs = self.s.refs_map()
            v = verify(answer, refs)
            emit(progress, "verifying", f"核验 {v.total} 个数字，{len(v.problems)} 个有问题")
            attempts = 1
            while answer and not v.passed and attempts <= max_revisions:
                emit(progress, "revising", f"核验发现 {_issues(v)} 处问题，请模型修订（第 {attempts} 次）")
                problems = "\n".join(f"- “{c.text}”：{c.message}" for c in v.problems[:10])
                flags = "\n".join([f"- 合规：{f}" for f in v.compliance_flags] + [f"- {f}" for f in v.consistency_flags])
                messages += [resp.as_message(),
                             {"role": "user", "content": "核验器发现以下问题，请修改后重新作答（数字必须来自已获取的证据并标注编号，"
                                                         f"否则删除；不要再调用工具）：\n{problems}\n{flags}"}]
                try:
                    resp = self.llm.chat(messages, tools=TOOLS, tool_choice="none")
                except LLMError as exc:  # 修订失败时保留原回答（已标注问题），而不是整体报错
                    warnings.append(f"修订请求失败，保留原回答：{exc}")
                    break
                meter.add(resp)
                attempts += 1
                new_answer = resp.content.strip()
                new_v = verify(new_answer, refs)
                if new_answer and _issues(new_v) <= _issues(v):
                    answer, v = new_answer, new_v
            if v.problems:
                warnings.append(f"仍有 {len(v.problems)} 个数字未通过核验，已在回答中标注")
            warnings += v.consistency_flags
        except LLMError as exc:
            refs = self.s.refs_map()
            answer = f"大模型调用失败：{exc}"
            v = verify("", refs)
            attempts = 0
            warnings.append(str(exc))
        note = f"\n\n> 数据时效（系统生成）：{data_asof(self.r)}" + ("；同业数据取自各自公司的快照。" if any(
            st.tool == "compare_peers" for st in steps) else "。") if answer and attempts else ""
        return AgentAnswer(question=question, answer=answer + note, annotated_answer=annotate(answer, v) + note,
                           refs={r: e.id for r, e in refs.items()}, evidence={e.id: e for e in refs.values()},
                           verification=v, steps=steps, attempts=attempts, model=getattr(self.llm, "model", None),
                           usage=meter.as_dict(), warnings=warnings, elapsed_ms=int((time.monotonic() - started) * 1000),
                           ok=bool(answer.strip()) and attempts > 0)


__all__ = ["ResearchAgent", "AgentAnswer", "AgentStep", "TOOLS", "KEY_METRICS"]
