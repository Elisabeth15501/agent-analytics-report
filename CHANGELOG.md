# Changelog

本文件记录 Agent 用量分析报告（agent-analytics-report）的版本变更。

## [1.7.0] — 2026-09-15

### 🎯 F17 双源对账 P1 / P2 + 低置信度最贵模型告警（A1 / A2 / A3）

**F17 · P1 请求数反推（估算）**
- trace 是 generation 粒度，与官方「请求数」差一个量级（实测约 11.9x）。新增按 `session_id` + 时间窗（默认 15 分钟）聚类的启发式反推「估算请求数」，写入 `summary.estimated_request_count`，报告 §1 概览新增「估算请求数（generation 反推）」行，并在 L1 下展示「估算请求数 ↔ 官方请求数」倍率，消除「11.9x = 用量暴涨」的误读。
- 解析同时兼容 WorkBuddy trace 的 Unix 毫秒时间戳与 claude-code / codex 适配器的 ISO 8601 字符串（`_parse_started_at`）。
- **明确不做**：模型名归一化（剥离 `custom-local:` 前缀）——那是 WorkBuddy 自建 vs 外部 API 的唯一来源标识（2026-09-14 已决策 ❌）。

**F17 · P2 路由别名解析**
- `auto` / `fast-model` / `balanced-model` / `extreme-model` 等路由别名执行时**不记录落地底层模型**，报告显式列出这些别名「未解析具体模型、单独成组、不计入最贵模型与省钱建议等成本结论」，其单价/花费为所有计费模型均价估算值，仅供横向对比。

**A1 · 省钱杠杆过滤低置信度模型**
- `build_savings_insights` 的「付费模型」集合排除低置信度模型（`low_confidence_reason`），不推荐从折扣/时段模型迁走；改在 §4.4 顶部提示折扣/时段模型，避免把官方减免当成「可省的钱」。

**A2 · 机读偏差方向**
- `pricing.json` 新增 `low_confidence_bias` 段（`over`/`under`/`mixed`）；报告渲染 `⚠↑`（高估）/ `⚠↓`（低估）箭头，并在模型表、成本条形图、成本横幅、脚注统一说明方向。

**A3 · 低置信度最贵模型高位告警**
- 当低置信度模型恰好是本期最贵（或 Top-3）时，在 §3 顶部 + §4.4 顶部加醒目告警横幅：**真实花费以官方账单为准，不要据此切换模型**。

- 回归：修复 `_cluster_requests` 对 `started_at` 格式的假设（原误用 `ts_to_dt` 处理 ISO 字符串 → `NameError` / `TypeError`），claude-code / codex 端到端采集恢复绿。

## [1.6.2] — 2026-09-14

### 🐛 修复：低置信度模型标记改为纯 ⚠（去 span 包裹）

- 模型表与成本条形图的低置信度标记原为 `<span class="lc-flag" title="低置信度估算">⚠</span>`，
  HTML 中多了一层 span 包裹，与 Markdown 版的纯 `⚠` 不一致。改为纯 `⚠` 字符，
  并删除不再使用的 `.lc-flag` CSS 规则。
- 验证：生成报告 HTML 中 `lc-flag` 零匹配；`test_cost_confidence.py` 9 passed。

## [1.6.1] — 2026-09-14

### 🐛 修复：§3.5 双源对账把「收费版变体」误报成 trace 盲区

- **根因**：trace 侧早已按 `display_merge` 把收费版变体归并显示（`hy3-x` → `hy3`、
  `hy4-preview-x` → `hy4-preview`），但官方侧**没有过同一层映射**，对账用精确模型名匹配，
  于是 `hy3-x` / `hy4-preview-x` 匹配不上 trace 的 `hy3` / `hy4-preview`，被判成
  「官方有、trace 无」的盲区。
- **实测影响**（2026-08-13 ~ 09-12，30 日导出 802 请求 / 2902.39 积分）：
  虚报盲区 410.95 积分，占 `missing_credits` 的 **75.7%**；真实盲区只有 131.35。
- **修复**：新增 `collapse_display_aliases()`，官方侧先按 `display_merge` 归拢到基础名
  （键按小写索引，与 `ca_core.merge_display_key` 口径一致），再与 trace 比对；
  被归并的变体写进 `variants` 字段。报告里渲染为 `hy3（含 hy3-x）`，
  让读者一眼看出「这是同一模型的收费版，不是官方独有的模型」。
