"""评测②（三组数字幻觉对比）的流程测试：真实快照 + MockLLM，不调用外部 API。

A 组的“虚构数字”是人工构造的（synthetic），用来验证统计与抽检流程本身是否正确，不代表任何真实模型的表现。
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time

import httpx
import pytest

from finsight.analytics.diagnosis import diagnose
from finsight.cli import main as cli_main
from finsight.config import LLMSettings, load_settings
from finsight.data.service import load_service
from finsight.evaluation import ablation
from finsight.llm import gateway as gw
from finsight.llm.gateway import LLMError, LLMResponse, MockLLM, OpenAICompatibleClient
from finsight.reporting.report import build_context

SNAP = load_settings().snapshot_dir
pytestmark = pytest.mark.skipif(not (SNAP / "manifest.jsonl").exists(), reason="没有数据快照")

A_TEXT = ("## 核心结论\n公司2025年净利润约 888.88 亿元，资产负债率仅 12.34%，毛利率 77.77%。\n"
          "## 主要风险\n应收账款周转天数 456 天，存在回款压力。\n")


@pytest.fixture(scope="module")
def diag():
    svc = load_service(SNAP)
    if "600519" not in svc.available_symbols():
        pytest.skip("没有 600519 快照")
    return diagnose(svc.company("600519"))


def diag_of_factory(diag):
    return lambda code: diag


def _drafts(diag) -> tuple[str, str]:
    ctx = build_context(diag)
    period = diag.periods[0].end
    ref = ctx.id_to_ref[f"m:gross_margin@{period}"]
    shown = f"{diag.evidence[f'm:gross_margin@{period}'].value * 100:.2f}%"
    good = f"毛利率为 {shown}[{ref}]。"
    return f"## 核心结论\n{good}净资产收益率高达 45.6%，远超同业。", f"## 核心结论\n{good}"


def test_three_groups_on_one_company(diag):
    draft, fixed = _drafts(diag)
    rec = ablation.run_company(diag, MockLLM(script=[A_TEXT, draft, fixed]), model="mock-llm")
    assert rec["C"]["revised"] and rec["C"]["draft_markdown"]
    s = ablation.score_company(rec, diag)
    assert s["A"]["total"] >= 4 and s["A"]["problems"] >= 3  # 虚构的数字在该公司证据里找不到归属
    assert s["B"]["problems"] == 1 and s["B"]["unverified"] == 1  # 初稿里那个无证据的 45.6%
    assert s["C"]["problems"] == 0  # 终稿通过核验
    assert s["B"]["total"] == s["C"]["total"] + 1  # 修订删掉了那个数字
    summ = ablation.summarize([s])
    assert summ["revised_reports"] == 1 and summ["groups"]["B"]["problem_rate"] > summ["groups"]["C"]["problem_rate"] == 0
    assert summ["groups"]["A"]["problem_rate"] > summ["groups"]["B"]["problem_rate"]


def test_pure_prompt_gives_no_evidence_and_no_numbers(diag):
    llm = MockLLM(script=[A_TEXT, *_drafts(diag)])
    ablation.run_company(diag, llm, model="mock-llm")
    pure = llm.requests[0]["messages"]
    assert [m["role"] for m in pure] == ["system", "user"]
    assert "【证据清单】" not in pure[1]["content"]
    assert "%" not in pure[1]["content"] and "亿元" not in pure[1]["content"]  # 提示词里没有任何财务数字可抄
    assert "【证据清单】" in llm.requests[1]["messages"][1]["content"]  # C 组才给证据


def test_budget_guard_stops_remaining_companies(diag, tmp_path):
    def responder(messages, tools):
        raise LLMError("费用保护：已估算花费 8.9 元，将超过预算 9.00 元，已停止发送")

    res = ablation.run_all(["600519", "000858", "000568"], diag_of_factory(diag), MockLLM(responder=responder),
                           tmp_path / "ab", model="mock-llm", workers=1, log=lambda m: None)
    assert res["stopped_by_budget"]
    assert res["outcome"]["600519"] == "error" and res["outcome"]["000858"] == "not_started"
    assert not (tmp_path / "ab" / "companies").exists()


def test_audit_sheet_is_blind_generated_once_and_scored(diag, tmp_path):
    draft, fixed = _drafts(diag)
    out = tmp_path / "ab"
    ablation.run_all(["600519"], diag_of_factory(diag), MockLLM(script=[A_TEXT, draft, fixed]), out, model="mock-llm",
                     workers=1, log=lambda m: None)
    diag_of = diag_of_factory(diag)

    ablation.score_dir(out, diag_of, make_audit=False)  # 没跑完时不生成抽检表
    assert (out / "ablation_report.md").exists() and not (out / "audit_key.json").exists()

    ablation.score_dir(out, diag_of)
    sheet, keyfile = out / "audit_sheet.csv", out / "audit_key.json"
    key = json.loads(keyfile.read_text(encoding="utf-8"))
    with sheet.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(key) > 5 and {g for k in key.values() for g in k["groups"]} == {"A", "B", "C"}
    header = " ".join(rows[0].keys())
    assert "组" not in header and "核验" not in header  # 盲审：抽检表里没有组别和核验器结论
    for r in rows:
        assert r["待判断的数字"]
    shared = [s for s, k in key.items() if k["groups"] == ["B", "C"]]
    assert shared and len({(k["code"], k["sentence"], k["number"]) for s, k in key.items() if s in shared}) == len(shared)
    key_text = keyfile.read_text(encoding="utf-8")

    # 模拟“人工”：核验器标问题的判错，其余判对，再有一个“不确定”
    sheet_text = []
    flagged = {s for s, k in key.items() if k["verifier_status"] in ablation.PROBLEM}
    for i, r in enumerate(rows):
        r["判定（填：对 / 错 / 不确定）"] = "不确定" if i == 0 else "错" if r["编号"] in flagged else "对"
        sheet_text.append(r)
    filled = tmp_path / "filled.csv"
    with filled.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(sheet_text)

    res = ablation.score_dir(out, diag_of, audit_file=filled)
    assert keyfile.read_text(encoding="utf-8") == key_text  # 抽检样本不会因为重新打分而改变
    audit = res["audit"]
    assert audit["filled"] == len(rows)
    assert audit["groups"]["A"]["wrong"] > 0 and audit["groups"]["C"]["wrong"] == 0
    assert sum(audit["groups"][g]["unsure_or_blank"] for g in "ABC") >= 1
    v = audit["verifier_vs_human"]
    assert v["fp"] == 0 and v["fn"] == 0 and v["tp"] > 0
    assert "人工抽检（独立口径）" in (out / "ablation_report.md").read_text(encoding="utf-8")


def test_wilson_interval():
    lo, hi = ablation.wilson(0, 10)
    assert lo == 0.0 and 0.25 < hi < 0.35
    lo, hi = ablation.wilson(5, 10)
    assert 0.2 < lo < 0.25 and 0.75 < hi < 0.8
    assert ablation.wilson(0, 0)[0] != ablation.wilson(0, 0)[0]  # NaN


def test_cli_score_and_missing_key(diag, tmp_path, monkeypatch, capsys):
    draft, fixed = _drafts(diag)
    out = tmp_path / "ab"
    ablation.run_all(["600519"], diag_of_factory(diag), MockLLM(script=[A_TEXT, draft, fixed]), out, model="mock-llm",
                     workers=1, log=lambda m: None)
    assert cli_main(["eval", "ablation-score", str(out)]) == 0
    assert "三组" in capsys.readouterr().out and (out / "audit_sheet.csv").exists()

    class NoKey:
        enabled = False
        provider = "kimi"

    monkeypatch.setattr("finsight.config.llm_settings", lambda provider=None: NoKey())
    assert cli_main(["eval", "ablation", "--out", str(tmp_path / "x")]) == 1  # 没有 Key：不会调用任何接口
    assert "未配置" in capsys.readouterr().out


# ------------------------------------------------------------------ 费用账本 / 断点续跑 / 无效输出（评审指出的问题）

class Killed(BaseException):
    """模拟进程在请求途中被杀（BaseException：不会被 `except Exception` 吞掉）。"""


def _run(diag, llm, out, **kw):
    kw.setdefault("workers", 1)
    kw.setdefault("log", lambda m: None)
    model = kw.pop("model", "mock-llm")
    return ablation.run_all(["600519"], diag_of_factory(diag), llm, out, model=model, **kw)


def _real_client(handler) -> OpenAICompatibleClient:
    cfg = LLMSettings(provider="kimi", base_url="https://llm.test/v1", api_key="sk-test", model="kimi-k3.1-pro",
                      reasoning_effort="low")
    return OpenAICompatibleClient(cfg, transport=httpx.MockTransport(handler), backoff_s=0)


def _chat_ok(text, usage=None, finish="stop") -> httpx.Response:
    return httpx.Response(200, json={"model": "kimi-k3.1-pro", "usage": usage or {"prompt_tokens": 2000, "completion_tokens": 1500},
                                     "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                                  "finish_reason": finish}]})


def _is_a(request: httpx.Request) -> bool:
    return json.loads(request.content)["messages"][0]["content"] == ablation.PURE_SYSTEM


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setattr(gw, "BUDGET", gw.BudgetGuard())  # 每个测试一个新的进程级保护，互不影响
    monkeypatch.delenv("LLM_BUDGET_CNY", raising=False)
    return gw.BUDGET


def test_run_all_saves_results_and_resumes_without_new_requests(diag, tmp_path):
    draft, fixed = _drafts(diag)
    out = tmp_path / "ab"
    logs: list[str] = []
    res = _run(diag, MockLLM(script=[A_TEXT, draft, fixed]), out, log=logs.append)
    assert res["outcome"] == {"600519": "done"} and not res["stopped_by_budget"]
    assert (out / "companies" / "600519.json").exists() and not list(out.rglob("*.tmp")) and not (out / "run.lock").exists()
    from finsight.samples import build_report_sample  # 导出的 C 组简报可直接入库为缓存样例
    sample = build_report_sample(json.loads((out / "reports" / "600519.json").read_text(encoding="utf-8")), diag,
                                 source="docs/eval/ablation-test/reports/600519.json")
    assert sample["kind"] == "report" and sample["code"] == "600519"
    ledger = ablation.read_ledger(out)
    assert [a["status"] for a in ledger["attempts"]] == ["done"]
    again = MockLLM(script=[])  # 已完成的不再发送任何请求
    assert _run(diag, again, out, log=logs.append)["outcome"] == {"600519": "done"} and not again.requests
    assert any("跳过" in m for m in logs)


def test_every_request_is_journaled_before_sending_and_failures_are_charged(diag, tmp_path, guard):
    """A 成功后 C 的各次尝试都 HTTP 500（服务端拒绝，不计费）：账本 = A 的真实用量；每次发送都有记录。"""
    out, seen = tmp_path / "ab", []

    def handler(request):
        # 发送时账本里这次请求的 reserve 必须已经落盘
        seen.append(ablation.read_ledger(out)["requests"])
        return _chat_ok(A_TEXT) if _is_a(request) else httpx.Response(500, text="boom")

    res = _run(diag, _real_client(handler), out)
    assert res["outcome"] == {"600519": "degraded"}
    assert seen == [1, 2, 3, 4, 5]  # A 一次 + C 重试 4 次，每次发送时账本都已多一条
    led = ablation.read_ledger(out)
    assert led["requests"] == 5 and not led["unsettled_requests"] and not led["corrupt"]
    a_cost = gw.estimate_cost_cny("kimi-k3.1-pro", {"prompt_tokens": 2000, "completion_tokens": 1500})
    assert led["total_cost_cny_est"] == pytest.approx(a_cost) == pytest.approx(guard.spent)
    stages = [json.loads(line).get("stage") for line in (out / "ledger.jsonl").read_text("utf-8").splitlines()
              if json.loads(line)["ev"] == "reserve"]
    assert stages == ["A", "C", "C", "C", "C"]


def test_timeout_is_charged_at_upper_bound(diag, tmp_path, guard):
    n = {"c": 0}

    def handler(request):
        if _is_a(request):
            return _chat_ok(A_TEXT)
        n["c"] += 1
        if n["c"] == 1:
            raise httpx.ReadTimeout("timed out", request=request)  # 服务端可能已经计费
        return _chat_ok(_drafts(diag)[1])

    _run(diag, _real_client(handler), tmp_path / "ab")
    led = ablation.read_ledger(tmp_path / "ab")
    a_cost = gw.estimate_cost_cny("kimi-k3.1-pro", {"prompt_tokens": 2000, "completion_tokens": 1500})
    assert led["total_cost_cny_est"] == pytest.approx(guard.spent) and led["total_cost_cny_est"] > 2 * a_cost
    settles = [json.loads(line) for line in (tmp_path / "ab" / "ledger.jsonl").read_text("utf-8").splitlines()
               if json.loads(line)["ev"] == "settle"]
    assert [s["exact"] for s in settles] == [True, False, True]  # 超时那次不是精确值


def test_killed_mid_request_keeps_reservation_and_resume_reuses_finished_stage(diag, tmp_path, guard, monkeypatch):
    out = tmp_path / "ab"

    def handler(request):
        if _is_a(request):
            return _chat_ok(A_TEXT)
        raise Killed()  # C 的请求发出后进程被杀：没有结算

    with pytest.raises(Killed):
        _run(diag, _real_client(handler), out)
    assert not (out / "run.lock").exists()  # 锁随运行结束释放
    led = ablation.read_ledger(out)
    assert len(led["unsettled_requests"]) == 1 and led["unsettled_requests"][0]["stage"] == "C"
    assert led["unsettled_cny"] > 0 and led["total_cost_cny_est"] == pytest.approx(led["settled_cny"] + led["unsettled_cny"])
    assert (out / "partial" / "600519.json").exists() and not (out / "companies").exists()

    # 恢复：新进程、新的保护；A 组结果直接复用（不再发 A 请求），未结算的占用计入累计
    monkeypatch.setattr(gw, "BUDGET", gw.BudgetGuard())
    sent = []

    def handler2(request):
        sent.append(_is_a(request))
        return _chat_ok(_drafts(diag)[1])

    logs: list[str] = []
    res = _run(diag, _real_client(handler2), out, budget=50.0, log=logs.append)
    assert res["outcome"] == {"600519": "done"} and sent == [False]
    assert any("没有结算记录" in m for m in logs)
    assert ablation.read_ledger(out)["unsettled_requests"]  # 上次那笔仍保留，直到用户对照账单
    assert "LLM_BUDGET_CNY" not in os.environ  # 进程环境不留尾巴


def test_budget_is_cumulative_per_directory_across_runs(diag, tmp_path, guard):
    out = tmp_path / "ab"
    out.mkdir()
    j = ablation.Journal(out / "ledger.jsonl")
    j.append({"ev": "reserve", "id": "x1", "reserved": 9.6, "code": "600519", "stage": "C"})
    j.append({"ev": "settle", "id": "x1", "charged": 9.5, "exact": True})
    never = MockLLM(responder=lambda m, t: (_ for _ in ()).throw(AssertionError("预算已用完，不应发送请求")))
    with pytest.raises(ablation.BudgetExhausted, match="9.50"):
        _run(diag, never, out, budget=9.0)
    # 预算还剩一点：进程内保护按“剩余额度”设置，更大的请求会在发出前被拒绝
    j.append({"ev": "reserve", "id": "x2", "reserved": 0.0, "code": "600519", "stage": "C"})
    sent = []

    def handler(request):
        sent.append(1)
        return _chat_ok(A_TEXT)

    res = _run(diag, _real_client(handler), out, budget=9.51)
    assert res["stopped_by_budget"] and not sent and res["outcome"]["600519"] == "error"


def test_ledger_write_failure_blocks_sending(diag, tmp_path, guard, monkeypatch):
    sent = []

    def handler(request):
        sent.append(1)
        return _chat_ok(A_TEXT)

    def boom(self, rec):
        raise OSError("disk full")

    monkeypatch.setattr(ablation.Journal, "append", boom)
    _run(diag, _real_client(handler), tmp_path / "ab")
    assert not sent  # 账本写不进去，一个请求也不发


def test_corrupt_ledger_refuses_but_torn_last_line_is_ignored(diag, tmp_path, guard):
    out = tmp_path / "ab"
    out.mkdir()
    ok_line = json.dumps({"ev": "reserve", "id": "a", "reserved": 0.1})
    (out / "ledger.jsonl").write_text(ok_line + '\n{"ev": "settle", "id": "a", "char', encoding="utf-8")  # 最后一行写了一半
    assert not ablation.read_ledger(out)["corrupt"]
    (out / "ledger.jsonl").write_text(ok_line + "\n乱码一行\n" + ok_line.replace('"a"', '"b"') + "\n", encoding="utf-8")
    assert ablation.read_ledger(out)["corrupt"]
    with pytest.raises(RuntimeError, match="损坏"):
        _run(diag, MockLLM(script=[]), out)


def test_two_runs_cannot_share_a_directory_and_stale_lock_is_taken_over(tmp_path):
    out = tmp_path / "ab"
    with ablation.RunLock(out):
        with pytest.raises(ablation.RunBusy):
            with ablation.RunLock(out):
                pass
    assert not (out / "run.lock").exists()
    (out / "run.lock").write_text("pid=1", encoding="utf-8")
    old = time.time() - 600
    os.utime(out / "run.lock", (old, old))  # 遗留的旧锁（进程崩溃）
    with ablation.RunLock(out, stale_s=120):
        assert (out / "run.lock").exists()
    (out / "run.lock").write_text("pid=1", encoding="utf-8")  # 刚更新过的锁 = 另一个运行还活着
    with pytest.raises(ablation.RunBusy):
        with ablation.RunLock(out):
            pass


def test_empty_or_truncated_a_is_invalid_and_c_is_not_run(diag, tmp_path):
    for bad in (LLMResponse(content="", finish_reason="stop"), LLMResponse(content="半截内容", finish_reason="length")):
        out = tmp_path / f"ab{bool(bad.content)}"
        llm = MockLLM(script=[bad])
        res = _run(diag, llm, out)
        assert res["outcome"] == {"600519": "a_invalid"} and len(llm.requests) == 1  # 没有再为 C 组花钱
        assert (out / "failed" / "600519.json").exists() and not (out / "companies").exists()
        assert not (out / "partial" / "600519.json").exists()  # 无效的 A 不保存，下次重新发 A


def test_degraded_c_keeps_a_and_retry_only_reruns_c(diag, tmp_path):
    out, calls = tmp_path / "ab", {"n": 0}

    def responder(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return A_TEXT
        raise LLMError("模拟：服务暂时不可用")  # C 组失败 → generate_report 降级为模板

    res = _run(diag, MockLLM(responder=responder), out)
    assert res["outcome"] == {"600519": "degraded"}
    assert (out / "failed" / "600519.json").exists() and (out / "partial" / "600519.json").exists()
    assert not (out / "companies").exists()
    draft, fixed = _drafts(diag)
    retry = MockLLM(script=[draft, fixed])  # 只剩 C 组的两次请求；A 不会再发
    assert _run(diag, retry, out)["outcome"] == {"600519": "done"}
    assert len(retry.requests) == 2 and "【证据清单】" in retry.requests[0]["messages"][1]["content"]
    assert [a["status"] for a in ablation.read_ledger(out)["attempts"]] == ["degraded", "done"]


def test_truncated_c_is_excluded_and_revision_failure_is_flagged(diag, tmp_path):
    draft, fixed = _drafts(diag)
    out = tmp_path / "trunc"
    res = _run(diag, MockLLM(script=[A_TEXT, LLMResponse(content=fixed, finish_reason="length")]), out)
    assert res["outcome"] == {"600519": "c_truncated"} and (out / "failed" / "600519.json").exists()

    out2, n = tmp_path / "revfail", {"n": 0}

    def responder(messages, tools):
        n["n"] += 1
        if n["n"] == 1:
            return A_TEXT
        if n["n"] == 2:
            return draft  # 初稿含无证据数字，触发修订
        raise LLMError("模拟：修订请求失败")

    assert _run(diag, MockLLM(responder=responder), out2)["outcome"] == {"600519": "done"}  # 初稿保留，仍是有效记录
    r = ablation.score_dir(out2, diag_of_factory(diag), make_audit=False)
    assert r["summary"]["revision_failed_reports"] == 1 and r["per_company"][0]["B"]["problems"] == 1


def test_score_dir_excludes_invalid_records_and_states_prompt_difference(diag, tmp_path):
    draft, fixed = _drafts(diag)
    out = tmp_path / "ab"
    _run(diag, MockLLM(script=[A_TEXT, draft, fixed]), out)
    bad = json.loads((out / "companies" / "600519.json").read_text(encoding="utf-8"))
    bad.update(code="000858", name="五粮液")
    bad["A"]["text"] = ""  # 手工放进去的无效记录（A 为空）
    (out / "companies" / "000858.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    r = ablation.score_dir(out, diag_of_factory(diag), make_audit=False)
    assert r["summary"]["companies"] == 1 and r["summary"]["excluded"][0]["code"] == "000858"
    report = (out / "ablation_report.md").read_text(encoding="utf-8")
    assert "提示词不同" in report and "000858" in report and "同一提示词" not in report
    meta = json.loads((out / "ablation.json").read_text(encoding="utf-8"))["meta"]
    assert meta["prompts"]["A_system"] == ablation.PURE_SYSTEM and meta["prompts"]["C_system_sha256"]
    (out / "companies" / "600519.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(Exception, match="有效记录"):
        ablation.score_dir(out, diag_of_factory(diag), make_audit=False)


def test_cli_refuses_when_directory_budget_is_used_up(diag, tmp_path, monkeypatch, capsys):
    out = tmp_path / "ab"
    out.mkdir()
    j = ablation.Journal(out / "ledger.jsonl")
    j.append({"ev": "reserve", "id": "x1", "reserved": 9.6})
    j.append({"ev": "settle", "id": "x1", "charged": 9.5, "exact": True})

    class Cfg:
        enabled = True
        provider = "kimi"
        model = "kimi-k3.1-pro"

    never = MockLLM(responder=lambda m, t: (_ for _ in ()).throw(AssertionError("预算已用完，不应发送请求")))
    monkeypatch.setattr("finsight.config.llm_settings", lambda provider=None: Cfg())
    monkeypatch.setattr("finsight.llm.gateway.make_client", lambda cfg: never)
    assert cli_main(["eval", "ablation", "--out", str(out), "--budget", "9"]) == 1
    assert "不再运行" in capsys.readouterr().out and not never.requests


# ------------------------------------------------------------------ 第二轮复核（eee3ad5）指出的边界问题

def test_torn_ledger_tail_is_cleared_so_new_records_are_not_swallowed(tmp_path):
    out = tmp_path / "ab"
    out.mkdir()
    reserve = json.dumps({"ev": "reserve", "id": "a", "reserved": 0.1, "code": "600519", "stage": "A"})
    (out / "ledger.jsonl").write_text(reserve + "\n" + '{"ev": "settle", "id": "a", "char', encoding="utf-8")  # 崩溃时写了一半
    j = ablation.Journal(out / "ledger.jsonl")  # 打开账本时清走残片
    j.append({"ev": "reserve", "id": "b", "reserved": 0.2})
    j.append({"ev": "settle", "id": "b", "charged": 0.05, "exact": True})
    led = ablation.read_ledger(out)
    assert not led["corrupt"] and led["requests"] == 2
    assert led["total_cost_cny_est"] == pytest.approx(0.1 + 0.05)  # a 未结算按上界，b 按实际
    assert "char" in (out / "ledger.torn").read_text(encoding="utf-8")  # 残片留档，没有悄悄丢掉


def test_complete_record_without_trailing_newline_is_kept(tmp_path):
    out = tmp_path / "ab"
    out.mkdir()
    # 刚落盘的 reserve 只差结尾换行：它可能对应一个已经发出的请求，必须保留
    (out / "ledger.jsonl").write_text(json.dumps({"ev": "reserve", "id": "a", "reserved": 0.3}), encoding="utf-8")
    assert ablation.read_ledger(out)["total_cost_cny_est"] == pytest.approx(0.3)
    j = ablation.Journal(out / "ledger.jsonl")
    j.append({"ev": "reserve", "id": "b", "reserved": 0.2})
    led = ablation.read_ledger(out)
    assert not led["corrupt"] and led["requests"] == 2 and led["total_cost_cny_est"] == pytest.approx(0.5)
    assert not (out / "ledger.torn").exists()


def test_garbage_last_line_with_newline_is_corruption_not_a_torn_tail(tmp_path):
    out = tmp_path / "ab"
    out.mkdir()
    (out / "ledger.jsonl").write_text(json.dumps({"ev": "reserve", "id": "a", "reserved": 0.1}) + "\n乱码\n", encoding="utf-8")
    assert ablation.read_ledger(out)["corrupt"]


def test_run_all_recovers_from_torn_tail_and_keeps_counting(diag, tmp_path, guard):
    out = tmp_path / "ab"
    out.mkdir()
    (out / "ledger.jsonl").write_text(json.dumps({"ev": "reserve", "id": "old", "reserved": 0.4}) + "\n" + '{"ev": "settle", "id"',
                                      encoding="utf-8")
    res = _run(diag, _real_client(lambda r: _chat_ok(_drafts(diag)[1]) if not _is_a(r) else _chat_ok(A_TEXT)), out)
    assert res["outcome"] == {"600519": "done"}
    led = ablation.read_ledger(out)
    assert not led["corrupt"] and led["requests"] == 3  # 旧的一笔 + A + C
    assert led["total_cost_cny_est"] == pytest.approx(0.4 + guard.spent)  # 旧的未结算按上界计入


def test_settle_write_failure_stops_sending_and_keeps_memory_equal_to_ledger(diag, tmp_path, guard, monkeypatch):
    orig, sent = ablation.Journal.append, []

    def flaky(self, rec):
        if rec.get("ev") == "settle":
            raise OSError("disk full")
        return orig(self, rec)

    monkeypatch.setattr(ablation.Journal, "append", flaky)

    def handler(request):
        sent.append(_is_a(request))
        return _chat_ok(A_TEXT)

    out = tmp_path / "ab"
    res = _run(diag, _real_client(handler), out)
    assert sent == [True]  # A 发出后结算记录写不进账本：后面的请求一律不发
    assert guard.fault and "无法写入账本" in guard.fault and res["stopped_by_budget"]
    led = ablation.read_ledger(out)
    assert len(led["unsettled_requests"]) == 1  # 账本里 A 只有占用、没有结算 → 按上界计
    assert guard.spent == pytest.approx(led["total_cost_cny_est"]) and guard.pending == 0  # 进程内与账本一致
    assert (out / "partial" / "600519.json").exists()  # A 的结果已经拿到，不浪费：下次只补 C


def test_hard_killed_process_leaves_recoverable_state(diag, tmp_path, monkeypatch):
    """真正终止一个子进程（os._exit：不跑任何清理代码），再从遗留的账本 / 锁 / 部分结果恢复。
    这是 Linux 上的真实进程终止；Windows 上的表现没有验证。"""
    out = tmp_path / "ab"
    script = """
