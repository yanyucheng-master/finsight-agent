"""评测①：结论核验器的错误检出率（全自动、可复现，不依赖大模型）。

方法：对 9 家公司的模板简报（其中每个数字都已正确引用证据），逐个数字注入一种错误，
再交给核验器，统计该数字是否被判为问题（不一致 / 无证据 / 引用无效）。
同时设置两组对照：原文不动（误报率），以及合理的四舍五入（不应被判错）。

注入类型（模拟大模型常见的数字错误）
* perturb_1pct / perturb_5pct / perturb_20pct：数值偏差 1% / 5% / 20%
* shift_decimal：小数点错位（×10）
* unit_swap：金额单位写错（亿元 ↔ 万元）
* wrong_ref：引用了另一条证据
* fabricate_uncited：去掉引用并改写数值（模拟“凭空编造”）
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from finsight.analytics.diagnosis import diagnose
from finsight.data.service import DataService
from finsight.reporting.report import build_context, template_report
from finsight.reporting.verifier import NUM_RE, verify

KINDS = ("perturb_1pct", "perturb_5pct", "perturb_20pct", "shift_decimal", "unit_swap", "wrong_ref",
         "fabricate_uncited")
CONTROLS = ("unchanged", "rounding_ok")
FLAGGED = {"mismatch", "unverified", "invalid_ref"}


@dataclass
class Tally:
    total: int = 0
    hit: int = 0
    examples: list[str] = field(default_factory=list)

    def add(self, ok: bool, example: str) -> None:
        self.total += 1
        self.hit += int(ok)
        if not ok and len(self.examples) < 5:
            self.examples.append(example)

    @property
    def rate(self) -> float:
        return self.hit / self.total if self.total else float("nan")


def _fmt_like(original: str, value: float) -> str:
    """按原数字的小数位与千分位格式输出新值。"""
    decimals = len(original.split(".", 1)[1]) if "." in original else 0
    s = f"{abs(value):,.{decimals}f}" if "," in original else f"{abs(value):.{decimals}f}"
    return ("-" if value < 0 else "") + s


def _true_display(ev, unit: str) -> float | None:
    """证据真值换算到原文显示单位（用于生成“正确的四舍五入”对照）。"""
    if not isinstance(ev.value, (int, float)) or isinstance(ev.value, bool):
        return None
    v = float(ev.value)
    if ev.unit == "ratio" and unit in ("%", "％"):
        return v * 100
    if ev.unit == "CNY":
        return v / {"亿元": 1e8, "万元": 1e4}.get(unit, 1.0)
    return v


def _mutate(text: str, start: int, end: int, kind: str, refs: list[str], all_refs: list[str],
            rng: random.Random, true_value: float | None = None) -> str | None:
    seg = text[start:end]
    m = NUM_RE.match(text, start)
    if not m:
        return None
    sign = -1.0 if m.group("sign") in ("-", "−") else 1.0
    num, unit = m.group("num"), m.group("unit") or ""
    value = sign * float(num.replace(",", ""))
    num_span = (m.start("sign") if m.group("sign") else m.start("num"), m.end("num"))
    cite = re.match(r"\[(E\d+)\]", text[end:end + 8])

    def replace_num(new_value: float) -> str | None:
        new = _fmt_like(num, new_value)
        if new.lstrip("-") == num and (new_value < 0) == (value < 0):
            return None  # 舍入后没有变化
        return text[:num_span[0]] + new + text[num_span[1]:]

    if kind.startswith("perturb_"):
        pct = {"perturb_1pct": 0.01, "perturb_5pct": 0.05, "perturb_20pct": 0.20}[kind]
        return replace_num(value * (1 + pct * rng.choice((-1, 1))))
    if kind == "shift_decimal":
        return replace_num(value * 10)
    if kind == "unit_swap":
        if unit not in ("亿元", "万元"):
            return None
        swapped = "万元" if unit == "亿元" else "亿元"
        return text[:start] + seg.replace(unit, swapped, 1) + text[end:]
    if kind == "wrong_ref":
        if not cite:
            return None
        others = [r for r in all_refs if r not in refs]
        if not others:
            return None
        new_ref = rng.choice(others)
        return text[:end] + f"[{new_ref}]" + text[end + len(cite.group(0)):]
    if kind == "fabricate_uncited":
        out = replace_num(value * 1.073 if value else 7.3)
        if out is None:
            return None
        # 去掉该数字之后最近的一个引用标记
        tail = re.sub(r"\[E\d+\]", "", out[num_span[0]:], count=1)
        return out[:num_span[0]] + tail
    if kind == "rounding_ok":
        decimals = len(num.split(".", 1)[1]) if "." in num else 0
        if decimals == 0 or true_value is None:
            return None
        # 用证据真值做“四舍五入到少一位小数”，避免对已舍入的显示值二次舍入
        q = Decimal(1).scaleb(-(decimals - 1))
        rounded = abs(Decimal(repr(true_value)).quantize(q, rounding=ROUND_HALF_UP))
        new = f"{rounded:,.{decimals - 1}f}" if "," in num else f"{rounded:.{decimals - 1}f}"
        return text[:num_span[0]] + ("-" if true_value < 0 else "") + new + text[num_span[1]:]
    return None


def run(service: DataService, *, seed: int = 20261028) -> dict:
    rng = random.Random(seed)
    tallies = {k: Tally() for k in KINDS + CONTROLS}
    companies = []
    for code in service.available_symbols():
        diag = diagnose(service.company(code))
        if diag.profile.company_type != "general":
            continue
        ctx = build_context(diag)
        text = template_report(diag, ctx)
        base = verify(text, ctx.refs)
        all_refs = list(ctx.refs)
        companies.append({"code": code, "name": diag.profile.name, "claims": base.total})
        for c in base.claims:
            tallies["unchanged"].add(c.status == "verified", f"{code} {c.text}: {c.status}")
            ev = next((e for e in ctx.refs.values() if e.id == c.evidence_id), None)
            m = NUM_RE.match(text, c.start)
            true_value = _true_display(ev, (m.group("unit") or "") if m else "") if ev is not None else None
            for kind in KINDS + ("rounding_ok",):
                mutated = _mutate(text, c.start, c.end, kind, c.refs, all_refs, rng, true_value)
                if mutated is None:
                    continue
                v = verify(mutated, ctx.refs)
                target = next((x for x in v.claims if x.start == c.start), None)
                if kind == "rounding_ok":
                    ok = target is not None and target.status == "verified"
                else:
                    ok = target is not None and target.status in FLAGGED
                tallies[kind].add(ok, f"{code} {c.text} → {target.text if target else '?'}: "
                                      f"{target.status if target else 'missing'}")
    detect = [tallies[k] for k in KINDS]
    summary = {
        "seed": seed,
        "companies": companies,
        "detection": {k: {"cases": tallies[k].total, "detected": tallies[k].hit, "rate": round(tallies[k].rate, 4),
                          "misses": tallies[k].examples} for k in KINDS},
        "controls": {k: {"cases": tallies[k].total, "passed": tallies[k].hit, "rate": round(tallies[k].rate, 4),
                         "failures": tallies[k].examples} for k in CONTROLS},
        "overall_detection_rate": round(sum(t.hit for t in detect) / max(1, sum(t.total for t in detect)), 4),
        "false_positive_rate": round(1 - tallies["unchanged"].rate, 4) if tallies["unchanged"].total else None,
    }
    return summary


LABELS = {"perturb_1pct": "数值偏差 1%", "perturb_5pct": "数值偏差 5%", "perturb_20pct": "数值偏差 20%",
          "shift_decimal": "小数点错位（×10）", "unit_swap": "金额单位写错（亿元↔万元）", "wrong_ref": "引用错误的证据",
          "fabricate_uncited": "去掉引用并编造数值", "unchanged": "原文不动（对照）", "rounding_ok": "合理四舍五入（对照）"}


def to_markdown(s: dict) -> str:
    lines = ["# 评测①：结论核验器错误检出率", "",
             f"样本：{len(s['companies'])} 家公司的模板简报，共 {sum(c['claims'] for c in s['companies'])} 个带引用的数字；"
             f"随机种子 {s['seed']}（结果可复现）。", "",
             "| 注入的错误 | 用例数 | 检出数 | 检出率 |", "|---|---|---|---|"]
    for k, v in s["detection"].items():
        lines.append(f"| {LABELS[k]} | {v['cases']} | {v['detected']} | {v['rate']:.2%} |")
    lines += ["", f"**总体检出率：{s['overall_detection_rate']:.2%}**", "",
              "| 对照组 | 用例数 | 通过数 | 通过率 |", "|---|---|---|---|"]
    for k, v in s["controls"].items():
        lines.append(f"| {LABELS[k]} | {v['cases']} | {v['passed']} | {v['rate']:.2%} |")
    lines += ["", f"误报率（原文被判错的比例）：{s['false_positive_rate']:.2%}", ""]
    misses = {k: v["misses"] for k, v in s["detection"].items() if v["misses"]}
    if misses:
        lines.append("## 漏检样例（用于改进）")
        for k, ex in misses.items():
            lines.append(f"- {LABELS[k]}：" + "；".join(ex[:3]))
    return "\n".join(lines) + "\n"


def save(summary: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    j = out_dir / "verifier_bench.json"
    m = out_dir / "verifier_bench.md"
    j.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    m.write_text(to_markdown(summary), encoding="utf-8")
    return j, m
