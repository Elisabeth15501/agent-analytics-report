# FAQ · agent-analytics-report 常见问题

这里汇总使用本技能时最容易撞上的问题。按「安装 → 生成 → 计价 → 自定义模型 → 标记与合并 → 数据源与隐私 → 分类与异常 → 排查」排列，单篇自足，不用再翻 SKILL.md 和 README。

> 适用版本：**v1.3.0**（核对 `config.json` / `metadata.json`）。**目前仅适配 WorkBuddy。**

---

## 一、适用与安装

**Q1. 除了 WorkBuddy，还支持别的 Agent 吗？**
不支持。数据采集全部来自 WorkBuddy 本机目录（`~/.workbuddy/`、工作区会话目录、`workbuddy.db`、`usage-log.json`），计价库 `scripts/pricing.json` 里也是 WorkBuddy 官方接口的模型与单价，不是通用市场价。`ADAPTERS.md` 里预留了多 Agent 接缝，但 Trae、千问办公等适配器都还没实现——别对外声称已兼容。

**Q2. 怎么装？**
两种方式：

```bash
# 方式一（推荐）：SkillHub 搜索 agent-analytics-report 一键安装

# 方式二：手动
git clone <your-repo-url> ~/.workbuddy/skills/agent-analytics-report
```

手动装完重启 WorkBuddy 生效。

**Q3. `skillhub upgrade` 会不会冲掉我的配置？**
不会，前提是你把自定义内容写在 `scripts/pricing.local.json`。这个文件被 `.gitignore` 排除、不在发布包里，升级时发布版 `pricing.json` 被整目录覆盖重写，但 `pricing.local.json` 永远不会被碰——你填的单价自动保留，不用备份。**反过来，写进 `pricing.json` 的东西升级就会丢。**

**Q4. 报告没数据，是不是少开了什么？**
Token 数据来自 traces 目录，需要在 WorkBuddy 里启用 trace 记录。没开就没数据，报告会是空的。

---

## 二、生成报告

**Q5. 最省事的用法是什么？**
直接对 Agent 说一句就行，不用记参数：「生成周报」「帮我出一份本月的 token 消耗报告」「统计下这个月花了多少」。Agent 会自动跑采集 + 渲染。

**Q6. 支持哪些时间粒度？**

| 参数 | 含义 |
|------|------|
| `--period day` | 今天（单日） |
| `--period week`（默认） | 最近 7 天 |
| `--period month` | 最近 30 天 |
| `--period year` | 最近 365 天（滚动窗口，会跨年） |
| `--days N` | 自定义滚动 N 天 |
| `--start` + `--end` | 绝对日期范围 |

**优先级：绝对日期 > `--days` > `--period`。** 三个脚本（`collect_usage_data.py` / `generate_report.py` / `analyze_tokens.py`）都支持。

**Q7. 我想统计「2026 年 7 月」，用 `--period month` 对吗？**
不对。`--period` 是**滚动**窗口（最近 30 天），会跨月。想要自然月/自然年，用绝对日期：

```bash
python scripts/generate_report.py --start 2026-07-01 --end 2026-07-31 --output 七月报告.md
python scripts/generate_report.py --start 2026-01-01 --end 2026-08-03 --output 年报.md
```

采集器会自动识别周期类型，报告头部「报告类型」行显示成 `月报 · 2026年7月` / `年报 · 2026年（年初至今）`。

**Q8. 三种输出格式怎么选？**

```bash
python scripts/generate_report.py data.json --output report.md    --format markdown  # 默认，适合阅读
python scripts/generate_report.py data.json --output report.html  --format html      # 交互图表 + 浅深色自适应
python scripts/generate_report.py data.json --output report.json  --format json      # 适合程序处理
```

不传 `data_file` 就直接实时采集：`python scripts/generate_report.py --period week --output report.md`。

