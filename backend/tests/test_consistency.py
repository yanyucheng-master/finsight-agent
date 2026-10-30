"""口径一致性检查与格式计数排除（证据为人工构造，synthetic）。"""

from __future__ import annotations

from finsight.domain import Evidence
from finsight.reporting.verifier import verify

P = "2025-12-31"


def fact(item, label, value):
    return Evidence(id=f"f:{item}@{P}", kind="fact", label=label, value=value, unit="CNY", period=P)


REFS = {
    "E1": fact("cf.ocf", "经营活动现金流量净额", 61_522_000_000.0),
    "E2": fact("is.np_parent", "归属于母公司股东的净利润", 82_320_000_000.0),
    "E3": fact("is.net_profit", "净利润", 85_310_000_000.0),
    "E4": Evidence(id=f"m:ocf_to_np@{P}", kind="metric", label="净现比", value=0.7212, unit="times", period=P,
                   formula="经营活动现金流量净额 ÷ 净利润", inputs=[f"f:cf.ocf@{P}", f"f:is.net_profit@{P}"]),
}


def test_ratio_next_to_confusable_denominator_is_flagged():
    """真实模型测试中出现的情形：归母净利润与净现比并列，但净现比分母是净利润。"""
    v = verify("经营现金流 615.22 亿元[E1]，归母净利润 823.20 亿元[E2]，净现比 0.72 倍[E4]。", REFS)
    assert v.verified == 3 and len(v.consistency_flags) == 1 and not v.passed
    assert "归属于母公司股东的净利润" in v.consistency_flags[0] and "[E4]" in v.consistency_flags[0]


def test_no_flag_when_true_input_is_cited_or_in_other_sentence():
    ok1 = verify("净利润 853.10 亿元[E3]，归母净利润 823.20 亿元[E2]，净现比 0.72 倍[E4]。", REFS)
    ok2 = verify("归母净利润 823.20 亿元[E2]。净现比为 0.72 倍[E4]。", REFS)
    assert ok1.passed and ok2.passed


def test_format_counts_in_headings_are_not_claims():
    text = "## 主要风险（按严重程度排序，每条 1–3 句）\n## 待办（3-5 条）\n净现比 0.72 倍[E4]。"
    v = verify(text, REFS)
    assert [c.text for c in v.claims] == ["0.72 倍"]


def test_cited_numbers_in_headings_are_still_checked():
    v = verify("## 净现比降至 0.50 倍[E4]\n", REFS)
    assert v.mismatch == 1
