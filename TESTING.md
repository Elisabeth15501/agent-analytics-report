# 测试工作流 / Testing Workflow

> 最后核查：2026-08-12。覆盖范围：同步状态 + 测试分层 + 推荐新增测试 + 执行顺序。

## 0. 三端同步现状（核查结论）

| 副本 | 当前状态 | 与本地关系 |
|------|---------|-----------|
| **本地工作树** | v1.1.1，已含 pytest+Allure 测试（HEAD `4e58156`） | 基准（source of truth） |
| **GitHub** (`origin/main`=`ef6bef1`) | 落后本地 **2 个提交**（未推送 `80cfc90` v1.1.1 发布 + `4e58156` 测试） | 本地比远程多 2 提交（`git rev-list --left-right --count origin/main...HEAD` → `0 2`） |
| **SkillHub** | 约在 `013d217`（回填 update_url）那次发布，早于 v1.1.1 与测试体系 | 三者中最旧 |

**结论：三者未同步。** 需补两步：① `git push` 本地 → GitHub；② `skillhub publish` 本地 → SkillHub（重新打包才会带上 v1.1.1 与测试）。

**已发现的同步隐患（发布前必修）：**
1. `config.json` version = `1.0.0` ≠ `metadata.json` version = `1.1.1`（两清单版本号不一致）。
2. `config.json.update_url` 当前是**页面 URL**（`https://skillhub.cn/skills/user_c5278a31/agent-analytics-report`），不是 manifest JSON 地址 → `skillhub upgrade` 会拉取失败（config.json 注释已自警告）。
3. `pytest.ini` 体积异常（约 193KB，正常应为数行），建议排查是否被 allure 结果误写入。

## 1. 测试分层覆盖矩阵

| 层 | 模块 | 已有测试 | 缺口 | 优先级 |
|----|------|---------|------|--------|
| **L0 数据采集层** | `collect_usage_data.py` | `tests/test_channel_attribution.py`（通道归因：同模型经不同接口拆分） | 成本数学、GLM-5.2 折扣、日期范围、异常检测、聚合分组、自定义模型发现 | 中（边界已覆盖，其余补 unit） |
| **L1 报告生成层** | `generate_report.py` | **无** | `merge_glm52_family`、三格式入口、§3.1/§3.2 渲染、图表构造器、洞察/异常渲染、格式化助手 | **高（最大盲区）** |
| **L2 定价/分类边界** | `pricing.json` + `parse_channel` | 部分（通道测试触及） | delisted vs custom_local 硬边界、SiliconFlow 前缀、timed_free、GLM-5.2 折扣率 | 高（最易回归） |
| **L3 CLI / 端到端** | 两个脚本的 `main()` | 无 | 参数解析（`--days/--period/--start/--end/--format`）、采集→生成串联 | 中 |
| **L4 发布一致性** | 打包/发布 | 无 | `pricing.local.json` 不进包、版本号两清单一致、update_url 为 manifest | 高（合规/升级） |

## 2. 建议新增测试文件（接现有 marker 体系）

- `tests/test_cost_math.py` — `price_of` / `compute_cost` / `trace_cost` / `effective_tokens_of` / `glm52_discount_multiplier`（0.79 / 0.5 折扣）/ `is_timed_free`。marker: `whitebox`+`regression`。
- `tests/test_pricing_boundary.py` — delisted vs custom_local 硬边界、SiliconFlow vendor 前缀强制 custom-local、同名模型走 gateway 误判（撞名兜底）。marker: `integration`+`regression`。
- `tests/test_report_generation.py` — `merge_glm52_family` 正确性（glm-5.2 + glm-5.2-x 合并、排序位、调用/花费求和）；`generate_markdown_report` / `generate_html_report` / `generate_json_report` 在固定 fixture `data` 上跑通且含关键章节；图表构造器在**空/退化数据**下不崩。marker: `smoke`+`golden`+`regression`。
- `tests/test_aggregation.py` — `aggregate_by_model` / `aggregate_by_exec_model` / `detect_cost_anomalies` / `resolve_date_range`（day/week/month/year + start/end 覆盖）。marker: `whitebox`。
- `tests/test_e2e_cli.py` — 用临时目录跑 `collect_usage_data.py main()` 再跑 `generate_report.py main()`，断言产物非空、关键章节存在。marker: `integration`+`blackbox`。
- `tests/test_publish_parity.py` — 模拟 skillhub 打包文件清单，断言 `pricing.local.json` / `data.json` / 测试报告* 不在其中；断言 `config.json.version == metadata.json.version`；断言 `update_url` 形如 manifest JSON。marker: `metadata`+`privacy`+`portability`。

## 3. 推荐执行顺序（工作流）

1. **修同步隐患**（P0）：统一 `config.json`/`metadata.json` 版本号；把 `update_url` 改为真实 manifest 地址；排查 `pytest.ini` 体积。
2. **补齐 L0 剩余 unit 测试**：`test_cost_math` + `test_pricing_boundary`（边界最易回归，且与现有通道测试一脉相承）。
3. **攻 L1 报告生成层**（最高价值盲区）：先写 `merge_glm52_family` 单测（历史上多次在此回归），再写三格式入口 golden 测试 + 图表退化数据测试。
4. **L3/L4 端到端 + 发布一致性**：`test_e2e_cli` + `test_publish_parity`（防止下次发布又把 `pricing.local.json` 打进包）。
5. **回归门禁**：每次改 `pricing.json` / `merge_glm52_family` / `parse_channel` 后跑全量；发布前必跑 `test_publish_parity`。

## 4. 运行命令

```bash
# 纯 pytest（无 allure 时自动降级为 no-op）
python -m pytest -q

# 仅冒烟
python -m pytest -m smoke -q

# 生成 Allure 报告
python -m pytest --alluredir=allure-results -q
allure serve allure-results
```

> 约定（见 `tests/conftest.py`）：marker 体系 `smoke/integration/blackbox/whitebox/metadata/contract/privacy/portability/golden/regression`；Allure 缺失时 `allure` 命名空间降级为 no-op，纯 pytest 仍可用。