**Q9. 报告标题怎么没有「周报」两个字？**
故意的。三个格式的标题统一是 `Workbuddy使用情况报告`，周期信息改由报告头部的「**报告类型**」行用日历日期呈现（`周报 · 2026 年第32周（2026-07-27 至 2026-08-03）`）。这样 md / html / json 四方一致，不会因为文件名改变而对不上。

---

## 三、成本与计价（最容易产生疑问的部分）

**Q10. 报告里的花费和 WorkBuddy 后台对不上？**
先确认三件事：

1. **口径**：报告按「实际计费模型」（`exec_model`）+ 接口通道聚合，这和后台一致；
2. **单价**：报告用的是公开刊例价估算。若某模型单价没配，或你走的是 `custom-local` 自建接口，实际账单以接口方为准；
3. **缓存**：报告主口径是「实际消耗（计费等效）」，比原始 token 小很多（见 Q14），别拿原始数字去对账单。

**Q11. 我想改单价，改哪个文件？**

| 文件 | 用途 | 会不会被升级覆盖 |
|------|------|------------------|
| `scripts/pricing.json` | 发布版：WorkBuddy 官方内置模型 + `auto` | **会** |
| `scripts/pricing.local.json` | 本地覆盖：你自己的第三方/自建模型 | **不会** |

两份都缺时，回退到 `collect_usage_data.py` 里的内置 `MODEL_PRICING` 常量。单位是**元 / 每百万 tokens，输入按缓存未命中价**。

截至 v1.3.0，发布版已配价的官方模型共 17 项（完整清单以 `scripts/pricing.json` 为准）：

另：自动路由器的三档**档位**（快速 / 均衡 / 极致，即 `fast-model` / `balanced-model` / `extreme-model`）走独立 `mode_rates` 段配置，不在上面这份「模型」清单里——它们按积分倍率计费，报告单设 §3.4 维度展示，详见 Q36 / Q37。

`auto`（路由别名）· `hy3` / `hy3-x` · `hy4-preview` / `hy4-preview-x` · `glm-5.3` / `glm-5.3-flash` / `glm-5.2` / `glm-5.2-x` / `glm-5.1` / `glm-5v-turbo` · `minimax-m3` · `kimi-k3` / `kimi-k2.7-code` / `kimi-k2.6` · `deepseek-v4-flash` / `deepseek-v4-pro`

其中 `hy3` 限时免费至 2026-09-30、`hy4-preview` 限时免费至 2026-09-10。已下架的历史官方模型单独放在 `delisted` 段（报告里标 🗄️），不在上面这份清单里。

**Q12. 报告显示「未配置」怎么办？**
报告 **§3.3** 会列出本期所有缺失单价的模型名，并给出可直接复制的 `pricing.local.json` 补写片段。照着贴进本地文件、重跑即可。未配置的模型不计入成本总额，也不会污染占比。

嫌手改麻烦，直接把模型名和单价告诉 Agent，让它照 `references/add-custom-models.md` 写。

**Q13. `auto` 的花费是怎么算的？**
`auto` 是智能路由入口，没有单一单价（配置里恒为 `null`）。采集器取「本周期内所有已配置且单价 > 0 的模型的均价」做代表性估算，报告里标 ℹ️ 注明是估算值。想要精确值，可以在 `pricing.local.json` 里给 `auto` 直接填 `{"input": .., "output": ..}` 覆盖。

**Q14. 「原始 Token」和「实际消耗」差好多，哪个是真的？**
两个都是真的，用途不同：

- **原始总 Token** = `totalTokens` = 输入 + 输出，其中缓存命中（`cached_tokens`）是**输入 token 的子集**；
- **实际消耗（计费等效）** = 原始总量 − 缓存命中 ×(1 − 0.1)，即缓存命中只按 10% 计入（`CACHE_DISCOUNT = 0.1`）；
- **缓存占比** = `cached_tokens / input_tokens`，越高说明在反复复用同一段上下文——数字看着吓人，实际很便宜。

报告里所有排名（任务类型、Top 任务、每日趋势、成本）都用**实际消耗**作主口径。

