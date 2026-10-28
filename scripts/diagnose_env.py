#!/usr/bin/env python3
"""本机环境与大模型连通性诊断（只用标准库，兼容 Python 3.6+）。

检查顺序：Python 版本 → 依赖包 → finsight 能否导入 → .env 格式（不输出密钥）→ 直接用 urllib 调用
DeepSeek / Kimi 接口（列模型 + 一次极短对话）。结果写入 data/runs/diagnose.json，供开发会话读取。
任何密钥都不会被打印或写入结果文件（出现在错误信息中的密钥会被替换为 ***）。
"""
import json
import os
import platform
import sys
import time
import traceback
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "data", "runs")
SECRETS = []


def redact(text):
    text = str(text)
    for s in SECRETS:
        if s:
            text = text.replace(s, "***")
    return text


def read_env():
    path = os.path.join(ROOT, ".env")
    info = {"exists": os.path.exists(path), "keys": {}}
    values = {}
    if not info["exists"]:
        return info, values
    raw = open(path, "rb").read()
    info["bytes"] = len(raw)
    info["has_bom"] = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig", errors="replace")
    info["crlf"] = "\r\n" in text
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        values[k] = v
        if k.endswith("_KEY"):
            meta = {
                "length": len(v),
                "starts_with_sk": v.startswith("sk-"),
                "has_whitespace": any(c.isspace() for c in v),
                "has_quotes": any(c in v for c in "\"'"),
                "has_nonprintable": any((ord(c) < 32 or ord(c) == 127) for c in v),
                "non_ascii": any(ord(c) > 127 for c in v),
            }
            info["keys"][k] = meta
            SECRETS.append(v)
        else:
            info.setdefault("settings", {})[k] = v
    return info, values


def http(method, url, key, body=None, timeout=25):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read().decode("utf-8", errors="replace")
            return {"status": r.status, "ms": int((time.time() - t0) * 1000), "body": payload}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ms": int((time.time() - t0) * 1000),
                "error": redact(e.read().decode("utf-8", errors="replace")[:400])}
    except Exception as e:  # 网络、证书、超时等
        return {"status": None, "ms": int((time.time() - t0) * 1000), "error": redact("%s: %s" % (type(e).__name__, e))}


def chat_body(model):
    """与 finsight.llm.gateway.reasoning_params 保持一致：两家都是推理模型，思考内容计入 max_tokens。"""
    body = {"model": model, "messages": [{"role": "user", "content": "只回复两个字：正常"}], "max_tokens": 2048}
    if model.startswith("kimi-k3"):
        body["reasoning_effort"] = "low"  # Kimi K3 系列始终思考，采样参数固定，不发送 temperature
    elif model.startswith("deepseek"):
        body.update({"thinking": {"type": "enabled"}, "reasoning_effort": "low", "temperature": 0.2})
    return body


def test_provider(name, bases, key, models):
    out = {"provider": name, "attempts": []}
    if not key:
        out["skipped"] = "未配置密钥"
        return out
    for base in bases:
        r = http("GET", base.rstrip("/") + "/models", key)
        entry = {"base_url": base, "models_status": r["status"], "models_ms": r["ms"]}
        if r["status"] == 200:
            try:
                entry["available_models"] = [m.get("id") for m in json.loads(r["body"]).get("data", [])][:40]
            except Exception:
                entry["available_models"] = "无法解析"
        else:
            entry["models_error"] = r.get("error")
        entry["chat"] = []
        candidates = list(models)
        if isinstance(entry.get("available_models"), list):
            candidates = [m for m in models if m in entry["available_models"]] or candidates
        for model in candidates[:3]:
            c = http("POST", base.rstrip("/") + "/chat/completions", key, chat_body(model), timeout=120)
            item = {"model": model, "status": c["status"], "ms": c["ms"]}
            if c["status"] == 200:
                try:
                    data = json.loads(c["body"])
                    choice = data["choices"][0]
                    msg = choice.get("message") or {}
                    item["reply"] = msg.get("content") or ""
                    item["finish_reason"] = choice.get("finish_reason")
                    item["reasoning_chars"] = len(msg.get("reasoning_content") or "")
                    item["usage"] = data.get("usage")
                except Exception:
                    item["reply"] = "无法解析"
            else:
                item["error"] = c.get("error")
            entry["chat"].append(item)
            if c["status"] == 200:
                break
        out["attempts"].append(entry)
        if any(x["status"] == 200 for x in entry["chat"]):
            break
    return out


def describe(c):
    if c.get("status") != 200:
        return "（%s）" % (c.get("error") or "")[:160]
    return "「%s」finish=%s，思考 %s 字" % (c.get("reply") or "", c.get("finish_reason"), c.get("reasoning_chars", "?"))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    report = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
              "python": {"version": sys.version.split()[0], "executable": sys.executable, "platform": platform.platform()},
              "proxies": sorted(urllib.request.getproxies().keys())}
    ok_python = sys.version_info >= (3, 10)
    report["python"]["meets_requirement_3_10"] = ok_python
    pkgs = {}
    for mod in ("fastapi", "uvicorn", "httpx", "pydantic", "dotenv"):
        try:
            m = __import__(mod)
            pkgs[mod] = str(getattr(m, "__version__", getattr(m, "VERSION", "ok")))
        except Exception as e:
            pkgs[mod] = "缺失：%s" % type(e).__name__
    report["packages"] = pkgs
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    try:
        import finsight.agent.research_agent  # noqa: F401
        report["finsight_import"] = "ok"
    except Exception:
        report["finsight_import"] = traceback.format_exc().strip().splitlines()[-6:]
    env_info, values = read_env()
    report["env"] = env_info
    report["deepseek"] = test_provider("deepseek", ["https://api.deepseek.com"], values.get("DEEPSEEK_API_KEY", ""),
                                       ["deepseek-flash", "deepseek-v4-pro"])
    report["kimi"] = test_provider("kimi", ["https://api.moonshot.cn/v1", "https://api.moonshot.ai/v1"],
                                   values.get("KIMI_API_KEY", ""), ["kimi-k3.1-pro", "kimi-k3", "kimi-k2.6"])
    if not os.path.isdir(RUNS):
        os.makedirs(RUNS)
    path = os.path.join(RUNS, "diagnose.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(redact(json.dumps(report, ensure_ascii=False, indent=1)))

    print("Python %s（%s）%s" % (report["python"]["version"], report["python"]["executable"],
                                 "" if ok_python else "  ← 需要 3.10 及以上"))
    print("依赖包：" + "，".join("%s=%s" % kv for kv in pkgs.items()))
    print("finsight 导入：" + ("正常" if report["finsight_import"] == "ok" else "失败 → " + " | ".join(report["finsight_import"][-2:])))
    for k, meta in env_info.get("keys", {}).items():
        flags = [n for n in ("has_whitespace", "has_quotes", "has_nonprintable", "non_ascii") if meta[n]]
        print(".env %s：长度 %d，%s%s" % (k, meta["length"], "以 sk- 开头" if meta["starts_with_sk"] else "不是 sk- 开头",
                                       ("，异常字符：" + "、".join(flags)) if flags else ""))
    for prov in ("deepseek", "kimi"):
        r = report[prov]
        if r.get("skipped"):
            print("%s：%s" % (prov, r["skipped"]))
            continue
        for a in r["attempts"]:
            chats = "；".join("%s→%s%s" % (c["model"], c["status"], describe(c)) for c in a["chat"])
            print("%s @ %s：模型列表 HTTP %s；对话：%s" % (prov, a["base_url"], a["models_status"], chats))
    print("诊断结果已写入", path)


if __name__ == "__main__":
    main()
