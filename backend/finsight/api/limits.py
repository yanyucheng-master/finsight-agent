"""在线演示的实时生成限额（次数）。

实时调用会花钱，所以演示站点要有两道闸：这里的“每天全站 / 每访客”次数，以及网关里的
LLM_BUDGET_CNY 进程级费用保护。次数持久化到文件，进程重启不会清零（日期按北京时间）。
缓存样例与模板简报不消耗限额。
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

CN_TZ = timezone(timedelta(hours=8))


class LimitExceeded(RuntimeError):
    pass


class DemoLimiter:
    def __init__(self, *, daily_limit: int = 0, per_ip_limit: int = 0, path: Path | None = None):
        self.daily_limit = max(0, daily_limit)
        self.per_ip_limit = max(0, per_ip_limit)
        self.path = path
        self._lock = threading.Lock()
        self._day = ""
        self._total = 0
        self._by_ip: dict[str, int] = {}
        self._load()

    @property
    def limited(self) -> bool:
        return bool(self.daily_limit or self.per_ip_limit)

    @staticmethod
    def _key(ip: str) -> str:
        return hashlib.sha256(ip.encode()).hexdigest()[:16]  # 不把访客 IP 明文写进文件

    @staticmethod
    def _today() -> str:
        return datetime.now(CN_TZ).strftime("%Y-%m-%d")

    def _roll(self) -> None:
        today = self._today()
        if self._day != today:
            self._day, self._total, self._by_ip = today, 0, {}

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._day = str(data.get("day", ""))
            self._total = int(data.get("total", 0))
            self._by_ip = {str(k): int(v) for k, v in dict(data.get("by_ip", {})).items()}
        except (OSError, ValueError, TypeError):
            self._day, self._total, self._by_ip = "", 0, {}

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"day": self._day, "total": self._total, "by_ip": self._by_ip}),
                                 encoding="utf-8")
        except OSError:
            pass  # 写不了文件时退化为仅内存计数

    def status(self, ip: str) -> dict:
        with self._lock:
            self._roll()
            used_ip = self._by_ip.get(self._key(ip), 0)
            return {"limited": self.limited, "daily_limit": self.daily_limit, "daily_used": self._total,
                    "daily_remaining": max(0, self.daily_limit - self._total) if self.daily_limit else None,
                    "per_ip_limit": self.per_ip_limit,
                    "per_ip_remaining": max(0, self.per_ip_limit - used_ip) if self.per_ip_limit else None}

    def release(self, ip: str) -> None:
        """退还一次额度（任务根本没能开始时）。"""
        with self._lock:
            self._roll()
            key = self._key(ip)
            if self._total > 0 and self._by_ip.get(key, 0) > 0:
                self._total -= 1
                self._by_ip[key] -= 1
                self._save()

    def reserve(self, ip: str) -> None:
        """占用一次实时生成额度；超限抛 LimitExceeded。"""
        with self._lock:
            self._roll()
            if self.daily_limit and self._total >= self.daily_limit:
                raise LimitExceeded("今日在线实时生成次数已用完，请查看缓存样例，或明天再试")
            key = self._key(ip)
            if self.per_ip_limit and self._by_ip.get(key, 0) >= self.per_ip_limit:
                raise LimitExceeded(f"每位访客每天可实时生成 {self.per_ip_limit} 次，您今天的次数已用完；缓存样例不受限制")
            self._total += 1
            self._by_ip[key] = self._by_ip.get(key, 0) + 1
            self._save()