- **明确不做**：不做模型名归一化（不剥离 `custom-local:` 前缀）——
  那是「WorkBuddy 自建 vs 外部 API」的唯一来源标识，剥掉就无法判断 token 归属。
  §3.2 已用 `is_local` / `is_custom` 通道标记把用量拆成 官方/网关 · 本地模型 · 外部 API 三节。
- 回归用例 3 + 1 个：变体归拢、不再误判盲区（含「不传 alias_map 会误报」的反向断言）、
  前缀不被剥离、§3.5 渲染 `hy3（含 hy3-x）`。
- 修复后：30 日导出 `missing_credits` 542.57 → **131.62**，盲区仅剩
  `hunyuan-image-alpha` / `hunyuan-image-v3.0-art` / `deepseek-v4-pro`（真实漏记）。

## [1.6.0] — 2026-09-14

### 🎯 F17 双源对账：官方用量导出接入（P0 全部落地）

把「本地 trace」与「官方用量导出」两个从未对过账的数据源接起来，成本从**估算**升级为**真值**。

- **新增适配器 `adapters/official_usage.py`**
  - 纯标准库解析 xlsx（`zipfile` + `xml.etree`），**不引入 openpyxl**，技能零新增依赖。
  - **按表头名映射，禁止硬编码列位**：官方导出 2026-09-13 起新增 `User Prompt` 列，
    按列位解析会把 Prompt 当成模型名（调试脚本已踩过）。缺任一必需列（模型 / 积分 / 时间）直接报错。
  - 兼容 5 列旧版与 6 列新版；同时支持共享字符串（`t="s"`）、`inlineStr` 与日期序列值还原。
  - `credits <= 0` 判为**免费请求**（官方对时段减免 / 免费额度的实际结果）。
- **`--import-official <xlsx>`**：导入后 `meta.cost_source = "official"`，成本口径切 **L1 真值**；
  导出按报告窗口过滤，窗口外行数记入 `meta.official_import.rows_out_of_window` 并在 stderr 提示。
  仅 `--source workbuddy` 可用（其它 Agent 无官方积分账单），混用退出码 2。
- **报告新增 §3.5 双源对账**（MD / HTML 同步）：
  - 官方请求数 vs trace generation 数 + 粒度倍数（实测约 11.9x，标注「不是用量暴涨」）
  - **官方有 / trace 无** → 判为 trace 盲区并汇总漏记积分（实测图像模型 + minimax-m3 = ¥251.74 / 8.7%）
  - **trace 有 / 官方无** → 判为本地模型 / 免费额度 / 路由别名，明确「不是漏记」
  - 两边都有 → 逐模型给官方积分 vs trace 估算
- **L1 横幅**：显示官方请求数 / 积分 / 免费次数与导出窗口；并声明 §3 模型成本表仍是 L2 估算、仅供结构参考。
- **P0-3 口径标注**：概览「调用次数」加注 `(generation 粒度)`，L1 下同时展示官方请求数，
  避免把 11.9x 粒度差读成「用量暴涨」。

### 📖 治理（P0-4）

- `ADAPTERS.md` 新增 **§二 官方用量导出适配器** 与 **§四 数据来源与已知偏差**
  （trace 盲区清单、trace 独有清单、hy4 免费/收费同标签实证表、怎么办三步）。
- `SKILL.md` 新增「**成本口径：L1 真值 / L2 估算**」章节与 `--import-official` 参数说明。

### 🔧 工程

- `_is_l1()` 以「**是否真拿到官方数据**」为准而非 `meta.cost_source` 标记：
  标记是 official 但数据缺失时回落 L2，避免渲染出「L1 真值（未导入）」这种自相矛盾的横幅。
- `collect_usage_data.py`：`meta.cost_source` 恒写入（默认 `estimate`），新增 `official_usage` /
  `reconciliation` / `summary.total_cost_official` 等字段。
- `generate_report.py`：`build_reconciliation_section(fmt, data)`（未导入时返回 `[]`）；
  新增 `.note` 样式；概览卡片在 L1 下增补「官方请求数」「官方积分合计」。
- 版本 1.5.2 → 1.6.0（同步 5 处：SKILL.md / config.json / metadata.json / CHANGELOG.md / releases.md）。

### 🧪 测试

- 新增 `tests/test_official_usage.py`（24 用例）：xlsx 解析（明文 / 共享串 / inlineStr / 日期序列 / 坏文件 / 缺表）、
  表头映射（**新版 6 列模型不取 Prompt 的回归门禁** / 别名 / 缺列报错）、聚合与免费判定、日期过滤、
  双源对账、CLI 端到端（**USERPROFILE 隔离，绝不读真实 ~/.workbuddy**）、零回归、L1/L2 渲染与脏数据回落。
