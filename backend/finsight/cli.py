"""命令行入口。

    finsight diagnose 600519            # 终端里输出风险体检摘要
    finsight diagnose 600519 --json out.json
    finsight verify-snapshots           # 校验全部快照对象的 SHA-256
    finsight serve --port 8000          # 启动 API 服务（及前端静态页面）
    finsight collect --symbols 600519   # 在线采集快照（需能访问数据源）
    finsight samples add report.json    # 把真实大模型输出重新核验后存为缓存样例
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
    if args.which == "verifier":
        from finsight.evaluation.verifier_bench import run, save, to_markdown

        summary = run(_service(), seed=args.seed)
        out = Path(args.out) if args.out else load_settings().data_dir.parent / "docs" / "eval"
        j, m = save(summary, out)
        print(to_markdown(summary))
        print(f"已写入 {j} 与 {m}")
        return 0
    return _eval_ablation(args)


def _eval_ablation(args: argparse.Namespace) -> int:
    """评测②：ablation 运行（调用大模型，有费用上限、可断点续跑）/ ablation-score 离线重新打分。"""
    import os

    from finsight.analytics.diagnosis import diagnose
    from finsight.evaluation import ablation

    svc = _service()
    root = load_settings().data_dir.parent
    provider = args.provider or "kimi"

    def diag_of(code: str):
        return diagnose(svc.company(code))

    if args.which == "ablation-score":
        out = Path(args.target) if args.target else root / "docs" / "eval" / f"ablation-{provider}"
        res = ablation.score_dir(out, diag_of, audit_file=Path(args.audit) if args.audit else None, per_group=args.per_group,
                                 regen_audit=args.regen_audit)
        print((out / "ablation_report.md").read_text(encoding="utf-8"))
        print(f"已写入 {out / 'ablation_report.md'}" + ("" if args.audit else f"；抽检表 {out / 'audit_sheet.csv'}"))
        return 0 if res else 1

    from finsight.config import llm_settings
    from finsight.llm.gateway import LLMError, make_client

    out = Path(args.out) if args.out else root / "docs" / "eval" / f"ablation-{provider}"
    codes = args.codes or ablation.DEFAULT_CODES
    os.environ.setdefault("LLM_MAX_TOKENS", "8192")  # 与 scripts/llm_smoke.py 一致：让逐请求的费用预留更贴近实际
    cfg = llm_settings(provider)
    if args.max_tokens > 0:
        import dataclasses

        cfg = dataclasses.replace(cfg, max_tokens=args.max_tokens)
    if args.require_model and getattr(cfg, "model", "") != args.require_model:
        print(f"模型不符：要求 {args.require_model}，实际配置 {getattr(cfg, 'model', '?')}（{provider}）。没有发送任何请求。")
        return 1
    if not cfg.enabled and not args.dry_run:
        print(f"未配置 {provider} 的 API Key（请先运行 scripts\\setup_env.bat）")
        return 1
    led0 = ablation.read_ledger(out)
    spent = led0["total_cost_cny_est"]
    if led0["corrupt"]:
        print(f"{out / 'ledger.jsonl'} 有读不懂的行，账本可能损坏，已拒绝继续（不会修改它）。没有发送任何请求。")
        return 1
    try:
        if not cfg.enabled:  # 只可能是 dry-run：用占位符构造客户端，仅为按真实提示词计算占用上界，不会发送任何请求
            import dataclasses

            cfg = dataclasses.replace(cfg, api_key="dry-run")
        llm = make_client(cfg)
        if hasattr(llm, "retries"):
            llm.retries = max(0, args.retries)  # 每次重试都会单独占用并记账
    except LLMError as exc:
        print("大模型不可用：", exc)
        return 1
    cap = args.budget or 0.0
    print(f"评测②：{cfg.provider} / {cfg.model}，公司 {len(codes)} 家，单次输出上限 {getattr(cfg, 'max_tokens', '?')}，"
          f"推理强度 {getattr(cfg, 'reasoning_effort', '?')}，并发 {args.workers}，失败重试 {args.retries} 次，输出 {out}")
    print(f"费用：本目录累计上限 {cap or '不限'} 元，账本已记账约 {spent:.3f} 元（未结算 {len(led0['unsettled_requests'])} 笔，按占用上界计），"
          f"本次最多再花约 {max(cap - spent, 0):.3f} 元（估算）。")
    print("提醒：这个上限只约束本输出目录的账本（请勿删除该目录或 ledger.jsonl）；总授权额度（含此前各次测试）请对照服务商控制台。")
    bounds = []
    if hasattr(llm, "build_payload"):
        for code in codes:
            try:
                b = ablation.reserve_bounds(diag_of(code), llm)
            except Exception as exc:  # noqa: BLE001
                print(f"  {code}：无法估算（{type(exc).__name__}: {exc}）")
                return 1
            bounds.append(b)
            print(f"  {code}：单次占用参考估计  A {b['A']:.3f} / C {b['C']:.3f} / 修订 ≈{b['C_revision']:.3f} 元")
        worst = max(max(b.values()) for b in bounds)
        print(f"  参考：任一时刻在途请求的占用约 {worst * max(args.workers, 1):.3f} 元。以上是参考估计，**不是数学上界**："
              "修订请求的输入含初稿和 reasoning_content、修订提示长度、多轮对话都按固定假设估算，真实长度可能更大；"
              "真实发送前的预留仍按当次实际请求内容逐次计算，剩余额度不足以占用时保护会拒绝发送。")
    if args.dry_run:
        print("dry-run：没有发送任何请求，也没有写入账本。")
        return 0
    if args.ask:
        try:
            answer = input("确认开始真实请求（会产生费用）？请输入 YES 后回车，其他任何输入都会取消：").strip()
        except EOFError:
            answer = ""
        if answer != "YES":
            print("已取消，没有发送任何请求。")
            return 1
    try:
        res = ablation.run_all(codes, diag_of, llm, out, model=cfg.model, workers=args.workers, budget=args.budget)
    except ablation.BudgetExhausted as exc:
        print(f"{exc}，不再运行。")
        return 1
    except (ablation.RunBusy, RuntimeError) as exc:
        print(exc)
        return 1
    done = [c for c in codes if (out / "companies" / f"{c}.json").exists()]
    print(f"\n完成 {len(done)} / {len(codes)} 家；本目录累计估算花费 {res['total_cost_cny_est']:.2f} 元（客户端估算，以服务商控制台为准）")
    rows = ablation.company_overview(codes, out)
    overview = ablation.overview_markdown(rows, total=res["total_cost_cny_est"], budget=cap, model=cfg.model,
                                          unsettled=res["unsettled_requests"])
    (out / "run_overview.md").write_text(overview, encoding="utf-8")
    print(overview)
    if res["stopped_by_budget"]:
        print("已触发费用保护，剩余公司未运行。额度取决于你的授权与服务商控制台的实际已用金额，请先核对，不要为了跑完而随意提高 --budget。")
    if not done:
        return 1
    complete = len(done) == len(codes)
    ablation.score_dir(out, diag_of, per_group=args.per_group, make_audit=complete)
    print((out / "ablation_report.md").read_text(encoding="utf-8"))
    if complete:
        print(f"抽检表：{out / 'audit_sheet.csv'}（用 Excel 打开，只填“判定”和“备注”两列）")
    else:
        missing = [c for c in codes if c not in done]
        print(f"未完成：{', '.join(missing)}。重新运行同一命令只会在剩余额度内补跑这些（已完成的 A 组结果会复用，额度不会因重跑而增加）；"
              f"全部完成后才自动生成抽检表。若不再补跑，可对已完成的公司运行 `finsight eval ablation-score {out}` 生成报告和抽检表（只覆盖已完成的公司）。")
    return 0 if complete else 2


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


def cmd_samples(args: argparse.Namespace) -> int:
    """缓存样例：把真实大模型输出（简报 / 问答 JSON）重新核验后存入 data/samples/。"""
    import json

    from finsight.analytics.diagnosis import diagnose
    from finsight.config import PROJECT_ROOT
    from finsight.samples import SampleError, SampleStore, build_ask_sample, build_report_sample

    store = SampleStore(load_settings().data_dir / "samples")

    def rel(path: str) -> str:  # 样例里记录相对项目根目录的来源，不记录本机绝对路径
        try:
            return Path(path).resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
        except ValueError:
            return Path(path).name

    if args.action == "list":
        for item in store.list():
            v = item.get("verification", {})
            print(f"{item['id']}  [{item['kind']}] {item['name']}（{item['code']}） {item.get('model')}  "
                  f"数字 {v.get('total')}，未通过 {v.get('mismatch', 0) + v.get('invalid_ref', 0) + v.get('unverified', 0)}")
        return 0
    svc = _service()
    failed = 0
    for path in args.files:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if "markdown" in raw:
                code = raw["code"]
                sample = build_report_sample(raw, diagnose(svc.company(code)), source=rel(path))
            elif "answer" in raw:
                if not args.code:
                    raise SampleError("问答样例需要用 --code 指定公司代码")
                if args.generated_at:
                    raw["generated_at"] = args.generated_at
                sample = build_ask_sample(raw, diagnose(svc.company(args.code)), source=rel(path))
            else:
                raise SampleError("无法识别的文件：既不是简报也不是问答")
            out = store.save(sample)
            v = sample["verification"]
            print(f"已入库 {out.name}：数字 {v['total']}，一致 {v['verified'] + v['unattributed']}，"
                  f"不一致 {v['mismatch']}，引用无效 {v['invalid_ref']}，无证据 {v['unverified']}，"
                  f"整体{'通过' if v['passed'] else '未通过（样例里会如实标红）'}")
        except (SampleError, KeyError, OSError, ValueError) as exc:
            failed += 1
            print(f"跳过 {path}：{exc}")
    return 1 if failed else 0


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
    p = sub.add_parser("eval", help="运行评测（verifier：核验器检出率；ablation：数字幻觉三组对比，调用大模型；ablation-score：离线重新打分）")
    p.add_argument("which", choices=["verifier", "ablation", "ablation-score"])
    p.add_argument("target", nargs="?", default="", help="ablation-score：评测目录")
    p.add_argument("--seed", type=int, default=20261028)
    p.add_argument("--out", default="")
    p.add_argument("--provider", choices=["deepseek", "kimi", "intern"], default=None)
    p.add_argument("--codes", nargs="*", default=None, help="ablation：公司代码（默认 9 家样本）")
    p.add_argument("--budget", type=float, default=5.0,
                   help="ablation：本目录**累计**费用上限（元，客户端估算，含此前各次运行；重跑不会增加额度），0 表示不限")
    p.add_argument("--workers", type=int, default=2, help="ablation：并发公司数")
    p.add_argument("--require-model", default="", help="ablation：模型名必须等于它，否则一个请求都不发（例如 kimi-k3.1-pro）")
    p.add_argument("--max-tokens", type=int, default=0, help="ablation：强制单次输出上限（覆盖 .env 的 LLM_MAX_TOKENS；0 = 沿用默认 8192）")
    p.add_argument("--retries", type=int, default=3, help="ablation：每个请求失败后的最多重试次数（每次发送都单独占用并记账）")
    p.add_argument("--dry-run", action="store_true", help="ablation：只核对参数、账本与各公司的费用占用上界，不发送任何请求")
    p.add_argument("--ask", action="store_true", help="ablation：打印运行计划后，必须手动输入 YES 才开始发送请求")
    p.add_argument("--audit", default="", help="ablation-score：填好的人工抽检表（csv）")
    p.add_argument("--per-group", type=int, default=20, help="人工抽检：A 组、B/C 共有数字各抽取的个数")
    p.add_argument("--regen-audit", action="store_true",
                   help="ablation-score：用新口径重新生成抽检表（旧表、旧报告留档为 *.vN.*）；旧表已有人工判定时拒绝")
    p.set_defaults(func=cmd_eval)
    p = sub.add_parser("samples", help="缓存样例：入库（重新核验）/ 列出")
    p.add_argument("action", choices=["add", "list"])
    p.add_argument("files", nargs="*")
    p.add_argument("--code", default="", help="问答样例所属公司代码")
    p.add_argument("--generated-at", default="", help="问答输出本身没有时间戳时，手工指明生成日期（如 2026-10-30）")
    p.set_defaults(func=cmd_samples)
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
