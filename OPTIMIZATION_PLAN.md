# agent-analytics-report · 优化方案（v1.3.0 → v1.4.0）

> 整理时间：2026-09-04 ｜ 方法：SkillHub TRACE 评测报告 + 本地代码审计

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

> 评测版本标注为「9 文件 / 284 用例」，与当前 11 文件 / 312 用例存在差异——文档数字需同步刷新。

---

## 1. 问题清单（按优先级排序）

### P0 — 必须修复（影响准确性 / 正确性）

| # | 问题 | 来源 | 影响 |
|---|------|------|------|
| P0-1 | **hy3 限免日期不一致**：`collect_usage_data.py` L57 写 2026-08-31，`pricing.json` L77 写 2026-09-30 | TRACE E.accuracy 扣 0.5 | 限免期内实际花费可能算错 |
| P0-2 | **文档数字陈旧**：FAQ Q33 写「10 文件 / 306 用例」，TECH_DEBT.md 仍为 v1.1.2 / 243 用例 | 自检 | 对外信息失真 |
| P0-3 | **示例报告非自动生成**：手工维护，刚手动改过两次日期 | 自检 | 未来改生成器随时可能让示例漂移 |
| P0-4 | **官方倍率覆盖路径 CI skip**：`test_config_cache_overrides_multiplier` 依赖本机缓存，CI 干净检出时跳过 | 自检 | 覆盖逻辑无回归保护 |

### P1 — 应当改进（影响可靠性 / 易用性）

| # | 问题 | 来源 | 影响 |
|---|------|------|------|
| P1-1 | **多 Agent 适配器仅停留在接口定义**：ADAPTERS.md 有规划但 Trae/千问办公均未实现 | TRACE R.func / A.boundary | SKILL.md 写「规划兼容更多 Agent」略显夸大 |
| P1-2 | **缺少操作系统兼容性说明** | TRACE A.boundary | 用户不知是否支持 macOS/Linux |
| P1-3 | **缺少数据量上限说明** | TRACE A.boundary | 大规模数据场景下行为未定义 |
| P1-4 | **自定义模型发现依赖 Agent 引导** | TRACE E.usability | 非 WorkBuddy 用户上手门槛高 |
| P1-5 | **错误处理可细化**：部分 `except Exception` 仅 log 回退，未区分业务错误与系统错误 | TRACE R.errorHandling | 故障排查困难 |

### P2 — 可选增强

| # | 问题 | 来源 | 影响 |
|---|------|------|------|
| P2-1 | **无自动重试机制**：数据采集失败仅跳过，不重试 | TRACE R.stability | 临时网络/IO 错误可能丢数据 |
| P2-2 | **测试数据含英文模型名** | TRACE T.domestic | 影响中文环境友好度评分 |
| P2-3 | **Kimi K3 价格引用媒体报道** | TRACE 总体 | 非一手官方来源，价格可能不准 |
| P2-4 | **任务分类为关键词启发式** | TRACE E.completeness / E.creativity | 边界 case 可能误判 |

---

## 2. 优化方案（按优先级）

### P0 — 正确性 / 门禁

#### #1 修复 hy3 限免日期不一致（预计 15 分钟）

**问题**：`collect_usage_data.py` L57 写 `timed_free["hy3"] = "2026-08-31"`，`pricing.json` L77 写 `"hy3": "2026-09-30"`。两个值不一致。

**修复**：以 `pricing.json` 为准（权威定价源），更新 `collect_usage_data.py` 中的硬编码值。

**验收**：`grep -n "2026-08-31\|2026-09-30" scripts/*.py` 确认无冲突；`pytest -q` 全绿。

#### #2 刷新文档数字（预计 10 分钟）

**问题**：FAQ Q33 写「10 文件 / 306 用例」，实际 11 文件 / 312 用例；TECH_DEBT.md 仍为 v1.1.2 / 243 用例。

**修复**：
- FAQ Q33：更新为「11 个测试文件、312 用例」
- TECH_DEBT.md 标题行：更新为 v1.3.0 / 312 用例

**验收**：三处数字与实测值 ±0 一致。

#### #3 示例报告改为 CI 自动生成（预计半天）

**问题**：示例报告手工维护，日期需手动改。

**修复**：
1. 新增 `scripts/gen_example_report.py`：用固定 seed 生成示例数据 → 调用生成器 → 输出到 `examples/sample-report.*`
2. 在 `tests/test_example_report_sanity.py` 中增加关键行断言：
   - 概览 4 行存在（活跃天数 / 会话总数 / 总 Token / 实际花费）
   - §3.4 包含快速 / 均衡 / 极致三档
   - §3.1 各模型花费合计 = 概览总花费（金额守恒）
3. 新增 `examples/.gitkeep` + 提交示例报告

**验收**：
- CI 中 `pytest` 跑新测试，断言通过
- 改生成器导致示例漂移时 CI 红

#### #4 让官方配置缓存覆盖路径进入 CI 验证（预计 1 小时）

**问题**：`test_mode_rates.py::test_config_cache_overrides_multiplier` 在无本机缓存时 `pytest.skip`，覆盖逻辑无回归保护。

