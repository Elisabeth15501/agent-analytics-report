---
name: agent-analytics-report
slug: agent-analytics-report
version: 1.3.0
metadata: metadata.json
displayName: Agent 用量分析报告
summary: 生成 Agent 用量与成本分析报告（日/周/月/年）：Token 消耗、任务类型、技能与自动化运行一目了然，异常自动预警。支持一句话触发：生成周报 / 月报 / 年报 / 日报。首发支持 WorkBuddy，规划兼容更多 Agent。
description: |
  Agent 用量分析报告生成器（支持日/周/月/年）。从本地数据源（traces、workbuddy.db、usage-log.json、会话目录）采集 Agent 使用数据，一键生成可读、可分享的多格式报告。首发支持 WorkBuddy，规划兼容更多 Agent。

  触发方式：当用户说「生成周报 / 月报 / 年报 / 日报」「帮我出一份本周使用报告」「统计下这个月的 token 消耗」等时触发，无需手动指定参数；也可用 --period / --days / --start / --end 自定义周期与日期范围。

  报告包含：
  - Token 消耗与成本：按实际计费模型对账，跟后台账单一致；每日趋势、缓存占比、成本货币化
  - 任务与技能：这段时间主要在干哪类活、哪些技能被反复调用
  - 自动化运行：每个任务跑了几次、成功还是失败、失败浪费了多少钱
  - 异常检测：成本 + Token 双口径，免费期高流量也不漏报；脏数据显式警告

  输出格式：Markdown（默认）/ HTML（可交互图表，浅色/深色自适应）/ JSON。

  隐私与权限：只读本机 Agent 数据，不联网、不上传任何内容。

  限制说明：数据来自本机，需启用 trace 记录；成本按模型公开单价估算，以实际账单为准；免费/限免期内成本口径参考意义有限，报告会自动标注并引导查看 Token 口径。
tags:
  - workbuddy
  - usage-report
  - token
  - cost-analysis
  - analytics
license: MIT
---

# Agent 使用情况报告生成器（支持日/周/月/年）

## 快速开始

用户请求时，按以下流程生成报告：

```
1. 运行 collect_usage_data.py 采集数据（可指定 --period day|week|month|year，或 --days N，或 --start/--end）
2. 运行 generate_report.py 生成 Markdown / HTML / JSON 报告
3. 输出报告摘要并展示完整报告
```

**示例命令**：

```bash
# 采集最近 7 天数据（默认周报窗口）
python scripts/collect_usage_data.py --period week --output data.json

# 生成月报（最近 30 天）
python scripts/generate_report.py --period month --output Agent_使用情况月报.md

# 自定义滚动天数（覆盖 --period）
python scripts/collect_usage_data.py --days 14 --output data.json

# 绝对日期范围（最优先，覆盖 --period 与 --days）
python scripts/generate_report.py --start 2026-06-01 --end 2026-06-30 --output 六月报告.md
```

### 时间窗口（可调节）

支持 4 种预设周期 + 自定义，用户可自由选择按 **天/周/月/年** 计算：

| 参数 | 取值 | 含义 | 报告类型（日历日期标识） |
|------|------|------|----------|
| `--period` | `day` | 今天（单日） | `日报 · 今日日期` |
| `--period` | `week`（默认） | 最近 7 天 | `周报 · 第 N 周（起止）` |
| `--period` | `month` | 最近 30 天 | `月报 · 年月` |
| `--period` | `year` | 最近 365 天（滚动窗口，会跨年） | `年报 · 起始年`（见下方说明） |
| `--days N` | 整数 | 自定义滚动 N 天（覆盖 `--period`） | `自定义（最近 N 天）` |
| `--start` + `--end` | `YYYY-MM-DD` | 绝对日期范围（最高优先级，覆盖以上全部） | 自动识别：单日→日报 / 整 7 天→周报 / 整月→月报 / 1/1~今年当天→年报；其余→自定义报告 |

> **报告标题统一为 `Workbuddy使用情况报告`**（Markdown / HTML / JSON 四方一致），不再随周期带「日报/周报…」后缀。周期信息改由报告头部的「**报告类型**」行以**日历日期**呈现，例如：
> - 日报 · 2026-08-03
> - 周报 · 2026 年第32周（2026-07-27 至 2026-08-03）
> - 月报 · 2026年7月
> - 年报 · 2026年（年初至今：2026-01-01 至当天）
> - 自定义报告 · 起止日期

