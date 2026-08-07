# agent-analytics-report 发布前检查清单

> 用途：发布到 SkillHub（分发）+ GitHub（源码备份/合规）前的逐项核对。
> 维护者本地文档。若不希望它进入发布包，可将其加入 `.gitignore` 或直接删除。

---

## 〇、类型与范围确认

- [ ] **Skill 类型**：本地只读 Agent 用量/成本分析报表生成器（utility / analytics 类，自然语言触发 collect → generate → present）
- [ ] **当前支持范围**：仅 WorkBuddy（单 Agent 绑定）；跨 Agent 适配器为规划项，`ADAPTERS.md` 已就绪
- [ ] **发布目标**：SkillHub（WorkBuddy 用户一键安装）+ GitHub（源码备份 & 合规，LICENSE 已在）

---

## 一、🔴 必做项（阻塞发布）

### P0 — 防止个人数据泄漏（最高优先级）

- [ ] `scripts/pricing.local.json` 当前**物理存在**且含你真实的硅基流动自定义模型。发布前必须排除出打包范围：
  - **方式 A（推荐，最稳）**：临时把该文件移出 skill 目录，发布完成后再放回
  - **方式 B**：先验证 `skillhub publish` 打包确实忽略 `.gitignore` 中的 `pricing.local.json`；否则一律用方式 A
- [ ] 确认 `collect_usage_data.py` 在 `pricing.local.json` 缺失时能回退到内置 `MODEL_PRICING`（已验证；发布包应为干净状态）

### P1 — 发布元信息

- [ ] `config.json` 的 `update_url` 现为占位符 → 执行 `skillhub publish` 拿到真实 manifest 地址后**回填**
- [ ] `metadata.json` 与 `SKILL.md` 的版本号一致（当前均 `1.0.0`）
- [ ] `SKILL.md` frontmatter `license: MIT` 与根目录 `LICENSE` 文件一致

### P2 — 仓库清洁度

- [ ] `git status` 干净：无 `data.json` / `测试报告*` / `报告*` / `使用情况报告*` 等个人数据被追踪（`.gitignore` 已覆盖，过一遍确认）
- [ ] 无 `*.pyc` / `__pycache__` / 临时脚本（如 `html_fixed.py` / `scratch*.py` / `*.tmp.py`）残留

---

## 二、✅ 已就绪（无需改动，仅确认）

- [x] `LICENSE` 文件存在（MIT）
- [x] `README.md` 存在（含安装/用法说明）
- [x] `metadata.json` / `_meta.json` / `config.json` 三件套齐全
- [x] `.gitignore` 已排除 `pricing.local.json`（GitHub 路径安全）
- [x] **无第三方商业 API 硬依赖**：纯本地处理；联网检索 `--lookup-pricing online` 为可选且**不计费**，符合"自治优先、可选增强"合规取向
- [x] 文案诚实：仅称"首发支持 WorkBuddy，规划兼容更多 Agent"，未提前声称支持其它 Agent（符合 `ADAPTERS.md` 警告）

---

## 三、分发渠道决策

- [ ] **飞书 / 钉钉导出：非上架必需**（代码 grep 零处 `webhook` / `feishu` / `dingtalk`）。建议作为 **v2 / 独立 companion skill**，待企业团队分享需求明确再做
- [ ] **GitHub**：源码备份 + 合规（LICENSE 已在），`.gitignore` 护体，风险最低 → `git init` + `commit` + `push`
- [ ] **SkillHub**：让 WorkBuddy 用户一键安装 → 先解决 P0 再 `skillhub publish`

---

## 四、发布后动作

- [ ] 回填 `config.json` 的 `update_url`（用 `skillhub publish` 返回的真实地址）
- [ ] 在 `README.md` / SkillHub 描述补充安装命令（如 `skillhub install agent-analytics-report`）
- [ ] 在 GitHub 打 Release tag `v1.0.0`
- [ ] **验证**：`skillhub install` 重装后，用干净 `pricing.json` 跑一份报告，确认：
  - 无个人模型泄漏
  - 计价回退正常（缺失单价模型走 §3.3 补写清单，不崩）

---

## 五、未来兼容（非阻塞，备忘）

- 跨 Agent（千问办公 / Trae Work / Kimi Work）改动**集中在 `collect_usage_data.py` 采集层加适配器**；报告生成层（`generate_report.py` 吃 `data` dict）基本不动
- 报告标题硬编码 `Workbuddy使用情况报告` → 跨 Agent 时需参数化（当前 WB 生态内可保留）
- 品牌文案（标题 / data source / footer / 图例）散落约 15 处，跨 Agent 时抽成配置变量即可

---

## 六、快速发布命令（参考）

```bash
# 0. 移走个人定价（P0 方式 A）
mv scripts/pricing.local.json /tmp/pricing.local.json.bak

# 1. GitHub 备份
cd <skill-root> && git init && git add -A && git commit -m "release: agent-analytics-report v1.0.0" && git push

# 2. SkillHub 发布（发布后回填 update_url）
skillhub publish

# 3. 放回个人定价
mv /tmp/pricing.local.json.bak scripts/pricing.local.json
```
