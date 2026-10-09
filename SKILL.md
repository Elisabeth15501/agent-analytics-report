---
name: agent-analytics-report
slug: agent-analytics-report
version: 1.9.0
metadata: metadata.json
displayName: Agent 用量分析报告
summary: 生成 Agent 用量与成本分析报告（日/周/月/年）：Token 消耗、任务类型、技能与自动化运行一目了然，异常自动预警。支持一句话触发：生成周报 / 月报 / 年报 / 日报。支持 WorkBuddy / Claude Code / Codex CLI / 千问办公 四种数据源，以及按 WorkBuddy 布局读取目录的通用入口 dumate（--source 切换，归属未证实）。
description: |
  Agent 用量分析报告生成器（支持日/周/月/年）。从本地数据源（traces、workbuddy.db、usage-log.json、会话目录）采集 Agent 使用数据，一键生成可读、可分享的多格式报告。数据源用 --source 切换：workbuddy（默认，读 ~/.workbuddy/）/ claude-code（读 ~/.claude/projects/ 的 JSONL 会话日志）/ codex（读 ~/.codex/sessions/ 的 rollout JSONL）/ qwenwork（读千问办公 ~/.qwenworkcn/ 的转录 + 调用日志 + agents.db，**在千问办公里跑就用它**）/ dumate（按 WorkBuddy 布局读取数据目录的通用入口，支持 DUMATE_HOME 覆盖；**数据归属未证实，报告内容可能完全是 WorkBuddy 的，勿当作百度搭子用量引用**）；更多 Agent 可扩展（详见 docs/ADAPTERS.md）。

  触发方式：当用户说「生成周报 / 月报 / 年报 / 日报」「帮我出一份本周使用报告」「统计下这个月的 token 消耗」等时触发，无需手动指定参数；也可用 --period / --days / --start / --end 自定义周期与日期范围。

  报告包含：
  - Token 消耗与成本：按实际计费模型对账，跟后台账单一致（⚠️ 千问办公等服务端不回传 token 的数据源除外：该源 token 为字符估算、金额一律以 `tokens_only` 呈现，估算值不可用于对账，见数据源表）；每日趋势、缓存占比、成本货币化
  - 任务与技能：这段时间主要在干哪类活、哪些技能被反复调用
  - 自动化运行：每个任务跑了几次、成功还是失败、失败浪费了多少钱
  - 异常检测：成本 + Token 双口径，免费期高流量也不漏报；脏数据显式警告

  输出格式：Markdown（默认）/ HTML（可交互图表，浅色/深色自适应）/ JSON。

  隐私与权限（分阶段）：**采集阶段只做本机只读采集**，默认全离线；仅显式指定自有端点时才访问对应端点，其余情况零网络、不上传。**配置阶段：仅在你明确确认单价后**，可写入你自己下载副本里的 `scripts/pricing.local.json`（补自定义模型单价）；该文件**不进发布包、升级不会被覆盖**。`scripts/task_rules.json`（调任务分类权重）**是发布资产、会被升级覆盖**，改它前请自行备份。逐条联网行为见正文「隐私与联网行为（完整清单）」。

  限制说明：数据来自本机，需启用 trace 记录；成本按模型公开单价估算，以实际账单为准；免费/限免期内成本口径参考意义有限，报告会自动标注并引导查看 Token 口径。
when_to_use: |
  当用户明确要求「生成 / 出一份 Agent 用量 / 成本 / Token / 周报 / 月报 / 年报 / 日报 / 使用情况报告」时触发；或要求「统计这段时间用了多少 token / 花了多少钱 / 哪些技能被反复调用 / 自动化跑得怎么样」时触发。
  不适用：用户要的是「实时操控 / 修改 Agent 配置 / 直接调用某个模型 API」而非「基于本地已有数据出分析报告」；或要的是「通用数据分析 / 非 Agent 用量类报表」；或用户数据不在本机已支持的五种数据源之一且无导出文件（此时应建议 --data-dir / --from-json 或自写适配器，而不是硬采）。
