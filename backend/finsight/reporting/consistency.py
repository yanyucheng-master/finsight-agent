"""口径一致性检查：逐个数字都对，组合起来仍可能误导。

真实模型测试（2026-10-29，kimi-k3.1-pro）中的例子：同一句并列“归母净利润 823.20 亿元”与“净现比 0.72 倍”，
但净现比的分母是净利润（含少数股东损益，853.10 亿元）。读者会以为 0.72 = 经营现金流 ÷ 归母净利润。

规则：同一句（或同一列表项）中引用了某个指标，同时引用了一个与该指标某项输入“容易混淆”的科目
（同组、同期、同公司），却没有引用真正的输入科目，就给出口径提示。只比较证据账本中的依赖关系，不做语义猜测。
"""

from __future__ import annotations

import re

from finsight.domain import Evidence

# 容易混淆、但口径不同的科目组
CONFUSABLE: list[frozenset[str]] = [
    frozenset({"is.net_profit", "is.np_parent", "is.np_deducted", "is.np_continuing"}),
    frozenset({"is.revenue", "is.total_revenue"}),
    frozenset({"bs.equity", "bs.equity_parent"}),
]
_GROUP = {item: g for g in CONFUSABLE for item in g}
_CITE = re.compile(r"[\[【]\s*(E\d+(?:\s*[,，、]\s*E\d+)*)\s*[\]】]")
_FACT = re.compile(r"^(?:(?P<co>[^|]+)\|)?f:(?P<item>[^@]+)@(?P<period>.+)$")


def _fact_key(eid: str, company: str | None = None) -> tuple[str | None, str, str] | None:
    m = _FACT.match(eid)
    if not m:
        return None
    return (m.group("co") or company, m.group("item"), m.group("period"))


def _company(eid: str) -> str | None:
    return eid.split("|", 1)[0] if "|" in eid else None


def _segments(text: str) -> list[str]:
    return [s for s in re.split(r"[。；;\n]", text) if s.strip()]


def check_consistency(text: str, refs: dict[str, Evidence]) -> list[str]:
    flags: list[str] = []
    seen: set[tuple[str, str]] = set()
    for seg in _segments(text):
        cited = [r.strip() for m in _CITE.finditer(seg) for r in re.split(r"[,，、]", m.group(1))]
        evs = {r: refs[r] for r in cited if r in refs}
        facts = {r: _fact_key(e.id) for r, e in evs.items() if e.kind == "fact"}
        fact_keys = {k for k in facts.values() if k}
        for mref, metric in evs.items():
            if metric.kind != "metric" or not metric.inputs:
                continue
            co = _company(metric.id)
            inputs = {k for k in (_fact_key(i, co) for i in metric.inputs) if k}
            for fref, fk in facts.items():
                if not fk or fk in inputs:
                    continue
                group = _GROUP.get(fk[1])
                rivals = [k for k in inputs if group and k[1] in group and k[0] == fk[0] and k[2] == fk[2]]
                if not rivals or any(r in fact_keys for r in rivals):
                    continue
                if (mref, fref) in seen:
                    continue
                seen.add((mref, fref))
                fact_label = evs[fref].label
                flags.append(f"口径提示：同一句引用了“{fact_label}”[{fref}]，但“{metric.label}”[{mref}]的公式是"
                             f"“{metric.formula}”，用的不是{fact_label}。请写明口径，或改为引用公式中的科目")
    return flags
