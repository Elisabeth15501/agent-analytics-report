# Releases

agent-analytics-report 的版本发布说明。每个版本都对应一个 GitHub Release（含完整 release notes），下表为速览。

> 完整、细粒度的变更历史见 [CHANGELOG.md](./CHANGELOG.md)。

| 版本 | 日期 | 主题 | GitHub Release |
|---|---|---|---|
| **v1.5.0** | 2026-09-07 | 任务分类加权评分（P2-3）· 定价自动更新（P2-2）· P0 数据完整性修复 | [tag/v1.5.0](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.5.0) |
| **v1.4.0** | 2026-09-06 | 多 Agent 数据源（Claude Code 适配器 MVP，P2-1） | [tag/v1.4.0](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.4.0) |
| **v1.3.0** | 2026-09-02 | 档位维度分析 · 模块拆分重构 · 孤儿 trace 修复 | [tag/v1.3.0](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.3.0) |
| **v1.2.1** | 2026-08-29 | 幽灵调用修复（Agent workflow span 还原） | —（仅 CHANGELOG） |
| **v1.2.0** | 2026-08-29 | display_merge 合并显示 · 定价库更新 · FAQ | —（仅 CHANGELOG） |
| **v1.1.3** | 2026-08-23 | hy3-x 定价 · 通道测试 Allure 化 | [tag/v1.1.3](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.1.3) |
| **v1.1.2** | 2026-08-12 | SkillHub 重新发布修正 | —（仅 CHANGELOG） |
| **v1.1.1** | 2026-08-11 | 日历对齐周期 · XSS 防护 | [tag/v1.1.1](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.1.1) |
| **v1.0.0** | 初始发布 | 首发 WorkBuddy Agent 用量与成本报告 | [tag/v1.0.0](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.0.0) |

---

## v1.5.0 — 2026-09-07

**任务分类加权评分（P2-3）· 定价自动更新机制（P2-2）· P0 数据完整性修复**

- 🐛 **P0 数据完整性修复**：修复 v1.3.0 模块拆分遗留的 `NameError`——`collect_task_types` 漏导入 `get_session_content` / `get_session_artifact_fingerprint`，导致 WorkBuddy 默认采集路径运行即崩溃。
- ✨ **任务分类加权评分（P2-3）**：`classify_task` 重写为加权评分——所有规则参与打分、信号密度取胜、ASCII 词自动加 `\b`、新增 `_task_confidence` 置信度；规则外置 `scripts/task_rules.json`；可选 LLM 分类器（opt-in，仅接受自备 OpenAI 兼容端点）。
- ✨ **定价自动更新（P2-2）**：新增 `scripts/fetch_pricing.py`——拉取 → 校验 → 候选 → 审核 → 落盘；合规不抓厂商网页，默认不落盘、显式 `--apply` 才写入并自动备份；CI `--check --stale-days` 过期检查。
- 🧪 **16 个测试文件、398 用例全绿**。
- 🔗 [GitHub Release v1.5.0](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.5.0)

## v1.4.0 — 2026-09-06

**多 Agent 数据源（Claude Code 适配器 MVP，P2-1）**

- ✨ **多 Agent 数据源**：新增 `--source` 参数，`--source claude-code` 统计 Claude Code 用量，报告管道零改动复用。
- ✨ **`adapters/claude_code.py`**：读取 `~/.claude/projects/**/*.jsonl`，`usage` 归一化为统一 trace schema 并现场合成会话记录；根目录三级回退、口径与 WorkBuddy 一致（缓存折扣计入）、坏行健壮性跳过。
- ✨ **Claude 模型定价入库**：Opus 4 / Sonnet 4 / Haiku 4（美元刊例价 ~7.2 汇率折算估算，可用 `pricing.local.json` 覆盖）。
- 📚 `ADAPTERS.md` 全量重写；FAQ 新增 Q43–Q45。
- 🧪 **14 个测试文件、353 用例全绿**。
- ⚠️ **已知限制**：Claude Code 源无技能调用 / 自动化运行维度，`duration_ms` 恒为 0，不区分 Max 订阅与按量 API。
- 🔗 [GitHub Release v1.4.0](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.4.0)

## v1.3.0 — 2026-09-02

**档位维度分析 · 模块拆分重构 · 孤儿 trace 修复**

- ✨ **档位维度分析**：新增快速 / 均衡 / 极致三档聚合；致命不一致归一化（`extreme-model` → `deep-model`）；本地权威倍率表优先。
- 🐛 **孤儿 trace 修复**：区分「孤儿 trace」与「真·无标题会话」，新增 `UNNAMED_LABEL` / `ORPHAN_LABEL` 双标签，避免误告警。
- 🧹 **模块拆分重构**：`collect_usage_data.py` 拆为 `ca_core` / `ca_sources` / `ca_sessions` / `ca_aggregate`；MD / HTML 报告生成去重。
- 🔗 [GitHub Release v1.3.0](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.3.0)

## v1.2.1 — 2026-08-29

**幽灵调用修复（Agent workflow span 还原）** · *无独立 GitHub Release，以下为 CHANGELOG 摘要*

