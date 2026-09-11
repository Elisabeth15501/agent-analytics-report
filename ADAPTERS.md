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

---

## 二、Claude Code 适配器

实现文件：`adapters/claude_code.py`

### 2.1 数据定位

`resolve_claude_projects_root()` 按以下优先级定位 projects 根目录：

1. `CLAUDE_PROJECTS_DIR` —— 直接指向 `projects/` 目录（测试与自定义安装位）
2. `CLAUDE_CONFIG_DIR` —— Claude Code 官方配置根覆盖，可逗号分隔多个根，取第一个含 `projects/` 者
3. 平台默认
   - Windows：`%APPDATA%/Claude/projects`
   - macOS / Linux：`~/.claude/projects`

`_iter_jsonl_files()` 递归扫描该目录下所有 `*.jsonl`，并兼容 Claude Desktop agent 模式的
`local-agent-mode-sessions` 嵌套 `projects/` 子树。

### 2.2 解析规则

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

### 2.3 会话派生

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

### 2.4 计价

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

### 2.5 已知限制（MVP）

- 只统计**单次对话内**的 token 与成本；Claude Code 不落盘会话级「任务名」，Top 任务榜以会话首条提问为题
- `duration_ms` 恒为 0（JSONL 无可靠的端到端耗时字段，未做推测）
- 不区分 Max 订阅 / API 计费：一律按刊例 API 价估算，订阅用户实际边际成本更低
- 子 Agent（Task tool）若写入同目录 JSONL 则已包含，否则不计

---

## 三、新增一个 Agent

### 3.1 需要新增的组件

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

### 3.2 验收清单

- [ ] `adapters/<agent>.py` 能读取该 Agent 用量并归一化为统一 schema
- [ ] 该 Agent 的模型单价可查（发布版或本地覆盖）
- [ ] `--source <agent>` 端到端跑通一份报告
- [ ] `tests/test_<agent>_adapter.py` 通过：解析正确性、日期过滤、成本计算、健壮性、CLI 黑盒
- [ ] 更新 `SKILL.md` / `README.md` / 本文档的「支持范围」表格
- [ ] 测试通过 `CLAUDE_PROJECTS_DIR` 之类的环境变量把 fixture 指向 `tmp_path`，**绝不读取用户真实目录**
