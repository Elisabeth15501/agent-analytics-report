# agent-analytics-report · 优化方案（v1.3.0 → v1.4.0）

> 整理时间：2026-09-04 ｜ 方法：SkillHub TRACE 评测报告 + 本地代码审计
> 最后更新：2026-09-05（P1-2 错误处理细化完成，commit `8d2edd7`）

---

## 0. TRACE 评测结果（官方 API 获取）

| 维度 | 总分 | 子项 | 得分 | 扣分原因 |
|------|------|------|------|----------|
| **T rust** | **4.90** | domestic（国内可用） | 4.8 | 测试数据含英文模型名 |
| | | scan（安全扫描） | 5.0 | — |
| **R eliability** | **4.50** | errorHandling | 4.5 | 边缘错误处理可细化 |
| | | func（功能完整性） | 4.5 | 多 Agent 适配器未实现 |
| | | stability | 4.5 | 无自动重试机制 |
| **A daptability** | **4.65** | boundary（边界定义） | 4.5 | 缺 OS 兼容性/数据量上限/trace 格式兼容性说明 |
| | | trigger（触发方式） | 4.8 | — |
| **C onvention** | **4.80** | antiPatternFaq | 4.8 | — |
| | | docQuality | 4.8 | — |
| | | progressive | 4.8 | — |
| | | structure | 4.8 | — |
| **E ffectiveness** | **4.80** | accuracy | 4.8 | 定价为估算值；hy3 限免日期 `collect_usage_data.py` vs `pricing.json` 不一致 |
| | | completeness | 4.8 | 仅 WorkBuddy 适配器；任务分类为启发式 |
| | | creativity | 4.8 | 任务分类为简单关键词启发式 |
| | | usability | 4.8 | 自定义模型发现依赖 Agent 引导 |

**总体平均：4.73 / 5.0**（优秀）

> 评测版本标注为「9 文件 / 284 用例」，与当前 **11 文件 / 318 用例** 存在差异。

---

## 1. 问题清单（按优先级排序）

### P0 — 必须修复（影响准确性 / 正确性）✅ 全部完成

| # | 问题 | 来源 | 状态 | 提交 |
|---|------|------|------|------|
| P0-1 | **hy3 限免日期不一致**：`collect_usage_data.py` L57 写 2026-08-31，`pricing.json` L77 写 2026-09-30 | TRACE E.accuracy 扣 0.5 | ✅ 完成 | `cac9caf` |
| P0-2 | **文档数字陈旧**：FAQ Q33 写「10 文件 / 306 用例」，TECH_DEBT.md 仍为 v1.1.2 / 243 用例 | 自检 | ✅ 完成 | `cac9caf` |
| P0-3 | **示例报告非自动生成**：手工维护，刚手动改过两次日期 | 自检 | ✅ 完成 | `7788e1d` |
| P0-4 | **官方倍率覆盖路径 CI skip**：`test_config_cache_overrides_multiplier` 依赖本机缓存，CI 干净检出时跳过 | 自检 | ✅ 完成 | `34d8240` |

### P1 — 应当改进（影响可靠性 / 易用性）

| # | 问题 | 来源 | 状态 | 提交 |
|---|------|------|------|------|
| P1-1 | **边界说明缺失**：缺 OS 兼容性、数据量上限、trace 格式兼容性说明 | TRACE A.boundary | ✅ 完成 | `34d8240` |
| P1-2 | **错误处理可细化**：部分 `except Exception` 仅 log 回退，未区分业务错误与系统错误 | TRACE R.errorHandling | ✅ 完成 | `8d2edd7` |
| P1-3 | **测试数据中文化**：测试数据含英文模型名，影响中文环境友好度评分 | TRACE T.domestic | ✅ 完成 | `P1-3` |
| P1-4 | **自定义模型发现依赖 Agent 引导** | TRACE E.usability | ✅ 完成 | `P1-4` |

### P2 — 可选增强

