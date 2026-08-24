# agent-analytics-report · 遗留隐患分析与解决方案

> 整理时间：2026-08-21 ｜ 版本：v1.1.2 ｜ 测试：243 用例全绿
> 关联：GitHub Pages `https://elisabeth15501.github.io/agent-analytics-report/`

---

## 一、此前摘要的"误判项"（本次已澄清，实际已正常）

| 项 | 结论 | 证据 |
|----|------|------|
| `config.json.update_url` 是页面 URL 而非 manifest | **已修复** | git `bb03fa7` 已改为 SkillHub API manifest 端点 `https://api.skillhub.cn/api/v1/skills/agent-analytics-report`，note 写明 405 旧值不可用 |
| `pytest.ini` 体积异常 | **正常** | 仅 395 B，内容为 `testpaths=tests` + `--alluredir=allure-results` |
| 测试产物污染仓库 | **已规避** | `.gitignore` 已排除 `allure-results/`、`__pycache__/`、`.pytest_cache/`、`report*.html`、`测试报告*.html` 等 |

---

## 二、真实遗留隐患与解决方案

### 隐患 A：Pages 内容为「人工静态快照」，会随时间陈旧
- **分析**：报告由本地手动 `pytest` + `gen_report.py` 生成后推送；未来代码改动不会自动刷新 Pages，展示页会逐渐过期。
- **解决（已落地）**：新增 `.github/workflows/pages.yml`，push 到 `main` 时自动「跑测试 → 生成自包含 HTML 报告 → 部署 Pages」。
- **仍需你操作**：把仓库 **Settings → Pages → Source** 从 `Deploy from a branch` 切到 `GitHub Actions`（见第四节）。

### 隐患 B：无 CI/CD 自动化
- **分析**：测试全靠本地手动执行，无门禁，回归易漏。
- **解决（已落地）**：同隐患 A 的 workflow；`workflow_dispatch` 支持手动触发。

### 隐患 C：报告生成脚本路径硬编码，不可移植
- **分析**：旧 `_gen_allure_report.py` 写死 `C:\Users\elisa\...` 绝对路径，换机器 / CI 直接失效。
- **解决（已落地）**：新增 `gen_report.py`，所有路径相对脚本自身（`os.path.dirname(__file__)`），本地与 CI 通用。旧脚本可废弃。

### 隐患 D：allure-results 累积导致报告重复计数
- **分析**：pytest 不清空 `allure-results/` 会**追加**历史运行 JSON，聚合时用例数虚高（实测曾出现 478 vs 实际 243）。
- **解决**：
  - 本地：每次生成前 `rm -rf allure-results && mkdir allure-results`；
  - CI：每次全新 checkout 天然隔离，不会累积；
  - 后续可增强：`gen_report.py` 生成前自动清空，或 pytest 改用临时目录。

### 隐患 E（可选）：未执行 skillhub publish
- **分析**：测试体系尚未分发到 SkillHub 平台（open.lenovomm.com / skillhub.cn）。
- **解决**：用 `skillhub publish`（对应 skillhub-publish skill）发布；`.gitignore` 已排除 `pricing.local.json` 等敏感/本地文件，发布安全。

### 隐患 F（低）：本地 git 跟踪引用偶显 stale
- **分析**：沙箱写受限时 `origin/main` 显示旧 commit，无害。
- **解决**：本机 `git fetch` 刷新即可。

---

## 三、当前交付状态

- ✅ GitHub Pages 已开启：`https://elisabeth15501.github.io/agent-analytics-report/`
- ✅ 落地页 `index.html` + 自包含测试报告 `test-report.html`（243 用例全绿）+ `.nojekyll` 已上线
- ✅ 可移植报告生成器 `gen_report.py` 已纳入仓库
- ✅ CI 工作流 `.github/workflows/pages.yml` 已就绪（切 source 即生效）

---

## 四、切换为 Actions 自动部署的步骤

1. 打开 `https://github.com/Elisabeth15501/agent-analytics-report/settings/pages`
2. **Build and deployment → Source** 改为 **GitHub Actions**
3. 之后每次 push `main` 自动部署，Pages 内容实时更新（无需再手动推送报告）
4. 如需回退：改回 `Deploy from a branch` (main / root) 即可

> 注意：GitHub Pages 的 Source 同一时间只能选一种。选 Actions 后，branch 模式不再生效；选 branch 则 Actions 部署被忽略。