- 收紧 `test_cost_confidence.py` 的 L1 契约（v1.5.2 预留）：光有 `cost_source` 标记不够，须真有 official 数据。
- 测试规模：**474 用例全绿**（450 → 474）。

### ✅ 零回归

不传 `--import-official` 时，采集 JSON 无 `official_usage` / `reconciliation` 键，
报告不渲染 §3.5，其余输出与 v1.5.2 完全一致。

---

## [1.5.2] — 2026-09-14

### ⚠️ 成本置信度（核心：修正**默认路径**的可信度）

**背景**：用官方用量导出对标后发现，静态价表（`pricing.json`）在数学上**无法表达**「服务端时段减免 / 用户免费额度」，
导致部分模型的估算严重偏离真实账单。最典型的是 `hy4-preview`：实测 2026-09-12~09-14 共 12 次调用中
**11 次夜间调用积分为 0、仅 1 次白天收 43.40**，而静态价表会把 12 次全部计成收费。

**对策不是让价表变准（做不到），而是让报告明确告诉用户「你在看哪一级」**：

- **成本两级制**：`L1 真值`（导入官方用量导出后取「积分」字段）/ `L2 估算`（默认路径，静态价表推算）。
  报告顶部新增**成本口径横幅**，L2 下显式警告「不含服务端时段减免 / 用户免费额度，请勿据此做预算或账单对账」。
- **新增 `pricing.json` 的 `low_confidence` 段**：登记「估算与实际计费存在已知系统性偏差」的模型及原因
  （首版含 `hy4-preview`、`hy4-preview-x`、`deepseek-v4.1-flash`、`glm-5.3`、`glm-5.3-flash`）。
  用户可直接编辑，无需改 Python；也可用 `pricing.local.json` 覆盖。
- **低置信度标记**：命中的模型在模型表与成本图表里标 ⚠，并附原因说明。
- **不进「最贵模型」结论**：低置信度模型被剔除，但**被剔除者必须逐条列出金额**
  （避免出现「用一个新的误导替换旧的误导」——若直接删掉，图上最大的成本项会悄悄消失）。
- 无 `low_confidence` 配置（或本期未命中）时**零噪音**：不新增任何标记。

### 🔧 工程

- `ca_core.py`：新增 `LOW_CONFIDENCE` + `low_confidence_reason()`；`_load_pricing_config()` 支持从
  `pricing.json` / `pricing.local.json` 加载与覆盖。
- `collect_usage_data.py`：`meta.low_confidence` 透传（渲染层不硬编码）。
- `generate_report.py`：新增 `_cost_confidence_banner()` / `_lc_reason()`，MD 与 HTML 同步渲染；
  补 `.lc-flag` / `.lc-note` 样式。
- 版本 1.5.1 → 1.5.2（同步 5 处：SKILL.md / config.json / metadata.json / CHANGELOG.md / releases.md）。

### 🧪 测试

- 新增 `tests/test_cost_confidence.py`（9 用例）：定价段加载、采集层透传、MD/HTML 横幅、
  L1 切换契约、图表 ⚠ 标记、**剔除项必须披露**、无配置零噪音、不传 map 时行为不变。
- 测试规模：**450 用例全绿**（441 → 450）。

---

## [1.5.1] — 2026-09-12

### 🔄 定价数据刷新（DeepSeek 换代 + Hy4 限免新规）

- **DeepSeek V4-Flash 下架**：`deepseek-v4-flash` 由 `models` 移入 `delisted`（历史调用仍按 ¥1/¥2 计价，报告标 🗄️ 官方已下架）。旧模型名仍可调用，但已由 V4.1-Flash 承接、按 Flash 价计费。
- **新增 DeepSeek V4.1-Flash**：`deepseek-v4.1-flash`（官方在售名 `deepseek-flash`，二者报告内合并显示为一行）。2026-09-10 12:00 上线，1M 上下文、原生多模态（图像理解）。
  - **峰谷双档计费**（元/百万 tokens，缓存未命中口径）：空闲 输入 1 / 输出 4 / 缓存命中 0.02，高峰翻倍 输入 2 / 输出 8 / 缓存命中 0.04；高峰时段 = 周一至周五 09:00-12:00、14:00-18:00。
  - 本表 `input`/`output` 取**空闲档**，高峰值另以 `peak_input`/`peak_output` 记录供查阅（代码只读 `input`/`output`）；新增 `_pricing_rules.peak_valley` 说明该口径。
