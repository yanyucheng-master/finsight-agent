#!/usr/bin/env python3
"""打包参赛用的“关键代码”压缩包（只用标准库）。

    python scripts/package_code.py                 # 输出 dist/FinSight-Agent-code-YYYYMMDD.zip
    python scripts/package_code.py --name 自定义.zip
    python scripts/package_code.py --dry-run       # 只列出将要打包的文件与体积，不写文件

做三件事：
1. 按白名单收集文件：源码、测试、前端源码与构建产物、脚本、文档与评测产物、数据快照（不含年报 PDF）、缓存样例；
   一律排除 .env、.venv、node_modules、__pycache__、.repo（git 备份）、data/runs（本机运行日志）、dist 等。
   前端构建目录里只收 index.html 实际引用的那一版 js / css（旧构建残留不进包）。
2. **密钥检查**：读取本机 .env 里所有 *_KEY / *_TOKEN / *_SECRET 的值（不打印），再加上常见密钥格式（sk-...），
   在每个待打包文件里逐字搜索；发现任何一处就中止，不生成压缩包。
3. 包内附 MANIFEST-SHA256.txt（每个文件的 SHA-256 与字节数），评委可据此核对文件未被改动。

大赛通用条款要求的外层压缩包命名“单位简称-作品名称-队长姓名-队长手机号-日期.zip”由参赛者自己完成；
本脚本只生成其中的“关键代码”部分。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 打包的顶层条目（相对项目根目录）。目录会递归收集，再经过 EXCLUDE 过滤。
INCLUDE = [
    "README.md", "CLAUDE.md", ".env.example", ".gitignore",
    "backend", "frontend", "scripts", "docs",
    "data/snapshots", "data/samples",
]
EXCLUDE_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".repo", "dist",
                ".git", ".idea", ".vscode", "out"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pdf", ".bundle", ".zip"}
EXCLUDE_NAMES = {".env", "Thumbs.db", ".DS_Store"}
EXCLUDE_GLOBS = ["*.egg-info"]

SECRET_ENV_RE = re.compile(r"^\s*([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))\s*=\s*(.+?)\s*$")
SECRET_PATTERNS = [re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}"), re.compile(rb"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{30,}")]


def _excluded(rel: Path) -> bool:
    parts = rel.parts
    if any(p in EXCLUDE_DIRS for p in parts[:-1]):
        return True
    if any(Path(p).match(g) for p in parts for g in EXCLUDE_GLOBS):
        return True
    if rel.name in EXCLUDE_NAMES:
        return True
    if rel.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return False


def _web_assets_in_use(web: Path) -> set[str]:
    """前端构建目录里实际用到的资源：index.html 直接引用的 js / css，以及它们按需加载的分块（传递闭包）。

    旧构建留下的文件（文件名带不同的哈希）不会被任何在用文件引用，因此被排除。"""
    index = web / "index.html"
    assets = web / "assets"
    if not index.exists() or not assets.is_dir():
        return set()
    names = {p.name for p in assets.iterdir() if p.is_file()}
    used = {m.split("/")[-1] for m in re.findall(r'["\'](?:\./|/)?(assets/[^"\']+)["\']', index.read_text(encoding="utf-8"))}
    used &= names
    queue = list(used)
    while queue:  # 分块之间按文件名互相引用（如 "./TrendChart-xxxx.js"）
        text = (assets / queue.pop()).read_text(encoding="utf-8", errors="ignore")
        for name in names - used:
            if name in text:
                used.add(name)
                queue.append(name)
    return {f"assets/{n}" for n in used}


def collect(root: Path = ROOT) -> list[Path]:
    web = root / "backend" / "finsight" / "web"
    used_assets = _web_assets_in_use(web)
    files: list[Path] = []
    for entry in INCLUDE:
        base = root / entry
        if base.is_file():
            candidates = [base]
        elif base.is_dir():
            candidates = sorted(p for p in base.rglob("*") if p.is_file())
        else:
            continue
        for path in candidates:
            rel = path.relative_to(root)
            if _excluded(rel):
                continue
            try:  # 旧构建残留：web/assets 下没被 index.html 引用的文件
                in_web = path.relative_to(web)
                if in_web.parts and in_web.parts[0] == "assets" and in_web.as_posix() not in used_assets:
                    continue
            except ValueError:
                pass
            files.append(path)
    return files


def secret_values(root: Path = ROOT) -> list[bytes]:
    """本机 .env 里的密钥值（只在内存里用于比对，不打印、不写出）。"""
    env = root / ".env"
    values: list[bytes] = []
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            m = SECRET_ENV_RE.match(line)
            if m:
                v = m.group(2).strip().strip('"').strip("'")
                if len(v) >= 8:  # 空值或占位符不算
                    values.append(v.encode("utf-8"))
    return values


def scan(files: list[Path], root: Path = ROOT) -> list[str]:
    """返回疑似包含密钥的文件（相对路径 + 原因，不含密钥本身）。"""
    secrets = secret_values(root)
    hits: list[str] = []
    for path in files:
        data = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        if any(s in data for s in secrets):
            hits.append(f"{rel}：包含本机 .env 中的密钥值")
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(data):
                hits.append(f"{rel}：包含疑似密钥格式（sk- / ghp_ 开头）")
                break
    return hits


def build(files: list[Path], out: Path, root: Path = ROOT) -> tuple[int, int]:
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    total = 0
    prefix = "finsight-agent"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(root).as_posix()
            data = path.read_bytes()
            total += len(data)
            lines.append(f"{hashlib.sha256(data).hexdigest()}  {len(data):>10}  {rel}")
            zf.writestr(f"{prefix}/{rel}", data)
        zf.writestr(f"{prefix}/MANIFEST-SHA256.txt", "\n".join(lines) + "\n")
    return len(files), total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="打包参赛用的关键代码（排除密钥与本机环境文件）")
    ap.add_argument("--name", default=f"FinSight-Agent-code-{date.today():%Y%m%d}.zip", help="输出文件名（放在 dist/ 下）")
    ap.add_argument("--dry-run", action="store_true", help="只列出将要打包的文件，不生成压缩包")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    files = collect()
    if not (ROOT / "backend" / "finsight" / "web" / "index.html").exists():
        print("[警告] 没有前端构建产物 backend/finsight/web/index.html：请先在 frontend 目录运行 npm run build")
    size = sum(p.stat().st_size for p in files)
    print(f"待打包文件 {len(files)} 个，合计 {size / 1e6:.1f} MB")
    hits = scan(files)
    if hits:
        print("[中止] 发现疑似密钥，没有生成压缩包：")
        for h in hits:
            print("  -", h)
        return 2
    print("密钥检查：未发现 .env 中的密钥值或常见密钥格式")
    if args.dry_run:
        by_top: dict[str, int] = {}
        for p in files:
            top = p.relative_to(ROOT).parts[0]
            by_top[top] = by_top.get(top, 0) + 1
        for k, v in sorted(by_top.items()):
            print(f"  {k}: {v} 个文件")
        return 0
    out = ROOT / "dist" / args.name
    n, total = build(files, out)
    print(f"已生成 {out.relative_to(ROOT)}（{n} 个文件，原始 {total / 1e6:.1f} MB，压缩后 {out.stat().st_size / 1e6:.1f} MB）")
    print("包内 MANIFEST-SHA256.txt 记录了每个文件的 SHA-256。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
