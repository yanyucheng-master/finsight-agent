"""核验器组合矩阵测试（synthetic）：系统地覆盖“排版 × 正负号 × 方向措辞 × 单位 × 证据符号 × 数值对错”。

动机：真实测试两次发现了单个例子没覆盖到的写法（紧贴汉字、英文逗号分隔、“下降+12.30%”）。
这里用一个与实现无关的“判定规则”（oracle）给出每种组合的期望结果，逐一比对。
"""

from __future__ import annotations

import itertools

import pytest

from finsight.domain import Evidence
from finsight.reporting.verifier import verify

P = "2025-12-31"
SEPARATORS = ["", " ", "，", ",", ", ", "、", "；", "和", "\n- "]
SIGNS = ["", "+", "-", "−"]
WORDS = ["为", "增长", "下降", "降至", "亏损", "减亏", "同比"]  # 数字前的措辞
UNITS = [("%", "ratio", 0.1230, "12.30"), ("亿元", "CNY", 1_032_800_000.0, "10.33"),
         ("万元", "CNY", 10_328_000.0, "1,032.80"), ("倍", "times", 0.72, "0.72")]
NEG = {"下降", "亏损"}
LEVEL = {"降至", "为", "同比"}


def oracle(sign: str, word: str, ev_negative: bool, magnitude_ok: bool) -> str | None:
    """期望状态；None 表示该组合本身语义不明，不纳入测试。"""
    if not magnitude_ok:
        return "mismatch"
    if word == "降至" and sign:
        return None  # “降至 -5%”这类写法罕见且有歧义
    if sign == "+" and word in NEG:
        return "mismatch"  # 措辞与符号矛盾
    if sign:
        text_negative = sign in ("-", "−")
    elif word in NEG:
        text_negative = True
    else:
        text_negative = False
    if word == "减亏" and not sign:
        text_negative = False
    return "verified" if text_negative == ev_negative else "mismatch"


CASES = []
for (unit, ev_unit, mag, shown), sign, word, ev_neg, ok in itertools.product(
        UNITS, SIGNS, WORDS, (False, True), (True, False)):
    exp = oracle(sign, word, ev_neg, ok)
    if exp is None:
        continue
    CASES.append((unit, ev_unit, mag, shown, sign, word, ev_neg, ok, exp))


@pytest.mark.parametrize("sep", SEPARATORS)
def test_matrix(sep):
    failures = []
    for unit, ev_unit, mag, shown, sign, word, ev_neg, ok, exp in CASES:
        target = -mag if ev_neg else mag
        refs = {"E1": Evidence(id=f"m:a@{P}", kind="metric", label="甲", value=0.9118, unit="ratio", period=P),
                "E2": Evidence(id=f"m:b@{P}", kind="metric", label="乙", value=target, unit=ev_unit, period=P)}
        num = shown if ok else shown.replace("2", "9", 1).replace("3", "8", 1)
        space = " " if unit in ("亿元", "万元") else ""
        text = f"甲为91.18%[E1]{sep}乙{word}{sign}{num}{space}{unit}[E2]。"
        v = verify(text, refs)
        got = [c.status for c in v.claims]
        if got != ["verified", exp]:
            failures.append(f"{text!r} ev={target}: {[(c.text, c.status) for c in v.claims]} 期望第二项 {exp}")
    assert not failures, f"{len(failures)}/{len(CASES)} 失败，例如：\n" + "\n".join(failures[:12])
