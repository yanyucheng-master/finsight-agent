#!/usr/bin/env python3
"""本机大模型冒烟测试：连通性（含工具调用往返）→ 可验证简报 → 研究问答。

结果写入 data/runs/（控制台输出同时写入 smoke_console.log），供开发会话读取复核。
用法：python scripts/llm_smoke.py [--provider deepseek]
"""
import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "data" / "runs"
sys.path.insert(0, str(ROOT / "backend"))

QUESTIONS = [
    ("600519", "茅台近三年的利润现金含量如何？和五粮液、泸州老窖相比毛利率怎么样？"),
    ("000002", "万科目前最主要的偿债风险是什么？请给出依据。"),
]


class Tee:
    """同时写控制台和日志文件（这样双击运行时能看到实时进度，结束后也有完整日志）。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="kimi")
    ap.add_argument("--budget", type=float, default=3.0, help="本次运行的费用上限（元，客户端估算），0 表示不限")
    args = ap.parse_args()
    os.environ["LLM_BUDGET_CNY"] = str(args.budget)
    # 冒烟测试把单次输出上限降到 8192（实测一份简报含思考约 3–5 千 token），使逐请求预留更贴近实际
    os.environ.setdefault("LLM_MAX_TOKENS", "8192")
    RUNS.mkdir(parents=True, exist_ok=True)
    log = open(RUNS / "smoke_console.log", "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log)
    sys.stderr = Tee(sys.__stderr__, log)
    print(f"开始：{time.strftime('%Y-%m-%d %H:%M:%S')}，Python {sys.version.split()[0]}（{sys.executable}）")
    try:
        return run(args.provider)
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        print(f"结束：{time.strftime('%Y-%m-%d %H:%M:%S')}")
        log.close()


def run(provider: str) -> int:
    from finsight.agent.research_agent import ResearchAgent
    from finsight.analytics.diagnosis import diagnose
    from finsight.cli import main as cli_main
    from finsight.config import llm_settings, load_settings
    from finsight.data.service import CompanyNotFound, load_service
    from finsight.llm.gateway import make_client
    from finsight.reporting.report import generate_report

    from finsight.llm.gateway import BUDGET

    failures: list[str] = []
    cfg = llm_settings(provider)
    summary = {"provider": provider, "config": cfg.public(), "started": time.strftime("%Y-%m-%d %H:%M:%S"),
               "checks": {}, "items": []}
    print("\n== 1/3 连通性检查（对话 + 工具调用往返）==")
    for prov in ("deepseek", "kimi"):
        passed = cli_main(["llm-check", "--provider", prov]) == 0
        summary["checks"][prov] = "通过" if passed else "未通过"
        if not passed:
            failures.append(f"{prov} 连通性检查未通过")

    if not cfg.enabled:
        print(f"未配置 {provider} 的 Key，跳过后续步骤（请先运行 scripts\\setup_env.bat）")
        return 1
    llm = make_client(cfg)
    svc = load_service(load_settings().snapshot_dir)

    def peers(code):
        try:
            return diagnose(svc.company(code))
        except CompanyNotFound:
            return None

    print(f"\n== 2/3 可验证研究简报（{cfg.model}，推理强度 {cfg.reasoning_effort}；每份约 0.5–2 分钟）==")
    for code in ("600519", "000002", "600518"):
        print(f"生成 {code} ……")
        try:
            rep = generate_report(diagnose(svc.company(code)), llm=llm, mode="llm")
            (RUNS / f"report_{code}_{provider}.json").write_text(rep.model_dump_json(indent=1), encoding="utf-8")
            v = rep.verification
            line = (f"简报 {code}：模式 {rep.mode}，尝试 {rep.attempts} 次，数字 {v.total} 个，通过 {v.verified}，"
                    f"无证据 {v.unverified}，不一致 {v.mismatch}，耗时 {rep.elapsed_ms} ms，用量 {rep.usage}"
                    + (f"，警告 {rep.warnings}" if rep.warnings else ""))
            if rep.mode != "llm":
                failures.append(f"简报 {code} 降级为模板")
            elif not v.passed:
                failures.append(f"简报 {code} 仍有未通过核验的内容")
        except Exception as exc:
            line = f"简报 {code} 失败：{exc}"
            failures.append(line)
            traceback.print_exc()
        print(line)
        summary["items"].append(line)

    print("\n== 3/3 研究问答智能体 ==")
    for i, (code, q) in enumerate(QUESTIONS, 1):
        print(f"提问 {i}（{code}）：{q}")
        try:
            ans = ResearchAgent(diagnose(svc.company(code)), llm, peer_loader=peers).ask(q)
            (RUNS / f"ask_{i}_{code}_{provider}.json").write_text(ans.model_dump_json(indent=1), encoding="utf-8")
            v = ans.verification
            line = (f"问答 {i}（{code}）：工具调用 {len(ans.steps)} 次，数字 {v.total} 个，通过 {v.verified}，"
                    f"无证据 {v.unverified}，不一致 {v.mismatch}，耗时 {ans.elapsed_ms} ms，用量 {ans.usage}"
                    + (f"，警告 {ans.warnings}" if ans.warnings else ""))
            if ans.attempts == 0 or not ans.answer:
                failures.append(f"问答 {i} 调用失败")
            elif not v.passed:
                failures.append(f"问答 {i} 仍有未通过核验的内容")
        except Exception as exc:
            line = f"问答 {i} 失败：{exc}"
            failures.append(line)
            traceback.print_exc()
        print(line)
        summary["items"].append(line)

    summary["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary["cost_cny_est"] = round(BUDGET.spent, 6)
    summary["budget_cny"] = BUDGET.limit
    summary["failures"] = failures
    (RUNS / "smoke_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n估算费用 {BUDGET.spent:.4f} 元（{BUDGET.calls} 次请求，按公开标价、不含税，实际以控制台账单为准）")
    print("未通过项：" + ("；".join(failures) if failures else "无"))
    print("完成，结果在", RUNS)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
