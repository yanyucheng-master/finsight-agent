"""命令行入口。

    finsight diagnose 600519            # 终端里输出风险体检摘要
    finsight diagnose 600519 --json out.json
    finsight verify-snapshots           # 校验全部快照对象的 SHA-256
    finsight serve --port 8000          # 启动 API 服务（及前端静态页面）
    finsight collect --symbols 600519   # 在线采集快照（需能访问数据源）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from finsight.config import load_settings


def _service():
    from finsight.data.service import load_service
    return load_service(load_settings().snapshot_dir)


def cmd_diagnose(args: argparse.Namespace) -> int:
    from finsight.analytics.diagnosis import diagnose
    from finsight.domain import fmt_value

    svc = _service()
    result = diagnose(svc.company(args.code), years=args.years)
    if args.json:
        Path(args.json).write_text(result.model_dump_json(indent=1), encoding="utf-8")
        print(f"已写入 {args.json}")
    ev = result.evidence
    p = result.profile
    print(f"\n{p.name}（{p.code}.{p.exchange}） {p.industry_em or ''}  公司类型={p.company_type}")
    print(f"年报期：{', '.join(x.end[:4] for x in result.periods)}   审计机构：{p.auditor or '—'}")
    cs = result.checks_summary
    print(f"数据核验：通过 {cs['pass']} / 未通过 {cs['fail']}（其中恒等式错误 {cs['error_fail']}） / 跳过 {cs['skip']}")
    print(f"\n综合结论：{result.overall.level}风险 —— {result.overall.headline}")
    for s in result.signals:
        mark = {"high": "▲高", "medium": "△中", "low": "·低", "info": "✓"}[s.severity]
        print(f"  {mark} [{s.rule}] {s.title}")
    latest = result.periods[0].end if result.periods else None
    if latest:
        print(f"\n关键指标（{latest[:4]}）：")
        for row in result.metrics:
            eid = row.values.get(latest)
            if eid and ev[eid].value is not None:
                print(f"  {row.label:<18} {fmt_value(ev[eid].value, ev[eid].unit)}")
    print(f"\n证据条目：{len(result.evidence)}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from finsight.analytics.diagnosis import diagnose
    from finsight.config import llm_settings
    from finsight.llm.gateway import LLMError, make_client
    from finsight.reporting.report import generate_report

    svc = _service()
    llm = None
    if args.mode != "template":
        cfg = llm_settings(args.provider) if args.provider else load_settings().llm
        if cfg.enabled:
            try:
                llm = make_client(cfg)
            except LLMError as exc:
                print("大模型不可用：", exc)
        else:
            print(f"未配置 {cfg.provider} 的 API Key，将使用模板报告")
    rep = generate_report(diagnose(svc.company(args.code)), llm=llm, mode=args.mode)
    v = rep.verification
    print(rep.annotated_markdown)
    print(f"\n—— 模式：{rep.mode}  模型：{rep.model or '—'}  尝试次数：{rep.attempts}  用量：{rep.usage or '—'}")
    print(f"—— 数字断言 {v.total} 个：核验通过 {v.verified}，未标引用但一致 {v.unattributed}，"
          f"不一致 {v.mismatch}，引用无效 {v.invalid_ref}，无证据 {v.unverified}；可核验率 {v.verifiable_rate:.1%}")
    for w in rep.warnings + v.compliance_flags:
        print("—— 警告：", w)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rep.model_dump_json(indent=1), encoding="utf-8")
        print(f"已保存 {out}")
    return 0


CHECK_TOOL = {"type": "function", "function": {
    "name": "lookup_metric", "description": "查询一家公司某个财务指标的最新取值",
    "parameters": {"type": "object", "properties": {"code": {"type": "string", "description": "6 位股票代码"},
                                                    "metric": {"type": "string", "description": "指标代码"}},
                   "required": ["code", "metric"]}}}


def cmd_llm_check(args: argparse.Namespace) -> int:
    """连通性自检：①列出可用模型 ②一次短对话 ③一次两轮的工具调用（验证 reasoning_content 回传）。

    结果写入 data/runs/llm_check_{provider}.json，便于开发会话排查。工具返回值是写死的测试值（synthetic）。
    """
    import json
    import time

    from finsight.config import llm_settings
    from finsight.llm.gateway import LLMError, make_client

    cfg = llm_settings(args.provider)
    report: dict = {**cfg.public(), "key_configured": cfg.enabled}
    tag = f"[{cfg.provider} / {cfg.model} / 推理强度 {cfg.reasoning_effort}]"
    if not cfg.enabled:
        print(f"{tag} 未配置 API Key")
        report["error"] = "no key"
    else:
        try:
            client = make_client(cfg)
            try:
                models = client.list_models()
                report["models"] = models[:50]
                if cfg.model not in models:
                    report["warning"] = f"模型 {cfg.model} 不在该 Key 的可用列表中"
                    print(f"{tag} 警告：{report['warning']}")
            except Exception as exc:  # 部分平台不开放 /models
                report["models_error"] = str(exc)[:200]
            t0 = time.monotonic()
            resp = client.chat([{"role": "user", "content": "只回复两个字：正常"}], max_tokens=2048)
            report["chat"] = {"reply": resp.content, "finish_reason": resp.finish_reason, "usage": resp.usage,
                              "reasoning_chars": len(resp.reasoning), "served_model": resp.model,
                              "latency_ms": int((time.monotonic() - t0) * 1000)}
            print(f"{tag} 对话正常：回复「{resp.content.strip()}」，思考 {len(resp.reasoning)} 字，"
                  f"耗时 {report['chat']['latency_ms']} ms，用量 {resp.usage}")
        except LLMError as exc:
            report["error"] = str(exc)
            print(f"{tag} 对话失败：{exc}")
        if "error" not in report:
            report["tool_roundtrip"] = _tool_roundtrip(client, tag)
            if not report["tool_roundtrip"].get("passed"):
                report["error"] = "工具调用往返未通过：" + str(report["tool_roundtrip"].get("error") or "见详情")
    out = load_settings().data_dir / "runs" / f"llm_check_{cfg.provider}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if "error" not in report else 1


def _tool_roundtrip(client, tag: str) -> dict:
    """模拟智能体的一步：模型调用工具 → 回传工具结果（连同 reasoning_content）→ 模型据此作答。"""
    import json
    import time

    from finsight.llm.gateway import LLMError

    out: dict = {"passed": False}
    messages = [{"role": "user", "content": "请调用 lookup_metric 工具查询 600519 的 gross_margin，"
                                            "然后用一句话告诉我结果，数值照抄工具返回值。"}]
    t0 = time.monotonic()
    try:
        first = client.chat(messages, tools=[CHECK_TOOL])
        out.update({"tool_calls": [{"name": t.name, "arguments": t.arguments} for t in first.tool_calls],
                    "first_finish_reason": first.finish_reason, "first_reasoning_chars": len(first.reasoning)})
        if not first.tool_calls:
            out["error"] = f"模型没有调用工具，直接回答：{first.content[:120]}"
            print(f"{tag} 工具调用：{out['error']}")
            return out
        messages.append(first.as_message())
        for tc in first.tool_calls:
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps({"metric": "gross_margin", "period": "2025", "value": "91.18%"},
                                                   ensure_ascii=False)})
        final = client.chat(messages, tools=[CHECK_TOOL])
        out.update({"final_reply": final.content[:300], "final_finish_reason": final.finish_reason,
                    "usage": [first.usage, final.usage], "latency_ms": int((time.monotonic() - t0) * 1000)})
        out["passed"] = "91.18" in final.content
        if not out["passed"]:
            out["error"] = "最终回答没有使用工具返回值"
        print(f"{tag} 工具调用往返{'通过' if out['passed'] else '未通过'}：调用 {first.tool_calls[0].name}"
              f"{first.tool_calls[0].arguments} → 回答「{final.content.strip()[:80]}」，耗时 {out['latency_ms']} ms")
    except LLMError as exc:
        out["error"] = str(exc)
        print(f"{tag} 工具调用往返失败：{exc}")
    return out


def cmd_ask(args: argparse.Namespace) -> int:
    from finsight.agent.research_agent import ResearchAgent
    from finsight.analytics.diagnosis import diagnose
    from finsight.config import llm_settings
    from finsight.data.service import CompanyNotFound
    from finsight.llm.gateway import make_client

    svc = _service()
    cfg = llm_settings(args.provider) if args.provider else load_settings().llm
    if not cfg.enabled:
        print(f"未配置 {cfg.provider} 的 API Key")
        return 1

    def peers(code: str):
        try:
            return diagnose(svc.company(code))
        except CompanyNotFound:
            return None

    agent = ResearchAgent(diagnose(svc.company(args.code)), make_client(cfg), peer_loader=peers)
    ans = agent.ask(args.question)
    for st in ans.steps:
        print(f"  · 调用 {st.tool}({st.arguments}) → {st.summary}")
    print("\n" + ans.annotated_answer)
    v = ans.verification
    print(f"\n—— 数字 {v.total} 个，核验通过 {v.verified}，无证据 {v.unverified}，不一致 {v.mismatch}；用量 {ans.usage}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(ans.model_dump_json(indent=1), encoding="utf-8")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from finsight.evaluation.verifier_bench import run, save, to_markdown

    if args.which == "verifier":
        summary = run(_service(), seed=args.seed)
        out = Path(args.out) if args.out else load_settings().data_dir.parent / "docs" / "eval"
        j, m = save(summary, out)
        print(to_markdown(summary))
        print(f"已写入 {j} 与 {m}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from finsight.data.snapshots import SnapshotIntegrityError, SnapshotStore

    store = SnapshotStore(load_settings().snapshot_dir)
    ok = bad = missing = 0
    for rec in store.iter_records():
        if not rec.ok:
            continue
        try:
            store.read_bytes(rec)
            ok += 1
        except SnapshotIntegrityError as exc:
            bad += 1
            print("篡改/损坏:", exc)
        except FileNotFoundError:
            missing += 1
    print(f"快照对象校验：正常 {ok}，哈希不一致 {bad}，文件缺失 {missing}")
    return 0 if bad == 0 else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("finsight.api.app:create_app", factory=True, host=args.host, port=args.port, reload=False)
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    from finsight.data import collector

    argv = ["--out", str(load_settings().snapshot_dir)] + (["--symbols", args.symbols] if args.symbols else [])
    return collector.main(argv + args.extra)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(prog="finsight", description="FinSight Agent 命令行")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("diagnose", help="对一家公司运行财务风险体检")
    p.add_argument("code")
    p.add_argument("--years", type=int, default=6)
    p.add_argument("--json", default="")
    p.set_defaults(func=cmd_diagnose)
    p = sub.add_parser("report", help="生成可验证研究简报（大模型 / 模板）")
    p.add_argument("code")
    p.add_argument("--mode", choices=["auto", "llm", "template"], default="auto")
    p.add_argument("--provider", choices=["deepseek", "kimi", "intern"], default=None)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("ask", help="向研究智能体提问")
    p.add_argument("code")
    p.add_argument("question")
    p.add_argument("--provider", choices=["deepseek", "kimi", "intern"], default=None)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_ask)
    p = sub.add_parser("llm-check", help="检查大模型 API 连通性")
    p.add_argument("--provider", choices=["deepseek", "kimi", "intern"], default="deepseek")
    p.set_defaults(func=cmd_llm_check)
    p = sub.add_parser("eval", help="运行评测（verifier：核验器错误检出率）")
    p.add_argument("which", choices=["verifier"])
    p.add_argument("--seed", type=int, default=20261028)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_eval)
    p = sub.add_parser("verify-snapshots", help="校验快照完整性")
    p.set_defaults(func=cmd_verify)
    p = sub.add_parser("serve", help="启动 API 服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser("collect", help="在线采集数据快照")
    p.add_argument("--symbols", default="")
    p.add_argument("extra", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_collect)
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