- **DeepSeek V4-Pro 下线预警**：官方计划 2026-09-14 12:00 下线，请求自动路由至 V4.1-Flash 并按 Flash 价计费。同条 `note` 记录了「本表 ¥3/¥6 与官方现行刊例 ¥9/¥27 不一致」的疑点，留待核对。
- **Hy4 preview 限免改规**：普适免费期已于 2026-09-10 结束，`timed_free.hy4-preview = 2026-09-10` **保留不变**（保证 09-10 及更早的历史调用继续计 ¥0）。新规为分人群 + 时段制（已体验用户夜间 23:00–08:00 免费；未体验用户在 2026-10-10 23:59 前首次开启起 14 天每日额度），静态价表无法表达，已记入 `_definitions.hy4_night_free`；需精确者用本机 `pricing.local.json` 覆盖。
- `mode_rates.fast.anchor`：原锚点 DeepSeek-V4-Flash 已下架，如实标注「沿用旧锚点、待按新倍率重锚」。
- `_sources` 补 DeepSeek 新定价页与 Hy4 限免新规来源；`_pricing_rules.updated` → 2026-09-12。

### 🧪 测试

- 回归守护：本次改造过程中全量测试抓到 1 处真实回归——移除 `timed_free.hy4-preview` 会使 2026-09-10 及以前的真实免费调用被计费（`tests/test_display_merge.py::test_merged_cost_counts_only_paid_variant`）。已回滚并保留该截止日。
- 测试规模：**441 用例全绿**。

### ⚠️ 行为变化

- 2026-09-11 起的 `hy4-preview` 调用由「限免 ¥0」变为按官方刊例价 6/18 计（老用户夜间免费额度为分人群 + 时段制，静态价表不计入，报告可能偏高）。
- 依赖 DeepSeek V4-Flash 名称的报表行现标 🗄️ 官方已下架。
- 版本 1.5.0 → 1.5.1。

## [1.5.0] — 2026-09-07

### 🐛 修复 / 数据完整性（P0 级）

- **修复 v1.3.0 模块拆分遗留的 NameError（WorkBuddy 默认路径完全不可用）**。`collect_task_types` 迁入 `ca_sessions.py` 时漏了导入 `get_session_content` / `get_session_artifact_fingerprint`（实际定义在 `ca_sources.py`），导致 `collect_usage_data.py --source workbuddy` 在真实运行时必然 `NameError` 崩溃。此前 353 个测试全绿却未发现——e2e 只覆盖了 `generate_report` 与 claude-code 分支，未触达 WorkBuddy 采集路径。已补显式导入 + 独立可调用回归测试。

### ✨ 新特性：任务分类加权评分（P2-3）

- **`classify_task` 从「顺序命中即返回」重写为加权评分**：所有规则参与打分取最高分；同分按原顺序打破平局。
  - **信号密度取胜**：同类型多个不同 pattern 命中各自累加，同一 pattern 重复命中几何衰减（0.35）——刷词无效，多样信号更有说服力；
  - **词边界修复**：纯 ASCII 词自动加 `\b`，`fix` 不再误命中 `prefix` / `fixture`（旧版 `re.search` 的真实误判）；
  - **置信度透明**：会话数据新增 `_task_confidence`（0~1），平票→0、一边倒→1，为报告端的「低置信度提示」留好接缝；
  - **规则外置** `scripts/task_rules.json`：每条 pattern 可配权重（强信号 2~2.5，弱信号 0.5~0.8），用户可直接增删改；文件缺失/损坏/含坏正则时安全回退内置规则或跳过该条。顺带修正原规则错别字 `小要`→`小说`。
- **可选 LLM 分类器（opt-in）**：`--task-classifier llm --task-llm-endpoint <URL> --task-llm-model <名>`。仅接受用户自备 OpenAI 兼容端点（本地 Ollama / 自有服务），**不内置任何第三方商业 API 默认值**；逐条分类失败打印 WARN 并回退启发式，绝不中断采集。默认 `heuristic` 完全离线。
- claude-code 数据源同样受益：`collect_task_types` 兼容 `_dialogue_text` 兜底，LLM 模式下两种数据源行为一致。

### ✨ 新特性：定价自动更新机制（P2-2）

