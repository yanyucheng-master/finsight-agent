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

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)


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


@dataclass(frozen=True)
class LLMSettings:
    """OpenAI 兼容接口配置。默认指向 DeepSeek；也可切换到 Kimi / 通义 / GLM 等。"""

    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-flash"
    temperature: float = 0.2
    timeout_s: float = 90.0
    max_tokens: int = 4000

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


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
    engine_version: str = "0.1.0"


def load_settings() -> Settings:
    data_dir = Path(_env("FINSIGHT_DATA_DIR") or (PROJECT_ROOT / "data"))
    snapshot_dir = Path(_env("FINSIGHT_SNAPSHOT_DIR") or (data_dir / "snapshots"))
    llm = LLMSettings(
        base_url=_env("LLM_BASE_URL", "https://api.deepseek.com"),
        api_key=_env("LLM_API_KEY") or _env("DEEPSEEK_API_KEY"),
        model=_env("LLM_MODEL", "deepseek-flash"),
        temperature=_env_float("LLM_TEMPERATURE", 0.2),
        timeout_s=_env_float("LLM_TIMEOUT_S", 90.0),
        max_tokens=_env_int("LLM_MAX_TOKENS", 4000),
    )
    return Settings(
        data_dir=data_dir,
        snapshot_dir=snapshot_dir,
        provider_mode=_env("FINSIGHT_PROVIDER", "snapshot") or "snapshot",
        llm=llm,
        agent_max_steps=_env_int("FINSIGHT_AGENT_MAX_STEPS", 6),
    )
