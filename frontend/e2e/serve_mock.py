"""端到端测试用的模拟模型服务：真实快照 + 带延时的 MockLLM，不调用任何外部 API、不花钱。

    python frontend/e2e/serve_mock.py 8765      # 然后 BASE_URL=http://127.0.0.1:8765 node frontend/e2e/smoke_async.cjs

模拟的是演示站点配置：demo_mode 开启，每位访客每天 2 次实时生成额度（用临时目录记账，不污染 data/）。
MockLLM 的回答只引用它从证据清单 / 工具结果里读到的数字，所以核验应当通过。
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
import tempfile
import time
from pathlib import Path

import uvicorn

from finsight.api.app import create_app
from finsight.config import PROJECT_ROOT, load_settings
from finsight.llm.gateway import LLMResponse, MockLLM, ToolCall

DELAY_S = 1.5


def responder(messages, tools):
    time.sleep(DELAY_S)  # 让前端有机会显示每个阶段
    if tools is None:  # 简报：从证据清单里找毛利率，原样引用
        prompt = messages[1]["content"]
        m = re.search(r"\[(E\d+)\] \d{4}年 毛利率 = ([\d.,\-]+%)", prompt)
        body = f"毛利率为 {m.group(2)}[{m.group(1)}]。" if m else "证据清单中没有毛利率。"
        return LLMResponse(content=f"## 核心结论\n{body}\n## 数据与方法说明\n模拟模型生成，仅用于界面测试。")
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    if not tool_msgs:
        return LLMResponse(content="", tool_calls=[
            ToolCall(id="c1", name="get_metrics", arguments={"codes": ["gross_margin"], "years": 1})])
    try:
        item = json.loads(tool_msgs[-1]["content"])["values"][0]
        return LLMResponse(content=f"毛利率为 {item['value']}[{item['ref']}]。")
    except Exception:  # noqa: BLE001
        return LLMResponse(content="现有工具没有返回可引用的毛利率。")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    tmp = Path(tempfile.mkdtemp(prefix="finsight-e2e-"))
    (tmp / "samples").symlink_to(PROJECT_ROOT / "data" / "samples")  # 缓存样例用真实的
    base = load_settings()
    settings = dataclasses.replace(
        base, llm=dataclasses.replace(base.llm, api_key="mock-not-a-key"), data_dir=tmp, snapshot_dir=PROJECT_ROOT / "data" / "snapshots",
        demo_mode=True, live_per_ip_daily=2, live_daily_limit=10)
    app = create_app(settings=settings, llm_factory=lambda name: MockLLM(responder=responder, model="mock-llm"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
