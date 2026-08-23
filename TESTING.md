# 测试工作流 / Testing Workflow

> 最后核查：2026-08-20。覆盖范围：同步状态 + 测试分层 + 推荐新增测试 + 执行顺序。
> **本版更新**：L0–L4 测试全部落地（共 8 个测试文件、231 用例，全量 pytest+Allure 通过，0 失败）；`metadata.json` 已对齐 `config.json` 至 **v1.1.2**（修复 1.1.1≠1.1.2 版本漂移，正是 L4 要抓的发布隐患）。

## 0. 三端同步现状（核查结论）

| 副本 | 当前状态 | 与本地关系 |
|------|---------|-----------|
| **本地工作树** | v1.1.2，已含 pytest+Allure 测试（L0–L4 共 8 文件、231 用例） | 基准（source of truth） |
| **GitHub** (`origin/main`) | 落后本地（未推送测试体系与 v1.1.2 版本对齐） | 本地比远程多提交 |
| **SkillHub** | 早于 v1.1.2 与测试体系 | 三者中最旧 |

**结论：三者未同步。** 需补两步：① `git push` 本地 → GitHub；② `skillhub publish` 本地 → SkillHub（重新打包才会带上 v1.1.2 与测试体系）。

**已发现的同步隐患（发布前必修）：**
1. ~~`config.json` version ≠ `metadata.json` version~~ — ✅ **已修复**：两清单统一为 `1.1.2`（由 L4 `test_publish_parity` 守护，防止复发）。
2. `config.json.update_url` 当前是**页面 URL**（`https://skillhub.cn/skills/user_c5278a31/agent-analytics-report`），不是 manifest JSON 地址 → `skillhub upgrade` 会拉取失败（config.json 注释已自警告）。
3. `pytest.ini` 体积异常（约 193KB，正常应为数行），建议排查是否被 allure 结果误写入。

## 1. 测试分层覆盖矩阵

| 层 | 模块 | 已有测试 | 缺口 | 优先级 |
|----|------|---------|------|--------|
| **L0 数据采集层** | `collect_usage_data.py` | `tests/test_channel_attribution.py`（通道归因）+ `tests/test_cost_math.py`（成本/折扣/限免/单价）+ `tests/test_calendar_period.py`（日历对齐）+ `tests/test_aggregation.py`（聚合/异常检测） | 自定义模型发现单测、其余定价边界（见 L2） | 低（L0 核心已覆盖） |
| **L1 报告生成层** | `generate_report.py` | `tests/test_report_generation.py`（`merge_glm52_family`、三格式入口、图表构造器空/退化数据、XSS 转义回归） | §3.1/§3.2 渲染逐字段对照断言、洞察/异常块渲染、格式化助手 `format_number` 等 | **持续补全（最大历史盲区，已补核心）** |
| **L2 定价/分类边界** | `pricing.json` + `parse_channel` | `tests/test_pricing_boundary.py`（channel 分支：router→None、openrouter-free→0、custom-local 命中/未命中、gateway 未知→None；delisted 历史计价；compute_cost 零/负/大数舍入；trace_cost blended 回退） | delisted vs custom_local 硬边界、SiliconFlow 前缀、同名模型走 gateway 误判（撞名兜底） | 高（最易回归，已补核心） |
| **L3 CLI / 端到端** | 两个脚本的 `main()` | `tests/test_e2e_cli.py`（黑盒：--help / 三格式生成到文件 / XSS 端到端 / 非法 --format 拦截，仅喂合成数据 JSON，不碰真实 db） | 采集→生成串联（需真实 db，当前用合成数据覆盖生成端） | 中 |
| **L4 发布一致性** | 打包/发布 | `tests/test_publish_parity.py`（metadata==config 版本；semver；交付物齐全；`.gitignore` 闸门：测试文件不被忽略、敏感/产物必须被忽略） | update_url 形如 manifest JSON 的断言（需联网拉取远端比对，留 CI） | 高（合规/升级，已补核心） |

## 2. 建议新增测试文件（接现有 marker 体系）