**Q15. 限免期为什么显示 ¥0？**
`pricing.json` 的 `timed_free` 段记录了限时免费模型及截止日（含当天），命中期内调用记 ¥0。比如 `hy3` 限免至 2026-09-30、`hy4-preview` 限免至 2026-09-10。这段时间别只看钱——报告 §4 会插入免责声明，提示优先看 **§4.3 Token 口径**（始终运行）和缓存占比。

**Q16. 我走自建接口（custom-local），钱算得准吗？**
按你在 `pricing.local.json` 的 `custom_local` 段填的单价算。没填则回退到同名官方模型价；没有同名官方模型就显示「未配置」。数字仅供参考，准确账单请看接口方网站——报告表格末尾也附了这条免责备注。

---

## 四、自定义模型（BYOM）

**Q17. 怎么把我的自建 / 第三方模型加进报告？**
最简单：把模型名和单价用大白话告诉 Agent。

> 我新加了俩自定义模型：`custom-local:deepseek-r1`（自建，输入 2 / 输出 8）和 `custom-local:qwen3-235b`（自建 vLLM，输入 1 / 输出 2）。都帮我写进 agent-analytics-report 的 pricing.local.json。

Agent 会找到文件、去前缀转小写、只追加不改动官方条目、改完回显给你确认。完整 Prompt 模板见 `references/add-custom-models.md`。

**下载后想一次性补齐所有用过的自定义模型**，用 `references/on-download-inject.md` 里的扫描 Prompt。

**Q18. 带 `custom-local:` 前缀和裸名，分别写哪一段？**

| 你的模型长什么样 | 写进哪一段 | 键名规则 |
|------------------|-----------|----------|
| `custom-local:deepseek-r1`（WorkBuddy 里配成 OpenAI 兼容端点） | `custom_local` | 去掉 `custom-local:` 前缀、转小写 → `deepseek-r1` |
| `deepseek-r1`（trace 里就是裸名） | `models` | 原名小写 |

```json
{
  "custom_local": { "deepseek-r1": {"input": 2.0, "output": 8.0} },
  "models":       { "my-own-model": {"input": 1.0, "output": 2.0} }
}
```

**Q19. OpenRouter 的 `:free` 模型要登记吗？**
不用。以 `:free` 结尾的模型走 `openrouter-free` 通道，自动记 ¥0，报告里正常显示「免费」。只有当你确认某个 `:free` 变体是 WorkBuddy 曾官方提供、现已下架的，才需要把**完整模型名（含 `:free` 后缀）**写进 `delisted` 段。

**Q20. 本地 Ollama 模型怎么算？**
零 API 成本，强制归零并标 🏠，不计入账单总额。它会优先于「外部自定义」识别，绝不会误标成官方下架。

---

## 五、模型标记与合并

**Q21. 报告里那些小图标什么意思？**

| 标记 | 含义 |
|------|------|
| 🔀 | 智能路由 / 聚合网关（OpenRouter、Groq 等），一次调用可能落到不同模型，单价仅供参考 |
| 🏠 | 本地推理（Ollama / localhost:11434），零 API 成本 |
| 🔧 | 用户自定义外部 API（自己接的第三方端点） |
| 🗄️ | WorkBuddy 官方曾提供、现已下架（历史调用仍需计价，不进 §3.3 缺失清单） |
| ℹ️ | 估算值（如 `auto` 路由均价） |

**Q22. 我的官方模型怎么被标成 🔧 外部自定义了？**
采集器运行时会读本机 `~/.workbuddy/models.json`，凡被配成自定义/外部端点（`vendor=custom` 或 url 含 openrouter）或本地 Ollama 的模型，无论 trace 通道记成什么，一律优先判为自定义。这是为了治「撞名官方模型但走外部端点」的误识别。如果你确实是走官方通道，检查一下 `models.json` 里这个模型是不是被配成了外部端点。

