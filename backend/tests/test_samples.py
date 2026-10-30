"""缓存样例：入库时用当前核验器重新核验，而不是照搬生成时的结论。

样例原料是真实 Kimi 输出（docs/eval/live-*），数字仍须逐一对得上当前快照里的证据。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from finsight.analytics.diagnosis import diagnose
from finsight.config import load_settings
from finsight.data.service import load_service
from finsight.samples import SampleError, SampleStore, build_ask_sample, build_report_sample

ROOT = Path(__file__).resolve().parents[2]
SNAP = load_settings().snapshot_dir
REPORT = ROOT / "docs/eval/live-20261030-r3/runs/report_600519_kimi.json"
ASK = ROOT / "docs/eval/live-20261030-vanke/ask_2_000002_completion.json"
pytestmark = pytest.mark.skipif(not (SNAP / "manifest.jsonl").exists() or not REPORT.exists(),
                                reason="没有数据快照或真实输出")


@pytest.fixture(scope="module")
def svc():
    return load_service(SNAP)


@pytest.fixture(scope="module")
def diag(svc):
    return diagnose(svc.company("600519"))


def _raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_real_kimi_report_is_reverified_with_current_verifier(diag):
    sample = build_report_sample(_raw(REPORT), diag, source="r3")
    v = sample["verification"]
    assert sample["reverified"] and sample["kind"] == "report" and sample["model"]
    assert v["total"] >= 30 and v["mismatch"] == 0 and v["invalid_ref"] == 0 and v["unverified"] == 0
    assert sample["result"]["mode"] == "llm" and "数据时效（系统生成）" in sample["result"]["markdown"]


def test_tampered_number_is_caught_on_ingest(diag):
    raw = copy.deepcopy(_raw(REPORT))
    assert "91.18%" in raw["markdown"]  # 毛利率，来自真实输出
    raw["markdown"] = raw["markdown"].replace("91.18%", "99.99%", 1)
    sample = build_report_sample(raw, diag, source="tampered")
    assert sample["verification"]["mismatch"] >= 1 and not sample["verification"]["passed"]


def test_missing_evidence_and_template_are_rejected(diag):
    raw = copy.deepcopy(_raw(REPORT))
    raw["refs"]["E1"] = "f:no.such_item@2025-12-31"
    with pytest.raises(SampleError, match="不存在"):
        build_report_sample(raw, diag, source="x")
    tmpl = copy.deepcopy(_raw(REPORT))
    tmpl["mode"] = "template"
    with pytest.raises(SampleError, match="模板"):
        build_report_sample(tmpl, diag, source="x")
    other = copy.deepcopy(_raw(REPORT))
    other["code"] = "000002"
    with pytest.raises(SampleError, match="不一致"):
        build_report_sample(other, diag, source="x")


@pytest.mark.skipif(not ASK.exists(), reason="没有万科问答输出")
def test_real_kimi_answer_is_reverified(svc):
    if "000002" not in svc.available_symbols():
        pytest.skip("没有 000002 快照")
    sample = build_ask_sample(_raw(ASK), diagnose(svc.company("000002")), source="vanke")
    assert sample["kind"] == "ask" and sample["verification"]["total"] >= 10
    assert sample["verification"]["mismatch"] == 0 and sample["verification"]["invalid_ref"] == 0


def test_failed_answer_is_not_a_sample(diag):
    bad = {"question": "q", "answer": "", "refs": {}, "ok": False}
    with pytest.raises(SampleError, match="无效"):
        build_ask_sample(bad, diag, source="x")


def test_store_roundtrip_and_path_traversal(diag, tmp_path):
    store = SampleStore(tmp_path / "samples")
    assert store.list() == []
    sample = build_report_sample(_raw(REPORT), diag, source="r3")
    store.save(sample)
    assert [s["id"] for s in store.list()] == [sample["id"]]
    assert store.get(sample["id"])["result"]["code"] == "600519"
    assert store.get("../../etc/passwd") is None and store.get("nope") is None
    # 重复入库是幂等的（id 由内容确定，不是随机数）
    assert build_report_sample(_raw(REPORT), diag, source="r3")["id"] == sample["id"]


FAILED_ASK = ROOT / "docs/eval/live-20261030-r3/runs/ask_1_600519_kimi.json"  # 预算停止，answer 是错误信息，没有 ok 字段


@pytest.mark.skipif(not FAILED_ASK.exists(), reason="没有该输出")
def test_old_format_failed_answer_is_rejected(diag):
    with pytest.raises(SampleError, match="无效"):
        build_ask_sample(_raw(FAILED_ASK), diag, source="x")