- 三个脚本（`collect_usage_data.py` / `generate_report.py` / `analyze_tokens.py`）均支持上述参数；优先级：**绝对日期 > `--days` > `--period`**。
- 报告内所有"本期/下期"措辞仍随周期自适应（日报→当日/次日，周报→本周/下周，月报→本月/下月，年报→本年/明年）。
- 想生成「某年 / 某月的年报 / 月报」但用 `--period` 是**滚动窗口**（会跨年 / 跨月）：推荐改用 `--start/--end` 绝对日期，采集器会自动识别为对应周期类型——例如 `--start 2026-01-01 --end 2026-08-03` → `年报 · 2026年`（年初至今）；`--start 2026-07-01 --end 2026-07-31` → `月报 · 2026年7月`。整年（1/1~12/31）也同样识别为年报。
- 原有 `--days`（仅滚动天数）仍完全兼容。
- **缺失单价模型的处理**：`collect_usage_data.py` 先读发布版 `scripts/pricing.json`，再合并本地覆盖 `scripts/pricing.local.json`（`models` / `timed_free` / `custom_local` 三段，后者优先、不进发布包）。当遇到新模型时：
  - **本地（默认）**：报告 §3.3 列出缺失模型名 + 可复制的 `pricing.local.json` 补写片段，不计入成本总额；
  - **联网（`--lookup-pricing online`）**：额外生成 DuckDuckGo 搜索链接；若提供 `--pricing-api <URL>`，会尝试从你自己的定价镜像端点拉价（拉到的价一律标 🌐 网络估算价，**不计入**任何成本总额，只供补写时参考）。两份 `pricing*.json` 缺失时回退到 `collect_usage_data.py` 内置 `MODEL_PRICING` 常量，向后兼容。

## 加入你自己的自定义模型（下载后本地配置，升级不丢失）

发布版 `scripts/pricing.json` 只含 WorkBuddy 官方内置模型（11 个）+ `auto`，`custom_local` 段为空 `{}` —— **开发者不会把自己的自建/第三方模型带进发布包**。你本地使用的自定义模型（如自主接入外部API、自建开源模型、走第三方网关的模型、其它 GLM/MiniMax/Kimi/DeepSeek 变体、腾讯混元、OpenRouter 免费模型等）请加在**你自己下载的那份** `scripts/pricing.local.json` 里。

> 🔑 **关键设计**：`pricing.local.json` 是**本地覆盖文件，不进发布包**。当你 `skillhub upgrade` 升级技能时，发布版 `pricing.json` 会被整目录覆盖重写，但 `pricing.local.json` 因为不在包里、永远不会被触碰 —— 你的自定义模型单价**自动保留、无需重注入**。

**两种加模型的方式（都只写 `pricing.local.json`，不碰发布版）：**

- **① 下载后首次 · 自动发现注入**：你从 SkillHub 下载技能、第一次生成报告（或主动说"把我的自定义模型加进报告"）时，Agent 会**自动扫描你本机 WorkBuddy 用过的 `custom-local:*` 模型**，问你单价后写进本地 `pricing.local.json`。全程照 [`references/on-download-inject.md`](references/on-download-inject.md) 的 Prompt 走，你只需回答每个模型的单价即可。
- **② 以后新加模型 · 大白话告诉 Agent**：之后你在 WorkBuddy 里新加了一个自定义模型，不用再全量扫描——照 [`references/add-custom-models.md`](references/add-custom-models.md)，用大白话把"模型名 + 单价"告诉 Agent，它直接写进 `pricing.local.json`。

**写入规则（Agent 会自动遵守）：**
- 走 `custom-local:` 通道的模型（WorkBuddy 里配成 OpenAI 兼容端点，名如 `custom-local:deepseek-r1`）→ 写进 `custom_local` 段，键为**去掉 `custom-local:` 前缀、小写**的底层名。例（自建 DeepSeek，单价按你自托管/API 实际成本填，写入 `pricing.local.json`）：
  ```json
  {
    "custom_local": {
      "deepseek-r1": {"input": 2.0, "output": 8.0},
      "deepseek-v3": {"input": 4.0, "output": 16.0}
    }
  }
  ```
  采集器遇到 `custom-local:deepseek-r1` 的 trace 会自动用这里的单价计费；若 `custom_local` 没配，则回退到同名官方模型价（DeepSeek 社区版 `deepseek-r1`/`deepseek-v3` 不在官方 `models` 内、会显示「未配置」，所以务必配上）。
- 裸名自定义模型（没带 `custom-local:` 前缀，trace 里就是 `deepseek-r1`）→ 直接加到 `models` 段，写法同上 `{ "deepseek-r1": {"input": 2.0, "output": 8.0} }`。

> 这样每个用户各自的自定义模型只在自己机器上生效，报告就能把自建接口的真实花费算进去，而发布版始终保持官方模型干净。只要写在 `pricing.local.json`（而非 `pricing.json`），`skillhub upgrade` 升级技能时它就不会被覆盖、无需备份或重注入。
>
> ⚠️ **开发者发布提醒**：`pricing.local.json` 是用户本机产物，**发布前务必确认你本机没有该文件**（它不进 Git、也不在 `.gitignore` 之外的打包豁免列表里），否则会把你的自定义模型一并发布出去。本技能当前发布态为官方模型干净版。