import json, os, sys
from pathlib import Path
import httpx
from finsight.analytics.diagnosis import diagnose
from finsight.config import LLMSettings, load_settings
from finsight.data.service import load_service
from finsight.evaluation import ablation
from finsight.llm.gateway import OpenAICompatibleClient

out, a_text = Path(sys.argv[1]), sys.argv[2]
diag = diagnose(load_service(load_settings().snapshot_dir).company("600519"))

def handler(request):
    if json.loads(request.content)["messages"][0]["content"] == ablation.PURE_SYSTEM:
        return httpx.Response(200, json={"model": "kimi-k3.1-pro", "usage": {"prompt_tokens": 2000, "completion_tokens": 1500},
                                         "choices": [{"index": 0, "message": {"role": "assistant", "content": a_text},
                                                      "finish_reason": "stop"}]})
    os._exit(7)  # C 组请求发出后进程被终止

cfg = LLMSettings(provider="kimi", base_url="https://llm.test/v1", api_key="sk-test", model="kimi-k3.1-pro", reasoning_effort="low")
llm = OpenAICompatibleClient(cfg, transport=httpx.MockTransport(handler), backoff_s=0)
ablation.run_all(["600519"], lambda c: diag, llm, out, model="kimi-k3.1-pro", workers=1, log=lambda m: None)
"""
    proc = subprocess.run([sys.executable, "-c", script, str(out), A_TEXT], capture_output=True, text=True, timeout=240)
    assert proc.returncode == 7, proc.stderr[-800:]
    assert (out / "run.lock").exists()  # 没来得及清理：遗留的锁
    led = ablation.read_ledger(out)
    assert len(led["unsettled_requests"]) == 1 and led["unsettled_requests"][0]["stage"] == "C" and not led["corrupt"]
    assert (out / "partial" / "600519.json").exists() and not (out / "companies").exists()

    with pytest.raises(ablation.RunBusy):  # 锁刚刚还有心跳：此刻不能抢
        _run(diag, MockLLM(script=[]), out)
    old = time.time() - 600
    os.utime(out / "run.lock", (old, old))  # 过了心跳超时：视为遗留，可以接管
    monkeypatch.setattr(gw, "BUDGET", gw.BudgetGuard())
    sent, logs = [], []

    def handler2(request):
        sent.append(_is_a(request))
        return _chat_ok(_drafts(diag)[1])

    res = _run(diag, _real_client(handler2), out, budget=50.0, log=logs.append, model="kimi-k3.1-pro")
    assert res["outcome"] == {"600519": "done"} and sent == [False]  # 只补 C，A 不重发
    assert any("没有结算记录" in m for m in logs) and len(ablation.read_ledger(out)["unsettled_requests"]) == 1


def test_truncated_revision_is_not_adopted_and_is_flagged(diag, tmp_path):
    draft, fixed = _drafts(diag)
    out = tmp_path / "ab"
    llm = MockLLM(script=[A_TEXT, draft, LLMResponse(content=fixed, finish_reason="length")])  # 修订稿被截断
    res = _run(diag, llm, out)
    assert res["outcome"] == {"600519": "done"}  # 初稿完整，仍是有效记录
    c = json.loads((out / "companies" / "600519.json").read_text(encoding="utf-8"))["C"]
    assert not c["revised"] and not c["truncated"] and c["verification"]["unverified"] == 1  # 采用的仍是初稿
    assert any("修订稿" in w for w in c["warnings"])
    r = ablation.score_dir(out, diag_of_factory(diag), make_audit=False)
    assert r["summary"]["revision_failed_reports"] == 1 and r["per_company"][0]["B"]["problems"] == 1
