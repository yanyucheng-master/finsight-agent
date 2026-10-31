"""评测②（三组数字幻觉对比）的流程测试：真实快照 + MockLLM，不调用外部 API。

A 组的“虚构数字”是人工构造的（synthetic），用来验证统计与抽检流程本身是否正确，不代表任何真实模型的表现。
"""

from __future__ import annotations

import csv
import json

import pytest

from finsight.analytics.diagnosis import diagnose
from finsight.cli import main as cli_main
from finsight.config import load_settings
from finsight.data.service import load_service
from finsight.evaluation import ablation
from finsight.llm.gateway import LLMError, MockLLM
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


def test_run_all_saves_ledger_and_resumes(diag, tmp_path):
    draft, fixed = _drafts(diag)
    out = tmp_path / "ab"
    logs: list[str] = []
    res = ablation.run_all(["600519"], diag_of_factory(diag), MockLLM(script=[A_TEXT, draft, fixed]), out,
                           model="mock-llm", workers=1, log=logs.append)
    assert res["outcome"] == {"600519": "done"} and not res["stopped_by_budget"]
    assert (out / "companies" / "600519.json").exists() and not list((out / "companies").glob("*.tmp"))
    # 导出的 C 组简报可以直接入库为缓存样例（入库时用当前核验器重新核验）
    from finsight.samples import build_report_sample
    sample = build_report_sample(json.loads((out / "reports" / "600519.json").read_text(encoding="utf-8")), diag,
                                 source="docs/eval/ablation-test/reports/600519.json")
    assert sample["kind"] == "report" and sample["code"] == "600519"
    ledger = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
    assert len(ledger["attempts"]) == 1 and ledger["total_cost_cny_est"] == res["total_cost_cny_est"] >= 0
    # 断点续跑：已完成的不再调用模型（脚本为空，一调用就会抛 LLMError）
    again = ablation.run_all(["600519"], diag_of_factory(diag), MockLLM(script=[]), out, model="mock-llm", workers=1,
                             log=logs.append)
    assert again["outcome"] == {"600519": "done"} and len(json.loads((out / "ledger.json").read_text("utf-8"))["attempts"]) == 1
    assert any("跳过" in m for m in logs)


def test_failed_company_goes_to_failed_dir_and_is_retried(diag, tmp_path):
    out = tmp_path / "ab"
    calls = {"n": 0}

    def responder(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return A_TEXT  # A 组成功
        raise LLMError("模拟：服务暂时不可用")  # C 组失败 → generate_report 降级为模板

    res = ablation.run_all(["600519"], diag_of_factory(diag), MockLLM(responder=responder), out, model="mock-llm",
                           workers=1, log=lambda m: None)
    assert res["outcome"] == {"600519": "degraded"}
    assert (out / "failed" / "600519.json").exists() and not (out / "companies").exists()
    # 下次运行会重试（不会因为 failed/ 里有记录就跳过）
    draft, fixed = _drafts(diag)
    res2 = ablation.run_all(["600519"], diag_of_factory(diag), MockLLM(script=[A_TEXT, draft, fixed]), out,
                            model="mock-llm", workers=1, log=lambda m: None)
    assert res2["outcome"] == {"600519": "done"}
    assert [a["status"] for a in json.loads((out / "ledger.json").read_text("utf-8"))["attempts"]] == ["degraded", "done"]


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


def test_budget_is_cumulative_per_directory(diag, tmp_path, monkeypatch, capsys):
    out = tmp_path / "ab"
    out.mkdir()
    (out / "ledger.json").write_text(json.dumps({"attempts": [], "total_cost_cny_est": 9.5}), encoding="utf-8")

    class Cfg:
        enabled = True
        provider = "kimi"
        model = "kimi-k3.1-pro"

    monkeypatch.setattr("finsight.config.llm_settings", lambda provider=None: Cfg())

    def never(*a, **k):
        raise AssertionError("预算已用完，不应创建客户端")

    monkeypatch.setattr("finsight.llm.gateway.make_client", never)
    assert cli_main(["eval", "ablation", "--out", str(out), "--budget", "9"]) == 1
    assert "不再运行" in capsys.readouterr().out