## 报告结构

生成的报告（日/周/月/年）包含以下章节：

1. **概览统计** — 活跃天数、会话总数、使用技能、自动化任务、产出文件、实际消耗 Token 与成本概览
2. **Token 消耗可视化** — 原始总 Token 与实际消耗对比、每日趋势图、缓存占比、成本货币化分析
3. **模型使用与成本对比** — 按模型统计 **调用次数、实际消耗 Token、单价（输入/输出分开计价，元/1M）、估算实际花费与占总花费比**；高亮 **🏆 最常使用模型** 与 **💸 最贵模型**；Markdown 用纯文本横向条形图（fenced 代码块）展示花费占比、HTML 用自包含内联横向条形图，两版数据/样式一致。未配置单价的模型显示「未配置」/「—」——**用户补单价请写入本地 `scripts/pricing.local.json` 的 `models`（或 `custom_local`）节点**（该文件不进发布包、升级不丢失；参考下方「加入你自己的自定义模型」）；官方模型若缺失可一并在此覆盖。**报告会自动在 §3.3 列出**本期所有缺失单价的模型名**与可复制的 `pricing.local.json` 补写片段，一眼可见缺了谁、怎么补。**双维度**：该章节现同时提供 **3.1 按接口/通道（计费维度）** 与 **3.2 按实际执行模型（使用维度）** 两张表——前者按你配置的 API 接口/通道聚合（费用结算依据，如 `auto` 路由、`custom-local` 自建接口各自独立成行），后者按 API 实际执行的底层模型名聚合（反映你真实使用了哪些模型、各多少次，例如走 `auto` 路由实际执行 `glm-5.2` 的调用会记到 `glm-5.2`）；**免费额度版 / 收费版合并显示（`display_merge`）**：WorkBuddy 对同一模型常提供两个入口——免费额度版（trace 记 `hy4-preview` / `hy3`）与免费额度用尽后的收费版（trace 记 `hy4-preview-x` / `hy3-x`）。这两者会按 `pricing.json` 的 `display_merge` 段**合并显示为一行**（如统一显示 `hy4-preview`），避免被误读成两个模型。**注意合并只改显示分组、不碰计费**：每条 trace 仍按它自己实际执行的模型单独计价，故免费额度版用量记 ¥0、收费版按刊例价，合并行的花费就等于其中**收费版那部分**的用量费用。增删合并对只改 `pricing.json`（或本地 `pricing.local.json`）的 `display_merge` 段，不用改 Python 代码。详见 `references/FAQ.md` Q23 / Q24。
   - **档位维度（§3.4 快速 / 均衡 / 极致）**：除 `auto` 外，WorkBuddy 自动路由还有三档档位——快速（`fast-model`，积分倍率 0.21x）/ 均衡（`balanced-model`，0.65x）/ 极致（`extreme-model`，1.20x；**配置缓存规范 id 为 `deep-model`，trace 字面量为 `extreme-model`，采集器已归一为 `deep-model` 后聚合**）。报告新增 §3.4 按档位聚合**调用次数、实际消耗 Token、估算单价（输入/输出分开计价，元/1M）、估算花费与占总花费比**；高亮档位间的相对成本差异。⚠️ **档位倍率仅作分析维度、不参与 ¥ 金额计算**：WorkBuddy 只对档位做积分倍率计费，trace 从不记录档位背后实际落地的底层模型，故按档位直接算「花费」在概念上不成立；档位 ¥ 单价用「倍率锚定法」估算（按已知模型官方倍率线性外推：快速≈¥1.24/2.47、均衡≈¥6.58/23.04、极致≈¥14.80/74.10），章节内明确标注为估算值，与 §3.1（账单口径）/ §3.2（入口视图）的真实计费完全解耦、互不影响。估算倍率**优先取本机权威倍率表** `~/.workbuddy/cache/acc-product-config-v3.json`（官方 48 模型 `credits` 倍率，缺失/解析失败则安全回退 `pricing.json` 的 `mode_rates`）；调整估算单价只改 `mode_rates` 段，不用改 Python 代码；无档位数据自动省略该章节、且不落 §3.3「未配置」假阳性。详见 `references/FAQ.md` Q36 / Q37。
   - **定价库覆盖范围（`scripts/pricing.json`）**：① **12 个 WorkBuddy 官方内置模型**（来源：[WorkBuddy 官方模型列表](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model)，2026-08，全部已配公开标价）：Hy3（限时免费至 2026-09-30）/ Hy4 preview 与 hy4-preview-x（2026-08 发布，hy4-preview 限时免费至 2026-09-10）/ GLM-5.3（与 GLM-5.2 同价）/ GLM-5.3-Flash（2026-08 发布，约 GLM-5.3 的 1/10）/ GLM-5.2 / GLM-5.1 / GLM-5v-Turbo / MiniMax-M3 / MiniMax-m2.7 / Kimi-K3 / Kimi-K2.7-Code / Kimi-K2.6 / Deepseek-V4-Flash / Deepseek-V4-Pro；外加 `auto` 智能路由别名（无单一单价，记为 `null`，报告内走 router_avg 均价估算）。② 你在 WorkBuddy 里**自行接入的第三方模型**（其它 GLM/MiniMax/Kimi/DeepSeek 变体、腾讯混元、OpenRouter 免费模型等）与 `custom-local:*` 自建接口**均不在此发布包内**——它们走 `pricing.json` 的 `custom_local` 段（**发布版为空 `{}`，需你下载后把单价写入自己的 `pricing.local.json`**，详见下方「加入你自己的自定义模型」）或回退到同名模型单价。报告会自动在 §3.3 列出所有缺失单价的模型名与可复制的补写片段。③ **已下架官方模型（`delisted` 段）**：WorkBuddy 会持续调整可选模型——历史上提供过、现已下架的官方模型（如早期免费 Flash、曾上架的社区/第三方模型）应放入 `pricing.json` 的 `delisted` 段而非删除。它们是官方模型，可随发布版走；放入后历史 trace 中这些模型的调用会被正常统计与计价（单价未知则标「已下架·未知」、不计成本），并在报告里以 🗄️ 标注，不会出现在 §3.3「缺失单价」清单。这样周报既能覆盖「当前可选模型」，也能覆盖「以前被调用过的模型」，符合「Agent 使用情况周报应包含任何曾发生过的调用」的目标。**核心原则**：发布版 `pricing.json` = 当前官方内置 `models` + 已下架官方 `delisted`；用户自己的第三方/自建模型只进 `pricing.local.json`（不进发布包）。报告模型清单由实际 trace 数据驱动，不在上述任一白名单里做硬过滤——因此即使某模型已从 WorkBuddy 下架或从未在定价库登记，只要历史 trace 里有调用，就会出现在报告里（下架官方模型标 🗄️，其它未登记模型标「未配置」并列入 §3.3）。**注意 `:free` 模型**：以 `:free` 结尾的 OpenRouter 免费模型（如 `nvidia/nemotron-3-nano-30b-a3b:free`、`poolside/laguna-xs-2.1:free`）走 `openrouter-free` 通道，自动免费（¥0），**无需在 `delisted` 登记**也会在报告里正常显示为「免费」——它们若是你自己的 OpenRouter 免费模型，本身就不在发布包里、也不影响公开版；若你确认某 `:free` 变体是 WorkBuddy 曾官方提供、现已下架的，把它的**完整模型名**（含 `:free` 后缀）写进 `delisted` 即可。