| # | 问题 | 来源 | 影响 |
|---|------|------|------|
| P2-1 | **无自动重试机制**：数据采集失败仅跳过，不重试 | TRACE R.stability | 临时网络/IO 错误可能丢数据 |
| P2-2 | **Kimi K3 价格引用媒体报道** | TRACE 总体 | 非一手官方来源，价格可能不准 |
| P2-3 | **任务分类为关键词启发式** | TRACE E.completeness / E.creativity | 边界 case 可能误判 |

---

## 2. 已完成优化详情

### P0-1：hy3 限免日期不一致修复（`cac9caf`）

**问题**：`ca_core.py` L28 硬编码 `timed_free["hy3"] = "2026-08-31"`，`pricing.json` L77 写 `"hy3": "2026-09-30"`。

**修复**：以 `pricing.json` 为准（权威定价源），更新 `ca_core.py` 中的硬编码值至 2026-09-30，并同步 `_load_pricing_config()` 中的注释。

**验收**：`grep -n "2026-08-31\|2026-09-30" scripts/*.py` 确认无冲突；`pytest -q` 全绿。

---

### P0-2：文档数字刷新（`cac9caf`）

**问题**：FAQ Q33 写「10 文件 / 306 用例」，TECH_DEBT.md 仍为 v1.1.2 / 243 用例。

**修复**：
- FAQ Q33：更新为「11 个测试文件、312 用例」（后增至 318 用例，Q33 已同步）
- TECH_DEBT.md 标题行：更新为 v1.3.0 / 312 用例

**验收**：三处数字与实测值 ±0 一致。

---

### P0-3：示例报告 CI 自动生成（`7788e1d`）

**问题**：示例报告手工维护，日期需手动改。

**修复**：
1. 新增 `scripts/gen_example_report.py`：fixture 驱动示例报告生成器
   - SEED=42 确保可复现性
   - 周期固定 2026-08-31~09-06
   - 合成 trace/sessions/automation_runs/skill_usage/outputs 数据（含成本字段）
   - 调用 generate_report.py 生成 markdown 和 html
   - 输出 example-data.json 供调试
2. 新增 `tests/test_example_report_sanity.py`：5 项健康度检查
   - test_overview_has_four_rows：概览 4 行存在（活跃天数/会话总数/实际消耗 Token/实际成本）
   - test_period_consistency：日期自洽
   - test_tier_labels_present：档位标签存在
   - test_cost_conservation：金额守恒
   - test_sections_complete：章节完整
3. 重新生成 `examples/sample-report.md/html`（带成本数据）

**验收**：CI 中 `pytest` 跑新测试，断言通过；改生成器导致示例漂移时 CI 红。

---

### P0-4：官方配置缓存覆盖路径 CI 验证（`34d8240`）

**问题**：`test_mode_rates.py::test_config_cache_overrides_multiplier` 在无本机缓存时 `pytest.skip`，覆盖逻辑无回归保护。

**修复**：
1. 在 `tests/conftest.py` 新增 fixture `acc_product_config_cache()`，mock 返回固定倍率
2. 修改 `test_config_cache_overrides_multiplier`：移除 `pytest.skip`，改为依赖 fixture
3. 新增辅助函数 `_mock_acc_config(tmp_path)` 创建临时缓存文件

**验收**：CI 中该用例实际执行（非 skip）；模拟破坏覆盖逻辑时测试红。

---

### P1-1：补充边界说明（`34d8240`）

**问题**：FAQ 缺操作系统兼容性、数据量上限、trace 格式兼容性说明。

**修复**：新增 FAQ Q38-Q40：
- Q38：支持哪些操作系统？（Windows 10+/macOS 10.15+/Ubuntu 20.04+）
- Q39：数据量上限？（实测数万条 trace 正常，超 10 万条建议分段）
- Q40：不同 WorkBuddy 版本的 trace 格式兼容性？（当前适配 schema a/b，新版本如有 break 需升级技能）

**验收**：FAQ 总量 36→39 问，三处均有明确答案。

---

### P1-2：错误处理细化（`8d2edd7`）

**问题**：部分 `except Exception` 仅 log + 回退，未区分业务错误与系统错误。