**Q23. 为什么 `hy4-preview` 和 `hy4-preview-x` 在报告里合并成一行了？费用怎么算？**
这是 `display_merge` 干的。WorkBuddy 对同一模型会提供两个入口：**免费额度版**（trace 记 `hy4-preview`）和**免费额度用尽后的收费版**（trace 记 `hy4-preview-x`）。报告里合并成一行更好读。

**合并只改显示分组，不碰钱**：每一条 trace 仍按它自己实际执行的模型单独计价——免费额度版记 ¥0，收费版按刊例价（hy4 是 6/18，hy3 是 1/4）。所以合并行的花费，就等于其中**收费版那部分**的用量费用。实测开关 `display_merge` 对照，两个维度（§3.1 账单口径、§3.2 入口视图）总金额都完全不变。

`hy3` / `hy3-x` 同理。

**Q24. 我不想合并 / 想加新的合并对，在哪改？**
改 `scripts/pricing.json`（或本地 `pricing.local.json`）的 `display_merge` 段，键是要被合并掉的变体名，值是合并后显示的基础模型名：

```json
"display_merge": {
  "hy4-preview-x": "hy4-preview",
  "hy3-x": "hy3"
}
```

删掉某条就不合并了；照格式加新的即可，不用改 Python 代码。

---

## 六、数据源与隐私

**Q25. 数据从哪来？**

| 数据源 | 路径 | 内容 |
|--------|------|------|
| Traces | `~/.workbuddy/traces/` | Token 消耗、模型信息、会话时长 |
| SQLite | `~/.workbuddy/workbuddy.db` | 会话元数据、自动化运行、信用消耗 |
| Usage Log | `~/.workbuddy/usage-log.json` | 技能使用记录、活跃天数 |
| 会话目录 | `~/WorkBuddy/` | 产出文件、记忆日志 |

各源字段结构见 `references/data_sources.md`。

**Q26. 会不会上传数据 / 联网？**
不会。全部本机读取、本地渲染，不调用任何外部 API。唯一的例外是 `--lookup-pricing online` 会生成一个搜索链接，以及 `--pricing-api <URL>` 会去你自己的定价镜像端点拉价——**都是可选的**，拉到的价一律标 🌐 网络估算价，不计入任何成本总额。

**Q27. 删掉的对话还算钱吗？**
算。已删除对话消耗掉的 token 和成本仍然计入总量（避免低估真实开销），但归属到「其他 / 未关联」，不会按会话明细展示。即便产物文件物理删除了，transcript 里的 `ImageGen`/`VideoGen` 调用记录和 `trackedFileBackups` 键名仍然保留，任务类型分类照样能用得上。

---

## 七、分类与异常

**Q28. 任务类型是怎么判定的？分错了怎么办？**
关键词启发式匹配，顺序命中即返回。判定顺序是：后台自动化优先 → 内容生成 → 代码开发 → …… 完整 11 类顺序表在 SKILL.md 的「任务类型分类规则」。

候选文本 = 对话内容（剥离 `<system-reminder>` 后）+ 生成物指纹（含已删除）+ 会话标题。因为是启发式，**误分类是正常现象**，命不中就归「其他」。要调整就得改 `collect_usage_data.py` 的 `classify_task`。

**Q29. 异常检测为什么有两套口径？**
因为限免期会拉低成本口径，导致真实 Token 峰值被漏报。所以报告同时跑：

- **💰 成本口径**——仅在确有真实成本时启用；
- **📊 Token 口径**——**始终运行**。

免费主导期（免费 Token 占比 ≥ 80% 或总成本为 0）会在 §4 顶部插免责声明，提示成本类洞察参考有限。

**Q30. 「幽灵调用 / 未解析调用」警告是什么？**
早期 WorkBuddy trace 可能同时缺 `sessionId` 和 `modelInfo`，被兜底记成 `default`、token 全 0，无法归属到任何模型或会话。报告会显式警告并单独统计，不影响可计费数据。

---

## 八、故障排查

**Q31. 报告空空如也 / 全是 0？**
按顺序排查：① trace 功能是否启用（Q4）；② 时间范围是否对（`--period` 是滚动窗口，要自然月请用 `--start/--end`）；③ `~/.workbuddy/` 下 traces 目录是否有数据。