- 🐛 **修复「幽灵调用」占比虚高（trace 采集 schema 盲区）**：WorkBuddy 的 `Agent workflow` 类 trace 顶层 `modelInfo` 为空、`totalTokens=0`、缺 `sessionId`，真实模型与 Token 藏在内部 `generation` span 的 `toolOutput`。旧采集器只扫顶层，把这类工作流整批误判为默认 `glm-5.2`，幽灵率虚高约 42%、真实用量低估约 15%。
- ✨ **新增 `_recover_model_info_from_spans()`**：顶层缺 `modelInfo` 时遍历 span 还原 `model` / `usage` 回填，幽灵率 42.0% → 4.7%，回收约 16.7M Token（模型分布 5 种扩到 10 种）。
- ⚠️ **已知限制**：`Agent workflow` trace 的 `sessionId` 完全缺失，「按会话维度」归属不可恢复；残余约 4% 为控制流 span，属真·不可归属。

## v1.2.0 — 2026-08-29

**display_merge 合并显示 · 定价库更新 · FAQ** · *无独立 GitHub Release，以下为 CHANGELOG 摘要*

- ✨ **`display_merge`：免费额度版 / 收费版合并显示**：同一模型两个入口（`hy4-preview` 免费额度版 / `hy4-preview-x` 收费版）过去拆成两行；现按 `pricing.json` 的 `display_merge` 段合并为一行。
  - **合并只改分组、不碰钱**：显示键取合并基础模型名，计费键仍是每条 trace 实际执行的 `exec_model`，免费额度版记 ¥0、收费版按刊例价计费。
  - 可配置：增删合并对只改 `pricing.json`（或 `pricing.local.json`）的 `display_merge` 段，无需改 Python；默认已配 `hy4-preview-x → hy4-preview`、`hy3-x → hy3`。
- 💰 **定价库更新**：新增 GLM-5.3-Flash（输入 0.8 / 输出 2.8）、Hy4 preview 系列（6 / 18）；`hy4-preview` 限时免费至 2026-09-10。
- 📚 **新增 `references/FAQ.md`（34 问）**；README / SKILL 同步。
- 🧪 新增 `tests/test_display_merge.py`（13 用例）。

## v1.1.3 — 2026-08-23

**hy3-x 定价 · 通道测试 Allure 化**

- 💰 **hy3-x 官方接口定价**：`pricing.json` 新增 `hy3-x`（输入 1 / 输出 4，缓存命中 0.25）；`collect_usage_data.py` 新增 `resolve_key_fn`，当 `model_key=hy3-x` 且 `exec_model=hy3` 时强制归入 hy3 行，修复 trace 标签误标。
- 🧪 **通道归因测试接入 pytest + Allure**：新增 `tests/test_channel_attribution.py`（marker 体系 + Allure 注解）、`tools/render_allure_html.py`（零依赖离线 HTML）、`pytest.ini`、`requirements-tests.txt`；测试仅依赖标准库 + pytest + allure-pytest。
- 🔗 [GitHub Release v1.1.3](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.1.3)

## v1.1.2 — 2026-08-12

**SkillHub 重新发布修正** · *无独立 GitHub Release，以下为 CHANGELOG 摘要*

- 🔧 **SkillHub 重新发布修正**：平台禁止打包无扩展名文件（`.gitignore`、`LICENSE`），将 `LICENSE` 更名为 `LICENSE.md`（GitHub 仍识别为许可证，`license: MIT` 声明不变）；`.gitignore` 仅用于 Git，不进发布包。
- **版本号升为 1.1.2**：覆盖平台上残留的 1.1.1 记录（首次发布因文件数超限被拒，平台仍写入了版本记录），以新版本号干净发布。
- 发布包已剔除 `pricing.local.json` / `allure-results` / `allure-report*` / `_meta.json` / `.pytest_cache` 等隐私与测试占位文件。

## v1.1.1 — 2026-08-11

**日历对齐周期 · XSS 防护**

- 📅 **周期定义改为日历对齐**：`week` = 当前 ISO 周（周一~周日）、`month` = 自然月、`year` = 自然年、`day` = 今天；周期标签改 ISO 8601（周报 `2026-W33` 等）。
- 🧪 **通道归因测试推广到全模型**：参数化覆盖 runtime 已知模型全集，新模型加入后测试自动覆盖。
- 🐛 **Bug 修复**：SiliconFlow vendor 前缀覆盖导致通道误判、跨窗口长会话 sid 缺失误判、**HTML 注入防护（XSS）**、脏数据 / 非法日期健壮性、报告生成时间改可读格式、自然语言触发语更新。
- 🔗 [GitHub Release v1.1.1](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.1.1)

## v1.0.0 — 初始发布

**首发 WorkBuddy Agent 用量与成本分析报告**

- ✨ 首发支持 WorkBuddy 的 Agent 用量与成本分析（日 / 周 / 月 / 年），覆盖 Token 消耗、任务类型、技能使用与自动化运行。
- 📄 多格式输出（Markdown / HTML / JSON），异常检测（成本 + Token 双口径），可扩展定价库。
- 🔗 [GitHub Release v1.0.0](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.0.0)
