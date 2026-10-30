"""大模型网关测试：用 httpx.MockTransport 模拟供应商接口（不访问网络）。

覆盖本机首次诊断暴露的问题：推理模型的参数差异、reasoning_content 回传、“HTTP 200 但正文为空”。
响应体是按官方文档字段手工构造的（synthetic）。
"""

from __future__ import annotations

import json

import httpx
import pytest

from finsight.config import LLMSettings, llm_settings
from finsight.llm.gateway import LLMError, OpenAICompatibleClient, reasoning_params


def settings(provider="deepseek", model="deepseek-flash", effort="low", **kw) -> LLMSettings:
    return LLMSettings(provider=provider, base_url="https://llm.test/v1", api_key="sk-test", model=model,
                       reasoning_effort=effort, **kw)


def ok(message: dict, finish="stop", usage=None) -> dict:
    return {"model": "served-model", "choices": [{"index": 0, "message": {"role": "assistant", **message},
                                                  "finish_reason": finish}],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}}


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.payloads: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.payloads.append(json.loads(request.content))
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)


def client(rec: Recorder, **kw) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(settings(**kw), transport=httpx.MockTransport(rec), backoff_s=0)


def test_kimi_k3_sends_effort_and_no_sampling_params():
    rec = Recorder((200, ok({"content": "正常"})))
    client(rec, provider="kimi", model="kimi-k3.1-pro").chat([{"role": "user", "content": "hi"}])
    p = rec.payloads[0]
    assert p["reasoning_effort"] == "low"
    assert "temperature" not in p and "top_p" not in p and "thinking" not in p


def test_deepseek_thinking_toggle():
    rec = Recorder((200, ok({"content": "a"})), (200, ok({"content": "b"})))
    client(rec).chat([{"role": "user", "content": "hi"}])
    client(rec, effort="none").chat([{"role": "user", "content": "hi"}])
    on, off = rec.payloads
    assert on["thinking"] == {"type": "enabled"} and on["reasoning_effort"] == "low" and "temperature" in on
    assert off["thinking"] == {"type": "disabled"} and "reasoning_effort" not in off


@pytest.mark.parametrize("model,expected,sampling", [
    ("kimi-k3", {"reasoning_effort": "high"}, False),
    ("kimi-k2.6", {"thinking": {"type": "enabled"}}, False),
    ("kimi-k2.7-code", {}, False),
    ("intern-s1", {}, True),
])
def test_reasoning_params_by_model_family(model, expected, sampling):
    assert reasoning_params(model, "high") == (expected, sampling)


def test_reasoning_content_is_parsed_and_passed_back():
    usage = {"prompt_tokens": 20, "completion_tokens": 50, "prompt_cache_hit_tokens": 8,
             "completion_tokens_details": {"reasoning_tokens": 42}}
    rec = Recorder((200, ok({"content": "", "reasoning_content": "先查毛利率。",
                             "tool_calls": [{"id": "call_1", "type": "function",
                                             "function": {"name": "get_metrics", "arguments": "{\"codes\": [\"roe\"]}"}}]},
                            finish="tool_calls", usage=usage)))
    resp = client(rec).chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "x"}}])
    assert resp.reasoning == "先查毛利率。" and resp.tool_calls[0].arguments == {"codes": ["roe"]}
    assert resp.usage["reasoning_tokens"] == 42 and resp.usage["prompt_cache_hit_tokens"] == 8
    msg = resp.as_message()
    assert msg["reasoning_content"] == "先查毛利率。" and msg["tool_calls"][0]["function"]["name"] == "get_metrics"
    assert rec.payloads[0]["tool_choice"] == "auto"


def test_empty_answer_cut_by_length_is_a_clear_error():
    """本机诊断复现：max_tokens 太小，思考还没结束就被截断，HTTP 200 但 content 为空。"""
    rec = Recorder((200, ok({"content": "", "reasoning_content": "用户要求只回复两个字……"}, finish="length")))
    with pytest.raises(LLMError, match="max_tokens"):
        client(rec).chat([{"role": "user", "content": "只回复两个字：正常"}], max_tokens=10)


