# agent-analytics-report

> 生成 Agent 用量分析报告：Token 消耗趋势、缓存命中占比、各模型成本对比、异常自动预警。支持日 / 周 / 月 / 年。
> **首发支持 WorkBuddy，规划兼容更多 Agent。**

---

## ⚠️ 当前支持范围（请先读）

本技能**目前仅适配 WorkBuddy**，请勿声称已兼容其他 Agent：

- 数据采集自 WorkBuddy 本机数据（`~/.workbuddy/`、会话目录、`workbuddy.db`、`usage-log.json` 等）；
- 计价库 `scripts/pricing.json` 内含 **WorkBuddy 官方接口的模型与单价**（非通用市价）；
- 升级机制依赖 `skillhub upgrade`。

架构已为「多 Agent 扩展」预留接缝（见 [ADAPTERS.md](ADAPTERS.md)），但 **Trae / 千问办公 等适配器尚未实现**。

---

## 功能

- **Token 分析**：消耗趋势、原始 vs 实际（含缓存抵扣）、缓存占比
- **成本估算**：按模型公开单价计算，以实际账单为准；限免模型自动标注（如混元 Hy3 限时免费至 2026-08-31，花费记 ¥0.00）
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

---

## 测试（pytest + Allure）

核心逻辑（通道归因、周期对齐、生成时间格式等）带回归测试，覆盖「同一模型经不同接口（gateway / custom-local）」的识别、跨窗口会话补全等场景：

```bash
cd agent-analytics-report

# 1. 安装测试依赖（建议在隔离 venv 中）
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements-tests.txt

# 2. 运行测试并生成 Allure 原始数据
.venv/Scripts/python.exe -m pytest tests/test_channel_attribution.py -v --alluredir=allure-results

# 3. 渲染自包含可视化报告（无需 Java）
.venv/Scripts/python.exe tools/render_allure_html.py --results-dir allure-results --output allure-report.html
```

- 用例按 `tests/conftest.py` 的 marker 体系标注（`smoke` / `integration` / `regression` / `golden` / `metadata` …），可用 `-m` 过滤，例如 `pytest -m regression`；
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

## 架构与扩展

| 文件 | 职责 |
|------|------|
| `scripts/collect_usage_data.py` | 数据采集（当前为 WorkBuddy 适配器） |
| `scripts/generate_report.py` | 报告渲染（四色标记：🔀路由 / 🏠本地 / 🔧外部自定义 / 🗄️官方已下架） |
| `scripts/pricing.json` | 官方模型计价库（含 `delisted` 历史下架模型） |
| `scripts/pricing.local.json` | 用户本地覆盖（gitignore，不发布） |
| `references/` `examples/` | 操作手册与使用示例 |

多 Agent 扩展方式见 [ADAPTERS.md](ADAPTERS.md)。

---

## License

MIT © Elisabeth15501 2026
