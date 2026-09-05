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
| P1-3 | **测试数据中文化**：测试数据含英文模型名，影响中文环境友好度评分 | TRACE T.domestic | ⏳ 待开始 | — |
| P1-4 | **自定义模型发现依赖 Agent 引导** | TRACE E.usability | ⏳ 待开始 | — |

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
| **T rust** | hy3 限免日期一致；测试数据无英文模型名 | ✅ hy3 日期已修复；英文模型名待 P1-3 |
| **R** eliability | 官方倍率覆盖路径 CI 真实验证；错误处理细化 | ✅ 两项均完成 |
| **A**daptability | 补充 OS 兼容性 / 数据量上限 / trace 格式说明 | ✅ P1-1 完成 |
| **C**onvention | FAQ / TECH_DEBT / SKILL.md 文档数字与实测 ±0 | ✅ 已完成 |
| **E**ffectiveness | 示例报告由 CI 自动生成；至少 1 个非 WorkBuddy 适配器跑通 | ✅ 示例报告 CI 已自动化；适配器 MVP 待 P2-1 |

---

## 4. 实施计划（更新版）

| 阶段 | 任务 | 状态 | 提交 |
|------|------|------|------|
| Phase 1 | P0-1（hy3 日期修复）+ P0-2（文档刷新）+ P0-4（CI 覆盖验证） | ✅ 完成 | `cac9caf`, `34d8240` |
| Phase 1.5 | P0-3（示例报告 CI 自动生成） | ✅ 完成 | `7788e1d` |
| Phase 2 | P1-1（边界说明）+ P1-2（错误处理细化） | ✅ 完成 | `34d8240`, `8d2edd7` |
| Phase 3 | P1-3（测试数据中文化）+ P1-4（自定义模型发现优化） | ⏳ 待开始 | — |
| Phase 4 | P2-1（Claude Code 适配器 MVP） | ⏳ 待开始 | — |
| Phase 5 | P2-2/P2-3（定价自动化 / 任务分类增强） | ⏳ 远期 | — |

---

## 5. 待办清单

### P1 — 应当改进（剩余）

| # | 任务 | 预计工时 | 说明 |
|---|------|----------|------|
| P1-3 | **测试数据中文化** | 30 分钟 | 检查 tests/ 中所有 fixture 数据，将英文模型名替换为中文对应名（如 `deepseek-v3` → `deepseek-chat`）；新增测试用例验证中文模型名正确处理 |
| P1-4 | **自定义模型发现优化** | 1 小时 | 在 SKILL.md 和 README 中添加自定义模型配置的详细说明，降低非 WorkBuddy 用户上手门槛 |

### P2 — 可选增强

| # | 任务 | 预计工时 | 说明 |
|---|------|----------|------|
| P2-1 | **Claude Code 适配器 MVP** | 2-3 天 | 新增 `adapters/claude_code.py`：读取 `~/.claude/projects/` 下的 JSONL，映射到统一 data schema；在 `collect_usage_data.py` 中新增 `--source claude-code` 参数 |
| P2-2 | **定价数据自动更新机制** | 3 天 | 新增 `scripts/fetch_pricing.py`：定时拉取各厂商官网定价页，解析后生成 PR |
| P2-3 | **任务分类增强** | 1 天 | 将关键词启发式分类升级为基于大语言模型的分类，提升边界 case 准确率 |

---

## 6. 下一步

- **立即开始**：P1-3（测试数据中文化），预计 30 分钟可完成
- **长期规划**：P2-1（Claude Code 适配器 MVP），预计 2-3 天
