"""生成过程的进度回调。

简报与问答由推理模型驱动，一次要等 0.5–2 分钟；回调让前端能显示“整理证据 → 撰写 → 核验 → 修订”的阶段，
而不是一个无反馈的转圈。回调只用于展示：它抛出任何异常都不能影响生成结果。
"""

from __future__ import annotations

from collections.abc import Callable

# (阶段, 说明)。阶段取值：evidence / drafting / thinking / tool / verifying / revising
Progress = Callable[[str, str], None]


def emit(progress: Progress | None, stage: str, detail: str = "") -> None:
    if progress is None:
        return
    try:
        progress(stage, detail)
    except Exception:  # noqa: BLE001 - 进度回调失败不得影响生成
        pass
