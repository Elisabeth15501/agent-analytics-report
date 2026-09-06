# agent-analytics-report

> 生成 Agent 用量分析报告：Token 消耗趋势、缓存命中占比、各模型成本对比、异常自动预警。支持日 / 周 / 月 / 年。
> **数据源可切换：WorkBuddy（默认）+ Claude Code。**

---

## 当前支持范围

| 数据源 | `--source` | 状态 | 读取位置 |
|---|---|---|---|
| WorkBuddy | `workbuddy`（默认） | ✅ | `~/.workbuddy/`（traces + `workbuddy.db` + `usage-log.json` + 会话目录） |
| Claude Code | `claude-code` | ✅ | `~/.claude/projects/**/*.jsonl` |
| Trae / 千问办公 | — | ⬜ 未实现 | — |

- **WorkBuddy**：完整能力，含技能调用、自动化运行、任务分类；
- **Claude Code**：解析会话日志的 token / 成本 / 任务类型 / 每日趋势，无技能与自动化维度（JSONL 里没有这两类数据）。
- 计价库 `scripts/pricing.json` 内含 **WorkBuddy 官方接口的模型与单价**（非通用市价，含 GLM-5.3 / GLM-5.3-Flash / Hy4 preview 等），以及 Claude 系列模型的**人民币折算估算价**（以 Anthropic 美元刊例价为准，可用 `pricing.local.json` 覆盖）；
- 升级机制依赖 `skillhub upgrade`。

新增数据源的扩展方式见 [ADAPTERS.md](ADAPTERS.md)。

---

## 功能

- **Token 分析**：消耗趋势、原始 vs 实际（含缓存抵扣）、缓存占比
- **成本估算**：按模型公开单价计算，以实际账单为准；限免模型自动标注（如混元 Hy3 限时免费至 2026-09-30、Hy4 preview 至 2026-09-10，期间花费记 ¥0.00）
- **免费额度版 / 收费版合并显示**：同一模型的两个入口（如 `hy4-preview` 与 `hy4-preview-x`、`hy3` 与 `hy3-x`）在报告中合并为一行，费用只计收费版用量——合并只改显示分组，不碰计费（`pricing.json` 的 `display_merge` 段可增删合并对）
- **异常双口径**：成本 + Token 独立检测，免费期高流量也不漏报；脏数据显式警告
- **自定义模型（Bring Your Own Models）**：带上你自己的模型一起计费，升级不丢
- **报告格式**：Markdown / HTML（含交互图表、浅深色自适应）/ JSON

---

## 安装

### 方式一：SkillHub（推荐）
在 SkillHub 搜索 `agent-analytics-report` 一键安装。

### 方式二：手动
```bash
git clone <your-repo-url> ~/.workbuddy/skills/agent-analytics-report
```
重启 WorkBuddy 后技能生效。

---

## 使用

```bash
# 1. 采集最近 7 天数据
python scripts/collect_usage_data.py --period week --output data.json

# 2. 生成报告（Markdown + HTML）
python scripts/generate_report.py data.json --output report.md   --format markdown
python scripts/generate_report.py data.json --output report.html --format html
```

### 切换数据源（Claude Code）

```bash
python scripts/collect_usage_data.py --source claude-code --period week --output data.json
python scripts/generate_report.py data.json --output report.html --format html

# 自定义 Claude Code projects 目录
CLAUDE_PROJECTS_DIR=/path/to/projects \
  python scripts/collect_usage_data.py --source claude-code --period week --output data.json
```

---

## 测试（pytest + Allure）

本技能附带一套分层回归测试，覆盖从数据采集、计费等效折算、报告生成到发布一致性的全链路。**全部用例使用合成 fixture 数据，不引用任何第三方商业 API、不含真实用量/个人信息**，可安全公开（适合作为作品集在 GitHub Pages 展示）。

测试分层（共 16 个测试文件、398 用例全绿）：

