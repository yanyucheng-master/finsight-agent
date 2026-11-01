"""大模型网关：OpenAI 兼容的 Chat Completions 客户端 + 测试用 Mock。

* 支持 DeepSeek / Kimi / 书生等任何 OpenAI 兼容接口（见 config.LLM_PROVIDERS）；
* 兼容推理模型：按模型族发送正确的推理强度参数、不向固定采样参数的模型发送 temperature、
  读取 reasoning_content 并在多轮 / 工具调用中原样回传（DeepSeek 带 tools 时不回传会 400，Kimi K3 文档要求回传）；
* 对 429 / 5xx / 网络错误做有限次退避重试；4xx 立即失败并给出可读错误；
* “HTTP 200 但正文为空且因长度截断”视为错误并给出原因（思考内容也计入 max_tokens）；
* 记录每次调用的模型、耗时与 token 用量（含推理 token、缓存命中 token），供成本统计与评测使用；
* MockLLM 按脚本返回结果，使核验、修订循环、智能体流程可以在离线环境中被完整测试。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from finsight.config import FALLBACK_PRICE_CNY, LLM_PRICES_CNY, LLMSettings


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
    reasoning: str = ""  # 推理模型的思考内容（reasoning_content）；只用于回传与排查，不展示、不作为答案

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"

    def as_message(self) -> dict[str, Any]:
        """转为可追加到对话历史的 assistant 消息（推理模型要求连同 reasoning_content 一起回传）。"""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.reasoning:
            msg["reasoning_content"] = self.reasoning
        if self.tool_calls:
            msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                  "function": {"name": tc.name, "arguments": tc.raw_arguments or json.dumps(tc.arguments, ensure_ascii=False)}}
                                 for tc in self.tool_calls]
        return msg


class LLMClient(Protocol):
    model: str

    def chat(self, messages: Sequence[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
             tool_choice: str | None = None, temperature: float | None = None, max_tokens: int | None = None,
             json_mode: bool = False) -> LLMResponse: ...


def estimate_cost_cny(model: str, usage: dict[str, int]) -> float:
    """按公开标价估算一次调用的费用（元，不含税）。"""
    hit_p, miss_p, out_p = LLM_PRICES_CNY.get(model, FALLBACK_PRICE_CNY)
    prompt = int(usage.get("prompt_tokens", 0))
    hit = min(prompt, int(usage.get("prompt_cache_hit_tokens", 0) or usage.get("cached_tokens", 0)))
    out = int(usage.get("completion_tokens", 0))
    return (hit * hit_p + (prompt - hit) * miss_p + out * out_p) / 1e6


class BudgetGuard:
    """进程级费用保护（环境变量 LLM_BUDGET_CNY，未设置或 ≤0 表示不限）。这是客户端估算，不是服务商的硬限额。

    * 每一次发送（包括重试）之前单独预留上界：输入 UTF-8 字节数 × 未命中单价 + max_tokens × 输出单价
      （一个 token 至少 1 字节，所以字节数是输入 token 的上界）；
    * 预留在同一把锁里原子地计入“占用”，并发请求不能共用同一份余额；
    * 成功：按返回用量结算；返回 HTTP 错误：服务端拒绝，不计费；超时 / 网络中断 / 无法解析：费用不明，按预留上界记账；
    * 设置了预算但模型不在价格表中：拒绝发送（预算不明确时停止）。
    """

    def __init__(self) -> None:
        self.spent = 0.0
        self.pending = 0.0
        self.calls = 0
        self._lock = threading.Lock()
        self._tl = threading.local()  # 每个线程同一时刻只有一次发送在途：reserve 压栈、settle 出栈
        self.listeners: list[Callable[[dict[str, Any]], None]] = []

    def add_listener(self, fn: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """登记账本监听器（评测脚本用它把每次发送持久化）。返回取消登记的函数。

        reserve 事件在请求发出**之前**调用，监听器抛异常 = 拒绝发送（账本写不进去就不花钱）；
        settle 事件在结算之后调用，监听器出错不影响调用方（该笔预留在账本里保持“未结算”，按上界计）。"""
        self.listeners.append(fn)
        return lambda: self.listeners.remove(fn) if fn in self.listeners else None

    @property
    def limit(self) -> float:
        try:
            return float(os.environ.get("LLM_BUDGET_CNY", "0") or 0)
        except ValueError:
            return 0.0

    def reserve(self, model: str, payload: dict[str, Any]) -> float:
        limit = self.limit
        if limit > 0 and model not in LLM_PRICES_CNY:
            raise LLMError(f"费用保护：模型 {model} 不在价格表 config.LLM_PRICES_CNY 中，无法估算费用，已停止发送")
        _, miss_p, out_p = LLM_PRICES_CNY.get(model, FALLBACK_PRICE_CNY)
        nbytes = len(json.dumps({k: payload.get(k) for k in ("messages", "tools")}, ensure_ascii=False).encode("utf-8"))
        amount = (nbytes * miss_p + int(payload.get("max_tokens", 0)) * out_p) / 1e6
        with self._lock:
            if limit > 0 and self.spent + self.pending + amount > limit:
                raise LLMError(f"费用保护：已估算花费 {self.spent:.4f} 元（另有 {self.pending:.4f} 元进行中），"
                               f"本次请求最多可能再花 {amount:.4f} 元，将超过预算 {limit:.2f} 元（LLM_BUDGET_CNY），已停止发送")
            rid = uuid.uuid4().hex[:12]
            for fn in list(self.listeners):  # 先持久化占用，再发送
                try:
                    fn({"ev": "reserve", "id": rid, "model": model, "reserved": amount})
                except Exception as exc:  # noqa: BLE001
                    raise LLMError(f"费用保护：无法持久化本次请求的费用占用（{type(exc).__name__}: {exc}），已停止发送") from exc
            self.pending += amount
            stack = self._tl.__dict__.setdefault("stack", [])
            stack.append(rid)
        return amount

    def settle(self, reserved: float, actual: float | None) -> None:
        """actual=None 表示费用不明，按预留上界记账。"""
        with self._lock:
            self.pending -= reserved
            charged = reserved if actual is None else actual
            self.spent += charged
            self.calls += 1
            stack = self._tl.__dict__.get("stack") or []
            rid = stack.pop() if stack else None
        if rid is not None:
            for fn in list(self.listeners):
                try:
                    fn({"ev": "settle", "id": rid, "charged": charged, "exact": actual is not None})
                except Exception:  # noqa: BLE001 —— 结算记录写不进去：账本里保持未结算，按预留上界计
                    pass


BUDGET = BudgetGuard()


@dataclass
class UsageMeter:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    cost_cny: float = 0.0

    def add(self, resp: LLMResponse) -> None:
        u = resp.usage
        self.cost_cny += estimate_cost_cny(resp.model, u) if u else 0.0
        self.calls += 1
        self.prompt_tokens += int(u.get("prompt_tokens", 0))
        self.completion_tokens += int(u.get("completion_tokens", 0))
        self.reasoning_tokens += int(u.get("reasoning_tokens", 0))
        self.cached_tokens += int(u.get("prompt_cache_hit_tokens", 0) or u.get("cached_tokens", 0))
        self.latency_ms += resp.latency_ms

    def as_dict(self) -> dict[str, int | float]:
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "reasoning_tokens": self.reasoning_tokens, "cached_tokens": self.cached_tokens,
                "latency_ms": self.latency_ms, "cost_cny_est": round(self.cost_cny, 6)}


def _parse_tool_calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
    out = []
    for tc in raw or []:
        fn = tc.get("function") or {}
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
        except json.JSONDecodeError:
            args = {"__unparsed__": args_raw}
        if not isinstance(args, dict):
            args = {"__unparsed__": args_raw}
        out.append(ToolCall(id=tc.get("id") or f"call_{len(out)}", name=fn.get("name", ""), arguments=args,
                            raw_arguments=args_raw if isinstance(args_raw, str) else json.dumps(args_raw)))
    return out


def _flatten_usage(raw: dict[str, Any] | None) -> dict[str, int]:
    """保留整数字段；嵌套字段（如 completion_tokens_details.reasoning_tokens）提到顶层。"""
    out: dict[str, int] = {}
    for k, v in (raw or {}).items():
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            out[k] = v
        elif isinstance(v, dict):
            for kk, vv in v.items():
                if isinstance(vv, int) and not isinstance(vv, bool):
                    out[kk if kk not in out else f"{k}.{kk}"] = vv
    return out


def reasoning_params(model: str, effort: str) -> tuple[dict[str, Any], bool]:
    """按模型族返回 (额外请求参数, 是否发送 temperature)。依据各家 2026-10 的官方文档，见 config.LLM_PROVIDERS 注释。"""
    m = model.lower()
    level = effort if effort in ("low", "high", "max") else "low"
    if m.startswith("kimi-k3"):  # 始终思考；采样参数固定
        return {"reasoning_effort": level}, False
    if m.startswith("kimi-k2.7"):  # 始终思考，thinking 只接受 enabled，干脆不传
        return {}, False
    if m.startswith("kimi-k2"):  # k2.5 / k2.6：可开关思考；temperature 固定
        return {"thinking": {"type": "disabled" if effort == "none" else "enabled"}}, False
    if m.startswith("kimi-"):
        return {}, False
    if m.startswith("deepseek"):
        if effort == "none":
            return {"thinking": {"type": "disabled"}}, True
        return {"thinking": {"type": "enabled"}, "reasoning_effort": level}, True
    return {}, True


class OpenAICompatibleClient:
    def __init__(self, settings: LLMSettings, *, retries: int = 3, transport: httpx.BaseTransport | None = None,
                 backoff_s: float = 1.0):
        if not settings.api_key:
            raise LLMError(f"未配置 {settings.provider} 的 API Key（请在项目根目录 .env 中设置）")
        self.settings = settings
        self.model = settings.model
        self.retries = retries
        self.backoff_s = backoff_s
        self._client = httpx.Client(timeout=httpx.Timeout(settings.timeout_s, connect=15.0), transport=transport)

    @property
    def url(self) -> str:
        return self.settings.base_url.rstrip("/") + "/chat/completions"

    def build_payload(self, messages, *, tools=None, tool_choice=None, temperature=None, max_tokens=None,
                      json_mode=False) -> dict[str, Any]:
        extra, send_temperature = reasoning_params(self.settings.model, self.settings.reasoning_effort)
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": list(messages),
            "max_tokens": max_tokens or self.settings.max_tokens,
            **extra,
        }
        if send_temperature:
            payload["temperature"] = self.settings.temperature if temperature is None else temperature
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def chat(self, messages, *, tools=None, tool_choice=None, temperature=None, max_tokens=None,
             json_mode=False) -> LLMResponse:
        payload = self.build_payload(messages, tools=tools, tool_choice=tool_choice, temperature=temperature,
                                     max_tokens=max_tokens, json_mode=json_mode)
        headers = {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"}
        last: str = ""
        for attempt in range(self.retries + 1):
            reserved = BUDGET.reserve(self.settings.model, payload)  # 每次发送（含重试）都单独预留
            started = time.monotonic()
            try:
                r = self._client.post(self.url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                BUDGET.settle(reserved, None)  # 超时 / 断线：服务端可能已计费，按上界记账
                last = f"网络错误：{type(exc).__name__}: {exc}"
            else:
                if r.status_code == 200:
                    try:
                        resp = self._parse(r, started, payload["max_tokens"])
                    except LLMError:
                        BUDGET.settle(reserved, None)
                        raise
                    BUDGET.settle(reserved, estimate_cost_cny(self.settings.model, resp.usage) if resp.usage else None)
                    return resp
                BUDGET.settle(reserved, 0.0)
                last = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code in (400, 401, 402, 403, 404, 422):
                    raise LLMError(f"{self.settings.provider} 调用失败（{last}）")
            if attempt < self.retries:
                time.sleep(min(self.backoff_s * 2 ** attempt, 8))
        raise LLMError(f"{self.settings.provider} 调用多次重试后仍失败（{last}）")

    def _parse(self, r: httpx.Response, started: float, max_tokens: int) -> LLMResponse:
        try:
            data = r.json()  # DeepSeek 排队时会先发送空行保活，json 解析会忽略首部空白
        except ValueError as exc:
            raise LLMError(f"{self.settings.provider} 返回的不是 JSON：{r.text[:200]}") from exc
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        resp = LLMResponse(
            content=msg.get("content") or "",
            tool_calls=_parse_tool_calls(msg.get("tool_calls")),
            model=data.get("model") or self.settings.model,
            usage=_flatten_usage(data.get("usage")),
            latency_ms=int((time.monotonic() - started) * 1000),
            finish_reason=choice.get("finish_reason"),
            reasoning=msg.get("reasoning_content") or msg.get("reasoning") or "",
        )
        if not resp.content.strip() and not resp.tool_calls and resp.truncated:
            raise LLMError(f"{self.settings.provider} 输出在给出答案前就达到了 max_tokens={max_tokens} 上限"
                           f"（推理模型的思考内容也计入上限，已思考 {len(resp.reasoning)} 字）。"
                           "请调大 LLM_MAX_TOKENS 或调低 LLM_REASONING_EFFORT")
        return resp

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

    def chat(self, messages, *, tools=None, tool_choice=None, temperature=None, max_tokens=None,
             json_mode=False) -> LLMResponse:
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools, "tool_choice": tool_choice,
                              "json_mode": json_mode})
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