**修复**：
1. 在 `tests/conftest.py` 新增 fixture `acc_product_config_cache()`，mock 返回固定倍率（fast=0.21 / balanced=0.65 / extreme=1.20）
2. 修改 `test_config_cache_overrides_multiplier`：移除 `pytest.skip`，改为依赖 fixture
3. 新增辅助函数 `_mock_acc_config(tmp_path)` 创建临时缓存文件

**验收**：
- CI 中该用例实际执行（非 skip）
- 模拟破坏覆盖逻辑（改 `mode_rates` 倍率）时测试红

---

### P1 — 可维护性 / 真实性

#### #5 补充边界说明（预计 30 分钟）

**问题**：缺操作系统兼容性、数据量上限、trace 格式兼容性说明。

**修复**：
- FAQ 新增 Q38-Q40：
  - Q38：支持哪些操作系统？（Windows 10+/macOS 10.15+/Ubuntu 20.04+）
  - Q39：数据量上限？（实测数万条 trace 正常，超 10 万条建议分段）
  - Q40：不同 WorkBuddy 版本的 trace 格式兼容性？（当前适配 schema a/b，新版本如有 break 需升级技能）

**验收**：FAQ 新增 3 问，三处均有明确答案。

#### #6 错误处理细化（预计 1 小时）

**问题**：部分 `except Exception` 仅 log + 回退，未区分业务错误与系统错误。

**修复**：
- 将 `except Exception` 拆分为：
  - `except (json.JSONDecodeError, KeyError, ValueError)`：数据格式错误
  - `except (FileNotFoundError, PermissionError)`：IO 错误
  - `except Exception`：兜底（保留，但 log 级别提升）
- 在报告 warning 中区分错误类型（如「数据解析失败」vs「文件不存在」）

**验收**：`grep -c "except Exception" scripts/*.py` 减少 50%+；新增异常场景测试用例。

#### #7 测试数据中文化（预计 30 分钟）

**问题**：测试数据含英文模型名，影响中文环境友好度评分。

**修复**：
- 检查 `tests/` 中所有 fixture 数据，将英文模型名替换为中文对应名（如 `glm-5.2` 保留，`deepseek-v3` → `deepseek-chat` 等）
- 新增测试用例验证中文模型名正确处理

**验收**：`grep -rn "deepseek-v[0-9]\|gpt-[0-9]" tests/` 确认无英文模型名（官方保留名除外）。

---

### P2 — 产品 / 跨 Agent

#### #8 跨 Agent 兼容 MVP（预计 2-3 天）

**问题**：SKILL.md 写「规划兼容更多 Agent」但无实际适配器。

**修复**：
1. 先支持 Claude Code 的 JSONL session 日志（最常见替代数据源）
2. 新增 `adapters/claude_code.py`：读取 `~/.claude/projects/` 下的 JSONL，映射到统一 data schema
3. 在 `collect_usage_data.py` 中新增 `--source claude-code` 参数

**验收**：端到端跑通一份 Claude Code 数据源报告。

#### #9 定价数据自动化更新机制（预计 3 天）

**问题**：部分模型价格引用媒体报道，非一手来源。

**修复**：
1. 新增 `scripts/fetch_pricing.py`：定时拉取各厂商官网定价页，解析后生成 PR
2. 在 CI 中每月跑一次，对比现有 `pricing.json`，有变化则生成更新建议
3. 人工 review 后合并

**验收**：`pricing.json` 中每条模型价格都有官方来源 URL；超过 90 天未更新的标 ⚠️。

---

## 3. 验收指标（v1.4.0 准出）

| 维度 | 准出条件 |
|------|---------|
| **T rust** | hy3 限免日期一致；测试数据无英文模型名 |
| **R** eliability | 官方倍率覆盖路径 CI 真实验证；错误处理细化 |
| **A**daptability | 补充 OS 兼容性 / 数据量上限 / trace 格式说明 |
| **C**onvention | FAQ / TECH_DEBT / SKILL.md 文档数字与实测 ±0 |
| **E**ffectiveness | 示例报告由 CI 自动生成；至少 1 个非 WorkBuddy 适配器跑通 |

---

## 4. 实施计划

| 阶段 | 任务 | 预计工时 |
|------|------|----------|
| **Phase 1**（本周） | P0-1（hy3 日期修复）+ P0-2（文档刷新）+ P0-3（示例报告 CI） | 1 天 |
| **Phase 2**（下周） | P0-4（CI 覆盖验证）+ P1-1（边界说明）+ P1-2（错误处理细化） | 2 天 |
| **Phase 3**（下下周） | P1-3（测试数据中文化）+ P2-1（Claude Code 适配器 MVP） | 3 天 |
| **Phase 4**（远期） | P2-2（定价自动化）+ P2-3（任务分类增强） | 待定 |

---

## 5. 下一步

- **立即开始**：P0-1（hy3 限免日期修复）和 P0-2（文档数字刷新），预计 25 分钟可完成
- **或先确认**：你有其他优先调整的方向吗？
