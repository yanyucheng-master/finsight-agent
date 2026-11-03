"""股票代码、品牌名里的数字不是财务断言（2026-11-02 真实评测复核发现：A 组“獐子岛（002069）”被当成数字 2069 判成无证据）。"""

from __future__ import annotations

import pytest

from finsight.reporting.verifier import verify


@pytest.mark.parametrize("text", [
    "獐子岛（002069）2025年年报显示经营承压。",
    "格力电器(000651)的现金流较好。",
    "【300750】宁德时代。",
    "股票代码：000651，本文仅作研究。",
    "证券代码 600519 对应贵州茅台。",
    "万科A 000002.SZ 的偿债压力较大。",
    "标的为SH600519。",
    "旗舰产品国窖1573的价格带稳定。",
    "五粮液 1618 的批价保持稳定。",
])
def test_codes_and_brand_numbers_are_not_claims(text):
    assert verify(text, {}).claims == []


@pytest.mark.parametrize("text, expected", [
    ("净利润为 123456 元。", ["123456 元"]),  # 6 位数字但不是代码上下文：仍是断言
    ("（123456）", ["123456"]),  # 括号里的 6 位数字，不是合法的 A 股代码前缀
    ("营业收入 600519亿元。", ["600519亿元"]),  # 带单位的金额不会被当成代码
    ("资产负债率为 96.83%。", ["96.83%"]),
    ("国窖1573元。", ["1573元"]),
    ("五粮液1618.50元。", ["1618.50元"]),
    ("国窖15730万元。", ["15730万元"]),
    ("经典1618 倍。", ["1618 倍"]),
    ("证券代码：600519.25亿元。", ["600519.25亿元"]),
    ("证券代码：600519 元。", ["600519 元"]),
])
def test_real_numbers_are_still_checked(text, expected):
    assert [c.text for c in verify(text, {}).claims] == expected
    assert all(c.status == "unverified" for c in verify(text, {}).claims)


def test_code_beside_a_real_claim_only_drops_the_code():
    text = "獐子岛（002069）的资产负债率为 96.83%，营业收入 12.34 亿元。"
    assert [c.text for c in verify(text, {}).claims] == ["96.83%", "12.34 亿元"]
