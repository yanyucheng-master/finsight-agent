# CLAUDE.md —— 给后续 AI 开发者的工作指南

先读：`docs/STATUS.md`（现状）→ `docs/ROADMAP.md`（计划）→ `docs/DESIGN.md`（架构与决策）→ `docs/COMPETITION.md`（赛事）。

## 不可违背的原则

1. **不编造数据**：真实数据只能来自 `data/snapshots`（采集器从公开源抓取）。测试里人工构造的数字必须标明 synthetic。
2. **大模型不产生数字**：报告和回答中的数字只能来自证据账本，引用格式为 `[[证据ID]]`；新增 LLM 功能必须经过核验器。
3. **不把未验证的东西标为完成**：STATUS.md 只写跑过、测过的内容。
4. 未经用户同意，不购买服务、不接入付费或需授权的数据源、不执行公开发布等外部操作。

## 代码地图（backend/finsight）

| 路径 | 职责 |
|---|---|
| `data/collector.py` | 只用标准库的采集器（**禁止 import 第三方包或 finsight 其他模块**，要能在用户的裸 Python 上运行） |
| `data/snapshots.py` | manifest + 内容寻址对象，读取时校验 SHA-256 |
| `data/schema.py` | 标准科目表与东方财富字段映射（字段名均经真实响应核对，改动前先查样本） |
| `data/eastmoney.py`、`data/service.py` | 解析 → `CompanyData`（分析引擎的唯一输入） |
| `analytics/engine.py` | `V`（依赖追踪数值）、`Calc`、指标注册表 `@metric` |
| `analytics/metrics.py`、`models.py` | 指标与 M / Z'' / F 模型（口径见文档字符串） |
| `analytics/checks.py`、`rules.py` | 数据核验、风险信号（阈值集中在 `RULE_PARAMS`） |
| `analytics/diagnosis.py` | 体检主流程 → `DiagnosisResult` |
| `api/app.py`、`cli.py` | FastAPI 与命令行 |

证据 ID：`f:{科目}@{期末}`、`m:{指标}@{期末}`、`c:{核验}@{期末}`、`s:{规则}@{期}`、`e:{公告ID}`、`t:audit@{期末}`。

## 常用命令

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e "backend[dev]"
cd backend && python -m pytest                 # 全部测试（集成测试依赖 data/snapshots）
python -m finsight.cli diagnose 600519         # 终端体检
python -m finsight.cli verify-snapshots        # 快照完整性
python -m finsight.cli serve                   # API：http://127.0.0.1:8000/docs
ruff check finsight tests
```

## 环境须知（重要）

- **云端开发容器**的出网有白名单：东方财富、巨潮、交易所、DeepSeek 等都返回 403。所以实时取数和 LLM 调用只能在用户的 Windows 电脑上进行。
- 用户本机项目路径为 `C:\Users\YYC\Projects\finsight-agent`，这是代码的“真身”。云端改动完成后，必须同步回这个文件夹（device_commit_files）。
- 新数据由用户在本机运行 `scripts\collect.bat` 采集，再从本机 stage 到容器的 `data/snapshots`。
- 用户本机有 Anaconda；电脑操作权限**不能控制终端类应用**，需要运行命令时请用户双击 `.bat`。
- LLM：DeepSeek（OpenAI 兼容），`LLM_BASE_URL=https://api.deepseek.com`，`LLM_MODEL=deepseek-flash`（模型名以官方文档为准），Key 放在 `.env` 中，禁止提交到仓库。
