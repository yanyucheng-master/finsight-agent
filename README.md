# FinSight Agent

**可追溯的上市公司投研智能体**：财务数字关联证据，计算过程可复查，结论仍需结合原始披露判断。

参赛：第三届“兴智杯”全国人工智能创新应用大赛 · 主题赛 2（行业赋能）·「AI+金融赋能应用赛」· 投研分析场景。

## 它解决什么问题

研究员做一家公司的财务风险排查，要读三大报表、附注和公告，再交叉核对、计算指标、写结论，费时且容易出错；
直接让通用大模型来写又快又顺，但它会“心算”、会记错年份、会编出看似合理的数字，在金融场景里不可接受。

FinSight 把“取数 → 数据核验 → 指标与模型 → 风险排查 → 撰写 → 复核”的投研流程改造成一条**可验证的智能体流程**：

| 环节 | 做法 | 谁负责 |
|---|---|---|
| 取数 | 公开披露数据的**原始响应快照**（URL、抓取时间、SHA-256），读取时校验哈希 | 确定性代码 |
| 数据核验 | 11 项会计恒等式、平衡项、跨期连续性、与数据源公布指标的交叉核验 | 确定性代码 |
| 指标与模型 | 39 个指标与 Beneish M / Altman Z'' / Piotroski F 三大模型，公式和输入全部登记进**证据账本** | 确定性代码 |
| 风险排查 | 21 条规则，阈值也作为可引用的证据（附文献 / 经验依据） | 确定性代码 |
| 撰写 / 问答 | 大模型只能使用带编号的证据（E1…En）写作，每个数字必须引用编号；问答智能体调用 8 个确定性工具取证 | 大模型 |
| 复核 | **结论核验器**逐个抽取数字、换算单位、按显示精度比对证据，不一致或无出处的打回修订一次，仍不合格的在文中标红；另查口径混用与投资建议合规 | 确定性代码 |

设计原则：**大模型不产生数字**。它负责组织与解读，数字只来自确定性引擎，并由核验器逐个复核。

## 快速开始（Windows，一键）

需要 Python 3.10+（官方安装包或 Anaconda 均可）。**不需要 Node.js**（前端已构建好），**不需要 API Key**。

1. 双击 `scripts\start.bat`：首次运行会在项目内创建 `.venv` 并安装依赖（1–3 分钟，不改动全局 Python），然后自动打开浏览器 <http://127.0.0.1:8000>。
2. 选一家样本公司，依次查看：风险体检 → 研究简报 → 智能问答 → 指标趋势 → 财务报表 → 数据核验。点击任意数字或 `E12` 这样的引用标签，可以一路回溯到原始字段、快照 URL 和 SHA-256。

不配置 Key 时可用：风险体检、指标、报表、数据核验、模板简报，以及 **9 家公司全部的真实大模型简报样例**和 2 个问答样例（真实 Kimi 输出，展示前用当前核验器重新核验）。

启用大模型实时生成：双击 `scripts\setup_env.bat` 写入 Kimi / DeepSeek 的 Key（只写入本机 `.env`，已被 `.gitignore` 忽略）。填了 Kimi Key 时默认使用 `kimi-k3.1-pro`（参赛模型），只填 DeepSeek 时使用 `deepseek-flash`（低成本测试）；也可以在 `.env` 里用 `LLM_PROVIDER` 切换。

## 命令行（跨平台）

```bash
python -m venv .venv && . .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e "backend[dev]"
cd backend
python -m finsight.cli diagnose 600519              # 终端里的财务风险体检
python -m finsight.cli report 600519 --mode template # 可验证简报（--mode llm 需要 Key）
python -m finsight.cli ask 600519 "近三年利润的现金含量如何？"   # 研究问答（需要 Key）
python -m finsight.cli verify-snapshots             # 校验全部快照的 SHA-256
python -m finsight.cli serve --open                 # 工作台 http://127.0.0.1:8000 ；接口文档 /docs
python -m pytest                                    # 全部离线测试（不调用任何外部接口）
```

前端开发：`cd frontend && npm ci && npm run build`（输出到 `backend/finsight/web/`，由后端托管）。

## 评测与复现

