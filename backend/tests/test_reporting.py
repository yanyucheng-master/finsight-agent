"""结论核验器与研究简报流水线测试。

核验器测试使用人工构造的证据（synthetic），流水线测试使用真实快照 + MockLLM（不调用外部 API）。
"""

from __future__ import annotations

import pytest

from finsight.domain import Evidence
from finsight.llm.gateway import LLMError, LLMResponse, MockLLM
from finsight.reporting.report import build_context, generate_report
from finsight.reporting.verifier import verify


def ev(eid: str, label: str, value, unit: str) -> Evidence:
    return Evidence(id=eid, kind="metric", label=label, value=value, unit=unit, period="2025-12-31")


REFS = {
    "E1": ev("m:gross_margin@2025-12-31", "毛利率", 0.911795, "ratio"),
    "E2": ev("f:bs.cash@2025-12-31", "货币资金", 51_690_610_946.5, "CNY"),
    "E3": ev("m:ocf_to_np@2025-12-31", "净现比", 0.7212, "times"),
    "E4": ev("m:dio@2025-12-31", "存货周转天数", 1418.73, "days"),
    "E5": ev("m:revenue_growth@2025-12-31", "营业收入增速", -0.0121, "ratio"),
}


def test_all_supported_numbers_verify():
    text = ("2025年毛利率为 91.18%[E1]，货币资金 516.91 亿元[E2]，净现比 0.72 倍[E3]，"
            "存货周转天数 1,418.7 天[E4]。营业收入下降 1.21%[E5]。")
    v = verify(text, REFS)
    assert v.total == 5 and v.verified == 5, [(c.text, c.status, c.message) for c in v.claims]
    assert any("负号" in c.message for c in v.claims)  # “下降 1.21%” 对应负增速


def test_detects_mismatch_unverified_invalid_and_unattributed():
    text = "毛利率为 85.00%[E1]。净利率高达 45.6%。毛利率 91.18%。净现比 0.72 倍[E99]。"
    v = verify(text, REFS)
    status = {c.text: c.status for c in v.claims}
    assert status["85.00%"] == "mismatch"
    assert status["45.6%"] == "unverified"
    assert status["91.18%"] == "unattributed"
    assert status["0.72 倍"] == "invalid_ref"
    assert not v.passed


def test_years_counts_and_list_numbers_are_not_claims():
    text = "## 1. 核心结论\n2025年共发现 3 项风险，近 3 年净现比 0.72 倍[E3]。\n2. 其他"
    v = verify(text, REFS)
    assert [c.text for c in v.claims] == ["0.72 倍"]


def test_compliance_flags_investment_advice():
    v = verify("综合来看建议买入，目标价 2000 元。", {})
    assert v.compliance_flags and not v.passed


# ------------------------------------------------------------------ 流水线（真实快照 + MockLLM）

@pytest.fixture(scope="module")
def diag():
    from finsight.analytics.diagnosis import diagnose
    from finsight.config import load_settings
    from finsight.data.service import load_service

    svc = load_service(load_settings().snapshot_dir)
    if "600519" not in svc.available_symbols():
        pytest.skip("没有 600519 快照")
    return diagnose(svc.company("600519"))


def _good_sentence(diag) -> tuple[str, str]:
    ctx = build_context(diag)
    period = diag.periods[0].end
    ref = ctx.id_to_ref[f"m:gross_margin@{period}"]
    shown = f"{diag.evidence[f'm:gross_margin@{period}'].value * 100:.2f}%"
    return f"毛利率为 {shown}[{ref}]。", ref


def test_template_report_is_fully_verified(diag):
    rep = generate_report(diag, mode="template")
    assert rep.mode == "template" and rep.verification.total > 5
    assert rep.verification.verified == rep.verification.total


def test_llm_report_hallucination_is_caught_and_revised(diag):
    good, _ = _good_sentence(diag)
    draft = f"## 核心结论\n{good}净资产收益率高达 45.6%，远超同业。"
    fixed = f"## 核心结论\n{good}"
    llm = MockLLM(script=[draft, fixed])
    rep = generate_report(diag, llm=llm, mode="llm")
    assert rep.mode == "llm" and rep.attempts == 2 and rep.revised
    assert rep.verification.passed
    revision_request = llm.requests[1]["messages"][-1]["content"]
    assert "45.6%" in revision_request  # 核验器把幻觉数字反馈给了模型
    # 证据清单与规则一同发给了模型
    assert "【证据清单】" in llm.requests[0]["messages"][1]["content"]


