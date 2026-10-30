"""核验器回归测试：取自 2026-10-30 第二轮真实 Kimi 测试发现的漏检 / 误报（docs/eval/live-20261030/）。

旧版本的 NUM_RE 左边界用了 (?<![\\w.])，而 Python 中汉字属于 \\w：
* “毛利率为91.18%”这类紧贴汉字的数字整体漏检（真实简报 39 个数字只抽到 9 个），改成 99.99% 也判通过；
* “为1,032.80 万元”被截成“032.80 万元”，正确数字被判不一致；
* 显式写反的正负号（证据 -12.30%，文本 +12.30%）因“绝对值一致”被判通过。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finsight.domain import Evidence
from finsight.reporting.verifier import verify

LIVE = Path(__file__).resolve().parents[2] / "docs" / "eval" / "live-20261030"
P = "2025-12-31"


def metric(value, unit="ratio", label="增长率"):
    return {"E1": Evidence(id=f"m:synthetic@{P}", kind="metric", label=label, value=value, unit=unit, period=P)}


@pytest.mark.parametrize("text,status", [
    ("增长率为 -12.30%[E1]。", "verified"),
    ("增长率为-12.30%[E1]。", "verified"),
    ("增长率为 +12.30%[E1]。", "mismatch"),       # 显式符号写反
    ("增长率为+12.30%[E1]。", "mismatch"),
    ("营业收入下降 12.30%[E1]。", "verified"),     # 负值用“下降”表述
    ("营业收入下降12.30%[E1]。", "verified"),
    ("营业收入增长 12.30%[E1]。", "mismatch"),     # 证据为负，却写成增长且不带符号
])
def test_sign_semantics_negative_evidence(text, status):
    v = verify(text, metric(-0.123))
    assert [c.status for c in v.claims] == [status], [(c.text, c.status, c.message) for c in v.claims]


@pytest.mark.parametrize("text,status", [
    ("营业收入增长 5.00%[E1]。", "verified"),
    ("营业收入下降 5.00%[E1]。", "mismatch"),      # 证据为正，措辞却是下降
    ("营业收入为-5.00%[E1]。", "mismatch"),
])
def test_sign_semantics_positive_evidence(text, status):
    v = verify(text, metric(0.05))
    assert [c.status for c in v.claims] == [status]


def test_level_after_decline_word_is_not_a_sign():
    """“降至 0.72 倍”描述水平，不是负值；顿号分隔的前一分句里的“下降”也不影响后面的数字。"""
    refs = metric(0.7212, unit="times", label="净现比")
    assert verify("净现比降至0.72倍[E1]。", refs).passed
    assert verify("核实经营现金流下降、净现比0.72倍[E1]的原因。", refs).passed


def test_thousands_separator_next_to_chinese():
    refs = {"E3": Evidence(id=f"f:is.np_parent@{P}", kind="fact", label="归母净利润", value=10_328_000.0,
                           unit="CNY", period=P)}
    v = verify("2025年归母净利润为1,032.80 万元[E3]。", refs)
    assert [(c.text, c.status) for c in v.claims] == [("1,032.80 万元", "verified")]


def test_compact_numbers_are_all_extracted():
    refs = {"E1": Evidence(id=f"m:gross_margin@{P}", kind="metric", label="毛利率", value=0.911795, unit="ratio",
                           period=P)}
    assert verify("毛利率为91.18%[E1]。", refs).passed
    v = verify("毛利率为99.99%[E1]。", refs)
    assert v.mismatch == 1 and not v.passed


@pytest.fixture(scope="module")
def live_600519():
    path = LIVE / "report_600519.json"
    if not path.exists():
        pytest.skip("缺少真实测试归档")
    from finsight.analytics.diagnosis import diagnose
    from finsight.config import load_settings
    from finsight.data.service import load_service

    svc = load_service(load_settings().snapshot_dir)
    if "600519" not in svc.available_symbols():
        pytest.skip("没有 600519 快照")
    rep = json.loads(path.read_text(encoding="utf-8"))
    diag = diagnose(svc.company("600519"))
    refs = {k: diag.evidence[v] for k, v in rep["refs"].items() if v in diag.evidence}
    return rep["markdown"], refs


def test_real_report_full_extraction_and_injected_error(live_600519):
    """真实简报（数字紧贴汉字）：旧版只抽到 9 个数字，现在全部抽取；注入的错误能被发现。"""
    text, refs = live_600519
    v = verify(text, refs)
    assert v.total >= 35 and v.passed, [(c.text, c.status, c.message) for c in v.problems]
    bad = verify(text.replace("毛利率为91.18%[E17]", "毛利率为99.99%[E17]"), refs)
    assert bad.mismatch == 1 and not bad.passed
