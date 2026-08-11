# Changelog

本文件记录 Agent 用量分析报告（agent-analytics-report）的版本变更。

## [Unreleased]

### 🧪 测试 / 工程化
- **通道归因测试接入 pytest + Allure**：`tests/test_channel_attribution.py` 按 `conftest.py` 的 marker 体系（`smoke` / `integration` / `regression` / `golden` / `metadata` 等）标注，并叠加 Allure 注解（`feature` / `story` / `title` / `severity`），步骤与归因明细作为附件内联。
- **新增 Allure 自包含可视化报告**：`tools/render_allure_html.py` 零依赖将 `allure-results/` 渲染为离线 HTML（无需 Java）；官方 `allure serve` 亦可消费同一份数据。
- **测试配置与依赖固化**：新增 `pytest.ini`（指定 `--alluredir=allure-results`）、`requirements-tests.txt`；`.gitignore` 增补 `allure-results/`、`allure-report*`。
- 测试仅依赖标准库 + `pytest` + `allure-pytest`，不引用任何第三方商业 API。

## [1.1.1] — 2026-08-11

### ✨ 新特性 / 改进
- **周期定义改为日历对齐**：`--period` 不再随生成时刻滚动，而是固定对齐日历边界，不论生成报告在周中、月末还是年中：
  - `week` = 当前日历周（周一 ~ 周日，ISO 周）
  - `month` = 当前自然月（1 日 ~ 月底）
  - `year` = 当前自然年（1/1 ~ 12/31）
  - `day` 保持「今天」不变
- **通道归因测试推广到全模型**：`tests/test_channel_attribution.py` 改为 pytest 风格，在收集阶段动态加载 runtime 已知模型全集（pricing / 已下架 / 用户自定义 / 本地发现）进行参数化；新增「同会话两次调用、Key 不同仍归同一接口」用例，覆盖集成与回归。新模型加入后测试自动覆盖，无需改测试代码。
- **报告生成时间改为可读格式**：MD / HTML / JSON 三处统一为 `YYYY/MM/DD HH:MM:SS (UTC+08:00)`；`analyze_tokens.py`（Token 子报告）同步。
- **自然语言触发语更新**：`metadata.json` / `SKILL.md` 补充「生成周报 / 月报 / 年报 / 日报」等一句话触发描述。

### 🐛 Bug 修复
- **修复 SiliconFlow vendor 前缀覆盖导致通道误判**：`collect_usage_data.py` 中，当会话已显式声明 `custom-local:` 时，不再被 trace 实际执行模型的 vendor 前缀（如 `zai-org/glm-5.2`）覆盖，确保「同一模型经不同接口（官方 gateway vs 自建 custom-local）」在 §3.1 正确拆行。
- **修复跨窗口长会话 sid 缺失导致通道误判**：补全会话后并入 `sid_to_rawmodel` 并重采 trace，避免长会话原始模型退化成执行模型名而误判通道。
- **HTML 注入防护（XSS）**：`generate_report.py` 对所有 HTML 动态字段（会话标题、模型名、自动化名、未配置模型、限时免费映射、异常块等）加 `html.escape`。
- **脏数据 / 非法日期健壮性**：新增 `_to_num` 安全归一化（脏数据不再让整段采集崩溃）；非法 `--start/--end` 显式抛出中文错误并以退出码 2 结束。
- **日历周期标签改为 ISO 8601**：周报·`2026-W33`、月报·`2026-08`、年报·`2026`，与后台周期口径一致。

### 🧪 测试
- 通道归因测试全量通过（参数化覆盖全部已知模型，含 vendor 前缀自定义模型）。
- 测试套件仅依赖标准库 + pytest，不包含任何第三方商业 API 引用。

---

## [1.0.0] — 初始发布
- 首发支持 WorkBuddy 的 Agent 用量与成本分析报告（日/周/月/年），覆盖 Token 消耗、任务类型、技能使用与自动化运行。
- 多格式输出（Markdown / HTML / JSON），异常检测（成本 + Token 双口径），可扩展定价库。
