# 数据源参考

## 一、Traces 目录

**路径**：`~/.workbuddy/traces/`

每个子目录以 PID 命名，包含一个或多个 `trace_*.json` 文件。

### Trace JSON 结构

```json
{
  "trace": {
    "traceId": "trace_xxx",
    "name": "Agent workflow",
    "workerPid": 12345,
    "startedAt": "2026-07-20T10:00:00.000Z",
    "endedAt": "2026-07-20T10:05:00.000Z",
    "duration": 300000,
    "status": "ok",
    "spanCount": 10,
    "totalTokens": 100000,
    "sessionId": "uuid",
    "modelInfo": {
      "models": ["auto"],
      "totalInputTokens": 90000,
      "totalOutputTokens": 10000,
      "totalCachedTokens": 50000,
      "callCount": 5
    }
  }
}
```

### 关键字段

| 字段 | 说明 |
|------|------|
| `totalTokens` | 总 Token 消耗 |
| `totalInputTokens` | 输入 Token |
| `totalOutputTokens` | 输出 Token |
| `totalCachedTokens` | 缓存命中 Token |
| `callCount` | API 调用次数 |
| `models` | 使用的模型列表 |
| `startedAt`/`endedAt` | ISO 8601 时间戳 |

---

## 二、WorkBuddy SQLite 数据库

**路径**：`~/.workbuddy/workbuddy.db`

### 主要表结构

#### sessions 表

| 列名 | 类型 | 说明 |
|------|------|------|
| id | TEXT | 会话 UUID |
| cwd | TEXT | 工作目录 |
| title | TEXT | 会话标题 |
| custom_title | TEXT | 用户自定义标题 |
| status | TEXT | 状态（completed/active） |
| created_at | INTEGER | 创建时间戳（毫秒） |
| mode | TEXT | 模式（craft/plan/ask） |
| model | TEXT | 使用的模型 |
| is_background_automation | INTEGER | 是否自动化后台任务 |

#### automation_runs 表

| 列名 | 类型 | 说明 |
|------|------|------|
| thread_id | TEXT | 运行线程 ID |
| automation_id | TEXT | 自动化任务 ID |
| status | TEXT | 状态 |
| result_success | INTEGER | 是否成功 |
| created_at | INTEGER | 创建时间戳 |
| thread_title | TEXT | 运行标题/摘要 |

#### session_usage 表

| 列名 | 类型 | 说明 |
|------|------|------|
| session_id | TEXT | 会话 ID |
| used | INTEGER | 已使用量 |
| size | INTEGER | 总容量 |
| credit_json | TEXT | 信用消耗 JSON |

---

## 三、Usage Log

**路径**：`~/.workbuddy/usage-log.json`

### 结构

```json
{
  "version": 1,
  "skills": {
    "skill-name": {
      "id": "skill-name",
      "type": "skill",
      "lastUsedDate": "2026-07-20",
      "recentDates": ["2026-07-20", "2026-07-19"],
      "firstSeenDate": "2026-06-01"
    }
  },
  "activeDays": ["2026-07-20", "2026-07-19"]
}
```

---

## 四、会话目录

**路径**：`~/WorkBuddy/`

目录命名格式：`YYYY-MM-DD-HH-MM-SS`

### 目录结构

```
2026-07-20-10-00-00/
├── output_file.html
├── report.md
└── .workbuddy/
    └── memory/
        ├── 2026-07-20.md
        └── MEMORY.md
```

### 记忆日志格式

```markdown
# 2026-07-20 工作日志

## 任务标题
- 完成内容 1
- 完成内容 2
```

---

## 五、自动化 API

通过 `automation_update` 工具查询：

```python
automation_update(mode="list")  # 列出所有自动化
automation_update(mode="view", id="...")  # 查看详情
```

### 自动化配置字段

| 字段 | 说明 |
|------|------|
| name | 任务名称 |
| scheduleType | once/recurring |
| rrule | RFC 5545 规则 |
| status | ACTIVE/PAUSED |
| cwds | 工作目录列表 |