4. **成本深度分析（每会话 / 异常 / 省钱）** — 覆盖行业调研头号诉求「每任务/每会话成本」；含 **4.1 每会话成本 Top 10**（按会话聚合 effective_cost，含任务类型、实际消耗、调用次数、主要模型）、**4.2 每会话成本分布**（按单会话成本分桶：¥0–1/¥1–5/¥5–20/¥20–50/¥50+）、**4.3 异常/飙升检测（成本 + Token 双口径）**——同时跑「💰成本口径」与「📊Token 口径」两套独立检测：成本口径仅在确有真实成本时启用；**Token 口径始终运行**，避免免费/限时免费模型拉低成本口径时漏报真实 Token 峰值（例如某日 11.43M token 环比 +315%）。免费主导期（免费 Token 占比≥80% 或总成本为 0）会在 §4 顶部插入免责声明，提示 §4.4/4.5/4.6 成本类洞察在免费期参考有限，应优先看 §4.3 Token 口径与 §4.6 缓存、**4.4 省钱杠杆自动洞察**（基于实际执行维度，对高占比付费模型给出更便宜替代与预计月省估算，如 glm-5.2→glm-4.5-air 预计月省 ¥7+）
5. **任务类型统计** — 按任务类型分类的会话数量和占比、任务类型分布图
6. **任务 Token 消耗统计** — 按任务类型聚合的 **实际消耗（计费等效）Token** 与估算成本（排名按实际消耗而非原始总量），定位真正吃 token 的任务类型；含**缓存占比**列解释"为什么原始数字大但实际便宜"；占比图：**Markdown 报告用纯文本横向条形图**（fenced 代码块，主题安全、窄项不糊、永不重叠），**HTML 报告仍用自包含内联 SVG 环形图**（currentColor + CSS 变量，双主题兼容）；并列出 **Top 10 实际消耗最高的任务对话框**（含自动化任务会话，按会话实际消耗排序，含任务类型、原始总量、缓存占比与估算成本）
7. **技能使用统计** — 技能使用次数、最近使用时间
8. **自动化任务运行情况** — 各自动化任务状态（执行中/已暂停/已删除）、运行次数、成功/失败、最近一次结果、最近一次运行日期（无论任务是否已停止均显示）
9. **产出物清单** — 产出文件列表
10. **核心洞察与建议** — 高峰日分析、任务类型洞察、缓存效率评估（钱优先视角）
11. **下期展望** — 基于 Token 消耗趋势与自动化运行概况动态生成的建议（如检查 token 额度、处理待审核任务）。**注意**：「下期展望」中的自动化建议仅统计「执行中（ACTIVE）」的自动化；已停止（已暂停/已删除）的自动化即便有 PENDING_REVIEW 运行也**不计入待审核**，亦不出现在自动化建议中。

