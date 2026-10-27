#!/usr/bin/env python3
"""采集真实数据快照（零依赖）。用法：

    python scripts/collect_snapshots.py --symbols-file scripts/demo_symbols.txt

直接按文件路径加载 backend/finsight/data/collector.py，不需要安装任何第三方包。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("finsight_collector", ROOT / "backend" / "finsight" / "data" / "collector.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--out" not in argv:
        argv += ["--out", str(ROOT / "data" / "snapshots")]
    raise SystemExit(module.main(argv))
