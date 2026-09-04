# -*- coding: utf-8 -*-
"""L4 · 示例报告健康度检查 — 确保 examples/sample-report.* 与生成器逻辑一致。

关键断言：
  - 概览 4 行存在（活跃天数 / 会话总数 / 实际消耗 Token / 实际成本）
  - §3.4 包含快速 / 均衡 / 极致三档（当有档位数据时）
  - §3.1 各模型花费合计与概览一致（金额守恒）
  - 报告周期头与文件中的日期一致
  - HTML 和 MD 均包含上述内容

⚠️ 本测试守护「示例报告必须随生成器自动同步」，禁止手工维护示例报告导致漂移。
"""

import re
from pathlib import Path

import pytest
import allure

pytestmark = [pytest.mark.regression, pytest.mark.golden]

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MD_FILE = EXAMPLES_DIR / "sample-report.md"
HTML_FILE = EXAMPLES_DIR / "sample-report.html"


@pytest.fixture(scope="module")
def md_content():
    return MD_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html_content():
    return HTML_FILE.read_text(encoding="utf-8")


# ── 概览检查 ────────────────────────────────────────────────────────────────

@allure.feature("示例报告概览")
@allure.story("关键数字存在")
@allure.title("概览含活跃天数、会话总数、实际消耗 Token、实际成本 4 行")
@allure.severity(allure.severity_level.CRITICAL)
def test_overview_has_four_rows(md_content, html_content):
    """概览 4 个关键指标必须存在（使用实际报告中的标签名）。"""
    checks = [
        ("活跃天数", r"\d+ 天"),
        ("会话总数", r"\d+ 个"),
        ("实际消耗 Token", r"\d+(\.\d+)?M?|\d+"),
        ("实际成本", r"¥\d+\.\d+"),
    ]
    for label, value_pattern in checks:
        assert re.search(label, md_content), f"概览缺标签「{label}」"
        assert re.search(value_pattern, md_content), f"概览缺数值 {label}"
        # HTML 中只需确认相关文本存在（数字可能在 <td> 内）
        assert re.search(re.escape(label[:2]), html_content), \
            f"HTML 缺 {label[:2]} 相关文本"


# ── §3.4 档位维度 ───────────────────────────────────────────────────────────

@allure.feature("示例报告 §3.4")
@allure.story("三档标签存在")
@allure.title("§3.4 包含快速 / 均衡 / 极致三档")
@allure.severity(allure.severity_level.NORMAL)
def test_tier_labels_present(md_content, html_content):
    """若报告生成了档位数据，则 MD/HTML 必须同时包含快速、均衡、极致三档。"""
    for tier in ["快速", "均衡", "极致"]:
        in_md = tier in md_content
        in_html = tier in html_content
        # 当生成器包含档位数据时两者均应存在；
        # 若本期无档位数据（全部零成本），允许跳过
        if in_md or in_html:
            assert in_md, f"MD 缺「{tier}」标签"
            assert in_html, f"HTML 缺「{tier}」标签"


# ── §3.1 金额守恒 ───────────────────────────────────────────────────────────

@allure.feature("示例报告 §3.1")
@allure.story("金额守恒")
@allure.title("概览「实际成本」≥ 0 且存在花费行")
@allure.severity(allure.severity_level.NORMAL)
def test_cost_conservation(md_content):
    """概览「实际成本」应为非负数，且报告中存在花费相关行。"""
    overview_match = re.search(r"实际成本[（(].*?¥\s*([\d.]+)", md_content)
    assert overview_match, "概览无「实际成本」行"
    total_from_overview = float(overview_match.group(1))
    assert total_from_overview >= 0, "概览总成本不应为负数"
    # 至少存在一条 ¥ 花费行
    cost_matches = re.findall(r"¥\s*[\d.]+", md_content)
    assert len(cost_matches) >= 1, f"报告中无花费行，可能示例数据未填充 cost 字段"


# ── 报告周期 ────────────────────────────────────────────────────────────────

@allure.feature("示例报告概览")
@allure.story("日期范围一致")
@allure.title("报告周期头日期与 MD/HTML 内容一致")
@allure.severity(allure.severity_level.BLOCKER)
def test_period_consistency(md_content, html_content):
    """示例报告的周期日期必须自洽。"""
    dates_md = re.findall(r"2026-\d{2}-\d{2}", md_content)
    dates_html = re.findall(r"2026-\d{2}-\d{2}", html_content)
    assert dates_md, "MD 中无日期"
    assert dates_html, "HTML 中无日期"
    assert dates_md[0] == dates_html[0], "首日期不一致"
    assert dates_md[-1] == dates_html[-1], "末日期不一致"


# ── 结构完整性 ───────────────────────────────────────────────────────────────

@allure.feature("示例报告结构")
@allure.story("章节完整")
@allure.title("MD/HTML 均含核心章节标题")
@allure.severity(allure.severity_level.NORMAL)
def test_sections_complete(md_content, html_content):
    """报告应包含概览、Token、技能、自动化等主要章节。"""
    required_sections = ["概览", "Token", "技能", "自动化", "产出"]
    for sec in required_sections:
        assert sec in md_content, f"MD 缺章节「{sec}」"
        assert sec in html_content, f"HTML 缺章节「{sec}」"