tags:
  - workbuddy
  - usage-report
  - token
  - cost-analysis
  - analytics
---

# Agent 使用情况报告生成器（支持日/周/月/年）

> 本文件是**操作手册**。详细的数据源路径、计价表、Token/成本口径、任务分类规则、36 问 FAQ 收口到 `references/FAQ.md` 与 `docs/ADAPTERS.md`，按需查阅，不在此展开。

## 一、贯穿性硬约束（每轮必须遵守）

以下规则在**每一次**执行时都必须成立，即使上下文被压缩也不能丢：

1. **只读采集 + 离线优先**：采集阶段只读取本机数据（`~/.workbuddy` 等），默认零网络、不上传。仅当用户**显式传入** `--pricing-api` / `--task-llm-endpoint` 时才访问**用户自己的**端点；`--lookup-pricing online` 只本地拼接搜索链接、**不发请求**。绝不接受从 trace / 会话内容派生的 URL（防 SSRF）。
2. **写文件必须用户逐条确认**：只有 `scripts/pricing.local.json`（补自定义模型单价）与 `scripts/task_rules.json`（调分类权重）会被写；且**必须先回显待写入清单、等用户确认后才落盘**。`pricing.local.json` 不进发布包、升级不覆盖；`task_rules.json` 是发布资产、升级会覆盖（改前备份）。
3. **归属告警（dumate）**：`--source dumate` 按 WorkBuddy 布局读目录，**记录里没有任何字段能区分它来自百度搭子还是 WorkBuddy**（实测 `workbuddy.db` 217 会话匹配 qianfan/dumate 命中 0 行）。报告标题可写「百度搭子」，但**内容可能完全是 WorkBuddy 的**，必须带归属告警，勿当作百度搭子用量引用。
4. **估算 ≠ 账单（qwenwork 等）**：服务端不回传 token 的源（千问办公），token 为**字符估算**、金额一律 `tokens_only`，**不可用于对账或账单核对**，真实消耗以官方账单为准。
5. **成本口径分级标注**：默认 L2 估算（静态单价 × token），报告顶部横幅必须标注「不含服务端时段减免，请勿据此做预算或账单对账」；传入 `--import-official <xlsx>` 才升级为 L1 真值。
6. **报告标题统一**：标题固定为 `Workbuddy使用情况报告`，周期由报告头部「报告类型」行以日历日期标识（日报/周报/月报/年报/自定义），不随周期带后缀。

## 二、单次执行流程（5 步闭环）

**输入**：用户的自然语言请求（如「出本周报告」）或显式参数。
**输出**：报告文件（默认 Markdown）+ 3–5 条核心发现的摘要。

1. **确定时间范围**
   - 输入：用户说的周期或参数。
   - 行为：默认 `--period week`；用户可给 `--period day|week|month|year`、`--days N`、或 `--start/--end` 绝对范围（优先级：绝对 > `--days` > `--period`）。
   - 兜底：用户没说就按周（最近 7 天）；无法解析时回问用户而非猜。
2. **确定数据源**
   - 输入：用户在哪个 Agent 里跑。
   - 行为：`--source` 默认 `workbuddy`；在千问办公内跑必须 `--source qwenwork`；想显示「百度搭子」标题用 `--source dumate`（见硬约束 3）。
   - 兜底：默认源采到 0 条数据时，提示可能选错源并列出可用源。
3. **采集数据**
   - 命令：`python scripts/collect_usage_data.py --source <src> --period week --output data.json`（或实时模式：不传 data_file 直接进第 4 步）。
   - 兜底：trace 未启用 / 目录不存在 → 报错并提示开启 trace 或换 `--source`。
4. **生成报告**
   - 命令：`python scripts/generate_report.py data.json --output report.md`（或 `--format html|json`）；想实时生成可合并为 `generate_report.py --period week --output report.md`。
   - 兜底：data.json 缺字段 → 提示重跑采集。
5. **展示结果**：输出 3–5 条核心发现摘要，用 `present_files` 展示完整报告。

