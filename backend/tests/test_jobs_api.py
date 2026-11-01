"""异步任务、阶段进度、演示限额的测试（真实快照 + MockLLM，不调用外部 API）。"""

from __future__ import annotations

import dataclasses
import threading
import time

import pytest
from fastapi.testclient import TestClient

from finsight.analytics.diagnosis import diagnose
from finsight.api.app import create_app
from finsight.api.jobs import JobManager, JobsBusy
from finsight.api.limits import DemoLimiter, LimitExceeded
from finsight.config import load_settings
from finsight.data.service import load_service
from finsight.llm.gateway import LLMResponse, MockLLM
from finsight.reporting.report import build_context, generate_report

SNAP = load_settings().snapshot_dir
pytestmark = pytest.mark.skipif(not (SNAP / "manifest.jsonl").exists(), reason="没有数据快照")


@pytest.fixture(scope="module")
def svc():
    return load_service(SNAP)


@pytest.fixture(scope="module")
def code(svc):
    if "600519" not in svc.available_symbols():
        pytest.skip("没有 600519 快照")
    return "600519"


def _good_text(svc, code) -> str:
    diag = diagnose(svc.company(code))
    ctx = build_context(diag)
    period = diag.periods[0].end
    ref = ctx.id_to_ref[f"m:gross_margin@{period}"]
    shown = f"{diag.evidence[f'm:gross_margin@{period}'].value * 100:.2f}%"
    return f"## 核心结论\n毛利率为 {shown}[{ref}]。"


def _app(svc, tmp_path, llm=None, **overrides):
    settings = dataclasses.replace(load_settings(), data_dir=tmp_path, **overrides)
    factory = (lambda name: llm) if llm is not None else None
    return TestClient(create_app(settings=settings, service=svc, llm_factory=factory))


