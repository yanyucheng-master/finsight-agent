"""计算引擎单元测试。

注意：本文件中的数字是**人工构造的测试向量**（synthetic），仅用于验证公式与缺失值语义，
不代表任何真实公司的财务数据。真实数据测试见 test_real_snapshots.py。
"""

from __future__ import annotations

import pytest

import finsight.analytics.metrics  # noqa: F401
import finsight.analytics.models  # noqa: F401
from finsight.analytics.engine import Engine, V, vsum
from finsight.data.service import CompanyData
from finsight.domain import CompanyProfile, Fact, Period, SourceRef

SRC = SourceRef(provider="synthetic", dataset="unit_test", snapshot_id="t", sha256="0" * 64, url="synthetic://",
                fetched_at="2026-01-01T00:00:00Z")


def make_company(rows: dict[str, dict[str, float]], company_type: str = "general") -> CompanyData:
    facts: dict[str, dict[str, Fact]] = {}
    periods = []
    for end, items in rows.items():
        periods.append(Period(end=end, type="FY", label=f"{end[:4]}年报", fiscal_year=int(end[:4])))
        facts[end] = {k: Fact(item=k, period=end, value=v, source=SRC.model_copy(update={"field": k}))
                      for k, v in items.items()}
    periods.sort(key=lambda p: p.end, reverse=True)
    profile = CompanyProfile(code="000000", exchange="SZ", name="测试公司", company_type=company_type)  # type: ignore[arg-type]
    return CompanyData(profile=profile, annual_periods=periods, interim_periods=[], facts=facts)


def test_v_tracks_refs_and_division_by_zero():
    a, b = V(10.0, frozenset({"x"})), V(0.0, frozenset({"y"}))
    r = a / b
    assert r.value is None and r.refs == {"x", "y"} and "<除数为零>" in r.missing
    s = (a + 5) * 2
    assert s.value == 30.0 and s.refs == {"x"}


def test_vsum_optional_components():
    assert vsum(V(1.0, frozenset({"a"})), V(None), required=0).value == 1.0
    assert vsum(V(None), V(None), required=1).value is None
    assert vsum(V(None), V(None), required=0).value == 0.0


def test_gross_margin_formula_and_provenance():
    data = make_company({"2025-12-31": {"is.revenue": 200.0, "is.cogs": 50.0}})
    ev = Engine(data).compute("gross_margin", "2025-12-31")
    assert ev.value == pytest.approx(0.75)
    assert set(ev.inputs) == {"f:is.revenue@2025-12-31", "f:is.cogs@2025-12-31"}
    assert ev.formula and "营业成本" in ev.formula


def test_average_balance_requires_adjacent_year():
    data = make_company({
        "2025-12-31": {"is.np_parent": 30.0, "bs.equity_parent": 120.0},
        "2023-12-31": {"bs.equity_parent": 80.0},  # 缺少 2024 年报
    })
    ev = Engine(data).compute("roe", "2025-12-31")
    assert ev.value is None and ev.status == "missing"
    assert "2024" in (ev.note or "")


def test_roe_uses_average_equity():
    data = make_company({
        "2025-12-31": {"is.np_parent": 30.0, "bs.equity_parent": 120.0},
        "2024-12-31": {"bs.equity_parent": 80.0},
    })
    ev = Engine(data).compute("roe", "2025-12-31")
    assert ev.value == pytest.approx(30.0 / 100.0)


def test_ocf_to_np_not_meaningful_when_loss():
    data = make_company({"2025-12-31": {"cf.ocf": 10.0, "is.net_profit": -5.0}})
    ev = Engine(data).compute("ocf_to_np", "2025-12-31")
    assert ev.value is None and "净利润≤0" in (ev.note or "")


def test_growth_base_non_positive_is_missing():
    data = make_company({"2025-12-31": {"is.np_parent": 10.0}, "2024-12-31": {"is.np_parent": -3.0}})
    ev = Engine(data).compute("np_parent_growth", "2025-12-31")
    assert ev.value is None


def test_beneish_components_combine():
    rows = {
        "2025-12-31": {"is.revenue": 150.0, "is.cogs": 90.0, "bs.accounts_receivable": 30.0, "bs.current_assets": 100.0,
                       "bs.fixed_assets": 60.0, "bs.total_assets": 200.0, "cf.depreciation": 6.0,
                       "is.selling_exp": 10.0, "is.admin_exp": 8.0, "bs.current_liabilities": 50.0,
                       "bs.long_loans": 20.0, "is.net_profit": 20.0, "cf.ocf": 5.0},
        "2024-12-31": {"is.revenue": 100.0, "is.cogs": 55.0, "bs.accounts_receivable": 15.0, "bs.current_assets": 80.0,
                       "bs.fixed_assets": 55.0, "bs.total_assets": 160.0, "cf.depreciation": 6.0,
                       "is.selling_exp": 8.0, "is.admin_exp": 7.0, "bs.current_liabilities": 40.0,
                       "bs.long_loans": 20.0, "is.net_profit": 12.0, "cf.ocf": 11.0},
    }
    eng = Engine(make_company(rows))
    m = eng.compute("beneish_m", "2025-12-31")
    dsri = (30 / 150) / (15 / 100)
    gmi = ((100 - 55) / 100) / ((150 - 90) / 150)
    aqi = (1 - (100 + 60) / 200) / (1 - (80 + 55) / 160)
    sgi = 150 / 100
    depi = (6 / (6 + 55)) / (6 / (6 + 60))
    sgai = (18 / 150) / (15 / 100)
    lvgi = ((50 + 20) / 200) / ((40 + 20) / 160)
    tata = (20 - 5) / 200
    expected = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi - 0.172 * sgai
                + 4.679 * tata - 0.327 * lvgi)
    assert m.value == pytest.approx(expected)
    assert any(i.startswith("m:m_dsri") for i in m.inputs)


def test_not_applicable_for_banks():
    data = make_company({"2025-12-31": {"is.revenue": 100.0, "is.cogs": 50.0}}, company_type="bank")
    ev = Engine(data).compute("gross_margin", "2025-12-31")
    assert ev.status == "not_applicable"
