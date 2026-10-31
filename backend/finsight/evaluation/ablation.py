"""评测②：数字幻觉对比（三组）+ 独立口径的人工抽检。

要回答的问题：“让大模型分析上市公司，数字到底会错多少？证据账本和核验器各自挡住了多少？”

三组（同一模型、同一家公司、同一报告期）
* A 纯大模型：不给证据，只告诉它公司和报告期，要求写同样结构的简报。代表“直接问通用大模型”。
* B 有证据、无核验：给证据清单并要求引用，但**不经核验器**——取 C 的初稿（`Report.draft_markdown`）。
* C 完整 FinSight：有证据 + 核验器打回修订，取终稿。
B 与 C 共用同一次生成（B 就是 C 的初稿），所以对比是配对的，成本也不用重复付。

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
import json
import math
import os
import random
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from finsight.analytics.diagnosis import DiagnosisResult
from finsight.domain import Evidence, fmt_value
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

def run_company(diag: DiagnosisResult, llm: LLMClient, *, model: str) -> dict[str, Any]:
    """对一家公司跑 A 组与 C 组（含 B 初稿）。返回可直接存盘、之后可离线重新打分的原始记录。"""
    code, name = diag.profile.code, diag.profile.name
    period = diag.periods[0].end
    rec: dict[str, Any] = {"code": code, "name": name, "period": period, "model": model,
                           "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    meter = UsageMeter()
    t0 = time.monotonic()
    resp = llm.chat([{"role": "system", "content": PURE_SYSTEM},
                     {"role": "user", "content": pure_prompt(name, code, period[:4])}])
    meter.add(resp)
    rec["A"] = {"text": resp.content.strip(), "truncated": bool(resp.truncated), "usage": meter.as_dict(),
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    rep = generate_report(diag, llm=llm, mode="llm")
    rec["C"] = rep.model_dump(mode="json")
    return rec


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _rec_cost(rec: dict[str, Any]) -> float:
    return round(float(rec["A"]["usage"].get("cost_cny_est", 0.0)) + float(rec["C"].get("usage", {}).get("cost_cny_est", 0.0)), 6)


def read_ledger(out_dir: Path) -> dict[str, Any]:
    p = out_dir / "ledger.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"note": "费用为客户端按返回用量和价格表的估算，不是服务商账单；以服务商控制台为准", "attempts": [], "total_cost_cny_est": 0.0}


def run_all(codes: Sequence[str], diag_of: Callable[[str], DiagnosisResult], llm: LLMClient, out_dir: Path, *,
            model: str, workers: int = 2, log: Callable[[str], None] = print) -> dict[str, Any]:
    """逐家运行并立即存盘（断点续跑：已有 companies/{code}.json 的跳过）。

    * 费用保护触发（LLM_BUDGET_CNY）后不再启动新的公司，已完成的结果保留；
    * 模型失败、简报降级为模板的结果存到 failed/，不计入评测，下次运行会重试；
    * 每次尝试（成功或失败）的估算花费记入 ledger.json。
    """
    companies, failed = out_dir / "companies", out_dir / "failed"
    ledger, lock, stop = read_ledger(out_dir), threading.Lock(), threading.Event()
    todo = [c for c in codes if not (companies / f"{c}.json").exists()]
    skipped = [c for c in codes if c not in todo]
    if skipped:
        log(f"已有结果，跳过：{', '.join(skipped)}")
    outcome: dict[str, str] = {c: "done" for c in skipped}

    def settle(code: str, status: str, cost: float, detail: str = "") -> None:
        with lock:
            ledger["attempts"].append({"code": code, "status": status, "cost_cny_est": cost, "detail": detail[:300],
                                       "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            ledger["total_cost_cny_est"] = round(sum(a["cost_cny_est"] for a in ledger["attempts"]), 6)
            _atomic_write(out_dir / "ledger.json", json.dumps(ledger, ensure_ascii=False, indent=1))
            outcome[code] = status

    def one(code: str) -> None:
        if stop.is_set():
            outcome[code] = "not_started"
            return
        log(f"[{code}] 开始（A 组纯模型 → C 组完整流程，约 1–4 分钟）")
        try:
            rec = run_company(diag_of(code), llm, model=model)
        except LLMError as exc:
            if "费用保护" in str(exc):
                stop.set()
            log(f"[{code}] 失败：{exc}")
            settle(code, "error", 0.0, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 —— 一家公司出错不应拖垮整批
            log(f"[{code}] 异常：{type(exc).__name__}: {exc}")
            settle(code, "error", 0.0, f"{type(exc).__name__}: {exc}")
            return
        cost = _rec_cost(rec)
        if rec["C"].get("mode") != "llm":
            warn = "；".join(rec["C"].get("warnings", []))
            if "费用保护" in warn:
                stop.set()
            _atomic_write(failed / f"{code}.json", json.dumps(rec, ensure_ascii=False, indent=1))
            log(f"[{code}] 完整流程降级为模板（不计入评测，已存到 failed/）：{warn[:120]}")
            settle(code, "degraded", cost, warn)
            return
        _atomic_write(companies / f"{code}.json", json.dumps(rec, ensure_ascii=False, indent=1))
        # 同时导出 C 组简报：格式与 report 命令的输出相同，可直接用 `finsight samples add` 入库为缓存样例
        _atomic_write(out_dir / "reports" / f"{code}.json", json.dumps(rec["C"], ensure_ascii=False, indent=1))
        log(f"[{code}] 完成，估算花费 {cost:.3f} 元；C 组{'经过修订' if rec['C'].get('revised') else '初稿即通过核验'}")
        settle(code, "done", cost)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(one, todo))
    return {"outcome": outcome, "total_cost_cny_est": ledger["total_cost_cny_est"], "stopped_by_budget": stop.is_set()}


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
    """按组取出“文本 + 核验用证据池”。C 组降级为模板（模型失败）时没有 B/C。"""
    out = {"A": (rec["A"]["text"], _all_refs(diag))}
    c = rec["C"]
    if c.get("mode") == "llm":
        refs = refs_from_mapping(c["refs"], diag)
        final = split_note(c["markdown"])
        out["B"] = (c.get("draft_markdown") or final, refs)
        out["C"] = (final, refs)
    return out


def score_company(rec: dict[str, Any], diag: DiagnosisResult) -> dict[str, Any]:
    res: dict[str, Any] = {"code": rec["code"], "name": rec["name"], "degraded": rec["C"].get("mode") != "llm"}
    for g, (text, refs) in group_texts(rec, diag).items():
        res[g] = _stats(verify(text, refs))
    res["A"]["cost_cny"] = rec["A"]["usage"].get("cost_cny_est", 0.0)
    res["A"]["elapsed_s"] = rec["A"]["elapsed_ms"] / 1000
    if not res["degraded"]:
        res["C"]["cost_cny"] = rec["C"]["usage"].get("cost_cny_est", 0.0)
        res["C"]["elapsed_s"] = rec["C"]["elapsed_ms"] / 1000
        res["C"]["revised"] = bool(rec["C"].get("revised"))
    return res


def summarize(scores: Sequence[dict[str, Any]]) -> dict[str, Any]:
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
    return {"groups": groups, "companies": len(scores), "degraded": [s["code"] for s in scores if s["degraded"]],
            "revised_reports": sum(1 for s in scores if s.get("C", {}).get("revised"))}


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
             f"生成时间 {meta.get('generated', '')}。三组使用同一模型、同一公司、同一报告期；B 是 C 的初稿（配对对比）。", "",
             "## 核验器口径（自动，非真值）", "",
             "| 组 | 简报数 | 数字总数 | 问题数字 | 问题数字率 | 含问题数字的简报 | 平均成本(元) | 平均耗时(秒) |", "|---|---|---|---|---|---|---|---|"]
    for k in ("A", "B", "C"):
        r = g[k]
        lines.append(f"| {r['label']} | {r['reports']} | {r['numbers']} | {r['problems']} | {_pct(r['problem_rate'])} | "
                     f"{r['reports_with_problem']} / {r['reports']} | {r['mean_cost_cny'] if r['mean_cost_cny'] is not None else '—'} | "
                     f"{r['mean_elapsed_s'] if r['mean_elapsed_s'] is not None else '—'} |")
    lines += ["", f"被核验器打回修订的简报：{summary['revised_reports']} / {summary['companies']}；"
              f"降级为模板（模型失败，不计入 B/C）：{', '.join(summary['degraded']) or '无'}。", "",
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
              "* 同一模型、同一提示词；换模型或提示词结果会不同。", ""]
    return "\n".join(lines)


def load_records(out_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out_dir / "companies").glob("*.json"))]


def score_dir(out_dir: Path, diag_of: Callable[[str], DiagnosisResult], *, audit_file: Path | None = None,
              per_group: int = 15, make_audit: bool = True) -> dict[str, Any]:
    """从已保存的原始记录重新打分并写出报告 / 抽检表。不调用大模型，可反复运行。

    make_audit=False 用于“还没跑完”的中途打分：抽检表一经生成就固定，不能在数据不全时生成。"""
    records = load_records(out_dir)
    if not records:
        raise SampleError(f"{out_dir / 'companies'} 里没有原始记录")
    diags = {r["code"]: diag_of(r["code"]) for r in records}
    scores = [score_company(r, diags[r["code"]]) for r in records]
    summary = summarize(scores)
    keyfile = out_dir / "audit_key.json"
    audit = None
    if audit_file is not None:
        if not keyfile.exists():
            raise SampleError("缺少 audit_key.json：先不带 --audit 运行一次生成抽检表")
        audit = score_audit(audit_file, json.loads(keyfile.read_text(encoding="utf-8")))
    elif make_audit and not keyfile.exists():  # 抽检表只生成一次：重复打分不能换样本，否则已填的判定对不上
        rows, key = build_audit(records, diags, per_group=per_group)
        write_audit(rows, key, out_dir)
    meta = {"model": records[0].get("model"), "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    (out_dir / "ablation.json").write_text(json.dumps({"meta": meta, "summary": summary, "per_company": scores,
                                                       "audit": audit}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "ablation_report.md").write_text(to_markdown(summary, audit, meta), encoding="utf-8")
    return {"summary": summary, "audit": audit, "per_company": scores}
