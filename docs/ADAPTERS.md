# 多 Agent 数据源适配（ADAPTERS）

本技能通过 `--source` 参数切换**数据源适配器**。适配器负责把某个 Agent 的原生用量记录
归一化为统一 schema，下游的聚合、计价、报告渲染逻辑**完全复用**。

| 数据源 | `--source` 值 | 状态 | 数据位置 |
|---|---|---|---|
| WorkBuddy | `workbuddy`（默认） | ✅ 内置 | `~/.workbuddy/` |
| Claude Code | `claude-code` | ✅ 已实现 | `~/.claude/projects/**/*.jsonl` |
| OpenAI Codex CLI | `codex` | ✅ 已实现（MVP，需真实样例复核） | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` |
| Trae / 千问办公等 | — | ⬜ 未实现 | 见文末「新增一个 Agent」 |
| OpenClaw | — | ⬜ 未实现（规划中） | 见文末「新增一个 Agent」 |

**对账源**（与 `--source` 正交，仅 `workbuddy` 可用，用于把成本从估算升级为真值）：

| 对账源 | 参数 | 状态 | 数据位置 |
|---|---|---|---|
| 官方用量导出 | `--import-official <xlsx>` | ✅ 已实现（v1.6.0） | 官网下载的 `request-usage-*.xlsx` |

> ⚠️ 在适配器实现并验证前，请勿在文档 / 市场文案中声称已支持该 Agent。
> Codex 适配器已通过单元测试（11 例，覆盖解析 / 日期过滤 / 缓存折扣 / 健壮性 / CLI 端到端），
> 但由于 Codex CLI rollout schema 跨版本有差异（如 `reasoning_output_tokens` vs `reasoning_tokens`、
> `type` vs `item_type`），建议用一份你本机真实 `rollout-*.jsonl` 跑一次 `--source codex` 复核后再对外宣称支持。

---

## 一、快速使用

```bash
# Claude Code 数据源（自动定位 ~/.claude/projects）
python scripts/collect_usage_data.py --source claude-code --period week -o data.json
python scripts/generate_report.py data.json --output report.html --format html

# 自定义 projects 根目录（非默认安装位置 / 测试）
CLAUDE_PROJECTS_DIR=/path/to/projects \
  python scripts/collect_usage_data.py --source claude-code --period week -o data.json
```

`--source` 与 `--period` / `--days` / `--start` / `--end` 可自由组合，周期语义不变。

```bash
# 官方用量导出（成本真值 L1）——叠加在 workbuddy 源之上
python scripts/collect_usage_data.py --start 2026-09-12 --end 2026-09-14 \
  --import-official ~/Downloads/request-usage-2026-09-13.xlsx -o data.json
