"""缓存样例：把真实大模型生成过的简报 / 问答存成样例，在线演示时“秒开”，不消耗限额。

入库时不是原样复制，而是**用当前版本的核验器重新核验一遍**：按样例当时的引用映射（E# → 证据 ID）
取回现有证据，重跑 verify，样例里展示的核验结果因此总是最新核验器给出的，而不是生成时旧版本的结论。
引用的证据在当前快照里找不到时拒绝入库（说明数据或代码已变，样例不再可信）。

样例文件放在 data/samples/，随仓库提交。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from finsight.agent.research_agent import AgentAnswer
from finsight.analytics.diagnosis import DiagnosisResult
from finsight.domain import Evidence
from finsight.reporting.report import Report, data_asof
from finsight.reporting.verifier import annotate, verify

NOTE_SPLIT = "\n\n> 数据时效（系统生成）"


class SampleError(ValueError):
    pass


def _strip_note(text: str) -> tuple[str, str]:
    """把末尾由系统追加的“数据时效”行分离出来：核验只针对模型写的正文。"""
    i = text.find(NOTE_SPLIT)
    return (text[:i], text[i:]) if i >= 0 else (text, "")


def _summary_evidence(result: DiagnosisResult) -> Evidence:
    cs = result.checks_summary
    return Evidence(id="c:summary", kind="check", label="数据核验汇总", unit="text",
                    value=f"通过 {cs['pass']} 项，未通过 {cs['fail']} 项，跳过 {cs['skip']} 项")


def _verification_brief(v) -> dict[str, Any]:
    return {"total": v.total, "verified": v.verified, "unattributed": v.unattributed, "mismatch": v.mismatch,
            "invalid_ref": v.invalid_ref, "unverified": v.unverified, "benchmark": v.benchmark,
            "passed": v.passed}


def build_report_sample(raw: dict[str, Any], result: DiagnosisResult, *, source: str) -> dict[str, Any]:
    """raw 是 generate_report 保存下来的 Report JSON。"""
    if raw.get("mode") != "llm":
        raise SampleError("只有大模型生成的简报才需要缓存（模板简报是即时、免费的）")
    if raw.get("code") != result.profile.code:
        raise SampleError(f"简报公司 {raw.get('code')} 与体检结果 {result.profile.code} 不一致")
    refs: dict[str, Evidence] = {}
    for ref, eid in dict(raw["refs"]).items():
        if eid == "c:summary":
            refs[ref] = _summary_evidence(result)
        elif eid in result.evidence:
            refs[ref] = result.evidence[eid]
        else:
            raise SampleError(f"引用的证据 {eid} 在当前快照里不存在，样例不再可信")
    body, _ = _strip_note(raw["markdown"])
    v = verify(body, refs)
    note = f"\n\n> 数据时效（系统生成）：{data_asof(result)}。"
    warnings = [w for w in raw.get("warnings", []) if not w.startswith("口径提示")] + list(v.consistency_flags)
    report = Report(code=raw["code"], name=raw["name"], mode="llm", model=raw.get("model"), markdown=body + note,
                    annotated_markdown=annotate(body, v) + note, refs=dict(raw["refs"]), verification=v,
                    attempts=int(raw.get("attempts", 1)), revised=bool(raw.get("revised", False)),
                    usage=dict(raw.get("usage", {})), warnings=warnings, generated_at=raw["generated_at"],
                    elapsed_ms=int(raw.get("elapsed_ms", 0)), data_asof=data_asof(result))
    return _wrap("report", report.model_dump(mode="json"), name=report.name, code=report.code,
                 title=f"{report.name} 研究简报", model=report.model, generated_at=report.generated_at, v=v,
                 source=source, elapsed_ms=report.elapsed_ms)


def build_ask_sample(raw: dict[str, Any], result: DiagnosisResult, *, source: str) -> dict[str, Any]:
    """raw 是 AgentAnswer JSON。同业证据来自别的公司，所以直接用样例里保存的证据对象。"""
    # 早期输出没有 ok 字段：调用失败的回答 attempts 为 0（核验结果 passed=True 对它没有意义）
    if raw.get("ok") is False or not str(raw.get("answer", "")).strip() or int(raw.get("attempts", 0)) <= 0:
        raise SampleError("回答无效（调用失败或为空），不能作为样例")
    refs: dict[str, Evidence] = {}
    for ref, eid in dict(raw["refs"]).items():
        stored = raw.get("evidence", {}).get(eid)
        if stored is None:
            raise SampleError(f"样例缺少证据 {eid}")
        refs[ref] = Evidence.model_validate(stored)
    body, _ = _strip_note(raw["answer"])
    v = verify(body, refs)
    note = _strip_note(raw["answer"])[1]
    warnings = list(raw.get("warnings", [])) + [f for f in v.consistency_flags if f not in raw.get("warnings", [])]
    ans = AgentAnswer(question=raw["question"], answer=body + note, annotated_answer=annotate(body, v) + note,
                      refs=dict(raw["refs"]), evidence={e.id: e for e in refs.values()}, verification=v,
                      steps=raw.get("steps", []), attempts=int(raw.get("attempts", 1)), model=raw.get("model"),
                      usage=dict(raw.get("usage", {})), warnings=warnings,
                      elapsed_ms=int(raw.get("elapsed_ms", 0)), ok=True)
    return _wrap("ask", ans.model_dump(mode="json"), name=result.profile.name, code=result.profile.code,
                 title=raw["question"], model=ans.model, generated_at=raw.get("generated_at", ""), v=v,
                 source=source, elapsed_ms=ans.elapsed_ms)


def _wrap(kind: str, result: dict, *, name: str, code: str, title: str, model: str | None, generated_at: str, v,
          source: str, elapsed_ms: int) -> dict[str, Any]:
    slug = re.sub(r"[^0-9A-Za-z]+", "-", f"{kind}-{code}-{model or 'model'}-{hashlib.sha1(title.encode()).hexdigest()[:6]}").strip("-")
    return {"id": slug, "kind": kind, "code": code, "name": name, "title": title, "model": model,
            "generated_at": generated_at, "source": source, "elapsed_ms": elapsed_ms,
            "reverified": True, "verification": _verification_brief(v), "result": result}


class SampleStore:
    def __init__(self, directory: Path):
        self.dir = directory

    def _files(self) -> list[Path]:
        return sorted(self.dir.glob("*.json")) if self.dir.exists() else []

    def list(self) -> list[dict[str, Any]]:
        out = []
        for f in self._files():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({k: d[k] for k in ("id", "kind", "code", "name", "title", "model", "generated_at", "source",
                                          "elapsed_ms", "reverified", "verification") if k in d})
        return out

    def get(self, sample_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[0-9A-Za-z\-]+", sample_id):
            return None
        f = self.dir / f"{sample_id}.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, sample: dict[str, Any]) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{sample['id']}.json"
        path.write_text(json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")
        return path
