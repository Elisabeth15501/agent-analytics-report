# 扩展新 Agent（ADAPTERS）

本技能当前**仅实现 WorkBuddy 适配器**。本文说明如何新增一个 Agent（如 Trae、千问办公），
供后续贡献参考。目标：让采集入口与计价加载成为可切换的「缝」，而非重写整套逻辑。

> ⚠️ 在适配器实现并验证前，请勿在文档 / 市场文案中声称已支持该 Agent。

---

## 需要新增的两类组件

### 1. 数据采集适配器（DataAdapter）

`scripts/collect_usage_data.py` 目前硬编码读取 WorkBuddy 专属数据源：

- **路径**：`~/.workbuddy/...`、`workbuddy.db`、`usage-log.json`
- **trace schema**：`custom-local:` 前缀、`is_free` / `total_cost` / `sessionId` / `effective_tokens` 字段
- **自定义发现**：`discover_custom_models()` 读取 `~/.workbuddy/models.json`

新增 Agent 需把该 Agent 的用量记录**归一化**为上述统一 schema。建议抽象为：

```
adapters/
  workbuddy.py    # 现有逻辑迁于此
  trae.py         # 未来
  qwen_office.py  # 未来
```

并由 `get_adapter(agent)` 统一分发，CLI 增加 `--agent` 参数（默认 `workbuddy`）。

### 2. 计价注册表（按 Agent 分文件）

`scripts/pricing.json` 是 **WorkBuddy 官方接口**的模型 + 单价；其他 Agent 计不同的模型、不同的价
（例如 Trae 走 Claude / GPT-4o 体系、千问办公走通义 / Qwen 系）。建议改为按 Agent 分文件：

```
pricing/
  workbuddy.json    # 当前 pricing.json 的内容迁移于此
  trae.json         # 未来
  qwen_office.json  # 未来
```

由 `--agent` 选择加载；`pricing.local.json` 本地覆盖机制保持不变。

---

## 当前状态

- ✅ WorkBuddy 适配器（内置）
- ⬜ Trae 适配器（未实现）
- ⬜ 千问办公 适配器（未实现）

---

## 验收清单（新增一个 Agent 时）

- [ ] `adapters/<agent>.py` 能读取该 Agent 的用量数据并归一化为统一 schema
- [ ] `pricing/<agent>.json` 含该 Agent 的模型 + 单价
- [ ] `--agent <agent>` 端到端跑通一份报告
- [ ] README 与 SkillHub 文案更新支持范围（在此之前保持「仅 WorkBuddy」标注）