## 数据源说明

| 数据源 | 路径 | 内容 |
|--------|------|------|
| Traces | `~/.workbuddy/traces/` | Token 消耗、模型信息、会话时长 |
| SQLite DB | `~/.workbuddy/workbuddy.db` | 会话元数据、自动化运行、信用消耗 |
| Usage Log | `~/.workbuddy/usage-log.json` | 技能使用记录、活跃天数 |
| 会话目录 | `~/WorkBuddy/` | 产出文件、记忆日志 |

## Token 口径：原始总量 vs 实际消耗（计费等效）

报告刻意区分两种 token 口径，避免"看起来吃了很多、其实很便宜"的误读：

- **原始总 Token（含缓存命中）** = `totalTokens` = 输入 + 输出。其中 **`cached_tokens`（缓存命中）是输入 token 的子集**：它被模型读取并处理过（所以"算消耗了"），但命中了**提示词前缀缓存**，按行业惯例约 **1/10 的低价**计费，而非全新输入的全价。
- **实际消耗 Token（计费等效）** = `原始总量 − 缓存命中 ×(1−折扣)`，折扣 `CACHE_DISCOUNT=0.1`（见 `collect_usage_data.py`）。即缓存命中只按 10% 计入实际消耗。这是报告所有排名（任务类型、Top 任务、每日趋势、成本）的**主口径**。
- **缓存占比** = `cached_tokens / input_tokens`，越高说明该任务/会话大量复用同一段上下文（例如连续多轮生成、长 system prompt 反复重发），数字看起来大但实际成本很低——这正是"Agent 吃 token 的原因"的最佳解释。

> 数据校验：`totalTokens ≈ input + output`（cached 是 input 子集）在 150+ 条 trace 中稳定成立（仅个别四舍五入误差），故上述折算可靠。

字段映射（`traces` / `summary` / `task_token_stats` / `top_tasks` 均含）：`total_tokens`（原始）、`effective_tokens`（实际消耗）、`cached_tokens`、`effective_cost`（实际成本）、`cache_rate`（总体缓存占比）。

## 任务类型分类规则

任务类型判定分两步（见 `collect_usage_data.py` 的 `classify_task` / `collect_task_types`）：

1. **后台自动化会话优先**：若会话标记为后台自动化（`is_background_automation`），直接归为「自动化配置」，不依赖关键词；
2. **其余按「对话内容 + 生成物（含已删除）+ 会话标题」综合匹配**关键词：候选文本 = 对话内容 + 生成物指纹 + 标题，拼接后送入 `classify_task` 匹配。各部分来源：
   - **对话内容**：从 `~/.workbuddy/projects/<cwd哈希>/<sessionId>.jsonl` 读取 transcript，**逐段剥离 `<system-reminder>` 系统注入块**后匹配（必须在计入长度前剥离，否则首条 user 消息携带的巨型 system-reminder 会让截断提前触发、只剩裸 user_query）。抽取范围：
     - `type=message`：user/assistant 的 `content` 文本片段列表（`input_text`/`output_text`）；
     - `type=reasoning`：助手思考文本在 **`rawContent`** 字段（其 `content` 通常为空列表），需优先取 `rawContent`。
   - **生成物指纹（含已删除）**：见 `get_session_artifact_fingerprint`——即便产物文件已物理删除，以下记录在 transcript 里仍保留：
     - `function_call` 记录里的 **`ImageGen` / `VideoGen`** 工具调用（确定性内容生成证据）；
     - `file-history-snapshot.trackedFileBackups` 的**键名**（编辑器曾跟踪的文件名，删除后键名仍保留，是"已删除生成物"的最佳来源，含 `.png/.mp4/.html` 等）。
     指纹以 `[artifacts] imagegen/videogen 媒体文件名...` 形式注入候选文本，供「内容生成」类型强判定（媒体文件名规则必须紧跟 `[artifacts]` 标记，避免"领券截图/二维码"等非创作媒体误判）。
   - 对话内容与生成物指纹均缺失时，回退到会话标题。

匹配规则按**顺序命中即返回**，关键顺序约定：

