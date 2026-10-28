"""大模型网关：OpenAI 兼容的 Chat Completions 客户端 + 测试用 Mock。

* 支持 DeepSeek / Kimi / 书生等任何 OpenAI 兼容接口（见 config.LLM_PROVIDERS）；
* 对 429 / 5xx / 网络错误做有限次退避重试；4xx 立即失败并给出可读错误；
* 记录每次调用的模型、耗时与 token 用量，供成本统计与评测使用；
* MockLLM 按脚本返回结果，使核验、修订循环、智能体流程可以在离线环境中被完整测试。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from finsight.config import LLMSettings


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    finish_reason: str | None = None

    def as_message(self) -> dict[str, Any]:
        """转为可追加到对话历史的 assistant 消息。"""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                  "function": {"name": tc.name, "arguments": tc.raw_arguments or json.dumps(tc.arguments, ensure_ascii=False)}}
                                 for tc in self.tool_calls]
        return msg


class LLMClient(Protocol):
    model: str

    def chat(self, messages: Sequence[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
             temperature: float | None = None, max_tokens: int | None = None,
             json_mode: bool = False) -> LLMResponse: ...


@dataclass
class UsageMeter:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0

    def add(self, resp: LLMResponse) -> None:
        self.calls += 1
        self.prompt_tokens += int(resp.usage.get("prompt_tokens", 0))
        self.completion_tokens += int(resp.usage.get("completion_tokens", 0))
        self.latency_ms += resp.latency_ms

    def as_dict(self) -> dict[str, int]:
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "latency_ms": self.latency_ms}


def _parse_tool_calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
    out = []
    for tc in raw or []:
        fn = tc.get("function") or {}
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
        except json.JSONDecodeError:
            args = {"__unparsed__": args_raw}
        out.append(ToolCall(id=tc.get("id") or f"call_{len(out)}", name=fn.get("name", ""), arguments=args,
                            raw_arguments=args_raw if isinstance(args_raw, str) else json.dumps(args_raw)))
    return out


class OpenAICompatibleClient:
    def __init__(self, settings: LLMSettings, *, retries: int = 3, transport: httpx.BaseTransport | None = None):
        if not settings.api_key:
            raise LLMError(f"未配置 {settings.provider} 的 API Key（请在项目根目录 .env 中设置）")
        self.settings = settings
        self.model = settings.model
        self.retries = retries
        self._client = httpx.Client(timeout=settings.timeout_s, transport=transport)

    @property
    def url(self) -> str:
        return self.settings.base_url.rstrip("/") + "/chat/completions"

    def chat(self, messages, *, tools=None, temperature=None, max_tokens=None, json_mode=False) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": list(messages),
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.settings.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"}
        last: str = ""
        for attempt in range(self.retries + 1):
            started = time.monotonic()
            try:
                r = self._client.post(self.url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last = f"网络错误：{type(exc).__name__}: {exc}"
            else:
                if r.status_code == 200:
                    data = r.json()
                    choice = (data.get("choices") or [{}])[0]
                    msg = choice.get("message") or {}
                    return LLMResponse(
                        content=msg.get("content") or "",
                        tool_calls=_parse_tool_calls(msg.get("tool_calls")),
                        model=data.get("model") or self.settings.model,
                        usage={k: int(v) for k, v in (data.get("usage") or {}).items() if isinstance(v, int)},
                        latency_ms=int((time.monotonic() - started) * 1000),
                        finish_reason=choice.get("finish_reason"),
                    )
                body = r.text[:300]
                last = f"HTTP {r.status_code}: {body}"
                if r.status_code in (400, 401, 403, 404, 422):
                    raise LLMError(f"{self.settings.provider} 调用失败（{last}）")
            time.sleep(min(2 ** attempt, 8))
        raise LLMError(f"{self.settings.provider} 调用多次重试后仍失败（{last}）")

    def list_models(self) -> list[str]:
        r = self._client.get(self.settings.base_url.rstrip("/") + "/models",
                             headers={"Authorization": f"Bearer {self.settings.api_key}"})
        r.raise_for_status()
        return [m.get("id", "") for m in r.json().get("data", [])]


Responder = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], LLMResponse | str]


class MockLLM:
    """测试替身：按顺序返回脚本化回复，或调用 responder(messages, tools) 生成回复。记录全部请求。"""

    def __init__(self, script: Sequence[LLMResponse | str] | None = None, responder: Responder | None = None,
                 model: str = "mock-llm"):
        self.script = list(script or [])
        self.responder = responder
        self.model = model
        self.requests: list[dict[str, Any]] = []

    def chat(self, messages, *, tools=None, temperature=None, max_tokens=None, json_mode=False) -> LLMResponse:
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools, "json_mode": json_mode})
        if self.responder is not None:
            out = self.responder(list(messages), tools)
        elif self.script:
            out = self.script.pop(0)
        else:
            raise LLMError("MockLLM 脚本已用尽")
        resp = out if isinstance(out, LLMResponse) else LLMResponse(content=out)
        resp.model = resp.model or self.model
        resp.usage = resp.usage or {"prompt_tokens": sum(len(str(m.get("content", ""))) for m in messages) // 2,
                                    "completion_tokens": len(resp.content) // 2}
        return resp


def make_client(settings: LLMSettings) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(settings)