**Q32. 报告各章节的金额对不上？**
§3.1（账单口径）直接汇总 trace 级成本，必然与概览头条、每日表对账一致。§3.2（入口视图）是按入口单价**重算**的估算口径，与 trace 级有极小的浮点差异（约 0.0001 元量级），这是设计如此，不是 bug。

**Q33. 想跑测试 / 改代码，怎么上手？**

```bash
cd agent-analytics-report
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements-tests.txt
.venv/Scripts/python.exe -m pytest tests/ -v --alluredir=allure-results

# 无需 Java 的自包含 HTML 仪表盘
.venv/Scripts/python.exe tools/render_allure_html.py --results-dir allure-results --output allure-report.html
```

当前 **10 个测试文件、306 用例**。全部使用合成 fixture 数据，不含任何真实用量或个人资料，可安全公开。用例按 marker 分层（`smoke` / `unit` / `integration` / `regression` / `golden` / `metadata` / `privacy`），可用 `pytest -m regression` 过滤。

注意：`pytest.ini` 的 `addopts` 里带 `--alluredir`，若环境没装 `allure-pytest` 插件，跑测试要加 `-o addopts=""` 绕过。

**Q34. `tests/test_channel_attribution.py` 单独跑会崩？**
这个文件有近 190 个参数化集成用例，每个参数都要建临时 SQLite 和 traces 目录，本机资源不够时进程会被系统杀掉（表现为无任何输出、退出码 1）。**不是用例失败**。建议用 `-k` 分批跑，例如 `pytest tests/test_channel_attribution.py -k "hy3 or hy4"`。CI 上资源充足时可以全量跑。

**Q35. 我在用另一个类似 WorkBuddy 的工具，想用这个Skill，但目前只支持 WorkBuddy，可以怎样做？**
请参考 [ADAPTERS.md](ADAPTERS.md) 中的适配器说明。

---

## 九、档位维度（快速 / 均衡 / 极致）

**Q36. 报告 §3.4 的「档位」是什么？为什么单价是估算值？**
WorkBuddy 的 `auto` 自动路由下还有三档可选档位，按**积分消耗倍率**区分：

| 档位 | 别名（trace 字面量） | 积分倍率 |
|------|----------------------|----------|
| 快速 | `fast-model` | 0.21x |
| 均衡 | `balanced-model` | 0.65x |
| 极致 | `extreme-model`（配置缓存规范 id 为 `deep-model`，报告已归一） | 1.20x |

**这些是估算单价，不是真实账单价**：WorBuddy 只对档位做积分倍率计费，trace 里从不记录档位背后实际落地的底层模型，因此按档位直接算「花费」在概念上不成立。报告用「倍率锚定法」估算——按已知模型的官方倍率线性外推档位 ¥ 单价（快速 ≈¥1.24/2.47、均衡 ≈¥6.58/23.04、极致 ≈¥14.80/74.10），**仅用于横向对比档位间的相对成本**，章节内明确标注为估算值，且与 §3.1（账单口径）/ §3.2（入口视图）的真实计费完全解耦——改档位定价不影响任何真实金额。

想调估算单价只改 `pricing.json`（或 `pricing.local.json`）的 `mode_rates` 段，不用动代码。

**Q37. 为什么 §3.4 看不到档位背后的真实模型？**
因为 WorkBuddy 的计费语义就是「按档位积分倍率」而非「按底层模型 token」。trace 里只有档位别名（`fast-model` 等），底层模型对调用方不可见，所以本报告只能做**纯档位聚合**，无法像 §3.2 那样展开成具体模型。这不是采集器漏采，而是上游数据本身就不包含该信息。若你确实需要「某档位跑了哪些模型」，只能从 WorkBuddy 后台按档位账单查证，本报告无法还原。

---

> 至此共 **36 问**。单篇自足；更多细节见 SKILL.md 与 README.md。