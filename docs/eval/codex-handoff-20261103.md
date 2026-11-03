# Claude 第六轮交接的本机验收（2026-11-03）

本次完成代码审查、边界修复、本机完整离线测试与结果复现。用户随后指定由Codex填写40行，已完成原始财报AI辅助复核，详见 `codex-ai-audit-20261103.md`。不是独立人工盲审。没有发送模型请求，没有新增模型费用。

## 修复与证据

1. 收紧 `reporting/verifier.py` 的股票代码 / 品牌数字屏蔽：后续还有数字、小数、千分位或财务单位时保留完整金额。原逻辑漏掉“国窖1573元”“五粮液1618.50元”“证券代码：600519.25亿元”，并把“国窖15730万元”截成“0万元”。6 个新增回归用例修复前失败，修复后通过。
2. 补齐 `evaluation/ablation.py` 的填表保护：已有人工判定而 key 文件缺失时，普通打分和 `--regen-audit` 均拒绝覆盖。2 个新增回归用例修复前失败，修复后通过，并核对拒绝操作前后所有已有文件不变。
3. 更正报告解释：“无归属”可能是错误，也可能是证据缺漏；“有归属”可能碰巧匹配了错误指标或期间。因此自动问题数字率不是实际错误率的上下界。模型是否不知道新财报仍是待证实解释。

## 完整验证

| 项目 | 本机实测 |
|---|---|
| 环境 | Windows，Python 3.14.5，pytest 9.2.0，pytest-timeout 2.4.0 |
| 完整测试 | 183 passed，0 skipped，7.37 秒 |
| 云端缺少的 5 个测试文件 | engine、jobs_api、real_snapshots、verifier_matrix、verifier_regressions 均已包含 |
| 静态检查 | Ruff 0.17.3，backend/finsight 与 backend/tests 全部通过 |
| 注入评测 | spaced / compact 各 130 个原文数字；受控错误 100% 检出，原文对照误报率 0% |
| 新增失败用例 | 修复前 8 failed；修复后通过并纳入全套 |
| 警告 | Starlette TestClient 使用 httpx 的弃用提示 1 条 |

首次测试受沙箱限制，在 `asyncio` 创建本地 `socketpair` 的 accept 处超时；15 秒诊断堆栈保存后，在沙箱外运行同一隔离副本全套测试通过。没有因此更改异步应用逻辑。

完整命令（在隔离副本的 backend 目录，未载入 .env，API Key 清空）：

```powershell
python -m pytest -vv --timeout=60 --junitxml=<证据目录>/pytest-final.xml --basetemp=<证据目录>/pytest-final-tmp
```

核验器注入评测与离线重打分（项目根目录）：

```powershell
python -m finsight.cli eval verifier --out <证据目录>/verifier-bench
python -m finsight.cli eval ablation-score docs/eval/ablation-kimi
```

## 结果复现与数据保护

| 口径 | 结果 |
|---|---|
| A | 150 个数字，107 个问题数字（71.3%），8/9 家含问题数字 |
| A 无数字 | 002069；拒绝作答不构成数字准确性上的成功 |
| A 已归属的 43 个数字 | 报告期 0；往期且写明年份 13；往期却只写报告期年份 2；无年份 28 |
| B 与 C | 各 326 个数字，0 个自动问题数字；本批次 B = C，修订 0/9 |
| 抽检表 | 40 行，A 20、B/C 共有 20；无引用标记、无公司内重复数字，尚未填判定 |
| 账本 | 19 次请求、19 对预留与结算，无未结算；估算 5.164187 元，不含税，实际以控制台为准 |

重打分后的 summary 与 per_company 和 Claude v2 逐项相同。40 行抽检表与 key 均逐字节保留，未换样本。A 的可抽样公司为 8 家，各抽 2–3 条；B/C 为 9 家，各抽 2–3 条。

写回前检查了本机初始 506 个源文件哈希，均未变；副本的 companies、partial、reports、ledger、run_overview、当前抽检表与 v1 留档共 36 个文件逐字节一致。此次只更新派生报告的时间和解释文字。旧 v1 文件保留。

本次基线为本机 bundle `8dbd63e`。除 Claude 本次修复外，本机还有此前未提交的启动参数、预算停止、费用估算与 usage 记录修改，均纳入本次全套测试。提交明确列出文件，排除 .env、本地虚拟环境、data/runs 与无关前端构建资产；不推送 GitHub。

## 尚待完成

用户追加指定的AI辅助复核已完成：原表已填写，空白表另存，新增 `--audit-kind ai-assisted` 避免报告误称人工独立口径。最终完整测试184项通过，7.64秒。若以后另做独立人工盲审，需由未接触key与分组的人另填并保留本次结果。

自动统计不能替代这一步。本批次没有触发核验器修订，不能据此测得其边际价值；A 与 B/C 的提示词和证据均不同，差距只能作为这批样本中“证据 + 规则”整体效果的观察。