- **技能开发 / 代码开发 排在 自动化配置 之前**——后者的裸词 `automation`/`自动化` 极易在代码/agent 会话里 incidental 命中，会抢走真正的开发类任务；故 自动化配置 只保留高置信短语（定时任务、领券、提醒、每日任务/提醒、配置/设置自动化、`schedul`）。
- **内容生成 排在 代码开发 之前**：除对话内容里的短剧/剧本/分镜/小说/文案等创作信号外，更可靠的依据是**生成物指纹**（`imagegen`/`videogen` 工具调用、`[artifacts]` 媒体文件名）——即便对话 jsonl 已归档、或产物已删除，仍可据生成物判为内容生成。例：创建AI短剧生成团队（连续多轮中长文本/分镜/角色/视频生成）归内容生成。
- **代码开发 含 agent 团队搭建语义**（`agent.*team`、`多智能体`、`创建.*团队`、`团队搭建`），但仅当无内容创作/生成物信号时（如搭建开发团队协作规范）才归代码开发。
- 研究学习 / Bug修复 的通用词（学习/研究/调试）排在靠后，避免被 python 等通用词抢先。

当前规则（顺序敏感，命中即返回）：

| 顺序 | 任务类型 | 匹配关键词（示例） |
|------|----------|-----------|
| 1 | 技能安装 | skillhub install、安装 skill、技能安装 |
| 2 | 技能开发 | SKILL.md、技能创建/开发、打包 skill |
| 3 | 报告生成 | 周报、weekly report、AI report |
| 4 | Bug修复 | bug、修复、fix、误报、debug、调试、错误、速率限制 |
| 5 | 内容生成 | 短剧、剧本、分镜、小说、文案、故事/剧情、台词/旁白、文生图/文生视频/图生视频、AI生成图、图像生成、长文本/中长文本、连续生成、content generat、创作（创作型任务，token 主要消耗在内容创作而非写代码；例："创建AI短剧生成团队"因连续多轮中长文本/分镜/角色生成归此类） |
| 6 | 代码开发 | 编程、python、练习、编写/实现/新增、github、上传、添加支持、多格式、写代码、代码实现、agent团队/多智能体/创建团队 |
| 7 | 自动化配置 | 自动化任务、配置/设置自动化、定时任务、schedul、领券、提醒、每日任务/提醒 |
| 8 | 环境搭建 | SkillHub、环境搭建、配置python、install cli、环境变量 |
| 9 | 研究学习 | 第一性原理、学习、研究、教程、调研、趋势、对比、了解、用途、主线 |
| 10 | 代码分析 | hermes、解构、分析项目、代码结构、健康检查、审查、解读、诊断 |
| 11 | 文档编写 | 指南、guide、文档、规范、流程、交付 |

> 注：分类为关键词启发式，可能误分类；命不中任何规则则归为「其他」。
> **内容生成 vs 代码开发 优先级**：内容生成（#5）排在代码开发（#6）之前，避免"短剧/剧本/分镜"类创作任务被 `创建.*团队` 误判为代码开发；但内容生成规则已收紧为强创作领域词（去掉 `生成.*图片`/`生成.*内容`/`角色设定` 等会在二维码工具、开发规范、配置类标题里 incidental 命中的泛词），确保纯开发/配置/练习会话仍归代码开发或原类型。**Token 关联**：`aggregate_task_token_stats` 按 `traces.session_id → sessions.id → task_type` 聚合；`main()` 会补全会话（窗口内有 trace 但创建于窗口外的会话也纳入），确保 token 全部关联到任务类型、不出现「未关联」占位行（除非确有孤儿 trace）。

## 脚本说明

### scripts/collect_usage_data.py

主数据采集脚本，从所有数据源聚合数据。

```bash
python scripts/collect_usage_data.py --period week --output data.json
```

**输出字段**：
- `traces` — Token 消耗明细
- `sessions` — 会话列表
- `automation_runs` — 自动化运行记录
- `session_credits` — 信用消耗
- `skill_usage` — 技能使用统计
- `outputs` — 产出文件列表
- `daily_tokens` — 每日 Token 聚合
- `summary` — 汇总统计

### scripts/analyze_tokens.py

Token 消耗专项分析，生成详细的可视化报告。

```bash
python scripts/analyze_tokens.py data.json --output token_report.md
```

**分析维度**：
- 按日统计（总 Token、输入/输出/缓存）
- 按模型统计
- 按任务类型统计
- ASCII 趋势图

### scripts/generate_report.py

完整报告生成器（日/周/月/年），整合所有模块，支持多种输出格式。

```bash
# 生成 Markdown 报告（默认）
python scripts/generate_report.py data.json --output report.md

# 生成 HTML 报告
python scripts/generate_report.py data.json --output report.html --format html

# 生成 JSON 报告
python scripts/generate_report.py data.json --output report.json --format json

# 实时采集并生成报告（不传 data_file 即触发实时采集，--period/--days/--start/--end 指定范围）
python scripts/generate_report.py --period week --output report.md
```

