"""研究问答智能体测试（真实快照 + MockLLM，不调用外部 API）。"""

from __future__ import annotations

import pytest

from finsight.agent.research_agent import TOOLS, ResearchAgent
from finsight.domain import fmt_value
from finsight.llm.gateway import LLMResponse, MockLLM, ToolCall


@pytest.fixture(scope="module")
def env():
    from finsight.analytics.diagnosis import diagnose
    from finsight.config import load_settings
    from finsight.data.service import load_service

    svc = load_service(load_settings().snapshot_dir)
    if "600519" not in svc.available_symbols():
        pytest.skip("没有快照")
    cache = {}

    def loader(code):
        if code not in svc.available_symbols():
            return None
        cache.setdefault(code, diagnose(svc.company(code)))
        return cache[code]

    return loader("600519"), loader


def call(name, **args):
    return LLMResponse(content="", tool_calls=[ToolCall(id=f"c_{name}", name=name, arguments=args)])


def shown(diag, code):
    ev = diag.evidence[f"m:{code}@{diag.periods[0].end}"]
    return fmt_value(ev.value, ev.unit)


def test_tool_then_cited_answer_passes_verification(env):
    diag, _ = env
    llm = MockLLM(script=[call("get_metrics", codes=["ocf_to_np"], years=1),
                          f"结论：利润现金含量一般。净现比为 {shown(diag, 'ocf_to_np')}[E1]。"])
    ans = ResearchAgent(diag, llm).ask("茅台的利润现金含量如何？")
    assert ans.verification.passed and ans.verification.verified == 1
    assert ans.steps[0].tool == "get_metrics" and ans.steps[0].refs == ["E1"]
    assert ans.refs["E1"] == f"m:ocf_to_np@{diag.periods[0].end}"
    # 工具定义随请求发送；工具结果以 tool 消息回传
    assert llm.requests[0]["tools"] == TOOLS
    assert any(m.get("role") == "tool" for m in llm.requests[1]["messages"])


def test_hallucinated_number_triggers_revision(env):
    diag, _ = env
    good = f"净现比为 {shown(diag, 'ocf_to_np')}[E1]。"
    llm = MockLLM(script=[call("get_metrics", codes=["ocf_to_np"]), good + "行业平均约为 1.35 倍。", good])
    ans = ResearchAgent(diag, llm).ask("净现比和行业比怎么样？")
    assert ans.attempts == 2 and ans.verification.passed
    assert "1.35" in llm.requests[-1]["messages"][-1]["content"]


def test_unknown_tool_and_errors_are_fed_back(env):
    diag, _ = env
    llm = MockLLM(script=[call("no_such_tool"), "现有工具无法提供该信息。"])
    ans = ResearchAgent(diag, llm).ask("员工满意度如何？")
    assert ans.steps[0].error and ans.verification.total == 0


def test_compare_peers_scopes_evidence_by_company(env):
    diag, loader = env
    if loader("000858") is None:
        pytest.skip("缺少同业快照")
    llm = MockLLM(script=[call("compare_peers", codes=["000858"], metrics=["gross_margin"]), "已比较。"])
    ans = ResearchAgent(diag, llm, peer_loader=loader).ask("和五粮液比毛利率？")
    ids = set(ans.refs.values())
    assert any(i.startswith("600519|") for i in ids) and any(i.startswith("000858|") for i in ids)


def test_step_limit_forces_final_answer(env):
    diag, _ = env
    loop = [call("get_risk_signals") for _ in range(3)]
    llm = MockLLM(script=loop + ["基于已获取信息：整体风险较低。"])
    ans = ResearchAgent(diag, llm, max_steps=3).ask("有哪些风险？")
    assert any("最大工具调用步数" in w for w in ans.warnings) and ans.answer


def test_reasoning_is_passed_back_and_revision_disables_tools(env):
    """推理模型（DeepSeek 思考模式 / Kimi K3）要求在后续请求中回传 reasoning_content。"""
    diag, _ = env
    good = f"净现比为 {shown(diag, 'ocf_to_np')}[E1]。"
    first = call("get_metrics", codes=["ocf_to_np"])
    first.reasoning = "需要先查净现比"
    draft = LLMResponse(content=good + "行业平均约为 1.35 倍。", reasoning="组织答案")
    llm = MockLLM(script=[first, draft, good])
    ans = ResearchAgent(diag, llm).ask("净现比怎么样？")
    tool_turn = next(m for m in llm.requests[1]["messages"] if m["role"] == "assistant")
    assert tool_turn["reasoning_content"] == "需要先查净现比" and tool_turn["tool_calls"]
    revision = llm.requests[-1]
    assert revision["messages"][-2]["reasoning_content"] == "组织答案"
    assert revision["tools"] == TOOLS and revision["tool_choice"] == "none"
    assert ans.verification.passed and ans.attempts == 2


def test_failed_revision_keeps_answer(env):
    from finsight.llm.gateway import LLMError

    diag, _ = env
    good = f"净现比为 {shown(diag, 'ocf_to_np')}[E1]。"
    script = [call("get_metrics", codes=["ocf_to_np"]), good + "行业平均约为 1.35 倍。"]

    def responder(messages, tools):
        if script:
            return script.pop(0)
        raise LLMError("HTTP 400: tool_choice none unsupported")

    ans = ResearchAgent(diag, MockLLM(responder=responder)).ask("净现比？")
    assert ans.answer.startswith("净现比为") and ans.verification.unverified == 1
    assert any("保留原回答" in w for w in ans.warnings)


def test_failed_call_is_not_ok_even_though_empty_text_passes(env):
    from finsight.llm.gateway import LLMError

    diag, _ = env

    def boom(messages, tools):
        raise LLMError("费用保护：已停止发送")

    ans = ResearchAgent(diag, MockLLM(responder=boom)).ask("净现比？")
    assert ans.ok is False and ans.attempts == 0
