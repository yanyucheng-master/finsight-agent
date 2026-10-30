"""后台任务：简报 / 问答在线程里运行，前端轮询阶段进度。

内存实现：进程重启后任务丢失，演示用途足够；结果以 JSON 字典保存，和同步接口返回的结构一致。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from finsight.progress import Progress

# 各类任务的阶段顺序，前端据此画进度条
STAGES = {
    "report": ["evidence", "drafting", "verifying", "revising", "done"],
    "ask": ["thinking", "tool", "verifying", "revising", "done"],
}


class JobsBusy(RuntimeError):
    """排队任务太多。"""


@dataclass
class Job:
    id: str
    kind: str
    code: str
    live: bool  # 是否会调用大模型（计入限额）
    status: str = "queued"  # queued / running / done / failed
    stage: str = "queued"
    detail: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created: float = field(default_factory=time.monotonic)
    started: float | None = None
    finished: float | None = None

    def elapsed_s(self) -> float:
        end = self.finished if self.finished is not None else time.monotonic()
        return round(end - (self.started if self.started is not None else self.created), 1)

    def public(self, *, include_result: bool = True) -> dict[str, Any]:
        out = {"id": self.id, "kind": self.kind, "code": self.code, "live": self.live, "status": self.status,
               "stage": self.stage, "detail": self.detail, "events": list(self.events), "elapsed_s": self.elapsed_s(),
               "stages": STAGES.get(self.kind, []), "error": self.error}
        if include_result and self.status == "done":
            out["result"] = self.result
        return out


class JobManager:
    def __init__(self, max_workers: int = 2, ttl_s: float = 3600.0, max_jobs: int = 200):
        self.max_pending = max(2, max_workers * 3)
        self.ttl_s = ttl_s
        self.max_jobs = max_jobs
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="finsight-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, code: str, fn: Callable[[Progress], dict[str, Any]], *, live: bool) -> Job:
        with self._lock:
            self._prune()
            pending = sum(1 for j in self._jobs.values() if j.status in ("queued", "running"))
            if pending >= self.max_pending:
                raise JobsBusy("当前排队的生成任务过多，请稍后再试")
            job = Job(id=uuid.uuid4().hex[:12], kind=kind, code=code, live=live)
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, fn)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {"queued": sum(1 for j in self._jobs.values() if j.status == "queued"),
                    "running": sum(1 for j in self._jobs.values() if j.status == "running")}

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ 内部
    def _prune(self) -> None:
        now = time.monotonic()
        for jid in [k for k, j in self._jobs.items()
                    if j.finished is not None and now - j.finished > self.ttl_s]:
            del self._jobs[jid]
        if len(self._jobs) >= self.max_jobs:  # 兜底：只保留最新的一批已结束任务
            done = sorted((j for j in self._jobs.values() if j.finished is not None), key=lambda j: j.finished or 0)
            for j in done[: len(self._jobs) - self.max_jobs + 1]:
                del self._jobs[j.id]

    def _run(self, job: Job, fn: Callable[[Progress], dict[str, Any]]) -> None:
        job.started = time.monotonic()
        job.status = "running"

        def progress(stage: str, detail: str = "") -> None:
            job.stage, job.detail = stage, detail
            job.events.append({"stage": stage, "detail": detail, "t": round(time.monotonic() - job.started, 1)})

        try:
            job.result = fn(progress)
            job.stage, job.detail = "done", ""
            job.status = "done"
        except Exception as exc:  # noqa: BLE001 - 任务异常转为失败状态返回给前端
            job.error = f"{type(exc).__name__}: {str(exc)[:300]}"
            job.status = "failed"
        finally:
            job.finished = time.monotonic()