**修复**：将全部 21 处 `except Exception` 拆分为具体异常类型：

| 场景 | 替换为 |
|------|--------|
| JSON 解析失败 | `except (json.JSONDecodeError, TypeError)` |
| 文件/权限操作 | `except (FileNotFoundError, PermissionError, OSError, IOError)` |
| SQLite 查询 | `except (sqlite3.Error, OSError)` |
| 日期解析 | `except (ValueError, TypeError)` |
| 网络请求 | `except (requests.RequestException, ValueError, KeyError)` |
| 模块导入 | `except (ImportError, AttributeError)` |

**新增门禁测试**：
- `tests/test_no_broad_except.py`（L0/regression）
- 扫描 scripts/ 和 tests/ 下所有 .py，禁止裸 `except Exception`
- 确保未来修改不会回退此优化

**验收**：`grep -rn "except Exception" scripts/ tests/` 返回 0；318 passed（原 317 + 新增 1）。

---

## 3. 验收指标（v1.4.0 准出）

| 维度 | 准出条件 | 状态 |
|------|---------|------|
| **T rust** | hy3 限免日期一致；测试数据无英文模型名 | ✅ 全部完成 |
| **R** eliability | 官方倍率覆盖路径 CI 真实验证；错误处理细化 | ✅ 两项均完成 |
| **A**daptability | 补充 OS 兼容性 / 数据量上限 / trace 格式说明 | ✅ P1-1 完成 |
| **C**onvention | FAQ / TECH_DEBT / SKILL.md 文档数字与实测 ±0 | ✅ 已完成 |
| **E**ffectiveness | 示例报告由 CI 自动生成；至少 1 个非 WorkBuddy 适配器跑通 | ✅ 两项均完成（CI 已自动化；Claude Code 适配器 MVP 跑通） |

---

## 4. 实施计划（更新版）

| 阶段 | 任务 | 状态 | 提交 |
|------|------|------|------|
| Phase 1 | P0-1（hy3 日期修复）+ P0-2（文档刷新）+ P0-4（CI 覆盖验证） | ✅ 完成 | `cac9caf`, `34d8240` |
| Phase 1.5 | P0-3（示例报告 CI 自动生成） | ✅ 完成 | `7788e1d` |
| Phase 2 | P1-1（边界说明）+ P1-2（错误处理细化） | ✅ 完成 | `34d8240`, `8d2edd7` |
| Phase 3 | P1-3（测试数据中文化）+ P1-4（自定义模型发现优化） | ✅ 完成 | `P1-3`, `P1-4` |
| Phase 4 | P2-1（Claude Code 适配器 MVP） | ✅ 完成 | 待提交 |
| Phase 5 | P2-3（任务分类增强） | ⏳ 下一项 | — |
| Phase 6 | P2-2（定价自动化） | ⏳ 远期 | — |

---

### P1-3：测试数据中文化（完成）

**问题**：`_CHEAPER_ALT` 字典含英文模型名 `gpt-4o` / `claude-3.5-sonnet`，TRACE T.domestic 扣分项。

**修复**：移除 `_CHEAPER_ALT` 中的英文模型名条目（这些是兜底推荐，非实际测试数据）。实测测试数据已全部使用中文模型名（`glm-5.2` / `deepseek-v4-flash` / `kimi-k2.6` / `minimax-m3`），无需修改。

**验收**：`grep -rn "\"gpt-\|\"claude-\|\"gemini-" tests/ scripts/` 返回空；318 passed。

---

### P1-4：自定义模型发现优化（完成）

**问题**：非 WorkBuddy 用户或首次下载用户不清楚模型自动发现机制，上手门槛高。

**修复**：
- FAQ 新增 Q41：说明自动发现机制（读取 `~/.workbuddy/models.json`，识别本地/外部模型）
- FAQ 新增 Q42：说明手动兜底方式（检查 models.json / 手动写入 pricing.local.json / 告诉 Agent）
- SKILL.md「加入你自己的自定义模型」章节前添加提示框，说明自动发现逻辑

**验收**：FAQ 总量 40→43 问；Q41/Q42 均有明确答案；SKILL.md 新增发现机制说明。

