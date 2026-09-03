# Changelog

本文件记录 Agent 用量分析报告（agent-analytics-report）的版本变更。

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
