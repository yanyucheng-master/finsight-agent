"""基于真实数据快照的集成测试（data/snapshots 由采集器从公开数据源抓取）。

这些测试不硬编码任何“记忆中的”财务数字：期望值一律从原始响应中独立读取后计算，
用以验证解析、映射、公式、核验、证据链各环节的一致性。
"""

from __future__ import annotations

import json
import re

import pytest

from finsight.analytics.diagnosis import diagnose
from finsight.config import load_settings
from finsight.data.service import load_service
from finsight.data.snapshots import SnapshotStore

SNAP = load_settings().snapshot_dir
pytestmark = [pytest.mark.snapshots,
              pytest.mark.skipif(not (SNAP / "manifest.jsonl").exists(), reason="没有数据快照")]


@pytest.fixture(scope="module")
def svc():
    return load_service(SNAP)


@pytest.fixture(scope="module")
def results(svc):
    return {code: diagnose(svc.company(code)) for code in svc.available_symbols()}


def test_snapshot_objects_hash_verified():
    store = SnapshotStore(SNAP)
    checked = 0
    for rec in store.iter_records():
        if rec.ok and store.object_path(rec).exists():
            store.read_bytes(rec, verify=True)  # 哈希不一致会抛异常
            checked += 1
    assert checked > 100


def test_every_company_diagnoses_without_rule_errors(results):
    assert len(results) >= 5
    for code, r in results.items():
        assert not [s for s in r.signals if s.rule == "SYS"], f"{code} 有规则执行异常"
        assert r.overall.level in ("高", "中", "低")


def test_accounting_identities_hold_for_general_companies(results):
    for code, r in results.items():
        if r.profile.company_type != "general":
            continue
        fails = [c for c in r.checks if c.category == "identity" and c.status == "fail"]
        assert not fails, f"{code} 恒等式未通过: {[(c.code, c.period, c.diff) for c in fails]}"


def test_vendor_cross_checks_agree(results):
    for code, r in results.items():
        if r.profile.company_type != "general":
            continue
        fails = [c for c in r.checks if c.category == "vendor" and c.status == "fail"]
        assert not fails, f"{code} 与数据源指标不一致: {[(c.code, c.period, c.lhs, c.rhs) for c in fails]}"


def test_independent_recomputation_from_raw_json(svc, results):
    """不经过解析器，直接读原始 JSON 重算毛利率与资产负债率，与引擎结果比对（双实现校验）。"""
    store = svc.store
    code = "600519" if "600519" in results else next(iter(results))
    r = results[code]
    period = r.periods[0].end
    raw_income = raw_balance = None
    for dataset in ("income_annual", "balance_annual"):
        for rec in store.latest_run(provider="eastmoney", dataset=dataset, symbol=code):
            for row in json.loads(store.read_bytes(rec).decode("utf-8"))["data"]:
                if row["REPORT_DATE"][:10] == period:
                    if dataset == "income_annual":
                        raw_income = row
                    else:
                        raw_balance = row
    assert raw_income and raw_balance
    gm = (raw_income["OPERATE_INCOME"] - raw_income["OPERATE_COST"]) / raw_income["OPERATE_INCOME"]
    dr = raw_balance["TOTAL_LIABILITIES"] / raw_balance["TOTAL_ASSETS"]
    assert r.evidence[f"m:gross_margin@{period}"].value == pytest.approx(gm, rel=1e-12)
    assert r.evidence[f"m:debt_ratio@{period}"].value == pytest.approx(dr, rel=1e-12)


def test_evidence_chain_resolves_to_hashed_snapshot(svc, results):
    for code, r in results.items():
        for ev in r.evidence.values():
            if ev.kind == "fact":
                rec = svc.store.get(ev.source.snapshot_id)
                assert rec is not None and rec.sha256 == ev.source.sha256
            for dep in ev.inputs:
                assert dep in r.evidence, f"{code}: {ev.id} 依赖的 {dep} 不在证据账本中"


def test_signal_citations_exist(results):
    pat = re.compile(r"\[\[([^\]]+)\]\]")
    for code, r in results.items():
        for s in r.signals:
            for eid in pat.findall(s.summary):
                assert eid in r.evidence, f"{code} 信号 {s.rule} 引用了不存在的证据 {eid}"


def test_financial_companies_are_not_scored_with_general_metrics(results):
    for r in results.values():
        if r.profile.company_type in ("bank", "insurance", "securities"):
            gm = [e for eid, e in r.evidence.items() if eid.startswith("m:gross_margin@")]
            assert all(e.status == "not_applicable" for e in gm)


def test_api_end_to_end(svc):
    from fastapi.testclient import TestClient

    from finsight.api.app import create_app

    client = TestClient(create_app(service=svc))
    assert client.get("/api/health").json()["status"] == "ok"
    code = svc.available_symbols()[0]
    d = client.get(f"/api/companies/{code}/diagnosis")
    assert d.status_code == 200
    body = d.json()
    period = body["periods"][0]["end"]
    ev = client.get(f"/api/companies/{code}/evidence/m:debt_ratio@{period}").json()
    assert ev["chain"] and ev["snapshots"], "证据链应能回溯到快照"
    assert client.get("/api/companies/999999/diagnosis").status_code == 404