- **新增 `scripts/fetch_pricing.py`**：拉取（`--url` 自备镜像端点 / `--file` 本地 JSON）→ 校验 → 候选 → 人工审核 → 落盘。
  - **合规设计**：不抓取任何厂商网页，数据源完全自备；
  - **默认不落盘**：产出 `scripts/pricing.candidate.json` + `pricing-diff.md`（接受/拒绝明细表），显式 `--apply` 才写入且自动备份 `pricing.json.bak-<时间戳>`；
  - **校验前置**：非数字/负数/超上限（¥10000/百万）拒绝；与现价偏差超 `--max-ratio`（默认 5x）拒绝并要求 `--force` 放行；无变化条目跳过；
  - **CI 过期检查**：`--check --stale-days 30` 按 `_pricing_rules.updated` 判断，过期退出码 1；
  - **测试缝**：`FETCH_PRICING_ROOT` 环境变量重定向路径，黑盒测试零接触真实定价文件。
- 落盘不动 `pricing.local.json`（本地覆盖始终优先）。

### 🧪 测试

- 新增 `tests/test_task_classification.py`（28 用例）与 `tests/test_fetch_pricing.py`（17 用例），含 3 个真实 bug 的回归守护（漏导入 NameError、拒绝条目双重收录、default-arg 绑定绕过 monkeypatch）。
- 测试规模：**16 个测试文件、398 用例全绿**（原 14 文件 / 353 用例）。

### ⚠️ 行为变化

- 依赖规则顺序的边界 case 分类结果可能变化（这正是本次修复的目的：多信号类型不再被排在前面的单信号类型抢走）。
- 版本 1.4.0 → 1.5.0。

---

## [1.4.0] — 2026-09-06

### ✨ 新特性：多 Agent 数据源（Claude Code 适配器）

- **新增 `--source` 参数，数据源可切换**。此前技能只能统计 WorkBuddy；现在可用 `--source claude-code` 统计 Claude Code 的用量，报告格式、聚合逻辑、计价与渲染完全复用。

  ```bash
  python scripts/collect_usage_data.py --source claude-code --period week -o data.json
  python scripts/generate_report.py data.json --output report.html --format html
  ```

- **新增 `adapters/claude_code.py`**：读取 `~/.claude/projects/**/*.jsonl`（每个文件一次会话），解析每轮 assistant 消息的 `usage` 字段归一化为统一 trace schema，并现场合成会话记录，使下游任务分类、Top 任务、每日趋势、模型对比无需任何改动。
  - **根目录定位三级回退**：`CLAUDE_PROJECTS_DIR` > `CLAUDE_CONFIG_DIR` > 平台默认（Windows `%APPDATA%/Claude/projects`、Unix `~/.claude/projects`），并兼容 Claude Desktop agent 模式的嵌套 `projects/` 子树。
  - **口径与 WorkBuddy 一致**：`cache_read_input_tokens` 计入缓存折扣（命中只按 10% 计），`cache_creation_input_tokens` 以 `_cache_creation_input_tokens` 透明字段保留供后续精细计价。
  - **健壮性**：坏 JSON 行、非 dict 行、无 `usage` 的 assistant 行一律跳过，不抛异常。
- **Claude 模型定价入库**：`pricing.json` 与 `ca_core.MODEL_PRICING` 新增 Opus 4（108/540）、Sonnet 4（21.6/108）、Haiku 4（5.76/28.8）及日期后缀变体，单位元/百万 tokens。`parse_channel()` 识别 `claude-code:` 前缀，`price_of()` 新增该通道分支。
  - ⚠️ 单价为 **Anthropic 美元刊例价按 ~7.2 汇率折算的估算值**，已在文档与 FAQ 明确标注，可用 `pricing.local.json` 覆盖。

### 📚 文档

- **`ADAPTERS.md` 全量重写**：从「未来扩展设想」改为「已实现说明 + 新增 Agent 指南」，含字段映射表、会话派生结构、计价表、已知限制与验收清单。
- `SKILL.md` / `README.md` 支持范围改为三行表格（WorkBuddy / Claude Code / 未实现），补 `--source` 用法示例与数据源路径表。
- `references/FAQ.md` 新增 Q43（能统计 Claude Code 吗）/ Q44（为什么没有技能与自动化）/ Q45（单价准吗），FAQ 计数 43 → 45 问。

### 🧪 测试

- **新增 `tests/test_claude_code_adapter.py`（11 用例）**：JSONL 解析正确性、日期窗口过滤（闭区间）、缓存折扣与成本、坏行健壮性、会话派生与任务分类、`--source claude-code` CLI 黑盒（采集 + HTML 渲染）。fixture 经 `CLAUDE_PROJECTS_DIR` 指向 `tmp_path`，**不读取用户真实目录**。
- 测试规模：**14 个测试文件、353 用例全绿**（原 13 文件 / 318 用例）。