- `tests/test_cost_math.py` — ✅ **已落地**。`price_of` / `compute_cost` / `trace_cost` / `effective_tokens_of` / `glm52_discount_multiplier`（0.79/0.5 折扣）/ `is_timed_free`。marker: `unit`+`whitebox`。
- `tests/test_pricing_boundary.py` — ✅ **已落地**。price_of 显式 channel 分支（router→None、openrouter-free→0、custom-local 命中/未命中、gateway 未知→None）；delisted 模型历史计价非空；compute_cost 零/负/大数舍入；trace_cost 未配置/router blended 回退。marker: `unit`+`regression`。
- `tests/test_report_generation.py` — ✅ **已落地**。`merge_glm52_family` 正确性（glm-5.2 + glm-5.2-x + glm-5.2x 合并、排序位、调用/花费求和）；`generate_markdown_report` / `generate_html_report` / `generate_json_report` 在固定 fixture `data` 上跑通且含关键章节；图表构造器在**空/退化数据**下不崩；`_esc` XSS 转义回归（恶意模型名进入 HTML 报告被转义）。marker: `unit`+`whitebox`+`regression`。
- `tests/test_aggregation.py` — ✅ **已落地**。`aggregate_by_model` / `aggregate_by_exec_model`（求和与拆分）/ `_detect_daily_anomalies` / `_detect_session_anomalies` / `detect_cost_anomalies`（含全免费→`cost_all_zero`）。marker: `unit`+`whitebox`。
- `tests/test_calendar_period.py` — ✅ **已落地**。`resolve_date_range` 预设周期 day/week/month/year 日历对齐 + 绝对日期分类（day/week/month/year/custom）+ 无效日期抛错；`generate_report._calendar_period` 标签。marker: `unit`+`whitebox`。
- `tests/test_e2e_cli.py` — ✅ **已落地**。黑盒驱动 `generate_report.py` 命令行：`--help` 健康、合成数据 JSON → markdown/html/json 三格式生成到文件且非空含关键章节、恶意模型名经 CLI 生成 HTML 被转义、非法 `--format` 被 argparse 拦截（非零退出）。全程仅喂合成数据，不碰真实 db/traces。marker: `integration`+`blackbox`。
- `tests/test_publish_parity.py` — ✅ **已落地**。`config.json.version == metadata.json.version`（捕捉漂移）；两版本 semver；交付物齐全；`.gitignore` 闸门（测试文件不被忽略、敏感/产物文件必须被忽略）。marker: `metadata`+`contract`。

## 3. 推荐执行顺序（工作流）

1. **修同步隐患**（P0）：统一 `config.json`/`metadata.json` 版本号；把 `update_url` 改为真实 manifest 地址；排查 `pytest.ini` 体积。
2. **补齐 L0 剩余 unit 测试**：`test_cost_math` + `test_pricing_boundary`（边界最易回归，且与现有通道测试一脉相承）。
3. **攻 L1 报告生成层**（最高价值盲区）：先写 `merge_glm52_family` 单测（历史上多次在此回归），再写三格式入口 golden 测试 + 图表退化数据测试。
4. **L3/L4 端到端 + 发布一致性**：`test_e2e_cli` + `test_publish_parity`（防止下次发布又把 `pricing.local.json` 打进包）。
5. **回归门禁**：每次改 `pricing.json` / `merge_glm52_family` / `parse_channel` 后跑全量；发布前必跑 `test_publish_parity`。

## 4. 运行命令

```bash
# 使用托管 venv（已装 pytest + allure-pytest）
PY="C:/Users/elisa/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

# 全量 L0–L4 + 生成 Allure results
"$PY" -m pytest tests/ --alluredir=allure-results -q

# 仅某一层
"$PY" -m pytest tests/test_pricing_boundary.py -q   # L2
"$PY" -m pytest tests/test_e2e_cli.py -q            # L3
"$PY" -m pytest tests/test_publish_parity.py -q     # L4

# 仅冒烟
"$PY" -m pytest -m smoke -q

# 离线聚合 Allure results → 自包含 HTML（无需 Java；工作区脚本）
"$PY" "C:/Users/elisa/WorkBuddy/2026-08-05-02-26-14/_gen_allure_report.py"
# 产物：agent_analytics_l0_l4_allure_report.html（231/231 passed）
```

> 约定（见 `tests/conftest.py`）：marker 体系 `smoke/integration/blackbox/whitebox/metadata/contract/privacy/portability/golden/regression/unit`；Allure 缺失时 `allure` 命名空间降级为 no-op，纯 pytest 仍可用。
> Allure CLI 需 Java；本仓库附 `_gen_allure_report.py`（工作区脚本）可从 `allure-results/` 离线聚合成自包含 HTML 仪表盘，无需 Java。
