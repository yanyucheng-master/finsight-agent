"""确定性计算引擎：带“来源追踪”的数值类型 V 与指标注册表。

为什么不用 pandas 一把梭？
* 每个计算结果都要知道“由哪些原始事实算出”——V 在四则运算中自动合并依赖集合；
* 缺失值语义明确：任何输入缺失 → 结果缺失，并记录缺失的科目，绝不以 0 代替；
* 除以 0 → 缺失（并记录原因），不会产生 inf/NaN 混入报告。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from finsight.analytics.ledger import Ledger, metric_id
from finsight.data.schema import label as item_label
from finsight.data.service import CompanyData
from finsight.domain import Evidence, Unit, fmt_value


class V:
    """带依赖追踪的数值。value 为 None 表示缺失。"""

    __slots__ = ("value", "refs", "missing", "notes")

    def __init__(self, value: float | None, refs: frozenset[str] = frozenset(), missing: frozenset[str] = frozenset(),
                 notes: frozenset[str] = frozenset()):
        if value is not None and (math.isnan(value) or math.isinf(value)):
            value = None
        self.value = value
        self.refs = refs
        self.missing = missing
        self.notes = notes

    @staticmethod
    def of(x: V | float | int) -> V:
        return x if isinstance(x, V) else V(float(x))

    def _bin(self, other: V | float | int, fn: Callable[[float, float], float]) -> V:
        o = V.of(other)
        refs, missing, notes = self.refs | o.refs, self.missing | o.missing, self.notes | o.notes
        if self.value is None or o.value is None:
            return V(None, refs, missing, notes)
        try:
            return V(fn(self.value, o.value), refs, missing, notes)
        except ZeroDivisionError:
            return V(None, refs, missing | {"<除数为零>"}, notes)

    def __add__(self, o): return self._bin(o, lambda a, b: a + b)
    def __radd__(self, o): return V.of(o)._bin(self, lambda a, b: a + b)
    def __sub__(self, o): return self._bin(o, lambda a, b: a - b)
    def __rsub__(self, o): return V.of(o)._bin(self, lambda a, b: a - b)
    def __mul__(self, o): return self._bin(o, lambda a, b: a * b)
    def __rmul__(self, o): return V.of(o)._bin(self, lambda a, b: a * b)
    def __truediv__(self, o): return self._bin(o, lambda a, b: a / b)
    def __rtruediv__(self, o): return V.of(o)._bin(self, lambda a, b: a / b)
    def __neg__(self): return V(None if self.value is None else -self.value, self.refs, self.missing, self.notes)

    def __abs__(self):
        return V(None if self.value is None else abs(self.value), self.refs, self.missing, self.notes)

    @property
    def ok(self) -> bool:
        return self.value is not None

    def __repr__(self) -> str:
        return f"V({self.value!r}, refs={sorted(self.refs)})"


def vsum(*values: V, required: int = 1) -> V:
    """可选科目求和：缺失科目视为“未列示”（不报缺失），但至少要有 required 个科目有值。

    用于“应收类合计”“有息负债”等由多个可能不存在的科目组成的口径。
    """
    present = [v for v in values if v.ok]
    refs = frozenset().union(*(v.refs for v in values))
    if len(present) < required:
        return V(None, refs, frozenset().union(*(v.missing for v in values)))
    return V(sum(v.value for v in present), refs)  # type: ignore[misc]


@dataclass
class MetricDef:
    code: str
    label: str
    unit: Unit
    dimension: str
    formula: str
    fn: Callable[[Calc], V]
    description: str = ""
    higher_is_better: bool | None = None
    reference: str = ""
    applies_to: tuple[str, ...] = ("general",)


REGISTRY: dict[str, MetricDef] = {}


def metric(code: str, label: str, unit: Unit, dimension: str, formula: str, *, description: str = "",
           higher_is_better: bool | None = None, reference: str = "",
           applies_to: tuple[str, ...] = ("general",)) -> Callable[[Callable[[Calc], V]], Callable[[Calc], V]]:
    def deco(fn: Callable[[Calc], V]) -> Callable[[Calc], V]:
        if code in REGISTRY:
            raise ValueError(f"重复的指标代码 {code}")
        REGISTRY[code] = MetricDef(code, label, unit, dimension, formula, fn, description, higher_is_better,
                                   reference, applies_to)
        return fn
    return deco


@dataclass
class Calc:
    """单个年度报告期的计算上下文。"""

    data: CompanyData
    ledger: Ledger
    period: str
    annual: list[str]  # 全部年报期末日，降序
    engine: Engine
    used_missing: set[str] = field(default_factory=set)

    def _shift(self, lag: int) -> str | None:
        if lag == 0:
            return self.period
        try:
            idx = self.annual.index(self.period)
        except ValueError:
            return None
        j = idx + lag
        if j >= len(self.annual):
            return None
        target = self.annual[j]
        # 必须是相邻年度（防止年报缺失导致“跨年”比较）
        if int(target[:4]) != int(self.period[:4]) - lag:
            return None
        return target

    def f(self, item: str, lag: int = 0) -> V:
        """取原始事实。缺失时返回缺失值，并记录缺失科目。"""
        period = self._shift(lag)
        if period is None:
            tag = f"{item_label(item)}（{int(self.period[:4]) - lag}年）"
            return V(None, missing=frozenset({tag}))
        ev = self.ledger.fact_for(item, period)
        if ev is None or ev.value is None:
            return V(None, missing=frozenset({f"{item_label(item)}（{period[:4]}年）"}))
        return V(float(ev.value), frozenset({ev.id}))  # type: ignore[arg-type]

    def opt(self, item: str, lag: int = 0) -> V:
        """可选科目：缺失表示报表未列示（视为 0 参与 vsum），不计入缺失。"""
        v = self.f(item, lag)
        return v if v.ok else V(None)

    def opt_zero(self, item: str, lag: int = 0) -> V:
        """报表未列示即为 0 的科目（如商誉、存货）；期间本身缺失时仍返回缺失。"""
        if self._shift(lag) is None:
            return self.f(item, lag)
        v = self.f(item, lag)
        return v if v.ok else V(0.0)

    def avg(self, item: str) -> V:
        return (self.f(item) + self.f(item, 1)) / 2

    def m(self, code: str, lag: int = 0) -> V:
        """引用另一个指标（自动计算并登记）。"""
        period = self._shift(lag)
        if period is None:
            return V(None, missing=frozenset({f"{REGISTRY[code].label}（{int(self.period[:4]) - lag}年）"}))
        ev = self.engine.compute(code, period)
        if ev is None or ev.value is None:
            return V(None, missing=frozenset({f"{REGISTRY[code].label}（{period[:4]}年）"}))
        notes = frozenset({f"{REGISTRY[code].label}：{ev.note}"}) if ev.note and code.startswith("m_") else frozenset()
        return V(float(ev.value), frozenset({ev.id}), notes=notes)  # type: ignore[arg-type]

    def year(self, lag: int = 0) -> int:
        return int(self.period[:4]) - lag


def _expression(ev_ids: frozenset[str], ledger: Ledger) -> str:
    parts = []
    for eid in sorted(ev_ids):
        ev = ledger.get(eid)
        if ev is not None:
            parts.append(f"{ev.label}{'（' + ev.period[:4] + '）' if ev.period else ''}={fmt_value(ev.value, ev.unit)}")
    return "；".join(parts)


class Engine:
    """对一家公司按需计算指标，并把结果登记到证据账本（带缓存，避免重复计算）。"""

    def __init__(self, data: CompanyData, ledger: Ledger | None = None):
        self.data = data
        self.ledger = ledger or Ledger(data)
        self.annual = data.annual_ends()
        self._done: dict[tuple[str, str], Evidence | None] = {}
        self._stack: list[tuple[str, str]] = []

    def compute(self, code: str, period: str) -> Evidence | None:
        key = (code, period)
        if key in self._done:
            return self._done[key]
        if key in self._stack:
            raise RuntimeError(f"指标循环依赖: {' -> '.join(c for c, _ in self._stack)} -> {code}")
        mdef = REGISTRY[code]
        company_type = self.data.profile.company_type
        if company_type not in mdef.applies_to and "all" not in mdef.applies_to:
            ev = Evidence(id=metric_id(code, period), kind="metric", label=mdef.label, value=None, unit=mdef.unit,
                          period=period, formula=mdef.formula, status="not_applicable",
                          note=f"该指标不适用于{company_type}类公司的报表结构")
            self.ledger.add(ev)
            self._done[key] = ev
            return ev
        self._stack.append(key)
        try:
            ctx = Calc(self.data, self.ledger, period, self.annual, self)
            result = mdef.fn(ctx)
        finally:
            self._stack.pop()
        status = "ok" if result.ok else "missing"
        parts = sorted(result.notes)
        if not result.ok and result.missing:
            parts.append("缺少：" + "、".join(sorted(result.missing)))
        note = "；".join(parts) or None
        ev = Evidence(
            id=metric_id(code, period), kind="metric", label=mdef.label, value=result.value, unit=mdef.unit,
            period=period, formula=mdef.formula, expression=_expression(result.refs, self.ledger) or None,
            inputs=sorted(result.refs), status=status, note=note,  # type: ignore[arg-type]
        )
        self.ledger.add(ev)
        self._done[key] = ev
        return ev

    def series(self, code: str, years: int | None = None) -> list[Evidence]:
        periods = self.annual if years is None else self.annual[:years]
        out = []
        for p in periods:
            ev = self.compute(code, p)
            if ev is not None:
                out.append(ev)
        return out

    def compute_all(self, years: int = 6) -> dict[str, list[Evidence]]:
        return {code: self.series(code, years) for code in REGISTRY}


def metric_catalog() -> list[dict[str, Any]]:
    return [{"code": m.code, "label": m.label, "unit": m.unit, "dimension": m.dimension, "formula": m.formula,
             "description": m.description, "higher_is_better": m.higher_is_better, "reference": m.reference}
            for m in REGISTRY.values()]