**支持的输出格式**：
- `markdown` - Markdown 格式（默认），适合文档阅读
- `html` - HTML 格式，适合网页展示，包含样式和交互
- `json` - JSON 格式，适合程序处理和数据分析

## 测试与质量保障

本技能附带一套 **pytest + Allure 分层回归测试**（L0 数据采集 / L1 报告生成 / L2 定价边界 / L3 CLI 端到端 / L4 发布一致性，共 10 个测试文件、306 用例全绿），全部使用合成 fixture 数据，**不含任何真实用量/个人信息**，可安全公开用于作品集展示。运行方式与 Allure 报告渲染见 [README.md](README.md) 的「测试」章节。

几个关键的回归守护点：
- `test_publish_parity.py` 校验 `config.json` 与 `metadata.json` 版本号一致，防止发布版本漂移；
- `test_report_generation.py` 含 XSS 回归用例，确保恶意模型名在 HTML 报告中被强制转义；
- `test_e2e_cli.py` 黑盒验证报告生成 CLI 端到端可用（仅喂合成数据，绝不触碰真实 `workbuddy.db`）；
- `test_pricing_boundary.py` 守卫计价边界（通道分支、已下架模型、零/负/超大值、blended 回退精度）。

## 执行流程

当用户请求周报时：

1. **确认时间范围**
   - 默认：最近 7 天（`--period week`）
   - 用户可指定周期：`--period day|week|month|year`；或自定义 `--days N`；或绝对范围 `--start/--end`

2. **采集数据**
   - 运行 `collect_usage_data.py`
   - 输出 JSON 数据文件

3. **生成报告**
   - 运行 `generate_report.py`
   - 根据需要选择输出格式（markdown/html/json）
   - 输出相应格式的报告文件（标题统一为 `Workbuddy使用情况报告`，周期由报告头部「报告类型」行以日历日期标识）

4. **展示结果**
   - 输出报告摘要（3-5 条核心发现）
   - 使用 present_files 展示完整报告

## 示例输出

```markdown
# Workbuddy使用情况报告   # 标题固定，不随周期变后缀；周期见下方「报告类型」

> **报告周期**：2026-07-27 至 2026-08-03
> **报告类型**：周报 · 2026 年第32周（2026-07-27 至 2026-08-03）
> **生成时间**：2026-08-03 01:26

## 一、概览统计

| 指标 | 数值 |
|------|------|
| 活跃天数 | 7 天 |
| 会话总数 | 18 个 |
| 总 Token 消耗 | 14.91M |

## 二、Token 消耗可视化

### 每日趋势

```
2026-07-15 |████████ 4.47M
2026-07-16 |████████████ 6.61M
...
```

## 三、任务类型统计

| 任务类型 | 会话数 | 占比 |
|----------|--------|------|
| 技能安装 | 17 | 35.4% |
| 技能开发 | 7 | 14.6% |
...
```

## 注意事项