## 三、快速参考

### 数据源（`--source`）
| 取值 | 读哪 | 注意 |
|------|------|------|
| `workbuddy`（默认） | `~/.workbuddy/`（traces + workbuddy.db + usage-log.json + 会话目录） | 能力最全 |
| `claude-code` | `~/.claude/projects/**/*.jsonl` | 无技能/自动化维度；`CLAUDE_PROJECTS_DIR` 可覆盖 |
| `codex` | `~/.codex/sessions/.../rollout-*.jsonl` | `CODEX_HOME` 可覆盖 |
| `qwenwork` | `~/.qwenworkcn/`（转录 + runs 日志 + agents.db + outputs） | **千问办公里跑用它**；token 估算、金额 tokens_only |
| `dumate` | `~/.workbuddy/`（按 WorkBuddy 布局） | 归属未证实，见硬约束 3；`DUMATE_HOME` / `DUMATE_OUTPUTS_DIR` 可覆盖 |

### 计价模式（`--cost-mode`）
- `auto`（默认）：一个单价都没命中时自动切 `tokens_only`。
- `tokens-only`：强制隐藏金额维度。
- `priced`：无论如何都按现状出金额。

### 时间窗口
4 种预设（day/week/month/year）+ `--days N` + `--start/--end` 绝对范围；标题统一 `Workbuddy使用情况报告`，周期见报告头「报告类型」。

### 自定义模型（不碰发布版）
只写本地 `pricing.local.json`（不进发布包、升级不丢）：`custom_local` 段（名带 `custom-local:` 前缀）或 `models` 段（裸名）。流程与确认闸门见 `references/add-custom-models.md` 与 `references/on-download-inject.md`。

## 四、隐私与联网行为（完整清单）

分阶段口径（硬约束已列）：采集阶段只本机只读、默认离线；配置阶段仅确认单价后写本地 `pricing.local.json` / `task_rules.json`。

| 行为 | 触发条件 | 目标 | 说明 |
|---|---|---|---|
| 定价查价 | 显式 `--pricing-api <URL>` | 你自己的镜像端点 | 不内置默认端点；URL 只来自命令行参数，不从 trace/会话派生；价标 🌐 不计入总额 |
| 任务分类 | 显式 `--task-classifier llm` + `--task-llm-endpoint <URL>` | 你自己的 OpenAI 兼容端点 | 不内置默认端点；失败自动回退启发式 |
| 搜索链接 | `--lookup-pricing online` | **不发请求** | 纯字符串拼接 DuckDuckGo 链接，交你自点 |
| 默认 | 都不传 | 零网络 | 本机只读 + 本地渲染 |

`scripts/fetch_pricing.py` 是**独立、手动触发**的定价维护助手（发布者用，非用户采集路径）：默认只出候选与差异报告、不落盘、不抓厂商网页；数据源由你自备。

## 五、扩展与参考（按需查阅，不在此展开）
- 详细数据源路径、已知偏差、适配器接缝与验收清单：`docs/ADAPTERS.md`
- 36 问 FAQ（安装 / 生成 / 计价 / 分类 / 隐私 / 故障排查）：`references/FAQ.md`
- 自定义模型注入流程与确认闸门：`references/add-custom-models.md`、`references/on-download-inject.md`
- 报告章节结构、Token 口径（原始总量 vs 实际消耗计费等效）、成本口径（L1 真值 / L2 估算）、任务分类加权规则：随报告一起输出说明，并见代码 `scripts/` 内 docstring（如 `collect_usage_data.py` 的 `classify_task` / `collect_task_types`、`ca_core.py` 的口径常量）。

## 六、示例

**✅ 触发（应启用本技能）**
> 「帮我出一份本周的 Agent 使用情况报告，看看这周 token 花在哪了」

**❌ 不触发（不应启用本技能）**
> 「帮我把 Claude 的配置改成用 GLM-5.2 跑」——这是修改 Agent 配置 / 实时操控，不是基于本地已有数据出报告，本技能不做。
