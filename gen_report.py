# -*- coding: utf-8 -*-
"""gen_report.py — CI 入口：把 allure-results/ 渲染为自包含 HTML 测试报告。

本文件是 **薄封装（thin wrapper）**，不是第二套渲染实现：
实际的 HTML 渲染逻辑统一在 `tools/render_allure_html.py`（零依赖、支持
状态/严重度/标签筛选、步骤树、附件、暗色模式自适应）。这样仓库里只有一份
渲染源码，避免两份实现长期漂移（这正是 Clean Code 审计里点名的「同职能重复」）。

为什么保留这个文件而不是让 CI 直接调 tools/ 下的脚本：
  - CI（.github/workflows/pages.yml）约定调用 `python gen_report.py`，
    本文件把输出名固定为 `test-report.html`（Pages 流水线在 Assemble 步骤里
    直接 cp 这个文件名），并把 `tools/` 加入 import 路径，对流水线零改动。

用法：python gen_report.py   （需先 `pytest --alluredir=allure-results`）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "allure-results")
OUT_HTML = os.path.join(HERE, "test-report.html")


def main(argv=None):
    # 让 tools/ 下的模块可导入
    tools_dir = os.path.join(HERE, "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from render_allure_html import main as render_main

    rc = render_main([
        "--results-dir", RESULTS_DIR,
        "--output", OUT_HTML,
    ])
    # render_allure_html 在「目录里没有 *-result.json」时返回 1（仅告警），
    # 保持与原 gen_report.py 一致的「不中断 CI」语义；真正的错误（目录缺失，
    # 返回 2）仍向上抛出。
    if rc == 1:
        return 0
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