| 层 | 文件 | 覆盖要点 |
|----|------|----------|
| **L0 数据采集** | `test_cost_math.py` · `test_calendar_period.py` · `test_aggregation.py` · `test_display_merge.py` · `test_mode_rates.py` · `test_no_broad_except.py` · `test_example_report_sanity.py` | 计价（GLM 折扣 / 缓存折算 / 限免 / blended 回退）、日历周期对齐、聚合与异常双口径检测、display_merge 合并显示与计费分离、档位维度（快速/均衡/极致）倍率锚定与归一、禁止宽泛异常捕获、示例报告健康度 |
| **L1 报告生成** | `test_report_generation.py` | GLM-5.2 家族合并、XSS 转义、图表构建、三格式（md / html / json）跑通 |
| **L2 定价边界** | `test_pricing_boundary.py` | 通道分支、已下架模型、零/负/超大值、blended 精度 |
| **L3 CLI 端到端** | `test_e2e_cli.py` | 黑盒 subprocess 跑通报告生成三格式、CLI 参数校验、恶意模型名 XSS 回归 |
| **L4 发布一致性** | `test_publish_parity.py` | `config.json` / `metadata.json` 版本对齐、交付物齐全、`.gitignore` 闸门（敏感产物不进包） |
| **L0 适配器** | `test_claude_code_adapter.py` | Claude Code JSONL 解析、日期窗口过滤、缓存折扣与成本、坏行健壮性、会话派生与任务分类、`--source claude-code` CLI 黑盒（fixture 走 `CLAUDE_PROJECTS_DIR`，不读真实目录） |
| **L0 任务分类** | `test_task_classification.py` | 加权评分取代首匹配、词边界（fix≠prefix）、信号密度取胜、置信度、task_rules.json 外置与回退、LLM 分类器 mock 全路径、collect_task_types 独立可调用（v1.3.0 拆分漏导入回归） |
| **L0 定价更新** | `test_fetch_pricing.py` | 条目校验（schema/负数/倍率）、候选与 diff 产出、--apply 备份落盘、过期检查退出码、`FETCH_PRICING_ROOT` 隔离黑盒 |
| 既有 | `test_channel_attribution.py` · `test_session_labeling.py` | 通道归因、跨窗口会话补全、孤儿 trace 合并、任务类型标签正确性 |

运行方式：

```bash
cd agent-analytics-report

# 1. 安装测试依赖（建议隔离 venv）
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements-tests.txt

# 2. 运行全部测试并生成 Allure 原始数据
.venv/Scripts/python.exe -m pytest tests/ -v --alluredir=allure-results

# 3a. 用本机 Java 渲染原生交互报告（可选）
.venv/Scripts/python.exe -m allure serve allure-results

# 3b. 渲染自包含 HTML 仪表盘（无需 Java，适合 GitHub Pages 静态托管）
.venv/Scripts/python.exe tools/render_allure_html.py --results-dir allure-results --output allure-report.html
```

- 用例按 `tests/conftest.py` 的 marker 体系标注（`smoke` / `unit` / `integration` / `regression` / `golden` / `metadata` / `privacy` …），可用 `-m` 过滤，例如 `pytest -m regression`；
- 报告含用例步骤树、参数、归因明细附件；本机若装有 Java，也可直接 `allure serve allure-results` 消费同一份数据；
- 测试仅依赖标准库 + `pytest` + `allure-pytest`，**不引用任何第三方商业 API**；`allure-results/` 与 `allure-report*` 已被 `.gitignore` 排除，不进发布包。

---

## 自定义模型（BYOM）

发布版只内置 **WorkBuddy 官方在用模型 + 官方已下架历史模型**，`custom_local` 段为空 `{}`。

你自己的第三方 / 自建模型（OpenRouter 免费、硅基流动、自建 Ollama 等）写在**本机** `scripts/pricing.local.json`——
该文件已被 `.gitignore` 排除，**不进发布包，`skillhub upgrade` 升级也不会覆盖**，单价自动保留。

下载技能后首次生成报告时，Agent 会自动扫描本机用过的 `custom-local:*` 模型及 `~/.workbuddy/models.json`
中配置的自定义 / 外部 / Ollama 端点，问你单价后写入本地文件；之后用自然语言告诉 Agent 新增模型即可。

---

## 常见问题

花费对不上、模型显示「未配置」、想加自己的模型、想知道那些小图标什么意思——集中解答见 [references/FAQ.md](references/FAQ.md)（36 问，单篇自足）。

---

## 架构与扩展

| 文件 | 职责 |
|------|------|
| `scripts/collect_usage_data.py` | 数据采集门面（当前为 WorkBuddy 适配器）；内部已拆分为 `ca_core` / `ca_sources` / `ca_sessions` / `ca_aggregate` 四模块，门面仅做 `from X import *` |
| `scripts/generate_report.py` | 报告渲染（四色标记：🔀路由 / 🏠本地 / 🔧外部自定义 / 🗄️官方已下架） |
| `scripts/pricing.json` | 官方模型计价库（含 `delisted` 历史下架模型、`display_merge` 显示合并配置） |
| `scripts/pricing.local.json` | 用户本地覆盖（gitignore，不发布） |
| `references/` `examples/` | 操作手册与使用示例 |

多 Agent 扩展方式见 [ADAPTERS.md](ADAPTERS.md)。

---

## License

MIT © Elisabeth15501 2026
