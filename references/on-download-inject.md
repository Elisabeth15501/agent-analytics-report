# 下载本技能后：让 Agent 自动注入你的自定义模型

你从 SkillHub 下载 `agent-analytics-report` 后，**首次**让 Agent 生成报告（或主动说一句"把我的自定义模型加进报告"）时，Agent 会自动扫描你本机 WorkBuddy 用过的 `custom-local:*` 自建模型，问你单价，写进本地 `pricing.local.json` 的 `custom_local` 段。发布版不含任何开发者自建模型，所以这一步只影响你自己的副本。

## 给 Agent 的 Prompt（复制粘贴，大白话即可）

> 我刚下载了 agent-analytics-report 技能。请帮我把我在 WorkBuddy 里用过的自定义模型（custom-local 自建通道）的单价，自动加进这个技能的 pricing.local.json，这样以后报告能算出它们的真实花费。
>
> 请你照下面做：
> 1. 扫描我本机 WorkBuddy 数据目录（一般在 `~/.workbuddy` 下的 `workbuddy.db` 和 traces 文件），找出**所有出现过的 `custom-local:xxx` 模型名**。可以用这个命令发现：`grep -rhoE "custom-local:[a-zA-Z0-9._-]+" ~/.workbuddy 2>/dev/null | sort -u`
> 2. 把找到的自定义模型名列出来给我看。
> 3. 逐个问我每个模型的 RMB 单价：输入多少钱、输出多少钱（单位：元 / 每百万 tokens，按我接口方的实际账单填，比如自建 DeepSeek、自建开源模型的服务方）。
> 4. 把它们在定价文件 `（技能目录）/scripts/pricing.local.json` 的 `custom_local` 段写成：`"底层名(去掉 custom-local: 前缀、小写)": {"input": X, "output": Y}`。
> 5. 只往 `custom_local` 里追加/更新我的模型，**不要改**任何官方模型条目，也不要删其它字段。
> 6. 改完把新增的条目回显给我确认。
> 7. 如果某个模型我一时给不出单价，先跳过、标个「待补」，别卡住。
>
> 如果第 1 步一个 `custom-local:` 模型都没扫到，说明我还没用过自建接口，直接告诉我"未发现自定义模型"就好，不用写任何东西。

## Agent 视角的关键点
- 发现命令里 `~/.workbuddy` 在 Windows 上通常是 `C:\Users\<你>\.workbuddy`；grep 失败就改用对应系统的搜索。
- 键名规则：`custom-local:DeepSeek-R1` → 键 `deepseek-r1`（去前缀、转小写）。
- 写入的是 `pricing.local.json`（**本地覆盖文件，不进发布包**）：以后 `skillhub upgrade` 升级技能只会覆盖发布版 `pricing.json`，**不会触动** `pricing.local.json` —— 他的自定义模型单价自动保留，无需备份或重注入。
- 验证：写完重跑一次报告，§3.1 / §3.2 里这些自定义模型应正常显示单价与花费，不再标「未配置」。

## 之后再加新模型？
下载时这次注入的是"你目前用过的"。**以后**你在 WorkBuddy 里新加了一个自定义模型，照 [`add-custom-models.md`](add-custom-models.md) 用大白话告诉 Agent 即可，不用再全量扫描。
