#!/usr/bin/env python3
"""本机大模型冒烟测试：连通性 → 可验证简报 → 研究问答。结果写入 data/runs/，供开发会话读取复核。

用法：python scripts/llm_smoke.py [--provider deepseek]
"""
import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from finsight.agent.research_agent import ResearchAgent  # noqa: E402
from finsight.analytics.diagnosis import diagnose  # noqa: E402
from finsight.cli import main as cli_main  # noqa: E402
from finsight.config import llm_settings, load_settings  # noqa: E402
from finsight.data.service import CompanyNotFound, load_service  # noqa: E402
from finsight.llm.gateway import make_client  # noqa: E402
from finsight.reporting.report import generate_report  # noqa: E402

QUESTIONS = [
    ("600519", "茅台近三年的利润现金含量如何？和五粮液、泸州老窖相比毛利率怎么样？"),
    ("000002", "万科目前最主要的偿债风险是什么？请给出依据。"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="deepseek")
    args = ap.parse_args()
    runs = load_settings().data_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    summary = {"provider": args.provider, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "items": []}

    for prov in ("deepseek", "kimi"):
        cli_main(["llm-check", "--provider", prov])

    cfg = llm_settings(args.provider)
    if not cfg.enabled:
        print(f"未配置 {args.provider} 的 Key，跳过后续步骤（请先运行 scripts\\setup_env.bat）")
        return 1
    llm = make_client(cfg)
    svc = load_service(load_settings().snapshot_dir)

    def peers(code):
        try:
            return diagnose(svc.company(code))
        except CompanyNotFound:
            return None

    for code in ("600519", "000002", "600518"):
        try:
            rep = generate_report(diagnose(svc.company(code)), llm=llm, mode="llm")
            (runs / f"report_{code}_{args.provider}.json").write_text(rep.model_dump_json(indent=1), encoding="utf-8")
            v = rep.verification
            line = (f"简报 {code}：模式 {rep.mode}，尝试 {rep.attempts} 次，数字 {v.total} 个，通过 {v.verified}，"
                    f"无证据 {v.unverified}，不一致 {v.mismatch}，耗时 {rep.elapsed_ms} ms，用量 {rep.usage}")
        except Exception as exc:
            line = f"简报 {code} 失败：{exc}"
            traceback.print_exc()
        print(line)
        summary["items"].append(line)

    for i, (code, q) in enumerate(QUESTIONS, 1):
        try:
            ans = ResearchAgent(diagnose(svc.company(code)), llm, peer_loader=peers).ask(q)
            (runs / f"ask_{i}_{code}_{args.provider}.json").write_text(ans.model_dump_json(indent=1), encoding="utf-8")
            v = ans.verification
            line = (f"问答 {i}（{code}）：工具调用 {len(ans.steps)} 次，数字 {v.total} 个，通过 {v.verified}，"
                    f"无证据 {v.unverified}，耗时 {ans.elapsed_ms} ms")
        except Exception as exc:
            line = f"问答 {i} 失败：{exc}"
            traceback.print_exc()
        print(line)
        summary["items"].append(line)

    summary["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (runs / "smoke_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("完成，结果在", runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