- 数据仅来自本地 WorkBuddy，不涉及云端数据
- Token 数据来自 traces 目录，需确保 trace 功能已启用
- 任务类型分类基于关键词匹配，可能存在误分类
- 自动化运行数据来自 workbuddy.db，仅包含本地创建的任务
- HTML 格式报告包含样式美化，适合在浏览器中查看；报告采用 CSS 变量 + `data-theme` 属性，**支持手动切换浅色/深色/系统三态**（页头按钮，偏好持久化到 localStorage，刷新不丢失，无闪烁）；未手动选择时通过 `prefers-color-scheme: dark` 媒体查询**自动跟随系统深色模式**。背景/文字/表格/卡片/图表对比度均按主题切换。
- Markdown 格式报告的图表：**第 5 章任务类型占比** 与 **4.2 每会话成本分布** 均用 **fenced ```` ``` ```` 纯文本横向条形图**（与 3.1/3.2 模型条形图同风格），主题安全（文字取查看器代码块前景色，切换浅/深外观绝不消失）、每类一行永不重叠、窄项只是短条不糊。早期尝试过内联 SVG 环形图（被 .md 预览器剥离不显示）与 mermaid 饼图（窄扇区标签糊、强制 theme 在深色下不可靠），均弃用。**HTML 报告** 对应位置保留彩色 SVG 环形图（currentColor + CSS 变量、标签在侧边图例，已满足双主题要求；`build_donut_chart` 通过 `value_key`/`unit`/`center_label` 参数同时服务「任务类型 Token 占比」与「每会话成本分布（按会话数）」）。两版数据/标题/颜色语义一致，仅可视化类型因 Markdown 不能渲染内联 SVG 而不同（此为必要差异）。两版表格（如五、任务类型统计的「会话数」列）保持纯数字、样式一致。
- JSON 格式报告结构化数据，便于程序处理和集成
- 成本货币化按**模型单价（输入/输出分别计价，元/1M）**计算，单价表见 `collect_usage_data.py` 的 `MODEL_PRICING`。键名为本机真实模型名（已联网查证 2026-07-29 填入）：
  - `hy3` 腾讯混元官方 RMB：输入 1 / 输出 4；`deepseek-v4-pro` DeepSeek 官方永久价 RMB：输入 3 / 输出 6；
  - 智谱 GLM 系列（**bigmodel.cn 国内官方 RMB 价**，用户接口走 bigmodel.cn，非 Z.ai 美元折算）：`glm-5.3` 8/28（与 glm-5.2 同价，2026-08 发布，思考模式强制开启）、`glm-5.2` 8/28、`glm-5` 6/22、`glm-4.6v` 2/6、`glm-4.5-air` 1.2/8、`glm-4.7-flash` 免费（0/0）、`glm-5.2-x` 暂按 glm-5.2（均为 agent 长上下文档代表值，短上下文更低）；
  - 免费 `:free` 模型已标 0；
  - `auto` 为**智能路由别名**（执行时自动调配最适合模型，类似 openrouter/free），无单一单价——代码自动取「所有计费模型（单价>0）的均价」做代表性估算，报告中以 ℹ️ 注明「估算值」；若想精确，可在 `MODEL_PRICING` 给 `auto` 直接填 `{"input":..,"output":..}` 覆盖。
  - 未配置 / 未知模型显示「未配置」且不计入花费占比。以上为公开标价估算，请以 WorkBuddy 实际账单为准。
- **费用计算按「模型实际走的 API 接口（通道）」决定**（2026-07-29 改造）：模型标识符字符串本身编码了接口位置，由 `parse_channel()` 解析为四种通道——`gateway`（裸名，WorkBuddy 默认网关，GLM→bigmodel.cn / hy3→腾讯 / deepseek→DeepSeek）、`openrouter-free`（`org/model:free`，价 0）、`custom-local`（用户自建本地接口，前缀 `custom-local:`）、`router`（`auto`）。通道真相源是 `workbuddy.db` 的 `sessions.model`（带前缀），采集器按 `session_id` 关联 trace 并打上通道，因此 `custom-local:glm-4.6v` 与裸 `glm-4.6v` 会被**分作两行、分别计价**（修复了过去混为一价的漏洞）。`custom-local` 通道默认对齐同名模型在默认网关的单价（见 `CUSTOM_LOCAL_PRICING`，留空即沿用网关价）；若你的自建接口走别的账单且单价不同，在 `CUSTOM_LOCAL_PRICING` 按底层模型名填写即可覆盖。报告「模型使用与成本对比」表格末尾附免责备注：「以上计算只供参考，如果是外部自建接口（custom-local），请往接口相关网站查看账单」。

## 常见问题（FAQ）

> 完整版（36 问，覆盖安装 / 生成 / 计价 / 自定义模型 / 标记与合并 / 档位维度 / 数据源隐私 / 分类异常 / 故障排查）见 **[references/FAQ.md](references/FAQ.md)**，单篇自足。下方只保留最高频的几条。

**Q：报告里的花费和 WorkBuddy 后台对不上？**
A：报告按「实际计费模型」（exec_model / 接口通道）聚合，与后台口径一致；若你的模型单价未配置或走了 `custom-local` 自建接口，报告按公开标价估算，请以接口方账单为准。

**Q：我用了自己的自定义模型（如自建 DeepSeek），报告里显示「未配置」怎么办？**
A：发布版只带官方模型，你的自建模型需加在本地 `scripts/pricing.local.json`（不进发布包、升级不丢失）的 `custom_local` 段（模型名带 `custom-local:` 前缀时）或 `models` 段（裸名时）。填好单价后重跑即可正常计费，详见上方「加入你自己的自定义模型」。嫌麻烦就直接把模型名+单价贴给 Agent，让它照 `references/add-custom-models.md` 模板写进去。

**Q：免费期为什么花费显示很低甚至为 0？**
A：`hy3` 等限免入口和 `:free` 免费模型拉低了成本口径。报告会自动标注免费期，此时请优先看 §4.3 的 **Token 口径**（始终运行）和缓存占比，别只看钱。

**Q：报告里出现「幽灵调用 / 未解析调用」警告是什么？**
A：早期 WorkBuddy trace 可能同时缺 sessionId 与 modelInfo（被兜底记为 `default`、token 全 0），无法归属到任何模型/会话，报告会显式警告并单独统计，不影响可计费数据。

**Q：删除的对话还会出现在报告里吗？**
A：已删除对话消耗的 token/成本仍会计入总量（避免低估真实开销），但归属到「其他/未关联」，不会按会话明细展示。

**Q：数据会被上传或联网吗？**
A：不会。全部从本机 WorkBuddy 数据源读取并本地渲染，不调用任何外部 API。

**Q：支持其他 Agent 吗？**
A：首发支持 WorkBuddy；技能名与元数据已按跨 Agent 方向规划，后续版本将逐步兼容更多 Agent 的数据源。