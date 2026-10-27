"""东方财富 F10 / 巨潮资讯 原始响应解析：快照记录 → 标准化领域对象。

解析是纯函数：输入快照记录与其 JSON，输出带 SourceRef 的事实。解析过程中
任何“看不懂”的结构都显式记录为 parse_issues，而不是静默丢弃。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from finsight.data.schema import ITEMS, REPORT_TYPE_TO_PERIOD, Item
from finsight.data.snapshots import SnapshotRecord
from finsight.domain import EM_COMPANY_TYPES, CompanyType, Fact, Period, SourceRef


def _date10(value: Any) -> str | None:
    if not value:
        return None
    s = str(value)[:10]
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


def source_ref(record: SnapshotRecord, *, field: str | None = None, report_date: str | None = None,
               notice_date: str | None = None) -> SourceRef:
    return SourceRef(
        provider=record.provider,
        dataset=record.dataset,
        snapshot_id=record.id,
        sha256=record.sha256 or "",
        url=record.url,
        fetched_at=record.fetched_at,
        field=field,
        report_date=report_date,
        notice_date=notice_date,
    )


def company_type_from_index(html: bytes) -> CompanyType:
    m = re.search(rb'id="hidctype"\s+value="(\d)"', html) or re.search(rb'value="(\d)"\s+id="hidctype"', html)
    return EM_COMPANY_TYPES.get(m.group(1).decode(), "unknown") if m else "unknown"


def _pick(row: dict[str, Any], item: Item) -> tuple[float, str] | None:
    for field, sign in item.fields:
        raw = row.get(field)
        if raw is None or raw == "":
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        return (value * sign if value != 0 else 0.0), field
    return None


def parse_period(row: dict[str, Any]) -> Period | None:
    end = _date10(row.get("REPORT_DATE"))
    if not end:
        return None
    rtype = str(row.get("REPORT_TYPE") or "")
    ptype = REPORT_TYPE_TO_PERIOD.get(rtype)
    if ptype is None:
        month = end[5:7]
        ptype = {"12": "FY", "06": "H1", "03": "Q1", "09": "Q3"}.get(month, "OTHER")
    return Period(
        end=end,
        type=ptype,  # type: ignore[arg-type]
        label=str(row.get("REPORT_DATE_NAME") or f"{end[:4]}{rtype}"),
        fiscal_year=int(end[:4]),
        notice_date=_date10(row.get("NOTICE_DATE")),
        update_date=_date10(row.get("UPDATE_DATE")),
        audit_opinion=(str(row["OPINION_TYPE"]).strip() or None) if row.get("OPINION_TYPE") else None,
    )


def parse_statement_rows(record: SnapshotRecord, payload: Any, statement: str) -> tuple[list[Period], list[Fact], list[str]]:
    """解析一个报表分块响应。返回 (报告期, 事实, 解析问题)。"""
    issues: list[str] = []
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return [], [], [f"{record.dataset}: 响应缺少 data 列表"]
    periods: list[Period] = []
    facts: list[Fact] = []
    items = [it for it in ITEMS if it.statement == statement]
    for row in payload["data"]:
        if not isinstance(row, dict):
            continue
        period = parse_period(row)
        if period is None:
            issues.append(f"{record.dataset}: 无法解析报告期 {row.get('REPORT_DATE')!r}")
            continue
        periods.append(period)
        for item in items:
            picked = _pick(row, item)
            if picked is None:
                continue
            value, field = picked
            facts.append(Fact(
                item=item.code,
                period=period.end,
                value=value,
                source=source_ref(record, field=field, report_date=period.end, notice_date=period.notice_date),
            ))
    return periods, facts, issues


def parse_main_indicators(record: SnapshotRecord, payload: Any) -> dict[str, dict[str, Any]]:
    """东方财富计算的主要财务指标，按报告期索引（用于交叉核验）。"""
    rows = ((payload or {}).get("result") or {}).get("data") or []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        end = _date10(row.get("REPORT_DATE"))
        if end:
            out[end] = row
    return out


def parse_survey(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    basic = (payload.get("jbzl") or [{}])[0] or {}
    issue = (payload.get("fxxg") or [{}])[0] or {}
    return {
        "name": basic.get("SECURITY_NAME_ABBR") or basic.get("STR_NAMEA"),
        "full_name": basic.get("ORG_NAME"),
        "industry_em": basic.get("EM2016"),
        "industry_csrc": basic.get("INDUSTRYCSRC1"),
        "auditor": basic.get("ACCOUNTFIRM_NAME"),
        "province": basic.get("PROVINCE"),
        "business_scope": basic.get("BUSINESS_SCOPE"),
        "profile": (basic.get("ORG_PROFILE") or "").strip() or None,
        "listing_date": _date10(issue.get("LISTING_DATE")),
        "security_type": basic.get("SECURITY_TYPE"),
    }


_TAG_RE = re.compile(r"<[^>]+>")
CST = timezone(timedelta(hours=8))  # 巨潮公告时间戳为北京时间零点


def parse_announcements(record: SnapshotRecord, payload: Any) -> list[dict[str, Any]]:
    anns = (payload or {}).get("announcements") or []
    out = []
    for a in anns:
        if not isinstance(a, dict):
            continue
        ts = a.get("announcementTime")
        try:
            date = datetime.fromtimestamp(int(ts) / 1000, tz=CST).strftime("%Y-%m-%d") if ts else None
        except (TypeError, ValueError, OSError):
            date = None
        title = _TAG_RE.sub("", str(a.get("announcementTitle") or "")).strip()
        adjunct = a.get("adjunctUrl")
        out.append({
            "id": str(a.get("announcementId") or ""),
            "title": title,
            "date": date,
            "type": a.get("announcementTypeName"),
            "pdf_url": f"https://static.cninfo.com.cn/{adjunct}" if adjunct else None,
            "size_kb": a.get("adjunctSize"),
            "snapshot_id": record.id,
        })
    return out