python scripts/generate_report.py data.json --output report.html --format html
```

---

## 二、官方用量导出适配器（`--import-official`）

实现文件：`adapters/official_usage.py`（v1.6.0 / F17）

### 2.1 为什么需要它

本地 trace 只有「generation 粒度」的模型调用，存在两类**静态价表在数学上无法弥补**的偏差：

| 偏差 | 表现 | 实测（30 日窗口） |
|---|---|---|
| **盲区** | 图像模型、minimax-m3 等根本不进 trace | 漏记 **¥251.74**（占 WorkBuddy 成本 8.7%） |
| **标签不分免费/收费** | `hy4-preview` 夜间免费、白天收费，trace 的 `model` 字段两者相同 | 12 次调用里 11 次实际 0 积分，静态价表全计成收费 |
| 粒度差 | 官方 1「请求」 ≈ trace 11.9「generation」 | 802 请求 vs 9222 generation |

官方导出的「积分消耗」是**服务端实际计费结果**，是唯一真值。

### 2.2 导出格式与解析规则（实测两版）

| 版本 | 列顺序 |
|---|---|
| 旧版（~2026-09-12） | RequestID / 积分消耗 / 模型 / 客户端 / 时间 |
| 新版（2026-09-13 起） | RequestID / 积分消耗 / **User Prompt** / 模型 / 客户端 / 时间 |

- 所有单元格均为**明文字符串**（含积分与时间），无共享字符串、无日期序列值。
- 图像模型「客户端」列为空；聚合时归入 `(空)`。
- 时间为**北京时间**；trace 是 UTC，对账前已归一（`ca_core.TZ = UTC+8`），不存在 ±8h 错位。

> ⚠️ **禁止按固定列位解析**。官方 09-13 加列后，按列位解析会把 `User Prompt` 当成模型名
> （已在调试脚本里踩过一次）。适配器一律**按表头名映射**（大小写 / 空格不敏感，中英文别名均可），
> 缺任一必需列（模型 / 积分 / 时间）直接报错，不做猜测。

解析用标准库 `zipfile` + `xml.etree` 直接读 xlsx（zip + XML），**不引入 openpyxl**，
保证技能在任意用户机器上零依赖可用。同时兼容共享字符串（`t="s"`）、`inlineStr` 与日期序列值。

### 2.3 产出结构

`collect_official_usage(path, start_date, end_date)` 返回：

```python
{
  "source": "workbuddy-official",
  "rows":      [{request_id, credits, model, client, ts, date, hour, is_free, prompt}],
  "by_model":  [{name, requests, credits, free_requests, paid_requests, avg_credits}],
  "by_client": [...], "by_day": [...],
  "totals":    {requests, credits, free_requests, paid_requests, models, clients},
  "meta":      {file, sheet, columns, window, parsed_rows, skipped_rows},
  "filter":    {start_date, end_date},
}
```

`credits <= 0` 判为**免费请求**（官方对时段减免 / 免费额度的实际结果）。

`reconcile_with_trace(official, model_stats, alias_map=DISPLAY_MERGE)` 产出对账结果，
报告渲染为 **§3.5 双源对账**：`missing_in_trace`（官方有 / trace 无 → 成本被低估）、
`trace_only`（本地 / 免费 / 路由，不是漏记）、`both`（给粒度倍数）。

#### 2.3.1 变体归拢（`collapse_display_aliases`，v1.6.1）

trace 侧早已按 `display_merge` 把收费版变体归并显示为基名（`hy3-x` → `hy3`、
`hy4-preview-x` → `hy4-preview`，见 `ca_core.merge_display_key`）。**官方侧必须过同一层映射**，
否则精确匹配会对不上，把变体误报成盲区。

实测（30 日 / 802 请求）：不归拢时 `hy3-x`(97 / 397.43) 与 `hy4-preview-x`(1 / 13.52) 被误报，
虚报 410.95 积分 = `missing_credits` 的 75.7%；真实盲区只有 131.62。归拢后
`missing_credits` **542.57 → 131.62**，被归并的变体写进 `variants`，报告显示为 `hy3（含 hy3-x）`。

> ⚠️ **不做模型名归一化（v1.6.1 明确边界）**：这里只做 `display_merge` 里**显式配置**的
> 「同模型收费版变体」归拢，**不剥离 `custom-local:` 之类的来源前缀**。该前缀是
> 「WorkBuddy 自建 vs 外部 API」的唯一可辨识标识，剥掉就无法判断 token 归属；
> 来源信息另由 `is_local` / `is_custom` 通道标记承载，报告 §3.2 据此拆成
> 官方/网关 · 本地模型(🏠) · 外部 API(🔧) 三节。

### 2.4 成本两级制

| 级别 | 触发 | 成本来源 | 报告顶部横幅 |
|---|---|---|---|
| **L1 真值** | 传 `--import-official` | 官方「积分」字段 | ✅ 显示官方请求数 / 积分 / 免费次数 |
| **L2 估算** | 未传（默认） | 静态 `pricing.json` × token | ⚠️ 警告「不含时段减免，请勿据此对账」 |

`meta.cost_source` 标记本期口径。渲染层以「**是否真拿到官方数据**」为准而非标记本身——
标记是 official 但数据缺失时回落 L2，避免渲染出「L1 真值（未导入）」这种自相矛盾的横幅。

### 2.5 约束

- **纯只读**：只打开用户自己下载的本地文件，不写、不传、不联网（符合 ADR-4 / ADR-6）
- 仅 `--source workbuddy` 可用（其它 Agent 没有官方积分账单可对），混用退出码 2
- 导出按报告窗口过滤；窗口外的行数记入 `meta.official_import.rows_out_of_window` 并在 stderr 提示
- **零回归**：不传 `--import-official` 时，输出与 v1.5.x 完全一致（无 `official_usage` / `reconciliation` 键）

---

## 三、Claude Code 适配器

实现文件：`adapters/claude_code.py`

### 3.1 数据定位

`resolve_claude_projects_root()` 按以下优先级定位 projects 根目录：

1. `CLAUDE_PROJECTS_DIR` —— 直接指向 `projects/` 目录（测试与自定义安装位）
2. `CLAUDE_CONFIG_DIR` —— Claude Code 官方配置根覆盖，可逗号分隔多个根，取第一个含 `projects/` 者
3. 平台默认
   - Windows：`%APPDATA%/Claude/projects`
   - macOS / Linux：`~/.claude/projects`

`_iter_jsonl_files()` 递归扫描该目录下所有 `*.jsonl`，并兼容 Claude Desktop agent 模式的
`local-agent-mode-sessions` 嵌套 `projects/` 子树。

### 3.2 解析规则

会话文件（`<sessionId>.jsonl`）逐行 JSON，按 `type` 处理：

| 行类型 | 处理 |
|---|---|
| `user` | 记录 `cwd`；首条文本作为会话 `title`（截断 60 字符）；文本并入 `_dialogue_text` 供任务分类 |
| `assistant` | **有 `message.usage` 才产一条 trace**；无 usage 的行（纯思考 / 工具回显）跳过不计费 |
| 其它（`system` 等） | 忽略 |

字段映射（`assistant.message.usage` → 统一 trace）：

```
input_tokens                    → input_tokens
output_tokens                   → output_tokens
cache_read_input_tokens         → cached_tokens（计入缓存折扣）
cache_creation_input_tokens     → _cache_creation_input_tokens（透明字段，便于后续精细计价）
input + cache_read + cache_create + output → total_tokens
```

其余派生字段：`channel = "claude-code"`、`raw_model / model_key = "claude-code:<裸模型名>"`、
`effective_tokens` / `effective_cost` 走与 WorkBuddy 一致的缓存折扣口径（缓存命中只按 10% 计入）。

健壮性：坏 JSON 行、非 dict 行、缺字段行一律跳过（不抛异常）；会话元数据 `created_at` /
`updated_at` 取文件内最小 / 最大时间戳。

### 3.3 会话派生

Claude Code 没有独立的会话表，适配器从 JSONL 现场合成会话记录，字段与 WorkBuddy
`workbuddy.db → sessions` 对齐，使下游任务分类、Top 任务、会话榜无需改动：

```python
{
  "id": <文件名 stem>, "cwd": ..., "title": <首条 user 文本 60 字截断>,
  "custom_title": "", "status": "completed",
  "created_at": <最小时间戳 ms>, "created_date": "YYYY-MM-DD",
  "updated_at": <最大时间戳 ms>,
  "mode": "claude-code", "model": "claude-code:<首个 assistant 模型>",
  "is_background_automation": False, "version": "unknown",
  "_dialogue_text": "<全部 user/assistant 文本拼接>",   # 任务分类输入
}
```

### 3.4 计价

Claude 系列模型已写入 `scripts/pricing.json`（单位：元 / 百万 tokens）。**刊例价为美元，
本表按 ~7.2 折算人民币，仅为估算值**——请以 Anthropic 官方最新定价为准，或用
`scripts/pricing.local.json` 覆盖：

| 模型 | 输入 | 输出 |
|---|---|---|
| `claude-opus-4` / `claude-opus-4-5-20251101` | 108.0 | 540.0 |
| `claude-sonnet-4` / `claude-sonnet-4-20250514` | 21.6 | 108.0 |
| `claude-haiku-4` / `claude-haiku-4-20250514` | 5.76 | 28.8 |

`parse_channel()` 已识别 `claude-code:` 前缀，`price_of()` 对该通道取裸模型名单价。
未命中单价的模型走通用的「未配置」流程（计入 token、不计成本，并在报告中提示）。

### 3.5 已知限制（MVP）

- 只统计**单次对话内**的 token 与成本；Claude Code 不落盘会话级「任务名」，Top 任务榜以会话首条提问为题
- `duration_ms` 恒为 0（JSONL 无可靠的端到端耗时字段，未做推测）
- 不区分 Max 订阅 / API 计费：一律按刊例 API 价估算，订阅用户实际边际成本更低
- 子 Agent（Task tool）若写入同目录 JSONL 则已包含，否则不计

---

## 四、数据来源与已知偏差

> 本节回答一个问题：**报告里的数字，哪些能信、哪些只能看结构。**
> 数据来自 30 日官方导出 vs 本地 trace 的实测对标（2026-08-13 ~ 2026-09-12，WorkBuddy 客户端 773 条）。

### 4.1 两个数据源各自的口径

| | 官方用量导出 | 本地 trace |
|---|---|---|
| 粒度 | **请求**（一次用户提问 = 1） | **generation**（一次请求内可多轮生成） |
| 成本 | 服务端实际计费结果（`积分`） | 静态价表 × token **估算** |
| 时区 | 北京时间 | UTC（已归一 +08:00） |
| 覆盖 | 仅云端计费调用 | 含本地模型、免费额度、路由调用 |
| 可信度 | **L1 真值** | L2 估算（结构可信、金额不可信） |

同一份数据下，trace generation 数约为官方请求数的 **11.9 倍**——这是统计粒度不同，
**不是用量暴涨**。报告概览的「调用次数」已标注 `generation 粒度`。

### 4.2 trace 盲区（官方有、trace 无 → 成本被低估）

| 模型 | 性质 | 实测漏记 |
|---|---|---|
| `hunyuan-image-alpha` | 图像生成，客户端列为空 | ¥119.91 |
| `minimax-m3` | **WorkBuddy 客户端却不被 trace 记录** | ¥120.39 |
| `hunyuan-image-v3.0-art` | 图像生成 | ¥11.44 |
| **合计** | | **¥251.74（占 8.7%）** |

> 修正说明：早期版本曾把 `deepseek-v4-pro` 也列为盲区，实为 VSCode / Codebuddy 客户端调用，
> 不属于 WorkBuddy trace 的盲区，已剔除。

### 4.3 trace 独有（官方无 → 不是漏记，别去对账）

| 类型 | 例子 | 为什么不进官方账单 |
|---|---|---|
| 本地模型 | `custom-local:*` | 本机推理，零 API 成本 |
| 限免入口 | `hy3`（至 2026-09-30） | 官方侧免费，积分 = 0 |
| 路由别名 | `balanced-model` / `fast-model` | 未解析底层模型，P1 处理 |
| 免费时段 | `hy4-preview` 夜间 23:00–08:00 | 时段减免，积分 = 0 |

### 4.4 模型名不体现「是否免费」

同一个 `hy4-preview` 标签下，免费与收费的调用**模型名完全相同**：

| 时间 | 模型 | 时段 | 积分 |
|---|---|---|---|
| 2026-09-13 23:53 | `hy4-preview` | 夜间 | 0（免费） |
| 2026-09-12 11:32 | `hy4-preview` | 白天 | 43.40（收费） |
| 2026-08-29 02:42 | `hy4-preview-x` | 夜间 | 13.52（**`-x` 变体不参与夜间免费**） |

→ **绝不能靠模型名或调用时段推断是否免费**；免费状态只能从官方导出的「积分」字段读取。
→ `hy4-preview-x` 是「免费额度用尽后的收费变体」，`pricing.json` 维持 x0.29，**不得置 0**。

### 4.5 怎么办

1. **要准确金额** → 加 `--import-official <xlsx>`，报告切 L1 真值并出 §3.5 双源对账。
2. **只做趋势分析** → 默认 L2 即可，但别把金额当账单。
3. 受时段减免影响的模型已写入 `pricing.json` 的 `low_confidence` 段，L2 下标 ⚠ 并剔除出「最贵模型」结论。

---

## 五、新增一个 Agent

### 5.1 需要新增的组件

1. **采集适配器** `adapters/<agent>.py`

   提供统一入口，返回 `(traces, db_data)`：

   ```python
   def collect_<agent>(start_date: str, end_date: str, **kw):
       """返回 (traces: list[dict], db_data: {"sessions": list[dict]})"""
   ```

   - `traces` 每项须含：`date` / `session_id` / `total_tokens` / `input_tokens` /
     `output_tokens` / `cached_tokens` / `effective_tokens` / `total_cost` /
     `effective_cost` / `channel` / `raw_model` / `exec_model` / `is_free`
   - `db_data["sessions"]` 每项须含：`id` / `cwd` / `title` / `created_date` /
     `mode` / `model` / `_dialogue_text`（任务分类依赖它）

2. **计价**：把该 Agent 的模型单价写进 `scripts/pricing.json`（发布版）或让用户写
   `scripts/pricing.local.json`；新增通道前缀时在 `ca_core.parse_channel()` 与
   `price_of()` 各加一个分支。

3. **CLI 分发**：`scripts/collect_usage_data.py` 的 argparse `--source` 增加取值，
   `main()` 增加分支调用适配器；其余聚合与渲染逻辑无需改动。

### 5.2 验收清单

- [ ] `adapters/<agent>.py` 能读取该 Agent 用量并归一化为统一 schema
- [ ] 该 Agent 的模型单价可查（发布版或本地覆盖）
- [ ] `--source <agent>` 端到端跑通一份报告
- [ ] `tests/test_<agent>_adapter.py` 通过：解析正确性、日期过滤、成本计算、健壮性、CLI 黑盒
- [ ] 更新 `SKILL.md` / `README.md` / 本文档的「支持范围」表格
- [ ] 测试通过 `CLAUDE_PROJECTS_DIR` 之类的环境变量把 fixture 指向 `tmp_path`，**绝不读取用户真实目录**