def _wait(client: TestClient, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("任务超时未结束")


def test_report_job_reports_stages_in_order(svc, code, tmp_path):
    client = _app(svc, tmp_path, MockLLM(script=[_good_text(svc, code)]))
    start = client.post(f"/api/companies/{code}/report/jobs?mode=llm")
    assert start.status_code == 200 and start.json()["status"] in ("queued", "running", "done")
    body = _wait(client, start.json()["id"])
    assert body["status"] == "done" and body["result"]["mode"] == "llm"
    v = body["result"]["verification"]
    assert v["total"] >= 1 and v["mismatch"] == v["unverified"] == v["invalid_ref"] == 0
    assert not v["compliance_flags"] and not v["consistency_flags"]
    stages = [e["stage"] for e in body["events"]]
    assert stages[:3] == ["evidence", "drafting", "verifying"], stages
    assert body["stages"] == ["evidence", "drafting", "verifying", "revising", "done"]


def test_revision_stage_is_reported(svc, code, tmp_path):
    good = _good_text(svc, code)
    client = _app(svc, tmp_path, MockLLM(script=[good + "净资产收益率高达 45.6%。", good]))
    body = _wait(client, client.post(f"/api/companies/{code}/report/jobs?mode=llm").json()["id"])
    assert body["status"] == "done" and body["result"]["revised"]
    assert "revising" in [e["stage"] for e in body["events"]]


def test_template_job_is_free_and_not_counted(svc, code, tmp_path):
    client = _app(svc, tmp_path, MockLLM(), demo_mode=True, live_per_ip_daily=1, live_daily_limit=5)
    before = client.get("/api/demo").json()["live"]
    job = client.post(f"/api/companies/{code}/report/jobs?mode=template").json()
    assert job["live"] is False
    assert _wait(client, job["id"])["result"]["mode"] == "template"
    assert client.get("/api/demo").json()["live"] == before  # 模板简报不消耗额度


def test_live_limit_per_visitor_and_forwarded_header_is_ignored(svc, code, tmp_path):
    llm = MockLLM(responder=lambda m, t: LLMResponse(content=_good_text(svc, code)))
    client = _app(svc, tmp_path, llm, demo_mode=True, live_per_ip_daily=1, live_daily_limit=10)
    first = client.post(f"/api/companies/{code}/report/jobs?mode=llm")
    assert first.status_code == 200
    # 没有声明可信代理时，伪造 X-Forwarded-For 不能换来新的额度
    second = client.post(f"/api/companies/{code}/report/jobs?mode=llm", headers={"x-forwarded-for": "1.2.3.4"})
    assert second.status_code == 429 and "次数" in second.json()["detail"]
    assert client.get("/api/demo").json()["live"]["per_ip_remaining"] == 0
    _wait(client, first.json()["id"])


def test_forwarded_header_is_used_behind_trusted_proxy(svc, code, tmp_path):
    llm = MockLLM(responder=lambda m, t: LLMResponse(content=_good_text(svc, code)))
    client = _app(svc, tmp_path, llm, demo_mode=True, live_per_ip_daily=1, live_daily_limit=10, trust_proxy=True)
    a = client.post(f"/api/companies/{code}/report/jobs?mode=llm", headers={"x-forwarded-for": "1.1.1.1, 9.9.9.9"})
    b = client.post(f"/api/companies/{code}/report/jobs?mode=llm", headers={"x-forwarded-for": "2.2.2.2"})
    c = client.post(f"/api/companies/{code}/report/jobs?mode=llm", headers={"x-forwarded-for": "1.1.1.1"})
    assert (a.status_code, b.status_code, c.status_code) == (200, 200, 429)
    for r in (a, b):
        _wait(client, r.json()["id"])


def test_ask_job_lifecycle(svc, code, tmp_path):
    llm = MockLLM(script=["现有信息不足以回答，需要查阅年报附注。"])
    client = _app(svc, tmp_path, llm)
    start = client.post(f"/api/companies/{code}/ask/jobs", json={"question": "有什么风险？"})
    assert start.status_code == 200
    body = _wait(client, start.json()["id"])
    assert body["status"] == "done" and body["result"]["ok"] is True
    assert [e["stage"] for e in body["events"]][:2] == ["thinking", "verifying"]
    assert client.post(f"/api/companies/{code}/ask/jobs", json={"question": "  "}).status_code == 400


def test_ask_job_without_key_is_503(svc, code, tmp_path):
    client = _app(svc, tmp_path)  # 没有 llm_factory，也没有 Key
    if load_settings().llm.enabled:
        pytest.skip("本机配置了 Key")
    assert client.post(f"/api/companies/{code}/ask/jobs", json={"question": "x"}).status_code == 503


def test_unexpected_exception_becomes_failed_job(svc, code, tmp_path):
    def boom(messages, tools):
        raise RuntimeError("boom")

    client = _app(svc, tmp_path, MockLLM(responder=boom))
    body = _wait(client, client.post(f"/api/companies/{code}/report/jobs?mode=llm").json()["id"])
    assert body["status"] == "failed" and "boom" in body["error"] and "result" not in body


def test_unknown_job_and_company(svc, code, tmp_path):
    client = _app(svc, tmp_path)
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.post("/api/companies/999999/report/jobs?mode=template").status_code == 404


def test_progress_callback_errors_never_break_generation(svc, code):
    diag = diagnose(svc.company(code))

    def bad(stage, detail):
        raise RuntimeError("display bug")

    rep = generate_report(diag, mode="template", progress=bad)
    assert rep.mode == "template" and rep.verification.total > 5


def test_job_manager_refuses_when_too_many_pending():
    mgr = JobManager(max_workers=1)  # 排队上限 = 3
    gate = threading.Event()
    try:
        jobs = [mgr.submit("report", "x", lambda progress: (gate.wait(5), {})[1], live=False) for _ in range(3)]
        with pytest.raises(JobsBusy):
            mgr.submit("report", "x", lambda progress: {}, live=False)
    finally:
        gate.set()
        mgr.shutdown()
    assert len(jobs) == 3


def test_limiter_persists_and_rolls_over(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    lim = DemoLimiter(daily_limit=2, per_ip_limit=2, path=path)
    lim.reserve("a")
    lim.reserve("a")
    with pytest.raises(LimitExceeded):
        lim.reserve("a")
    # 重启（重新读文件）后额度没有被清零，文件里也没有明文 IP
    again = DemoLimiter(daily_limit=2, per_ip_limit=2, path=path)
    with pytest.raises(LimitExceeded):
        again.reserve("a")
    assert "\"a\"" not in path.read_text(encoding="utf-8")
    # 退还额度
    again.release("a")
    again.reserve("a")
    # 过了零点重新计数
    monkeypatch.setattr(DemoLimiter, "_today", staticmethod(lambda: "2099-01-01"))
    again.reserve("a")
    assert again.status("a")["daily_used"] == 1


def test_synchronous_endpoints_cannot_bypass_demo_limits(svc, code, tmp_path):
    """演示模式下，同步接口不经过额度和并发控制，所以不允许调用大模型。"""
    llm = MockLLM(responder=lambda m, t: (_ for _ in ()).throw(AssertionError("演示模式的同步接口不应调用大模型")))
    client = _app(svc, tmp_path, llm, demo_mode=True, live_per_ip_daily=1, live_daily_limit=5)
    assert client.post(f"/api/companies/{code}/report?mode=llm").status_code == 403
    assert client.post(f"/api/companies/{code}/ask", json={"question": "毛利率多少？"}).status_code == 403
    auto = client.post(f"/api/companies/{code}/report?mode=auto")  # auto 在演示模式下只给模板简报
    assert auto.status_code == 200 and auto.json()["mode"] == "template"
    assert client.post(f"/api/companies/{code}/report?mode=template").json()["mode"] == "template"
    assert not llm.requests and client.get("/api/demo").json()["live"]["daily_used"] == 0


def test_synchronous_endpoints_still_work_outside_demo_mode(svc, code, tmp_path):
    client = _app(svc, tmp_path, MockLLM(script=[_good_text(svc, code)]))
    assert client.post(f"/api/companies/{code}/report?mode=llm").json()["mode"] == "llm"
