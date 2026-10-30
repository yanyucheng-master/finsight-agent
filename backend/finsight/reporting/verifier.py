"""结论核验器：逐个检查大模型输出中的数字是否有证据支持、是否与证据一致。

处理流程
1. 屏蔽引用标记 [E12]、日期、年份、列表序号等非“断言数字”；
2. 抽取数值断言（数字 + 单位：亿元/万元/元/%/个百分点/倍/天），将其后最近的引用组与之关联；
3. 换算到证据的基本单位（元、小数比率、倍、天），按“显示精度”给出容差后比对；
4. 未引用的数字到全部证据中寻找可归属的值；仍找不到的判为“无证据”；
5. 合规检查：识别投资建议用语（金融强合规场景要求研究简报不构成投资建议）。

状态
* verified      引用正确且数值一致
* unattributed  数值能在证据中找到，但没有标注引用（数字本身是对的）
* mismatch      标注了引用，但数值与证据不一致
* invalid_ref   引用了不存在的证据编号
* unverified    没有任何证据支持（疑似幻觉或自行计算）
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from finsight.domain import Evidence, fmt_value
from finsight.reporting.consistency import check_consistency

ClaimStatus = Literal["verified", "unattributed", "mismatch", "invalid_ref", "unverified"]

CITE_RE = re.compile(r"[\[【]\s*(E\d+(?:\s*[,，、]\s*E\d+)*)\s*[\]】]")
NUM_RE = re.compile(
    # 左边界只排除“数字、ASCII 字母、小数点、千分位逗号”。不能用 \w：Python 里汉字也属于 \w，
    # 会导致“毛利率为91.18%”这类紧贴汉字的数字整个漏检、“为1,032.80”被截成“032.80”（2026-10-30 真实测试发现）。
    r"(?<![0-9A-Za-z.,])(?P<sign>[-−+]?)(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>亿元|万元|元/股|元|个百分点|百分点|pp|%|％|倍|天|x|X)?"
)
# 不视为断言数字的上下文
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
SKIP_AFTER = re.compile(r"^\s*(年|月|日|季度|季|月份|期|家|条|项|个(?!百分点)|名|位|次|号|章|节|款|级|类|种|大|小时|分钟|秒)")
# 写作格式计数（“1–3 句”“3-5 条”），不是财务断言；模型偶尔会把提示词里的格式要求抄进标题
FORMAT_COUNT = re.compile(r"^\s*(?:[–—\-~～至到]\s*\d+\s*)?(句|段|点|字|行|条|项|节)")
DATE_RE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
ADVICE_RE = re.compile(r"(建议|推荐|可以|应当|宜)\s*(买入|卖出|增持|减持|持有|抄底|加仓|减仓)|目标价|买入评级|卖出评级")

# 表示“变化方向为负”的措辞（只看数字前、同一分句内）。“降至 / 减至”描述的是水平，不算。
NEG_WORDS = re.compile(r"(下降|下滑|减少|下跌|回落|收缩|缩减|萎缩)(?!至|到)|亏损|净流出|流出|为负|负增长|减亏")

UNIT_SCALE = {"亿元": 1e8, "万元": 1e4, "元": 1.0, "元/股": 1.0}


class Claim(BaseModel):
    text: str  # 原文中的数字片段，如 “91.18%”
    start: int
    end: int
    value: float  # 换算前的显示数值（含符号）
    unit: str | None
    refs: list[str] = Field(default_factory=list)  # 关联的引用编号 E#
    evidence_id: str | None = None  # 匹配上的证据
    status: ClaimStatus
    expected: str | None = None  # 证据中的数值（展示格式）
    message: str = ""
    sentence: str = ""


class VerificationResult(BaseModel):
    claims: list[Claim]
    total: int
    verified: int
    unattributed: int
    mismatch: int
    invalid_ref: int
    unverified: int
    verifiable_rate: float  # (verified + unattributed) / total
    citation_rate: float  # verified / total
    compliance_flags: list[str] = Field(default_factory=list)
    consistency_flags: list[str] = Field(default_factory=list)  # 口径提示（见 consistency.py）

    @property
    def problems(self) -> list[Claim]:
        return [c for c in self.claims if c.status in ("mismatch", "invalid_ref", "unverified")]

    @property
    def passed(self) -> bool:
        return not self.problems and not self.compliance_flags and not self.consistency_flags


@dataclass
class _Candidate:
    value: float  # 换算到证据单位后的值
    tol: float
    note: str = ""


@dataclass
class _Masked:
    text: str
    spans: list[tuple[int, int]] = field(default_factory=list)


def _decimals(num: str) -> int:
    return len(num.split(".", 1)[1]) if "." in num else 0


def _candidates(display: float, num: str, unit: str | None, ev_unit: str) -> list[_Candidate]:
    """把显示的数字按可能的单位解释换算成证据单位下的候选值（含按显示精度的容差）。"""
    d = _decimals(num)
    half = 0.5 * 10 ** (-d) + 1e-12  # 显示精度的一半
    u = (unit or "").replace("％", "%").replace("X", "x")
    out: list[_Candidate] = []
    if ev_unit == "CNY":
        if u in UNIT_SCALE:
            s = UNIT_SCALE[u]
            out.append(_Candidate(display * s, half * s))
        elif not u:
            out.extend([_Candidate(display * s, half * s) for s in (1e8, 1e4, 1.0)])
    elif ev_unit == "cny_per_share":
        if u in ("元", "元/股", ""):
            out.append(_Candidate(display, half))
    elif ev_unit == "ratio":
        if u in ("%", "个百分点", "百分点", "pp"):
            out.append(_Candidate(display / 100, half / 100))
        elif not u:
            out.append(_Candidate(display, half))
            out.append(_Candidate(display / 100, half / 100))
    elif ev_unit == "times":
        if u in ("倍", "x", ""):
            out.append(_Candidate(display, half))
        if u in ("%", ""):
            out.append(_Candidate(display / 100, half / 100))
    elif ev_unit == "days":
        if u in ("天", ""):
            out.append(_Candidate(display, half))
    elif ev_unit in ("score", "count"):
        if u in ("", "分"):
            out.append(_Candidate(display, half))
    return out


def _matches(display: float, num: str, unit: str | None, ev: Evidence, *, signed: bool = False,
             negative_words: bool = False) -> tuple[bool, str]:
    """数值比对。正负号规则：
    * 文本带显式正负号（+12.30% / -1.21%）：必须与证据符号一致；
    * 文本不带符号、证据为负：只有数字前（同一分句）有“下降 / 亏损 / 流出”等负向措辞才算一致；
    * 文本不带符号、证据为正，但前面写了负向措辞：方向相反，判不一致。"""
    if isinstance(ev.value, bool) or ev.value is None:
        return False, ""
    if isinstance(ev.value, str):
        # 文本证据（如核验汇总、审计意见）：数字原样出现在证据文本中即视为一致
        return (num.replace(",", "") in ev.value.replace(",", "")), ""
    target = float(ev.value)
    for cand in _candidates(display, num, unit, ev.unit):
        if math.isclose(cand.value, target, abs_tol=cand.tol, rel_tol=1e-9):
            if not signed and negative_words and target > cand.tol:
                return False, "措辞表示下降 / 亏损，但证据为正值"
            return True, ""
        if (not signed and negative_words and target < 0
                and math.isclose(abs(cand.value), abs(target), abs_tol=cand.tol, rel_tol=1e-9)):
            return True, "数值一致，负号以文字表述（如“下降”）"
    return False, ""


def _sign_context(text: str, start: int) -> bool:
    """数字前、同一分句内（向前最多 10 个字）是否有负向措辞。"""
    left = max(text.rfind(ch, 0, start) for ch in "，,、。；;：:\n（(") + 1
    return bool(NEG_WORDS.search(text[max(left, start - 10):start]))


def _attribute(display: float, num: str, unit: str | None, refs: dict[str, Evidence], sentence: str, *,
               signed: bool = False, negative_words: bool = False) -> Evidence | None:
    """为未标注引用的数字寻找唯一可信的证据归属：数值一致（不允许正负号翻转）且
    证据名称出现在同一句中；否则仅在“有效数字较多且全局唯一”时归属，避免巧合匹配。"""
    hits = []
    for ev in refs.values():
        ok, note = _matches(display, num, unit, ev, signed=signed, negative_words=negative_words)
        if ok and not note:
            hits.append(ev)
    if not hits:
        return None
    named = [ev for ev in hits if ev.label and ev.label.split("（")[0] in sentence]
    if named:
        return named[0]
    digits = len(num.replace(",", "").replace(".", "").lstrip("0"))
    return hits[0] if len(hits) == 1 and digits >= 4 else None


def _sentence_at(text: str, pos: int) -> str:
    left = max(text.rfind(ch, 0, pos) for ch in "。；\n!?！？") + 1
    rights = [i for i in (text.find(ch, pos) for ch in "。；\n!?！？") if i != -1]
    right = min(rights) + 1 if rights else len(text)
    return text[left:right].strip()


def verify(text: str, refs: dict[str, Evidence]) -> VerificationResult:
    """refs：引用编号 → 证据（报告上下文中暴露给模型的证据）。"""
    # 1) 屏蔽引用与日期，记录引用组位置
    cites: list[tuple[int, int, list[str]]] = []
    for m in CITE_RE.finditer(text):
        ids = [x.strip() for x in re.split(r"[,，、]", m.group(1))]
        cites.append((m.start(), m.end(), ids))
    masked = list(text)
    for s, e, _ in cites:
        masked[s:e] = ["§"] * (e - s)  # 用非空白占位，避免被数字后的空白匹配吞掉
    for m in DATE_RE.finditer(text):
        masked[m.start():m.end()] = ["#"] * (m.end() - m.start())
    mtext = "".join(masked)

    claims: list[Claim] = []
    numbers = list(NUM_RE.finditer(mtext))
    for idx, m in enumerate(numbers):
        num, unit = m.group("num"), m.group("unit")
        raw = num.replace(",", "")
        # 跳过：年份、计数/序号、列表编号、Markdown 标题序号
        if not unit and YEAR_RE.match(raw):
            continue
        if not unit and SKIP_AFTER.match(mtext[m.end():m.end() + 3]):
            continue
        if not unit and FORMAT_COUNT.match(mtext[m.end():m.end() + 8]):
            continue
        line_start = mtext.rfind("\n", 0, m.start()) + 1
        prefix = mtext[line_start:m.start()].strip()
        line_end = mtext.find("\n", m.end())
        rest_of_line = mtext[m.end():line_end if line_end != -1 else len(mtext)]
        if not unit and prefix.startswith("#") and "§" not in rest_of_line:
            continue  # 标题中无单位、无引用的数字（如“前 3 大风险”）不是财务断言
        if not unit and prefix in ("", "-", "*", "#", "##", "###") and mtext[m.end():m.end() + 1] in (".", "、", ")", "）"):
            continue
        if not unit and "." not in raw and float(raw) < 10 and re.search(r"(第|前|近|共|约)\s*$", mtext[max(0, m.start() - 3):m.start()]):
            continue
        sign = -1.0 if m.group("sign") in ("-", "−") else 1.0
        signed = bool(m.group("sign"))
        neg_words = _sign_context(mtext, m.start())
        display = sign * float(raw)
        # 2) 关联引用：数字之后、下一个数字之前（同一句内）的第一个引用组
        next_start = numbers[idx + 1].start() if idx + 1 < len(numbers) else len(text)
        sent_end_candidates = [i for i in (text.find(ch, m.end()) for ch in "。；\n") if i != -1]
        sent_end = min(sent_end_candidates) if sent_end_candidates else len(text)
        limit = min(next_start, sent_end + 1)
        group = next(((s, e, ids) for s, e, ids in cites if m.end() <= s <= limit), None)
        ids = group[2] if group else []
        claim = Claim(text=text[m.start():m.end()].strip(), start=m.start(), end=m.end(), value=display, unit=unit,
                      refs=ids, status="unverified", sentence=_sentence_at(text, m.start()))
        if ids:
            missing = [i for i in ids if i not in refs]
            if missing and len(missing) == len(ids):
                claim.status = "invalid_ref"
                claim.message = f"引用编号 {'、'.join(missing)} 不在证据清单中"
            else:
                hit = None
                sign_note = ""
                for i in ids:
                    ev = refs.get(i)
                    if ev is None:
                        continue
                    ok, note = _matches(display, num, unit, ev, signed=signed, negative_words=neg_words)
                    if not ok and note:
                        sign_note = note
                    if ok:
                        hit = (i, ev, note)
                        break
                if hit:
                    claim.status, claim.evidence_id = "verified", hit[1].id
                    claim.expected = fmt_value(hit[1].value, hit[1].unit)
                    claim.message = hit[2]
                else:
                    first = refs[next(i for i in ids if i in refs)]
                    claim.status, claim.evidence_id = "mismatch", first.id
                    claim.expected = fmt_value(first.value, first.unit)
                    claim.message = (f"与 {ids[0]}（{first.label}）的数值 {claim.expected} 方向不一致：{sign_note}" if sign_note
                                     else f"与 {ids[0]}（{first.label}）的数值 {claim.expected} 不一致")
        else:
            hit_ev = _attribute(display, num, unit, refs, claim.sentence, signed=signed, negative_words=neg_words)
            if hit_ev is not None:
                claim.status, claim.evidence_id = "unattributed", hit_ev.id
                claim.expected = fmt_value(hit_ev.value, hit_ev.unit)
                claim.message = f"数值与证据“{hit_ev.label}”一致，但缺少引用标注"
            else:
                claim.message = "没有任何证据支持该数字（可能为自行计算或臆测）"
        claims.append(claim)

    flags = [f"疑似投资建议用语：“{m.group(0)}”" for m in ADVICE_RE.finditer(text)]
    total = len(claims)
    count = {s: sum(1 for c in claims if c.status == s)
             for s in ("verified", "unattributed", "mismatch", "invalid_ref", "unverified")}
    return VerificationResult(
        claims=claims, total=total, **count,
        verifiable_rate=((count["verified"] + count["unattributed"]) / total) if total else 1.0,
        citation_rate=(count["verified"] / total) if total else 1.0,
        compliance_flags=flags,
        consistency_flags=check_consistency(text, refs),
    )


def annotate(text: str, result: VerificationResult) -> str:
    """把未通过核验的数字在文本中显式标出（倒序替换，保持偏移有效）。"""
    out = text
    for c in sorted(result.problems, key=lambda c: c.start, reverse=True):
        out = out[:c.end] + "⚠️〔未核验〕" + out[c.end:]
    if result.consistency_flags:
        out += "\n\n" + "\n".join(f"> ⚠️ {f}" for f in result.consistency_flags)
    return out
