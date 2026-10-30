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
| `llm/gateway.py` | OpenAI 兼容网关 + `MockLLM`（测试一律用 Mock，禁止在测试中调用外部 API） |
| `reporting/verifier.py` | 结论核验器（数字抽取、引用关联、单位换算、容差比对、合规检查） |
| `reporting/report.py` | 证据上下文（E1..En）、LLM 简报 + 修订循环、模板降级 |
| `agent/research_agent.py` | 研究问答智能体（工具定义 `TOOLS`、会话证据编号、答案核验） |
| `api/app.py`、`cli.py` | FastAPI 与命令行 |
| `../../frontend/` | React 工作台；`npm run build` 输出到 `backend/finsight/web/`，由 FastAPI 托管 |

证据 ID：`f:{科目}@{期末}`、`m:{指标}@{期末}`、`c:{核验}@{期末}`、`s:{规则}@{期}`、`e:{公告ID}`、`t:audit@{期末}`、`p:{阈值名}`；同业证据加公司前缀 `000858|m:...`。
LLM 输出中的引用格式为 `[E12]`（会话内短编号，由上下文构建器映射到证据 ID）。

## 常用命令

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e "backend[dev]"
cd backend && python -m pytest                 # 全部测试（集成测试依赖 data/snapshots）
python -m finsight.cli diagnose 600519         # 终端体检
python -m finsight.cli verify-snapshots        # 快照完整性
python -m finsight.cli serve                   # 工作台 http://127.0.0.1:8000 ；API 文档 /docs
python -m finsight.cli report 600519 --mode template   # 可验证简报（--mode llm 需要 Key）
python -m finsight.cli ask 600519 "问题"        # 研究问答（需要 Key）
cd frontend && npm install && npm run build    # 前端构建（输出到 backend/finsight/web）
node frontend/e2e/smoke.cjs                    # 端到端冒烟（需先 serve）
ruff check finsight tests
```

## 环境须知（重要）

- **云端开发容器**的出网有白名单：东方财富、巨潮、交易所、DeepSeek 等都返回 403。所以实时取数和 LLM 调用只能在用户的 Windows 电脑上进行。
- 用户本机项目路径为 `C:\Users\YYC\Projects\finsight-agent`，这是代码的“真身”。云端改动完成后，必须同步回这个文件夹（device_commit_files）。
- 新数据由用户在本机运行 `scripts\collect.bat` 采集，再从本机 stage 到容器的 `data/snapshots`。
- `_python.bat` 在用户本机实际找到的是 Python 3.14（`AppData\Local\Programs\Python\Python314`，标准 Anaconda 路径下没有）；`_deps.bat` 首次运行时用它在项目内创建 `.venv` 并安装依赖，之后各脚本优先使用 `.venv`（不改动全局 Python）。电脑操作权限**不能控制终端类应用**，需要运行命令时请用户双击 `.bat`。
- 两家大模型都是推理模型，网关已适配（见 `config.LLM_PROVIDERS` 注释）：改动请求参数前先查官方文档，并在 `tests/test_gateway.py` 里补测试。
- LLM：测试用 DeepSeek，参赛用 Kimi（用户决定）。Key 由用户运行 `scripts\setup_env.bat` 写入本机 `.env`（远程工具禁止写 .env），禁止提交到仓库。
- 真实模型测试：用户运行 `scripts\llm_smoke.bat`（默认 Kimi、费用上限 3 元，`LLM_BUDGET_CNY`），结果写入 `data\runs\`，再 stage 回容器复核。两轮 Kimi 实测结果在 `docs/eval/live-20261029/`、`live-20261030/`。
- **核验器改动必须同时跑真实输出回归**（`tests/test_verifier_regressions.py`）和两种排版的注入评测；只用模板文本测会漏掉真实写法（2026-10-30 的教训：数字紧贴汉字时整段漏检）。
- 参赛赛题：主题赛 2「AI+金融赋能应用赛」（见 docs/COMPETITION.md）。
