# FinSight Agent

**可追溯的上市公司投研智能体**：报告里的每个财务数字都关联到证据，计算过程可以逐步复查。

参赛：第三届“兴智杯”全国人工智能创新应用大赛 · 主题赛 2（行业赋能）·「AI+金融赋能应用赛」· 投研分析场景。

## 它解决什么问题

研究员排查一家公司的财务风险，要读三大报表、附注和公告，交叉核对、计算指标、再写结论，耗时长，也容易出错。
通用大模型写得快，但会心算、记错年份，或写出看似合理却查无出处的数字。

FinSight 把“取数 → 数据核验 → 指标与模型 → 风险排查 → 撰写 → 复核”这段投研流程改造成可核验的智能体流程：

| 环节 | 做法 | 谁负责 |
|---|---|---|
| 取数 | 公开披露数据的**原始响应快照**（URL、抓取时间、SHA-256），读取时校验哈希 | 确定性代码 |
| 数据核验 | 11 项会计恒等式、平衡项、跨期连续性、与数据源公布指标的交叉核验 | 确定性代码 |
| 指标与模型 | 39 个指标与 Beneish M / Altman Z'' / Piotroski F 三大模型，公式和输入全部登记进**证据账本** | 确定性代码 |
| 风险排查 | 21 条规则，阈值也作为可引用的证据（附文献 / 经验依据） | 确定性代码 |
| 撰写 / 问答 | 大模型只能使用带编号的证据（E1…En）写作，每个数字必须引用编号；问答智能体调用 8 个确定性工具取证 | 大模型 |
| 复核 | **结论核验器**逐个抽取数字、换算单位、按显示精度比对证据，不一致或无出处的打回修订一次，仍不合格的在文中标红；另查口径混用与投资建议合规 | 确定性代码 |

设计原则：数字由确定性引擎计算，大模型负责组织和解读，文中每个数字再由核验器复核。

## 快速开始（Windows，一键）

需要 Python 3.10+（官方安装包或 Anaconda 均可）。前端已构建，不需要 Node.js；离线功能不需要 API Key。

1. 双击 `scripts\start.bat`：首次运行会在项目内创建 `.venv` 并安装依赖（1–3 分钟，不改动全局 Python），然后自动打开浏览器 <http://127.0.0.1:8000>。
2. 选一家样本公司，依次查看风险体检、研究简报、智能问答、指标趋势、财务报表和数据核验。点击任意数字或 `E12` 这样的引用标签，可以回溯到原始字段、快照 URL 和 SHA-256。

不配置 Key 时，可以使用风险体检、指标、报表、数据核验、模板简报，以及 9 家公司的大模型简报样例和 2 个问答样例（kimi-k3.1-pro 生成，展示前由当前核验器重新核验）。

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

| 评测 | 结果 | 复现（离线运行，在 `backend` 目录下） |
|---|---|---|
| ① 核验器检出率（受控注入） | 在 9 家公司模板简报的 130 个带引用数字上注入 8 类错误，两种排版下全部检出；原文与合理四舍五入零误报。真实模型输出的回归用例另见 `tests/test_verifier_regressions.py` | `python -m finsight.cli eval verifier` → `docs/eval/verifier_bench.md` |
| ② 数字可追溯性三组对比（kimi-k3.1-pro，9 家） | A 组（纯大模型，不给证据）150 个数字中有 107 个在证据库找不到归属；B/C 组（给证据、要求引用）326 个数字全部带引用且与证据一致。40 行 AI 辅助财报抽检（非盲审）中，A 组 2/20 判错，B/C 组 0/20。本批次初稿全部一次通过核验，B 与 C 相同 | `python -m finsight.cli eval ablation-score ../docs/eval/ablation-kimi --audit ../docs/eval/ablation-kimi/audit_sheet.csv --audit-kind ai-assisted` → `ablation_report.md` |
| ④ 耗时与成本 | 与评测② 同一次运行：完整流程每份简报平均约 76 秒、约 0.32 元（按公开标价估算，不含税） | 同上 |

重新调用大模型跑评测② 会产生费用：`scripts\eval_ablation.bat`（参数固定，先打印计划，需手动输入 YES；`dry` 只打印计划）。
历次真实模型测试的原始输出与费用记录在 `docs/eval/` 下，评测口径与局限写在各评测报告中。

## 在线演示与打包

- 公网演示：[FinSight 投研工作台](https://finsight-agent-20np.onrender.com)。公网版本展示缓存样例与确定性分析，未配置模型 Key；免费实例空闲后会休眠，首次访问需要等待约半分钟。发布记录见 [`docs/RELEASE-20261103.md`](docs/RELEASE-20261103.md)。
- 在线体验链接的部署步骤、环境变量和费用控制：[`docs/DEPLOY.md`](docs/DEPLOY.md)。
- 生成参赛用的关键代码压缩包：双击 `scripts\package_code.bat`（或 `python scripts/package_code.py`），输出到 `dist/`。脚本会排除 `.env`、`.venv`、`node_modules` 等本机文件，检查到 API Key 时停止打包，并在包内附每个文件的 SHA-256 清单。

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

- 数据来自公开披露渠道（东方财富 F10 公开财务数据、巨潮资讯），用于非商业研究与竞赛原型。采集器限速访问并保存原始响应以便审计，数据不转售、不再分发。年报 PDF 体积较大，未随仓库分发，manifest 中保留了哈希。
- 样本为 9 家 A 股公司（含康美药业、康得新、獐子岛等历史案例），数据截至各自 2025 年年报；界面与简报都会注明数据时效。
- 系统输出财务风险排查线索。预警模型的系数来自海外样本，结果用于提示核查方向。分析结果不构成投资建议。

## 已知局限与后续计划

- 未接入实时行情，Altman 模型使用账面版 Z''；银行等金融企业的报表结构不同，相关指标标记为不适用。
- 数据源的历史年度值会随公司追溯调整而更新，回测历史造假案例需要从原始年报 PDF 抽取当期数据。这部分回测列为后续工作。
- 核验器检查数字与所引证据是否一致；推理和口径的判断由口径一致性检查与人工复核补充。