### ⚠️ 已知限制（Claude Code 源）

- 无**技能调用**与**自动化运行**维度——Claude Code 的会话日志不落盘这两类数据，属客观限制而非采集失败。
- `duration_ms` 恒为 0（JSONL 无可靠端到端耗时字段，未做推测）。
- 不区分 Max 订阅与按量 API，一律按刊例价折算；订阅用户实际边际成本为 0。

---

## [1.3.0] — 2026-09-02

### ✨ 新特性 / 改进
- **新增「档位维度」分析（§3.4 快速 / 均衡 / 极致）**。WorkBuddy 自动路由器（`auto`）下还有三档可选档位：`fast-model`（快速，倍率 0.21x）/ `balanced-model`（均衡，0.65x）/ `extreme-model`（极致，1.20x）。过去报告只在 `auto` 行里用「全网均价」估算，看不到你实际按哪一档跑了多少。现在新增独立章节，按档位聚合调用次数、Token、估算花费与占比。
  - **档位倍率仅作分析维度，不参与 ¥ 金额计算**：这是 1.3.0 的核心口径决策——档位背后真实落地的底层模型在 trace 里从不记录（WorkBuddy 只对档位做积分倍率计费），因此按档位直接算出的「花费」在概念上不成立。报告里的档位 ¥ 单价是**倍率锚定法估算**（见下），仅用于横向对比档位间的相对成本，明确标注为估算值，与 §3.1（账单口径）/ §3.2（入口视图）的真实计费完全解耦、互不影响。
  - **致命不一致已归一化**：本机配置缓存里极致档的规范 id 是 `deep-model`，而 trace 里字面量是 `extreme-model`。采集器新增 `TIER_ALIASES` / `TIER_CANON` / `TIER_LABELS`，在 `parse_channel()` 把 `extreme-model` 归一为 `deep-model` 后聚合，避免两处拼写各自成行。
  - **数据来源：本地权威倍率表优先 + 手工映射兜底**。`_load_acc_product_config()` 读取本机 `~/.workbuddy/cache/acc-product-config-v3.json`（官方推送的 48 模型 credits 倍率表，`credits` 形如 `"x0.21"`，去 `x` 转 float），用官方倍率覆盖 `mode_rates` 的 multiplier；表缺失/解析失败时安全降级回 `pricing.json` 的锚定估算值（代码路径全部 `try/except` 兜底，serve 端产物无稳定性保证）。
  - 新增 `aggregate_by_tier(traces)`，复用 `aggregate_traces_by()` 并在两处并行 router-avg 实现（L739-746 与 L1435-1453）同步排除路由类别名，确保档位行不参与 `auto` 均价计算、也不污染各维度金额。
  - 实现要点：可配置——增删档位或调整估算单价只改 `pricing.json`（或 `pricing.local.json`）的 `mode_rates` 段，无需改 Python；报告 §3.4 含与 `auto` 一致的估算免责声明 + 可折叠 `mode_rates` 配置块；无档位数据自动返回空、不落 §3.3「未配置」假阳性（已配估算单价）。

### 📚 文档
- `references/FAQ.md` 新增档位专项（Q36 档位是什么 / 为什么单价是估算；Q37 为什么看不到档位背后的真实模型），版本号更新至 v1.3.0，FAQ 计数 34 → 36 问。
- README.md / SKILL.md 测试规模、FAQ 计数、§3 描述同步；SKILL.md 新增 §3.4 档位维度说明。

### 🐛 修复 / 数据完整性
- **修复「未命名会话」频繁出现（孤儿 trace 误标）**。旧逻辑在 `aggregate_by_session` / `aggregate_top_tasks` 里把「trace 的 session_id 在本地会话库查不到」的**孤儿 trace** 与「会话存在于本地库但无标题」的**真·无标题会话**混为一谈，统一兜底成「未命名会话」。实测近 7 天 11 个会话明细行里 7 个标「未命名会话」，但 7 个**全部是孤儿 trace**（真正无标题会话为 0），导致 23.6M token / ¥6.04 成本被错误归因到「未命名会话」名下，并触发「未命名高成本会话」误告警。
- **新增 `UNNAMED_LABEL` / `ORPHAN_LABEL` 双标签语义**：真·无标题会话仍标「未命名会话」；孤儿 trace 合并为**单一「未关联会话」汇总行**（不再逐条刷屏），且不再进入「未命名高成本会话」告警。MD/HTML 脚注同步说明三标签（未关联 / 未命名 / 其他）差异。
- 新增 `tests/test_session_labeling.py`（5 用例）守护该不变量。