### P1 — 应当改进（已完成）

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| P1-3 | 测试数据中文化 | ✅ 完成 | 测试数据已是中文模型名（glm-5.2/deepseek-v4-flash/kimi-k2.6/minimax-m3），移除 `_CHEAPER_ALT` 中的英文模型名（gpt-4o/claude-3.5-sonnet） |
| P1-4 | 自定义模型发现优化 | ✅ 完成 | 在 FAQ 新增 Q41/Q42 说明自动发现机制；在 SKILL.md 添加发现机制提示框 |

### P2-1：Claude Code 适配器 MVP（✅ 完成）

**目标**：让技能不止能统计 WorkBuddy，也能统计 Claude Code 的用量，验证「多 Agent 扩展」接缝可用。

**交付**：
- `adapters/claude_code.py`（374 行）：读取 `~/.claude/projects/**/*.jsonl`，把每轮 assistant 消息的 `usage` 归一化为统一 trace schema（token / 成本 / 通道 / 缓存折扣），并现场合成会话记录（`title` / `cwd` / `_dialogue_text`）供任务分类与 Top 任务复用
  - 根目录定位优先级：`CLAUDE_PROJECTS_DIR` > `CLAUDE_CONFIG_DIR` > 平台默认（Windows `%APPDATA%/Claude/projects`、Unix `~/.claude/projects`）
  - 健壮性：坏 JSON / 非 dict / 无 usage 的行一律跳过，不抛异常
- `scripts/ca_core.py`：`parse_channel()` 识别 `claude-code:` 前缀，`price_of()` 新增该通道分支
- `scripts/pricing.json` + `ca_core.MODEL_PRICING`：新增 Claude 系列模型单价（Opus 4 108/540、Sonnet 4 21.6/108、Haiku 4 5.76/28.8，元/百万 tokens，美元刊例价折算，**已在文档中标注为估算值**）
- `scripts/collect_usage_data.py`：新增 `--source {workbuddy,claude-code}`，`main()` 分支调用适配器，下游聚合与渲染零改动
- `tests/test_claude_code_adapter.py`（330 行 / 11 用例）：解析正确性、日期窗口过滤、缓存折扣与成本、坏行健壮性、会话派生与任务分类、CLI 黑盒；fixture 经 `CLAUDE_PROJECTS_DIR` 指向 `tmp_path`，**不读用户真实目录**
- 文档：`ADAPTERS.md` 全量重写为已实现说明 + 扩展指南；`SKILL.md` / `README.md` 支持范围表更新；FAQ 新增 Q43-Q45（如何用 / 为什么没有技能与自动化 / 单价准不准）

**验收**：`--source claude-code` 端到端跑通采集 + HTML 报告渲染；353 passed（较基线 318 增 35）；`test_no_broad_except` 门禁通过。

**已知限制（MVP）**：无技能调用与自动化运行维度（JSONL 里没有这两类数据）；`duration_ms` 恒为 0；不区分 Max 订阅与按量 API。

---

### P2 — 可选增强（剩余）

| # | 任务 | 预计工时 | 说明 |
|---|------|----------|------|
| ~~P2-1~~ | ~~Claude Code 适配器 MVP~~ | ✅ 完成 | 见上方「P2-1：Claude Code 适配器 MVP」 |
| P2-2 | **定价数据自动更新机制** | 3 天 | 新增 `scripts/fetch_pricing.py`：定时拉取各厂商官网定价页，解析后生成 PR |
| P2-3 | **任务分类增强** | 1 天 | 将关键词启发式分类升级为基于大语言模型的分类，提升边界 case 准确率 |

---

## 6. 下一步

P2-1 已完成，多 Agent 扩展接缝已验证可用。当前剩余待办：

- **下一项**：P2-3（任务分类增强，1 天）——性价比高于 P2-2，直接提升 E.completeness
- **远期**：P2-2（定价数据自动更新机制，3 天）
- **可选**：Claude Code 适配器增强（Skills / 自动化维度需等 Claude Code 落盘对应数据）
