# Releases

agent-analytics-report 的版本发布说明。每个版本都对应一个 GitHub Release（含完整 release notes），下表为速览。

> 完整、细粒度的变更历史见 [CHANGELOG.md](./CHANGELOG.md)。

| 版本 | 日期 | 主题 | GitHub Release |
|---|---|---|---|
| **v1.5.0** | 2026-09-07 | 任务分类加权评分（P2-3）· 定价自动更新（P2-2）· P0 数据完整性修复 | [tag/v1.5.0](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.5.0) |
| **v1.4.0** | 2026-09-06 | 多 Agent 数据源（Claude Code 适配器 MVP，P2-1） | [tag/v1.4.0](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.4.0) |
| **v1.3.0** | 2026-09-02 | 档位维度分析 · 模块拆分重构 · 孤儿 trace 修复 | [tag/v1.3.0](https://github.com/Elisabeth15501/agent-analytics-report/releases/tag/v1.3.0) |

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