### 🧪 测试
- **新增 `tests/test_mode_rates.py`（14 用例）**：TIER_ALIASES 四拼写、parse_channel 归一 extreme→deep、mode_rates 并入 MODEL_PRICING、config cache 倍率覆盖、cache 缺失回退（BLOCKER）、三档独立成行 + is_router 排除、无档位数据返回空、金额守恒（BLOCKER）、§3.1/§3.2/§3.4 档位金额一致（BLOCKER）、不污染 auto 均价、Agent workflow schema 还原 alias、无 usage 辅助 span 不虚增、报告段落含 §3.4、不落 unconfigured 假阳性。

测试规模同步更新：**11 个测试文件、312 用例全绿**（原 9 文件 / 284 用例）。

### 🧹 内部重构 / 工程（行为零变更，仅提升可维护性）
- **`collect_usage_data.py` 拆分为模块化架构（Phase 1a）**。原 2269 行单体脚本拆为：`ca_core.py`（公共常量 / 解析 / 单价工具）+ `ca_sources.py`（WorkBuddy 数据源读取）+ `ca_sessions.py`（会话归集）+ `ca_aggregate.py`（trace 聚合）；`collect_usage_data.py` 退化为仅做 `from X import *` 的薄门面。全部 312 用例零改动通过，CLI 行为字节级一致（由 `tests/test_publish_parity.py` 守护）。
- **收敛两处 router 均价实现（Phase 1b / D14）**。原 `ca_sources.py` 与 `ca_aggregate.py` 各自内联了一份「等权平均 router 单价」逻辑，新增公共 `_router_avg_unit_price(pairs)` 后两处统一调用，消除重复、降低后续改动漏改风险。
- **MD / HTML 报告生成去重（Phase 2 / D7）**。`generate_report.py` 中 5 对 MD/HTML 重复 builder——`_render_failed_automation_*`、`_render_cache_untitled_*`、`_render_anomaly_block_*`、`_free_period_disclaimer_*`、`build_cost_analysis_section_*`——合并为单一定义 + 按 `fmt` 分发的薄 wrapper，原 `*_md` / `*_html` 名称保留以保证外部调用 / 测试兼容。去重前后以「隔离金色基线」逐字节 diff 验证：仅生成时间行不同，其余输出 100% 一致。

---

## [1.2.1] — 2026-08-29

### 🐛 修复 / 数据完整性
- **修复「幽灵调用」占比虚高（trace 采集 schema 盲区）**。WorkBuddy 除扁平 LLM 调用 trace 外，还会写入 `Agent workflow` 类 trace：顶层 `modelInfo` 为空、`totalTokens=0`、`sessionId` 缺失，但模型与 Token 真实藏在内部 `generation` span 的 `toolOutput` 里。旧采集器只扫顶层字段，把这类真实工作流整批误判为「默认 glm-5.2」，导致幽灵率虚高约 42%、真实用量被低估约 15%。
- **新增 `_recover_model_info_from_spans()`**：顶层缺 `modelInfo` 时遍历 span 还原 `model` / `usage` 并回填 `collect_traces`。实测窗口内幽灵率 42.0% → 4.7%，回收约 16.7M Token（模型分布从 5 种扩到 10 种）。
- **已知限制**：`Agent workflow` 类 trace 的 `sessionId` 完全缺失（0 处），「按会话维度」归属仍不可恢复；残余约 4% 为控制流 span（`toolOutput` 无可解析 model），属真·不可归属。

---

## [1.2.0] — 2026-08-29

### ✨ 新特性 / 改进
- **`display_merge`：免费额度版 / 收费版合并显示**。WorkBuddy 对同一模型会提供两个入口——免费额度版（trace 记 `hy4-preview`）与免费额度用尽后的收费版（trace 记 `hy4-preview-x`）。过去它们在报告里被拆成两行、读起来像两个模型；现在按 `pricing.json` 新增的 `display_merge` 段合并为一行。
  - **合并只改分组，不碰钱**：引入「显示键 / 计费键分离」——显示键取合并后的基础模型名，计费键仍是每条 trace 实际执行的模型（`exec_model`）。因此免费额度版用量记 ¥0、收费版按刊例价计费，合并行的花费即等于其中**收费版那部分**的费用。
  - 实现要点：`aggregate_traces_by()` 新增 `resolve_billing_key_fn` 参数；`aggregate_by_model` 的合并行按 `exec_model` 计费；`timed_free_calls` 改用计费键统计，避免含收费调用的合并行被整体误标「限时免费」。
  - 可配置：增删合并对只改 `pricing.json`（或本地 `pricing.local.json`）的 `display_merge` 段，无需改动 Python 代码。默认已配 `hy4-preview-x → hy4-preview`、`hy3-x → hy3`。
  - 已验证：开关 `display_merge` 对照，§3.1（账单口径）与 §3.2（入口视图）两个维度的总金额均完全不变。

