"""快照仓库：读取采集器写入的 manifest.jsonl 与内容寻址的原始响应。

设计要点
* 只读、可复现：分析引擎只从快照读取数据，同一快照 → 同一结果；
* 防篡改：每次读取原始对象都重新计算 SHA-256 并与 manifest 记录比对；
* 可追溯：每条记录保留 URL、请求参数、抓取时间、HTTP 状态，供证据链展示。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SnapshotIntegrityError(RuntimeError):
    """原始对象的哈希与 manifest 不一致（文件被修改或损坏）。"""


@dataclass(frozen=True)
class SnapshotRecord:
    id: str
    run_id: str
    provider: str
    dataset: str
    symbol: str | None
    url: str
    method: str
    form: dict[str, Any] | None
    fetched_at: str
    status: int | None
    content_type: str
    size: int
    sha256: str | None
    object: str | None
    error: str | None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.object is not None and self.sha256 is not None

    def public(self) -> dict[str, Any]:
        """对外展示的来源信息（不含本地路径）。"""
        return {
            "snapshot_id": self.id,
            "provider": self.provider,
            "dataset": self.dataset,
            "url": self.url,
            "method": self.method,
            "fetched_at": self.fetched_at,
            "status": self.status,
            "sha256": self.sha256,
            "size": self.size,
            "run_id": self.run_id,
        }

    @classmethod
    def from_json(cls, row: dict[str, Any]) -> SnapshotRecord:
        req = row.get("request") or {}
        return cls(
            id=row["id"],
            run_id=row.get("run_id", ""),
            provider=row.get("provider", ""),
            dataset=row.get("dataset", ""),
            symbol=row.get("symbol"),
            url=req.get("url") or row.get("final_url") or "",
            method=req.get("method", "GET"),
            form=req.get("form"),
            fetched_at=row.get("fetched_at", ""),
            status=row.get("status"),
            content_type=row.get("content_type", ""),
            size=int(row.get("size") or 0),
            sha256=row.get("sha256"),
            object=row.get("object"),
            error=row.get("error"),
            meta=row.get("meta") or {},
        )


class SnapshotStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._records: list[SnapshotRecord] = []
        self._by_id: dict[str, SnapshotRecord] = {}
        self._loaded_size = -1
        self.reload()

    # -- 加载 ---------------------------------------------------------------------
    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.jsonl"

    def reload(self) -> None:
        records: list[SnapshotRecord] = []
        if self.manifest_path.exists():
            with self.manifest_path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(SnapshotRecord.from_json(json.loads(line)))
                    except (json.JSONDecodeError, KeyError):
                        continue  # 容忍被截断的最后一行（采集中断）
            self._loaded_size = self.manifest_path.stat().st_size
        self._records = records
        self._by_id = {r.id: r for r in records}

    def refresh_if_changed(self) -> None:
        size = self.manifest_path.stat().st_size if self.manifest_path.exists() else -1
        if size != self._loaded_size:
            self.reload()

    # -- 查询 ---------------------------------------------------------------------
    def get(self, record_id: str) -> SnapshotRecord | None:
        return self._by_id.get(record_id)

    def symbols(self) -> list[str]:
        return sorted({r.symbol for r in self._records if r.symbol and r.ok})

    def find(self, *, provider: str, dataset: str, symbol: str | None = None) -> list[SnapshotRecord]:
        """返回成功记录，按抓取时间升序。"""
        out = [r for r in self._records
               if r.ok and r.provider == provider and r.dataset == dataset and (symbol is None or r.symbol == symbol)]
        out.sort(key=lambda r: (r.fetched_at, r.id))
        return out

    def latest_run(self, *, provider: str, dataset: str, symbol: str | None = None) -> list[SnapshotRecord]:
        """同一数据集可能分多次请求（分页 / 分块），取最近一次采集批次中的全部记录。"""
        records = self.find(provider=provider, dataset=dataset, symbol=symbol)
        if not records:
            return []
        last_run = records[-1].run_id
        return [r for r in records if r.run_id == last_run]

    def runs(self) -> list[str]:
        return sorted({r.run_id for r in self._records})

    def iter_records(self) -> Iterable[SnapshotRecord]:
        return iter(self._records)

    # -- 读取原始对象（带完整性校验）---------------------------------------------
    def read_bytes(self, record: SnapshotRecord, *, verify: bool = True) -> bytes:
        if not record.ok:
            raise FileNotFoundError(f"快照记录 {record.id} 没有可用对象（error={record.error}）")
        path = self.root / record.object  # type: ignore[operator]
        data = path.read_bytes()
        if verify:
            digest = hashlib.sha256(data).hexdigest()
            if digest != record.sha256:
                raise SnapshotIntegrityError(
                    f"快照对象哈希不一致: {record.object} 期望 {record.sha256} 实际 {digest}")
        return data

    def read_json(self, record: SnapshotRecord, *, verify: bool = True) -> Any:
        raw = self.read_bytes(record, verify=verify)
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                return json.loads(raw.decode(enc))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        raise ValueError(f"快照对象不是合法 JSON: {record.object}")

    def object_path(self, record: SnapshotRecord) -> Path:
        return self.root / (record.object or "")
