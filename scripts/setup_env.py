#!/usr/bin/env python3
"""在本机交互式生成 .env（只用标准库）。密钥输入时不回显，只写入本机项目根目录的 .env。"""
import getpass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"


def ask(name: str) -> str:
    return getpass.getpass(f"{name} API Key（粘贴后回车，输入不显示；直接回车跳过）：").strip()


def main() -> None:
    if ENV.exists():
        if input(f"{ENV} 已存在，是否覆盖？(y/N) ").strip().lower() != "y":
            print("已取消，未修改 .env")
            return
    deepseek = ask("DeepSeek")
    kimi = ask("Kimi (Moonshot)")
    # 参赛使用 Kimi：填了 Kimi Key 就默认用它；只填 DeepSeek 时用 DeepSeek（低成本测试）
    provider = "kimi" if kimi else "deepseek"
    lines = [
        "# 本机密钥文件，已被 .gitignore 忽略，切勿提交或外发",
        f"LLM_PROVIDER={provider}",
        f"DEEPSEEK_API_KEY={deepseek}",
        f"KIMI_API_KEY={kimi}",
        "FINSIGHT_PROVIDER=snapshot",
        "",
    ]
    ENV.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入 {ENV}（DeepSeek：{'已填' if deepseek else '未填'}，Kimi：{'已填' if kimi else '未填'}；当前使用 {provider}）")


if __name__ == "__main__":
    main()
