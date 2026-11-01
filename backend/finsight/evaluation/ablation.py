"""评测②：数字幻觉对比（三组）+ 独立口径的人工抽检。

要回答的问题：“让大模型分析上市公司，数字到底会错多少？证据账本和核验器各自挡住了多少？”

三组（同一模型、同一家公司、同一报告期）
* A 纯大模型：不给证据，只告诉它公司和报告期，要求写同样结构的简报。代表“直接问通用大模型”。
* B 有证据、无核验：给证据清单并要求引用，但**不经核验器**——取 C 的初稿（`Report.draft_markdown`）。
* C 完整 FinSight：有证据 + 核验器打回修订，取终稿。
B 与 C 共用同一次生成（B 就是 C 的初稿），所以对比是配对的，成本也不用重复付。
注意：A 与 B/C 的**提示词不同**（A 只有简短的任务说明；B/C 用 FinSight 的系统提示、证据清单和写作规则），
这是实验干预的一部分，解释 A 与 B/C 的差距时不能全部归因于“证据”；B 与 C 的差别才只来自核验器。

指标与口径（务必如实写进报告）
* “问题数字”：A 组没有引用，按“在该公司全部证据里找不到唯一可信归属”计；B/C 组按“不一致 / 引用无效 / 无证据”计。
  这是**核验器口径**，不是真值：A 组里“证据库找不到”的数字可能只是证据库之外的真实数字（如行业均值）。
* 核验器自己统计、再用核验器证明改善，是循环论证。所以另做**人工抽检**：每组随机抽取若干数字，
  生成不标注组别的抽检表，由人对照原始数据判“对 / 错 / 不确定”；`ablation-score` 再据此算各组的人工错误率
  （Wilson 95% 区间），并反过来评估核验器本身的查准 / 查全。
* 样本小（约 9 家公司、单次生成），结论是“这批样本上的观察”，不是统计显著的普遍规律。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from finsight.analytics.diagnosis import DiagnosisResult
from finsight.domain import Evidence, fmt_value
from finsight.llm import gateway as gw
from finsight.llm.gateway import LLMClient, LLMError, UsageMeter
from finsight.reporting.report import generate_report
from finsight.reporting.verifier import Claim, verify
from finsight.samples import SampleError, refs_from_mapping, split_note

GROUP_LABEL = {
    "A": "A 纯大模型（无证据、无核验）",
    "B": "B 有证据、无核验（核验前的初稿）",
    "C": "C 完整 FinSight（证据 + 核验器修订）",
}
PROBLEM = {"mismatch", "invalid_ref", "unverified"}
# 评测样本：scripts/demo_symbols.txt 中除银行（金融企业不套用工商业指标）外的 9 家
DEFAULT_CODES = ["600519", "000858", "000568", "000651", "300750", "000002", "600518", "002450", "002069"]

PURE_SYSTEM = "你是一名严谨的证券研究分析师，为专业投研人员撰写上市公司财务风险研究简报。"


def pure_prompt(name: str, code: str, year: str) -> str:
    return (f"请撰写一份关于{name}（{code}）{year}年年报（报告期截至{year}年12月31日）的财务状况与风险研究简报，"
            "需要给出具体的关键财务数据和风险判断。\n输出 Markdown，固定使用以下五个二级标题：\n"
            "## 核心结论\n## 主要风险\n## 积极因素\n## 建议进一步核实的事项\n## 数据与方法说明")


# ------------------------------------------------------------------ 运行（会调用大模型）

class InvalidOutput(RuntimeError):
    """A 组输出无效（空 / 被截断）：不再继续花钱跑 C 组，也不计入评测。"""

    def __init__(self, message: str, a: dict[str, Any]):
        super().__init__(message)
        self.a = a


class RunBusy(RuntimeError):
    """同一输出目录已有另一个运行在进行（防止双击两次导致预算重复使用）。"""


class BudgetExhausted(RuntimeError):
    pass


_ctx = threading.local()  # 当前线程正在处理的公司 / 阶段，写进账本便于对账


@contextmanager
def _stage(code: str, stage: str):
    old = (getattr(_ctx, "code", None), getattr(_ctx, "stage", None))
    _ctx.code, _ctx.stage = code, stage
    try:
        yield
    finally:
        _ctx.code, _ctx.stage = old


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def a_problem(a: dict[str, Any]) -> str | None:
    if not str(a.get("text", "")).strip():
        return "A 组输出为空"
    if a.get("truncated"):
        return "A 组输出达到长度上限被截断，内容可能不完整"
    return None


def rec_problem(rec: dict[str, Any]) -> tuple[str, str] | None:
    """一条原始记录能否进入评测。返回 (类别, 原因)；None 表示有效。"""
    bad = a_problem(rec["A"])
    if bad:
        return "a_invalid", bad
    c = rec["C"]
    if c.get("mode") != "llm":
        return "degraded", "完整流程降级为模板：" + "；".join(c.get("warnings", []))[:150]
    if any("长度上限" in w for w in c.get("warnings", [])):
        return "c_truncated", "C 组输出达到长度上限被截断，内容可能不完整"
    return None


def run_a(diag: DiagnosisResult, llm: LLMClient, *, model: str) -> dict[str, Any]:
    meter = UsageMeter()
    t0 = time.monotonic()
    resp = llm.chat([{"role": "system", "content": PURE_SYSTEM},
                     {"role": "user", "content": pure_prompt(diag.profile.name, diag.profile.code, diag.periods[0].end[:4])}])
    meter.add(resp)
    return {"text": resp.content.strip(), "truncated": bool(resp.truncated), "usage": meter.as_dict(),
            "elapsed_ms": int((time.monotonic() - t0) * 1000), "model": model, "at": _now()}


def run_company(diag: DiagnosisResult, llm: LLMClient, *, model: str, a_done: dict[str, Any] | None = None,
                on_a: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """对一家公司跑 A 组与 C 组（含 B 初稿）。返回可直接存盘、之后可离线重新打分的原始记录。

    A 组先跑并立刻交给 on_a 存盘（断点续跑时 a_done 直接复用，不再重复付费）；A 为空或被截断时抛 InvalidOutput，
    不再继续跑 C 组。C 组降级 / 截断的记录照常返回，由调用方用 rec_problem 判断是否有效。"""
    code, name, period = diag.profile.code, diag.profile.name, diag.periods[0].end
    if a_done is None:
        with _stage(code, "A"):
            a = run_a(diag, llm, model=model)
        bad = a_problem(a)
        if bad:
            raise InvalidOutput(bad, a)
        if on_a:
            on_a(a)
    else:
        a = a_done
    with _stage(code, "C"):
        rep = generate_report(diag, llm=llm, mode="llm")
    return {"code": code, "name": name, "period": period, "model": model, "started": a.get("at", _now()),
            "A": a, "C": rep.model_dump(mode="json")}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _rec_cost(rec: dict[str, Any]) -> float:
    return round(float(rec["A"]["usage"].get("cost_cny_est", 0.0)) + float(rec["C"].get("usage", {}).get("cost_cny_est", 0.0)), 6)


# ---- 费用账本：追加写的 ledger.jsonl（每行 fsync），请求发出之前先落盘，结算后再追加一行

class Journal:
    """账本是唯一的事实来源：reserve（发送前的占用上界）→ settle（结算）。
    只有 reserve 没有 settle 的请求 = 进程中途被杀 / 关窗，服务端可能已经计费，按占用上界计入累计，
    直到用户对照服务商账单核对。服务商账单才是实际扣费的依据，这里只是客户端的保守估算。"""

    def __init__(self, path: Path):
        self.path, self._lock = path, threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, rec: dict[str, Any]) -> None:
        line = (json.dumps({**rec, "at": _now()}, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)  # Windows：二进制模式，不改换行
            try:
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)

    def listener(self, event: dict[str, Any]) -> None:
        self.append({**event, "code": getattr(_ctx, "code", None), "stage": getattr(_ctx, "stage", None)})


def read_ledger(out_dir: Path) -> dict[str, Any]:
    """从 ledger.jsonl 重算累计费用。最后一行写了一半（崩溃时）可以忽略——那条 reserve 还没发出请求；
    中间有读不懂的行说明账本损坏，corrupt=True，调用方应拒绝继续花钱。"""
    p = out_dir / "ledger.jsonl"
    reserves: dict[str, dict] = {}
    settles: dict[str, dict] = {}
    companies: list[dict] = []
    bad: list[int] = []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            ev = r["ev"]
        except (ValueError, KeyError, TypeError):
            bad.append(i)
            continue
        if ev == "reserve":
            reserves[r["id"]] = r
        elif ev == "settle":
            settles[r["id"]] = r
        elif ev == "company":
            companies.append(r)
    settled = sum(float(settles[i]["charged"]) for i in reserves if i in settles)
    open_ = [r for i, r in reserves.items() if i not in settles]
    unsettled = sum(float(r["reserved"]) for r in open_)
    last = len(lines) - 1
    return {"note": "费用为客户端估算（按返回用量 / 价格表；响应不明或未结算的请求按占用上界计），不是服务商账单；以服务商控制台为准",
            "total_cost_cny_est": round(settled + unsettled, 6), "settled_cny": round(settled, 6),
            "unsettled_cny": round(unsettled, 6), "unsettled_requests": [{"id": r["id"], "code": r.get("code"), "stage": r.get("stage"),
                                                                       "reserved": r["reserved"], "at": r.get("at")} for r in open_],
            "requests": len(reserves), "attempts": companies,
            "corrupt": any(i != last for i in bad), "bad_lines": len(bad)}


class RunLock:
    """同一输出目录同时只允许一个运行：run.lock 由运行中的进程定期“摸一下”，超过 stale_s 没动静视为遗留（崩溃 / 被杀）。"""

    def __init__(self, out_dir: Path, *, stale_s: float = 120.0, beat_s: float = 20.0):
        self.path, self.stale_s, self.beat_s = out_dir / "run.lock", stale_s, beat_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age < self.stale_s:
                    raise RunBusy(f"{self.path.parent} 已有另一个评测在运行（run.lock {age:.0f} 秒前更新过）。"
                                  "请等它结束；如果确认没有在运行，等约 2 分钟后重试。") from None
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            os.write(fd, f"pid={os.getpid()} at={_now()}".encode())
            os.close(fd)
            break
        else:
            raise RunBusy(f"无法获得 {self.path}")

        def beat() -> None:
            while not self._stop.wait(self.beat_s):
                try:
                    os.utime(self.path)
                except OSError:
                    pass

        self._thread = threading.Thread(target=beat, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _load_partial_a(path: Path, code: str, model: str) -> dict[str, Any] | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    a = d.get("A")
    if d.get("code") != code or d.get("model") != model or not isinstance(a, dict) or a_problem(a):
        return None
    return a


def run_all(codes: Sequence[str], diag_of: Callable[[str], DiagnosisResult], llm: LLMClient, out_dir: Path, *,
            model: str, workers: int = 2, budget: float = 0.0, log: Callable[[str], None] = print) -> dict[str, Any]:
    """逐家运行并立即存盘（断点续跑：已有 companies/{code}.json 的跳过；已完成的 A 组存在 partial/ 里，不再重复发送）。

    * 同一目录同时只允许一个运行（run.lock）；
    * 每次发送前先把费用占用写入 ledger.jsonl（写不进去就不发送），结算后再追加；`budget`>0 时按整个目录累计，
      剩余额度同步给进程内的 LLM_BUDGET_CNY；
    * 费用保护触发后不再启动新的公司；A 为空 / 被截断、C 降级为模板或被截断的结果存到 failed/，不计入评测，下次重试；
    * 一家公司出错不拖垮整批。"""
    companies, failed, partial = out_dir / "companies", out_dir / "failed", out_dir / "partial"
    todo = [c for c in codes if not (companies / f"{c}.json").exists()]
    skipped = [c for c in codes if c not in todo]
    outcome: dict[str, str] = {c: "done" for c in skipped}
    stop = threading.Event()
    with RunLock(out_dir):
        ledger = read_ledger(out_dir)
        if ledger["corrupt"]:
            raise RuntimeError(f"{out_dir / 'ledger.jsonl'} 中间有读不懂的行，账本可能损坏，无法保证累计费用，已拒绝继续（请把该文件发给开发会话）")
        if ledger["unsettled_requests"]:
            log(f"注意：账本里有 {len(ledger['unsettled_requests'])} 笔请求发出后没有结算记录（可能是上次中途关窗），"
                f"已按占用上界计入累计 {ledger['unsettled_cny']:.2f} 元；请以服务商账单核对。")
        old_budget = os.environ.get("LLM_BUDGET_CNY")
        if budget > 0:
            remaining = budget - ledger["total_cost_cny_est"]
            if remaining <= 0:
                raise BudgetExhausted(f"该目录已记账约 {ledger['total_cost_cny_est']:.2f} 元，达到或超过预算 {budget:.2f} 元")
            os.environ["LLM_BUDGET_CNY"] = f"{remaining:.4f}"  # 预算按整个目录累计（含此前各次运行）
        journal = Journal(out_dir / "ledger.jsonl")
        remove = gw.BUDGET.add_listener(journal.listener)
        lock = threading.Lock()

        def finish(code: str, status: str, detail: str = "") -> None:
            with lock:
                outcome[code] = status
                try:
                    journal.append({"ev": "company", "code": code, "status": status, "detail": detail[:300]})
                    _atomic_write(out_dir / "ledger.json", json.dumps(read_ledger(out_dir), ensure_ascii=False, indent=1))
                except OSError as exc:  # 账本写不进去时不会再有新请求发出（reserve 会失败），这里只记录
                    log(f"[{code}] 账本写入失败：{exc}")

        def one(code: str) -> None:
            if stop.is_set():
                outcome[code] = "not_started"
                return
            a_done = _load_partial_a(partial / f"{code}.json", code, model)
            log(f"[{code}] 开始（" + ("A 组已有，复用；" if a_done else "A 组纯模型 → ") + "C 组完整流程，约 1–4 分钟）")
            try:
                diag = diag_of(code)

                def save_a(a: dict[str, Any]) -> None:
                    _atomic_write(partial / f"{code}.json", json.dumps({"code": code, "model": model, "A": a},
                                                                       ensure_ascii=False, indent=1))

                rec = run_company(diag, llm, model=model, a_done=a_done, on_a=save_a)
            except InvalidOutput as exc:
                _atomic_write(failed / f"{code}.json", json.dumps({"code": code, "model": model, "A": exc.a, "reason": str(exc)},
                                                                  ensure_ascii=False, indent=1))
                log(f"[{code}] {exc}（不计入评测，已存到 failed/，下次运行会重试 A 组）")
                finish(code, "a_invalid", str(exc))
                return
            except LLMError as exc:
                if "费用保护" in str(exc):
                    stop.set()
                log(f"[{code}] 失败：{exc}")
                finish(code, "error", str(exc))
                return
            except Exception as exc:  # noqa: BLE001 —— 一家公司出错不应拖垮整批
                log(f"[{code}] 异常：{type(exc).__name__}: {exc}")
                finish(code, "error", f"{type(exc).__name__}: {exc}")
                return
            bad = rec_problem(rec)
            if bad:
                kind, why = bad
                if "费用保护" in why:
                    stop.set()
                _atomic_write(failed / f"{code}.json", json.dumps(rec, ensure_ascii=False, indent=1))
                log(f"[{code}] {why}（不计入评测，已存到 failed/；A 组结果已保留，下次只重跑 C 组）")
                finish(code, kind, why)
                return
            try:
                _atomic_write(companies / f"{code}.json", json.dumps(rec, ensure_ascii=False, indent=1))
                # 同时导出 C 组简报：格式与 report 命令的输出相同，可直接用 `finsight samples add` 入库为缓存样例
                _atomic_write(out_dir / "reports" / f"{code}.json", json.dumps(rec["C"], ensure_ascii=False, indent=1))
            except OSError as exc:  # 费用已经花了、结果没存下来：明确报错，账本里仍有这笔费用
                log(f"[{code}] 结果写盘失败：{exc}（费用已计入账本）")
                finish(code, "error", f"写盘失败：{exc}")
                return
            log(f"[{code}] 完成，估算花费 {_rec_cost(rec):.3f} 元；C 组{'经过修订' if rec['C'].get('revised') else '初稿即通过核验'}")
            finish(code, "done")

        try:
            if skipped:
                log(f"已有结果，跳过：{', '.join(skipped)}")
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                list(pool.map(one, todo))
        finally:
            remove()
            if old_budget is None:
                os.environ.pop("LLM_BUDGET_CNY", None)
            else:
                os.environ["LLM_BUDGET_CNY"] = old_budget
        final = read_ledger(out_dir)
        try:
            _atomic_write(out_dir / "ledger.json", json.dumps(final, ensure_ascii=False, indent=1))
        except OSError as exc:
            log(f"ledger.json 摘要写入失败：{exc}（ledger.jsonl 仍是完整账本）")
    return {"outcome": outcome, "total_cost_cny_est": final["total_cost_cny_est"], "stopped_by_budget": stop.is_set(),
            "unsettled_requests": len(final["unsettled_requests"])}


# ------------------------------------------------------------------ 打分（离线，可反复运行）

def _all_refs(diag: DiagnosisResult) -> dict[str, Evidence]:
    """A 组没有引用，用“该公司全部可引用的数值证据”作归属池（排除阈值参数，避免阈值吞掉公司数据）。"""
    pool = [e for e in diag.evidence.values()
            if e.status == "ok" and e.kind != "param" and isinstance(e.value, (int, float)) and not isinstance(e.value, bool)]
    return {f"E{i}": e for i, e in enumerate(pool, 1)}


def _stats(v) -> dict[str, int]:
    return {"total": v.total, "supported": v.verified + v.unattributed,
            "problems": v.mismatch + v.invalid_ref + v.unverified, "mismatch": v.mismatch,
            "invalid_ref": v.invalid_ref, "unverified": v.unverified, "benchmark": v.benchmark,
            "flags": len(v.consistency_flags) + len(v.compliance_flags)}


def group_texts(rec: dict[str, Any], diag: DiagnosisResult) -> dict[str, tuple[str, dict[str, Evidence]]]:
    """按组取出“文本 + 核验用证据池”。C 组降级为模板（模型失败）时没有 B/C（无效记录通常已被 rec_problem 排除）。"""
    out = {"A": (rec["A"]["text"], _all_refs(diag))}
    c = rec["C"]
    if c.get("mode") == "llm":
        refs = refs_from_mapping(c["refs"], diag)
        final = split_note(c["markdown"])
        out["B"] = (c.get("draft_markdown") or final, refs)
        out["C"] = (final, refs)
    return out


def score_company(rec: dict[str, Any], diag: DiagnosisResult) -> dict[str, Any]:
    """给一条**有效**记录（rec_problem 为 None）打分。"""
    res: dict[str, Any] = {"code": rec["code"], "name": rec["name"]}
    for g, (text, refs) in group_texts(rec, diag).items():
        res[g] = _stats(verify(text, refs))
    res["A"]["cost_cny"] = rec["A"]["usage"].get("cost_cny_est", 0.0)
    res["A"]["elapsed_s"] = rec["A"]["elapsed_ms"] / 1000
    res["C"]["cost_cny"] = rec["C"]["usage"].get("cost_cny_est", 0.0)
    res["C"]["elapsed_s"] = rec["C"]["elapsed_ms"] / 1000
    res["C"]["revised"] = bool(rec["C"].get("revised"))
    res["C"]["revision_failed"] = any("修订请求失败" in w for w in rec["C"].get("warnings", []))
    return res


def summarize(scores: Sequence[dict[str, Any]], excluded: Sequence[dict[str, str]] = ()) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for g in ("A", "B", "C"):
        rows = [s[g] for s in scores if g in s]
        total = sum(r["total"] for r in rows)
        problems = sum(r["problems"] for r in rows)
        groups[g] = {
            "label": GROUP_LABEL[g], "reports": len(rows), "numbers": total,
            "numbers_per_report": round(total / len(rows), 1) if rows else 0,
            "problems": problems, "problem_rate": (problems / total) if total else None,
            "reports_with_problem": sum(1 for r in rows if r["problems"] > 0),
            "reports_with_flags": sum(1 for r in rows if r["flags"] > 0),
            "mean_cost_cny": round(sum(r.get("cost_cny", 0.0) for r in rows) / len(rows), 3) if rows and g != "B" else None,
            "mean_elapsed_s": round(sum(r.get("elapsed_s", 0.0) for r in rows) / len(rows), 1) if rows and g != "B" else None,
        }
    return {"groups": groups, "companies": len(scores), "excluded": list(excluded),
            "revised_reports": sum(1 for s in scores if s["C"]["revised"]),
            "revision_failed_reports": sum(1 for s in scores if s["C"]["revision_failed"])}


# ------------------------------------------------------------------ 人工抽检

def _comparable(ev: Evidence, unit: str | None) -> float | None:
    """把证据值换算成与断言相同的显示单位，单位不兼容返回 None。"""
    if not isinstance(ev.value, (int, float)) or isinstance(ev.value, bool):
        return None
    v = float(ev.value)
    if unit in ("%", "％", "个百分点", "百分点", "pp"):
        return v * 100 if ev.unit == "ratio" else None
    if unit in ("亿元", "万元", "元"):
        return v / {"亿元": 1e8, "万元": 1e4, "元": 1.0}[unit] if ev.unit == "CNY" else None
    if unit in ("倍", "x", "X"):
        return v if ev.unit in ("times", "ratio") else None
    if unit == "天":
        return v if ev.unit == "days" else None
    return v if ev.unit in ("score", "times", "ratio", "days", "count") else None


def candidates(claim: Claim, diag: DiagnosisResult, k: int = 3) -> list[Evidence]:
    """给人工抽检准备对照证据：同单位里数值最接近的 k 条（句中出现名称的优先）。"""
    ranked = []
    for ev in diag.evidence.values():
        if ev.status != "ok" or ev.kind == "param":
            continue
        val = _comparable(ev, claim.unit)
        if val is None:
            continue
        diff = abs(val - abs(claim.value) if claim.value >= 0 else val - claim.value) / max(abs(val), abs(claim.value), 1e-9)
        named = ev.label.split("（")[0] in claim.sentence if ev.label else False
        ranked.append((not named, diff, ev.id, ev))
    ranked.sort(key=lambda t: t[:3])
    return [t[3] for t in ranked[:k]]


def build_audit(records: Sequence[dict[str, Any]], diags: dict[str, DiagnosisResult], *, per_group: int = 15,
                seed: int = 20261031) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    """每组随机抽 per_group 个数字，打乱后编号；表里不出现组别（盲审），组别与核验器结论存在 key 里。

    B 是 C 的初稿，两组的数字大部分完全相同：同一家公司、同一句子里的同一个数字只让人判一次，
    它同时计入 B 和 C（key 里 groups = ["B", "C"]），避免重复劳动，也避免同一个数字被判出两种结果。"""
    rng = random.Random(seed)
    picked: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for g in ("A", "B", "C"):
        pool = []
        for rec in records:
            diag = diags[rec["code"]]
            texts = group_texts(rec, diag)
            if g not in texts:
                continue
            text, refs = texts[g]
            for c in verify(text, refs).claims:
                if c.status != "benchmark":
                    pool.append((rec, c))
        for rec, c in rng.sample(pool, min(per_group, len(pool))):
            ident = ("A" if g == "A" else "BC", rec["code"], c.sentence, c.text)  # A 的证据池不同，不与 B/C 合并
            item = picked.setdefault(ident, {"rec": rec, "claim": c, "groups": []})
            item["groups"].append(g)
    items = list(picked.values())
    rng.shuffle(items)
    rows, key = [], {}
    for i, it in enumerate(items, 1):
        sid, rec, c = f"S{i:03d}", it["rec"], it["claim"]
        cands = candidates(c, diags[rec["code"]])
        rows.append({"编号": sid, "公司": f"{rec['name']}（{rec['code']}）", "报告期": rec["period"][:4] + "年",
                     "所在句子": c.sentence.replace("\n", " ")[:200], "待判断的数字": c.text,
                     "对照证据（系统找的最接近几条，仅供参考；请以原始财报为准）":
                         "；".join(f"{e.label}［{e.period or '—'}］= {fmt_value(e.value, e.unit)}（{e.id}）" for e in cands) or "（没有同单位的证据）",
                     "判定（填：对 / 错 / 不确定）": "", "备注": ""})
        key[sid] = {"groups": it["groups"], "code": rec["code"], "number": c.text, "sentence": c.sentence,
                    "verifier_status": c.status}
    return rows, key


def write_audit(rows: list[dict[str, str]], key: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    sheet = out_dir / "audit_sheet.csv"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig：Excel 直接打开不乱码
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["编号"])
        w.writeheader()
        w.writerows(rows)
    keyfile = out_dir / "audit_key.json"
    keyfile.write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    return sheet, keyfile


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


_OK = {"对", "正确", "✓", "√", "y", "yes", "ok", "是"}
_BAD = {"错", "错误", "✗", "×", "x", "n", "no", "否", "不对"}


def score_audit(sheet: Path, key: dict[str, Any]) -> dict[str, Any]:
    """读取填好的抽检表：各组人工错误率（排除“不确定 / 空”），并反过来评估核验器的查准、查全。"""
    labels: dict[str, str] = {}
    with sheet.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            v = (row.get("判定（填：对 / 错 / 不确定）") or "").strip().lower()
            sid = (row.get("编号") or "").strip()
            if sid in key:
                labels[sid] = "ok" if v in _OK else "bad" if v in _BAD else "unsure" if v else "blank"
    per_group: dict[str, Any] = {}
    for g in ("A", "B", "C"):
        ids = [s for s, k in key.items() if g in k["groups"]]
        ok = sum(1 for s in ids if labels.get(s) == "ok")
        bad = sum(1 for s in ids if labels.get(s) == "bad")
        lo, hi = wilson(bad, ok + bad)
        per_group[g] = {"sampled": len(ids), "judged": ok + bad, "wrong": bad, "unsure_or_blank": len(ids) - ok - bad,
                        "wrong_rate": (bad / (ok + bad)) if ok + bad else None, "ci95": [lo, hi]}
    tp = fp = fn = tn = 0
    for s, lab in labels.items():
        if lab not in ("ok", "bad"):
            continue
        flagged = key[s]["verifier_status"] in PROBLEM
        tp += flagged and lab == "bad"
        fp += flagged and lab == "ok"
        fn += (not flagged) and lab == "bad"
        tn += (not flagged) and lab == "ok"
    verifier = {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None}
    return {"groups": per_group, "verifier_vs_human": verifier,
            "filled": sum(1 for v in labels.values() if v in ("ok", "bad", "unsure"))}


# ------------------------------------------------------------------ 报告

def _pct(x: float | None) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.1f}%"


def to_markdown(summary: dict[str, Any], audit: dict[str, Any] | None, meta: dict[str, Any]) -> str:
    g = summary["groups"]
    lines = [f"# 评测②：数字幻觉对比（{meta.get('model', '?')}，{summary['companies']} 家公司）", "",
             f"生成时间 {meta.get('generated', '')}。三组使用同一模型、同一公司、同一报告期；B 是 C 的初稿（配对对比）。"
             "**提示词不同**：A 组只有一段简短的任务说明（不给证据、不要求引用，原文见 `ablation.json` 的 meta.prompts）；"
             "B/C 使用 FinSight 的系统提示、证据清单和写作规则。所以 A 与 B/C 的差别是“有证据 + 有规则”的整体效果，"
             "不能全部归因于证据本身；B 与 C 的差别才只来自核验器。", "",
             "## 核验器口径（自动，非真值）", "",
             "| 组 | 简报数 | 数字总数 | 问题数字 | 问题数字率 | 含问题数字的简报 | 平均成本(元) | 平均耗时(秒) |", "|---|---|---|---|---|---|---|---|"]
    for k in ("A", "B", "C"):
        r = g[k]
        lines.append(f"| {r['label']} | {r['reports']} | {r['numbers']} | {r['problems']} | {_pct(r['problem_rate'])} | "
                     f"{r['reports_with_problem']} / {r['reports']} | {r['mean_cost_cny'] if r['mean_cost_cny'] is not None else '—'} | "
                     f"{r['mean_elapsed_s'] if r['mean_elapsed_s'] is not None else '—'} |")
    excl = "；".join(f"{e['code']}（{e['reason']}）" for e in summary["excluded"]) or "无"
    lines += ["", f"被核验器打回修订的简报：{summary['revised_reports']} / {summary['companies']}；"
              f"修订请求失败而保留初稿：{summary['revision_failed_reports']}；"
              f"无效、未纳入统计的公司：{excl}。", "",
              "说明：A 组没有引用，“问题数字”指在该公司全部证据里找不到可信归属的数字，可能是编造，也可能是证据库之外的真实数字；"
              "B/C 组指不一致 / 引用无效 / 无证据。核验器统计自己的输出会有循环论证，因此下面用人工抽检校验。", ""]
    if audit is None:
        lines += ["## 人工抽检", "", "尚未提供填好的抽检表（`audit_sheet.csv`）。填完后运行 "
                  "`finsight eval ablation-score <目录> --audit <填好的csv>` 生成本节。", ""]
    else:
        lines += ["## 人工抽检（独立口径）", "", f"已判定 {audit['filled']} 个数字；“不确定 / 空”不计入错误率；"
                  "B、C 中完全相同的数字只判一次，同时计入两组。", "",
                  "| 组 | 抽样数 | 已判定 | 判错 | 人工错误率 | 95% 区间（Wilson） |", "|---|---|---|---|---|---|"]
        for k in ("A", "B", "C"):
            a = audit["groups"][k]
            lo, hi = a["ci95"]
            lines.append(f"| {GROUP_LABEL[k]} | {a['sampled']} | {a['judged']} | {a['wrong']} | {_pct(a['wrong_rate'])} | "
                         f"{_pct(lo)} – {_pct(hi)} |")
        v = audit["verifier_vs_human"]
        lines += ["", "核验器对照人工判定（核验器标出“问题数字” = 阳性；人工判“错” = 真阳性）：",
                  f"真阳性 {v['tp']}，假阳性 {v['fp']}，假阴性 {v['fn']}，真阴性 {v['tn']}；"
                  f"查准率 {_pct(v['precision'])}，查全率 {_pct(v['recall'])}。", ""]
    lines += ["## 局限", "", "* 样本很小（单次生成、少数公司），只能说明这批样本上的观察，不是统计显著的普遍规律。",
              "* 核验器只比较数值：A 组里“有归属”的数字只表示它与证据库中某个数值相近，不保证指标对得上"
              "（例如虚构的 12.34% 恰好与某个无关的增速一致），所以 A 组的问题数字率是下限，真实错误率以人工抽检为准。",
              "* 人工抽检对照的是系统找出的最接近证据，仍须以原始财报为准；无法确认的判“不确定”。",
              "* A 与 B/C 的提示词、证据和规则都不同（见上），这是实验设计的一部分；换模型或提示词结果会不同。",
              "* A 为空或被截断、C 降级为模板或被截断的公司不计入统计，并在上面列出。", ""]
    return "\n".join(lines)


def load_records(out_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out_dir / "companies").glob("*.json"))]


def score_dir(out_dir: Path, diag_of: Callable[[str], DiagnosisResult], *, audit_file: Path | None = None,
              per_group: int = 15, make_audit: bool = True) -> dict[str, Any]:
    """从已保存的原始记录重新打分并写出报告 / 抽检表。不调用大模型，可反复运行。

    make_audit=False 用于“还没跑完”的中途打分：抽检表一经生成就固定，不能在数据不全时生成。"""
    loaded = load_records(out_dir)
    records, excluded = [], []
    for r in loaded:
        bad = rec_problem(r)
        if bad:
            excluded.append({"code": r["code"], "reason": bad[1]})
        else:
            records.append(r)
    if not records:
        raise SampleError(f"{out_dir / 'companies'} 里没有可用的有效记录")
    diags = {r["code"]: diag_of(r["code"]) for r in records}
    scores = [score_company(r, diags[r["code"]]) for r in records]
    summary = summarize(scores, excluded)
    keyfile = out_dir / "audit_key.json"
    audit = None
    if audit_file is not None:
        if not keyfile.exists():
            raise SampleError("缺少 audit_key.json：先不带 --audit 运行一次生成抽检表")
        audit = score_audit(audit_file, json.loads(keyfile.read_text(encoding="utf-8")))
    elif make_audit and not keyfile.exists():  # 抽检表只生成一次：重复打分不能换样本，否则已填的判定对不上
        rows, key = build_audit(records, diags, per_group=per_group)
        write_audit(rows, key, out_dir)
    from finsight.reporting.report import SYSTEM_PROMPT

    meta = {"model": records[0].get("model"), "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "prompts": {"A_system": PURE_SYSTEM, "A_user_example": pure_prompt("（公司名）", "（代码）", "（年份）"),
                        "C_system_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16],
                        "note": "B/C 的系统提示、证据清单与规则见 reporting/report.py；A 与 B/C 的提示词不同，是实验干预的一部分"}}
    (out_dir / "ablation.json").write_text(json.dumps({"meta": meta, "summary": summary, "per_company": scores,
                                                       "audit": audit}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "ablation_report.md").write_text(to_markdown(summary, audit, meta), encoding="utf-8")
    return {"summary": summary, "audit": audit, "per_company": scores}