| 评测 | 结果（如实表述） | 复现（离线、免费；在 `backend` 目录下运行） |
|---|---|---|
| ① 核验器检出率（受控注入） | 9 家公司模板简报共 130 个带引用的数字，8 类注入错误 × 两种排版全部检出；原文与合理四舍五入零误报。受控实验，不代表能发现真实文本中的所有错误 | `python -m finsight.cli eval verifier` → `docs/eval/verifier_bench.md` |
| ② 数字可追溯性三组对比（kimi-k3.1-pro，9 家） | A 纯大模型（不给证据）150 个数字中 107 个在证据库找不到归属；B/C（给证据、要求引用）326 个数字全部带引用且与证据一致。另做 40 行 AI 辅助财报抽检（**非盲审、非独立真值**）：A 组 2/20 判错，B/C 0/20。本批次核验器没有触发修订（B = C），**看不出核验器的边际价值**；样本小，不是统计结论 | `python -m finsight.cli eval ablation-score ../docs/eval/ablation-kimi --audit ../docs/eval/ablation-kimi/audit_sheet.csv --audit-kind ai-assisted` → `ablation_report.md` |
| ④ 耗时与成本 | 与评测② 同一次运行：完整流程每份简报平均约 76 秒、约 0.32 元（估算，不含税，以服务商账单为准） | 同上 |

重新调用大模型跑评测② 会产生费用：`scripts\eval_ablation.bat`（参数固定，先打印计划，需手动输入 YES；`dry` 只打印计划）。
历次真实模型测试的原始输出与费用记录在 `docs/eval/` 下；口径、局限与复核过程见 `docs/STATUS.md`。

## 在线演示与打包

- 在线体验链接的部署步骤、环境变量和费用控制：[`docs/DEPLOY.md`](docs/DEPLOY.md)。
- 生成参赛用的“关键代码”压缩包：双击 `scripts\package_code.bat`（或 `python scripts/package_code.py`），输出到 `dist/`。脚本会排除 `.env`、`.venv`、`node_modules` 等本机文件，**发现任何 API Key 就中止**，并在包内附每个文件的 SHA-256 清单。

## 目录

```
backend/finsight/
  data/        采集器（只用标准库）· 快照仓库（SHA-256）· 科目标准化
  analytics/   计算引擎（依赖追踪）· 指标 / 模型 · 数据核验 · 风险规则 · 体检主流程
  reporting/   证据上下文 · 可验证简报 · 结论核验器 · 口径一致性检查
  agent/       研究问答智能体（8 个确定性工具）
  llm/         OpenAI 兼容网关（适配推理模型）· 费用保护 · MockLLM
  evaluation/  评测①②
  api/         FastAPI · 异步任务 · 演示限额
  web/         前端构建产物
backend/tests/ 离线测试（真实快照 + MockLLM）
frontend/      React + TypeScript + Tailwind + ECharts 工作台；e2e/ 为 Playwright 冒烟测试
data/          snapshots/ 原始响应快照（manifest.jsonl + objects/）· samples/ 真实模型输出的缓存样例
docs/          STATUS（现状与验证记录）· DESIGN · ROADMAP · COMPETITION · DEPLOY · eval/（评测产物）
scripts/       Windows 一键脚本：start / setup_env / collect / llm_smoke / eval_ablation / package_code
```

## 数据与合规

- 数据来自公开披露渠道（东方财富 F10 公开财务数据、巨潮资讯），仅用于非商业的研究与竞赛原型；采集器限速访问、保存原始响应以便审计，不转售或再分发数据。年报 PDF 体积大，不随仓库分发（manifest 中保留哈希）。
- 样本为 9 家 A 股公司（含康美药业、康得新、獐子岛等历史案例），数据截至各自 2025 年年报；界面与简报都会注明数据时效。
- 本系统输出的是**财务风险排查线索**，预警模型基于海外样本估计，只作提示；**不构成任何投资建议**。

## 已知局限

- 没有接入实时行情（市值），Altman 模型使用账面版 Z''；银行等金融企业不套用工商业指标。
- 数据源的历史年度值可能被公司追溯调整后覆盖，直接用它回测历史造假案例会有“未来信息泄露”；历史案例回测需要原始年报 PDF 抽取，尚未完成，不在本版本中声称。
- 核验器只检查数字与所引证据是否一致，不保证全文推理、口径与时效都正确。
