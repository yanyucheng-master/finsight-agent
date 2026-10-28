"""运行配置：从环境变量 / 项目根目录 .env 读取。

所有可调参数集中在这里，避免散落在代码中的魔法值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv 是可选的：没有安装时直接读取进程环境变量
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]

def _load_env_file(path: Path) -> None:
    """读取项目根目录 .env。优先用 python-dotenv；未安装时用内置的简易解析（KEY=VALUE，忽略注释）。"""
    if not path.exists():
        return
    if load_dotenv is not None:
        load_dotenv(path, override=False)
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(PROJECT_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# 大模型供应商预设（均为 OpenAI 兼容接口）。模型名会随供应商更新，以官方文档为准，可用 LLM_MODEL 覆盖。
#
# 两家都是“推理模型”（2026-10 查阅官方文档）：
# * DeepSeek deepseek-flash：思考模式默认开启（effort 默认 high），用 thinking={"type": enabled/disabled} 开关，
#   reasoning_effort 取 low/high/max；思考模式下 temperature 不生效；带 tools 的多轮请求必须回传 reasoning_content，否则 400。
# * Kimi kimi-k3 / kimi-k3.1-pro：始终思考，顶层 reasoning_effort 取 low/high/max（默认 max）；
#   temperature=1.0、top_p=0.95 等采样参数固定，传其他值会报错，所以不发送；多轮与工具调用要回传完整 assistant 消息。
# * 两家的思考内容都计入 max_tokens，上限过小会出现“HTTP 200 但回复为空”（正是本机首次诊断看到的现象）。
LLM_PROVIDERS: dict[str, dict[str, str | int]] = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash", "key_env": "DEEPSEEK_API_KEY",
                 "max_tokens": 16384},
    # 选 k3.1-pro 而非 k3：较新（2026-10-23 发布）、第三方资料显示单价更低且幻觉率更低；用户的 Key 已确认可用
    "kimi": {"base_url": "https://api.moonshot.cn/v1", "model": "kimi-k3.1-pro", "key_env": "KIMI_API_KEY",
             "max_tokens": 32768},
    "intern": {"base_url": "https://chat.intern-ai.org.cn/api/v1", "model": "intern-s1", "key_env": "INTERN_API_KEY",
               "max_tokens": 8192},
}

REASONING_EFFORTS = ("none", "low", "high", "max")


@dataclass(frozen=True)
class LLMSettings:
    """OpenAI 兼容接口配置。测试默认 DeepSeek（低成本），正式参赛计划使用 Kimi。"""

    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-flash"
    temperature: float = 0.2
    timeout_s: float = 300.0
    max_tokens: int = 16384
    # 推理强度：none（仅对可关闭思考的模型有效）/ low / high / max。默认 low：本系统的写作任务以“按证据清单表述”为主，
    # 低强度足够且显著更快、更省；数字正确性由核验器保证。可用 LLM_REASONING_EFFORT 调整。
    reasoning_effort: str = "low"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def public(self) -> dict[str, str | bool | int]:
        return {"provider": self.provider, "base_url": self.base_url, "model": self.model, "enabled": self.enabled,
                "reasoning_effort": self.reasoning_effort, "max_tokens": self.max_tokens}


def llm_settings(provider: str | None = None) -> LLMSettings:
    """按供应商读取配置：LLM_PROVIDER 选择预设；LLM_BASE_URL / LLM_MODEL / LLM_API_KEY 可整体覆盖。"""
    name = (provider or _env("LLM_PROVIDER", "deepseek") or "deepseek").lower()
    preset = LLM_PROVIDERS.get(name, LLM_PROVIDERS["deepseek"])
    explicit = provider is not None  # 显式指定供应商时，只使用该供应商的预设与密钥
    effort = _env("LLM_REASONING_EFFORT", "low").lower()
    return LLMSettings(
        provider=name,
        base_url=str((preset["base_url"] if explicit else _env("LLM_BASE_URL", str(preset["base_url"])))
                     or preset["base_url"]),
        api_key=_env(str(preset["key_env"])) or ("" if explicit else _env("LLM_API_KEY")),
        model=str((preset["model"] if explicit else _env("LLM_MODEL", str(preset["model"]))) or preset["model"]),
        temperature=_env_float("LLM_TEMPERATURE", 0.2),
        timeout_s=_env_float("LLM_TIMEOUT_S", 300.0),
        max_tokens=_env_int("LLM_MAX_TOKENS", int(preset["max_tokens"])),
        reasoning_effort=effort if effort in REASONING_EFFORTS else "low",
    )


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    snapshot_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "snapshots")
    # 数据源模式：snapshot = 只读本地快照（可复现、可离线）；live = 在线抓取并写入快照
    provider_mode: str = "snapshot"
    llm: LLMSettings = field(default_factory=LLMSettings)
    # 智能体安全边界
    agent_max_steps: int = 6
    # 引擎版本：写入每次分析结果，便于追溯
    engine_version: str = "0.2.0"


def load_settings() -> Settings:
    data_dir = Path(_env("FINSIGHT_DATA_DIR") or (PROJECT_ROOT / "data"))
    snapshot_dir = Path(_env("FINSIGHT_SNAPSHOT_DIR") or (data_dir / "snapshots"))
    llm = llm_settings()
    return Settings(
        data_dir=data_dir,
        snapshot_dir=snapshot_dir,
        provider_mode=_env("FINSIGHT_PROVIDER", "snapshot") or "snapshot",
        llm=llm,
        agent_max_steps=_env_int("FINSIGHT_AGENT_MAX_STEPS", 6),
    )