def test_unfixed_problems_are_annotated(diag):
    good, _ = _good_sentence(diag)
    bad = f"{good}市占率 37.5%。"
    rep = generate_report(diag, llm=MockLLM(script=[bad, bad]), mode="llm")
    assert rep.verification.unverified == 1
    assert "⚠️〔未核验〕" in rep.annotated_markdown and rep.warnings


def test_llm_failure_falls_back_to_template(diag):
    def boom(messages, tools):
        raise LLMError("网络不可达")

    rep = generate_report(diag, llm=MockLLM(responder=boom), mode="llm")
    assert rep.mode == "template" and any("降级" in w for w in rep.warnings)


def test_llm_response_message_roundtrip():
    resp = LLMResponse(content="ok")
    assert resp.as_message() == {"role": "assistant", "content": "ok"}


def test_verifier_benchmark_detects_injected_errors():
    """回归保护：受控注入的数字错误应被几乎全部检出，且原文零误报。"""
    from finsight.config import load_settings
    from finsight.data.service import load_service
    from finsight.evaluation.verifier_bench import run

    svc = load_service(load_settings().snapshot_dir)
    if len(svc.available_symbols()) < 3:
        pytest.skip("快照不足")
    s = run(svc)
    assert s["overall_detection_rate"] >= 0.99
    assert s["false_positive_rate"] == 0.0
    assert s["controls"]["rounding_ok"]["rate"] >= 0.99
    # 数字紧贴汉字时也必须全部抽取（2026-10-30 真实测试暴露的缺口）
    assert s["layouts"]["compact"]["claims"] == s["layouts"]["spaced"]["claims"]
    assert s["detection"]["sign_flip"]["rate"] >= 0.99


def test_report_revision_passes_back_reasoning(diag):
    good, _ = _good_sentence(diag)
    draft = LLMResponse(content=f"## 核心结论\n{good}净资产收益率高达 45.6%。", reasoning="思考过程")
    llm = MockLLM(script=[draft, f"## 核心结论\n{good}"])
    rep = generate_report(diag, llm=llm, mode="llm")
    assert llm.requests[1]["messages"][-2] == {"role": "assistant", "content": draft.content,
                                               "reasoning_content": "思考过程"}
    assert rep.verification.passed and rep.revised


def test_empty_llm_report_falls_back_to_template(diag):
    rep = generate_report(diag, llm=MockLLM(script=[""]), mode="llm")
    assert rep.mode == "template" and any("空报告" in w for w in rep.warnings)


def test_failed_revision_keeps_annotated_draft(diag):
    good, _ = _good_sentence(diag)
    calls = {"n": 0}

    def responder(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return f"## 核心结论\n{good}市占率 37.5%。"
        raise LLMError("HTTP 400: tool_choice not supported")

    rep = generate_report(diag, llm=MockLLM(responder=responder), mode="llm")
    assert rep.mode == "llm" and rep.verification.unverified == 1
    assert any("保留初稿" in w for w in rep.warnings) and "⚠️〔未核验〕" in rep.annotated_markdown


def test_truncated_revision_is_discarded_and_truncated_draft_is_flagged(diag):
    good, _ = _good_sentence(diag)
    bad = f"## 核心结论\n{good}市占率 37.5%。"
    fixed = f"## 核心结论\n{good}"
    # 修订稿被截断：即使它“问题更少”也不采用
    rep = generate_report(diag, llm=MockLLM(script=[bad, LLMResponse(content=fixed, finish_reason="length")]), mode="llm")
    assert not rep.revised and not rep.truncated and "37.5%" in rep.markdown
    assert any("修订稿" in w for w in rep.warnings) and rep.verification.unverified == 1
    # 初稿被截断但随后被完整的修订稿取代：最终采用的正文是完整的
    rep = generate_report(diag, llm=MockLLM(script=[LLMResponse(content=bad, finish_reason="length"), fixed]), mode="llm")
    assert rep.revised and not rep.truncated and not any("长度上限" in w for w in rep.warnings)
    # 初稿被截断且无需修订：最终正文来自被截断的回复
    rep = generate_report(diag, llm=MockLLM(script=[LLMResponse(content=fixed, finish_reason="length")]), mode="llm")
    assert rep.truncated and any("长度上限" in w for w in rep.warnings)
