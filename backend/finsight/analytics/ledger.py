"""证据账本：分析过程中产生的一切可引用数字与判断的登记处。

每条证据都有稳定 ID；派生证据记录公式、代入数值后的算式与依赖的证据 ID，
因此任何数字都可以沿 inputs 一路回溯到原始快照（SourceRef）。
"""

from __future__ import annotations

from collections.abc import Iterable

from finsight.data.schema import ITEM_BY_CODE
from finsight.data.service import CompanyData
from finsight.domain import Evidence, Fact, fmt_value


def fact_id(item: str, period: str) -> str:
    return f"f:{item}@{period}"


def metric_id(code: str, period: str) -> str:
    return f"m:{code}@{period}"


class Ledger:
    def __init__(self, data: CompanyData):
        self.data = data
        self._items: dict[str, Evidence] = {}

    # -- 登记 ---------------------------------------------------------------------
    def add(self, ev: Evidence) -> Evidence:
        self._items[ev.id] = ev
        return ev

    def fact(self, fact: Fact) -> Evidence:
        eid = fact_id(fact.item, fact.period)
        existing = self._items.get(eid)
        if existing is not None:
            return existing
        item = ITEM_BY_CODE.get(fact.item)
        unit = "cny_per_share" if fact.item.startswith("is.eps") else "CNY"
        return self.add(Evidence(
            id=eid, kind="fact", label=item.label if item else fact.item, value=fact.value, unit=unit,
            period=fact.period, source=fact.source, note=(item.note or None) if item else None,
        ))

    def fact_for(self, item: str, period: str) -> Evidence | None:
        f = self.data.fact(item, period)
        return self.fact(f) if f else None

    # -- 查询 ---------------------------------------------------------------------
    def get(self, eid: str) -> Evidence | None:
        return self._items.get(eid)

    def __contains__(self, eid: str) -> bool:
        return eid in self._items

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> list[Evidence]:
        return list(self._items.values())

    def closure(self, ids: Iterable[str]) -> list[Evidence]:
        """返回给定证据及其全部上游依赖（用于溯源展示）。"""
        seen: dict[str, Evidence] = {}
        stack = list(ids)
        while stack:
            eid = stack.pop()
            if eid in seen:
                continue
            ev = self._items.get(eid)
            if ev is None:
                continue
            seen[eid] = ev
            stack.extend(ev.inputs)
        return list(seen.values())

    def display(self, eid: str) -> str:
        ev = self._items.get(eid)
        return fmt_value(ev.value, ev.unit) if ev else "—"