def test_client_errors_are_not_retried_and_show_body():
    rec = Recorder((400, {"error": {"message": "invalid temperature: only 1 is allowed for this model"}}))
    with pytest.raises(LLMError, match="invalid temperature"):
        client(rec).chat([{"role": "user", "content": "hi"}])
    assert len(rec.payloads) == 1


def test_server_errors_are_retried():
    rec = Recorder((503, "busy"), (200, ok({"content": "好"})))
    assert client(rec).chat([{"role": "user", "content": "hi"}]).content == "好"
    assert len(rec.payloads) == 2


def test_leading_keepalive_blank_lines_are_tolerated():
    """DeepSeek 非流式请求排队时会先返回空行保活。"""
    body = "\n\n" + json.dumps(ok({"content": "正常"}))
    rec = Recorder((200, body))
    assert client(rec).chat([{"role": "user", "content": "hi"}]).content == "正常"


def test_provider_presets(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "sk-x")
    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)
    monkeypatch.setenv("LLM_REASONING_EFFORT", "bogus")
    k = llm_settings("kimi")
    assert k.model == "kimi-k3.1-pro" and k.max_tokens >= 16000 and k.reasoning_effort == "low"
    assert llm_settings("deepseek").max_tokens >= 8000


def test_llm_check_runs_tool_roundtrip_with_reasoning_passback(monkeypatch, tmp_path):
    import finsight.llm.gateway as gw
    from finsight.cli import main

    class FakeClient(gw.MockLLM):
        def list_models(self):
            return ["deepseek-flash"]

    fake = FakeClient(script=[
        gw.LLMResponse(content="正常", reasoning="嗯"),
        gw.LLMResponse(content="", reasoning="先调用工具", tool_calls=[
            gw.ToolCall(id="c1", name="lookup_metric", arguments={"code": "600519", "metric": "gross_margin"})]),
        gw.LLMResponse(content="600519 的毛利率为 91.18%。"),
    ])
    monkeypatch.setattr(gw, "make_client", lambda cfg: fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("FINSIGHT_DATA_DIR", str(tmp_path))
    assert main(["llm-check", "--provider", "deepseek"]) == 0
    rep = json.loads((tmp_path / "runs" / "llm_check_deepseek.json").read_text(encoding="utf-8"))
    assert rep["tool_roundtrip"]["passed"] and "api_key" not in json.dumps(rep)
    assert fake.requests[2]["messages"][1]["reasoning_content"] == "先调用工具"
    assert fake.requests[2]["messages"][2]["role"] == "tool"


def test_budget_guard_blocks_requests_that_could_exceed_budget(monkeypatch):
    import finsight.llm.gateway as gw

    monkeypatch.setattr(gw, "BUDGET", gw.BudgetGuard())
    monkeypatch.setenv("LLM_BUDGET_CNY", "0.5")
    rec = Recorder((200, ok({"content": "好"}, usage={"prompt_tokens": 1000, "completion_tokens": 1000})))
    c = client(rec, provider="kimi", model="kimi-k3.1-pro")
    c.chat([{"role": "user", "content": "hi"}], max_tokens=1000)  # 预留约 0.07 元
    assert abs(gw.BUDGET.spent - (1000 * 13 + 1000 * 68) / 1e6) < 1e-9
    with pytest.raises(LLMError, match="费用保护"):
        c.chat([{"role": "user", "content": "hi"}], max_tokens=32768)  # 最多 2.2 元，超预算
    assert len(rec.payloads) == 1  # 被拒绝的请求没有发出


def test_cost_estimate_uses_cache_hits():
    from finsight.llm.gateway import estimate_cost_cny

    cost = estimate_cost_cny("deepseek-flash", {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 400,
                                                "completion_tokens": 100})
    assert abs(cost - (400 * 0.04 + 600 * 2 + 100 * 8) / 1e6) < 1e-12