### 💰 定价库更新
- **新增 GLM-5.3-Flash**：输入 0.8 / 输出 2.8 元每百万 tokens（缓存命中 0.23，约为 GLM-5.3 的 1/10；国际版 z.ai / OpenRouter 为 $0.15 / $0.50）。
- **新增 Hy4 preview 与 hy4-preview-x**：输入 6 / 输出 18 元每百万 tokens（缓存命中 0.3）。`hy4-preview` 为免费额度版，`hy4-preview-x` 为额度用尽后的收费版。
- `hy4-preview` 限时免费至 **2026-09-10**（`timed_free`）。

### 📚 文档
- **新增 `references/FAQ.md`（34 问）**：按安装 / 生成 / 计价 / 自定义模型 / 标记与合并 / 数据源隐私 / 分类异常 / 故障排查分节，单篇自足，不用再翻 SKILL.md 与 README。含 `display_merge` 专项（为什么合并、费用怎么算、怎么改配置）。
- README.md 新增「常见问题」章节、SKILL.md 的 FAQ 章节顶部均指向完整版。
- 修正测试规模描述：**9 个测试文件、284 用例**（原写 8 文件 / 235 用例，已过时）。

### 🧪 测试
- **新增 `tests/test_display_merge.py`（13 用例）**：配置加载、两个维度均合并为一行、费用只计收费版（核心回归：若误用显示键计价，费用会被限免价吃成 ¥0）、合并前后金额守恒、限免标注正确、hy3 系列、未配置合并的模型不受影响。

---

## [1.1.3] — 2026-08-23

### ✨ 新特性 / 改进
- **hy3-x 官方接口定价**：`pricing.json` 新增 `hy3-x` 模型（输入 1 / 输出 4 元/1M tokens，缓存命中输入 0.25），与 hy3 同架构同价，为每日免费额度用完后按官方价计费的正确计价依据。
- **hy3 / hy3-x trace 误标修复**：WorkBuddy 在 2026-08-21 之前存在 trace 标签误标问题 —— hy3 调用被错误标记为 `model_key=hy3-x`，但 `exec_model=hy3`。`collect_usage_data.py` 新增 `resolve_key_fn` 回调，当 `model_key=hy3-x` 且 `exec_model=hy3` 时强制归入 hy3 行，确保账单口径与实际执行模型一致。

### 🧪 测试 / 工程化
- **通道归因测试接入 pytest + Allure**：`tests/test_channel_attribution.py` 按 `conftest.py` 的 marker 体系（`smoke` / `integration` / `regression` / `golden` / `metadata` 等）标注，并叠加 Allure 注解（`feature` / `story` / `title` / `severity`），步骤与归因明细作为附件内联。
- **新增 Allure 自包含可视化报告**：`tools/render_allure_html.py` 零依赖将 `allure-results/` 渲染为离线 HTML（无需 Java）；官方 `allure serve` 亦可消费同一份数据。
- **测试配置与依赖固化**：新增 `pytest.ini`（指定 `--alluredir=allure-results`）、`requirements-tests.txt`；`.gitignore` 增补 `allure-results/`、`allure-report*`。
- 测试仅依赖标准库 + `pytest` + `allure-pytest`，不引用任何第三方商业 API。

---

## [1.1.2] — 2026-08-12

### 🔧 发布 / 工程化
- **SkillHub 重新发布修正**：平台禁止打包无扩展名文件（`.gitignore`、`LICENSE`），将 `LICENSE` 更名为 `LICENSE.md`（GitHub 仍识别为许可证，`license: MIT` 声明不变）；`.gitignore` 仅用于 Git，不进发布包。
- **版本号升为 1.1.2**：覆盖平台上残留的 1.1.1 记录（首次发布因文件数超限被拒，平台仍写入了版本记录），以新版本号干净发布。
- 发布包已剔除 `pricing.local.json` / `allure-results` / `allure-report*` / `_meta.json` / `.pytest_cache` 等隐私与测试占位文件。

---

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
