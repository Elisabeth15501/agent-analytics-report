# -*- coding: utf-8 -*-
"""L1 · 报告生成层（Report Generation）— pytest + Allure 双可视化。

覆盖 generate_report.py 的「最易回归、历史上多次出问题」的接口：
  - merge_glm52_family        GLM-5.2 家族三形态合并为单行（§3.1 口径）
  - _esc                     HTML 转义（XSS 防御核心）
  - build_donut_chart / build_model_cost_chart  图表构造器在空/退化数据下不崩
  - generate_markdown_report / generate_html_report / generate_json_report
                            三格式在固定 fixture 上跑通且含关键章节
  - XSS 回归                 恶意模型名进入 HTML 报告时被转义，不出现原始标签

全部白盒 / 冒烟 / 回归，使用内置 fixture，不依赖真实数据。
"""

import json

import pytest

import allure

pytestmark = [pytest.mark.unit, pytest.mark.whitebox, pytest.mark.regression]


def _report_data():
    """构造一份结构完整、可驱动三格式生成器的 fixture 数据。"""
    return {
        "meta": {
            "period": "week", "start_date": "2026-08-10", "end_date": "2026-08-16",
            "timed_free": {"hy3": "2026-08-31"},
        },
        "summary": {
            "active_day_count": 5,
            "active_days": ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"],
            "total_sessions": 12, "total_traces": 40,
            "total_automation_runs": 3, "successful_automation_runs": 3,
            "total_outputs": 2, "skills_used": 4,
            "total_tokens": 1_000_000, "total_effective_tokens": 900_000,
            "total_input_tokens": 600_000, "total_output_tokens": 400_000,
            "total_cached_tokens": 100_000,
            "total_cost": 1.2, "total_effective_cost": 1.0,
            "total_input_cost": 0.7, "total_output_cost": 0.5,
            "task_type_distribution": {"代码生成": 500000, "问答": 300000, "其他": 100000},
        },
        "daily_tokens": {
            "2026-08-10": {"total": 200000, "effective": 180000, "effective_cost": 0.2},
            "2026-08-11": {"total": 220000, "effective": 200000, "effective_cost": 0.3},
        },
        "skill_usage": {"skills": {
            "ai-weekly": {"usage_count_in_range": 2, "last_used": "2026-08-11"},
            "office-token-booster": {"usage_count_in_range": 1, "last_used": "2026-08-12"},
        }},
        "outputs": [{"name": "report.md", "size": 1234}],
        "automation_runs": [{"name": "日报", "status": "success"}],
        "traces": [
            {"exec_model": "glm-5.2", "raw_model": "glm-5.2",
             "input_tokens": 600000, "output_tokens": 400000},
        ],
        "model_stats": [
            {"model": "glm-5.2", "calls": 10, "effective_tokens": 500000, "effective_cost": 0.8,
             "input_tokens": 300000, "output_tokens": 200000,
             "unit_price_input": 8.0, "unit_price_output": 28.0, "configured": True},
            {"model": "hy3", "calls": 5, "effective_tokens": 300000, "effective_cost": 0.0,
             "input_tokens": 200000, "output_tokens": 100000,
             "unit_price_input": 0.0, "unit_price_output": 0.0, "configured": True, "timed_free": True},
        ],
        "model_exec_stats": [
            {"model": "glm-5.2", "calls": 10, "effective_tokens": 500000,
             "effective_cost": 0.8, "input_tokens": 300000, "output_tokens": 200000,
             "unit_price_input": 8.0, "unit_price_output": 28.0, "configured": True},
        ],
        "session_stats": {"rows": [], "buckets": []},
        "session_credits": [],
        "sessions": [],
        "cost_anomalies": {},
        "savings_insights": {},
    }


# ── GLM-5.2 家族合并 ────────────────────────────────────────────────────────

def _fam_row(model, calls, tokens, cost):
    return {"model": model, "calls": calls, "effective_tokens": tokens,
            "effective_cost": cost, "input_tokens": tokens // 2, "output_tokens": tokens // 2,
            "unit_price_input": 8.0, "unit_price_output": 28.0, "configured": True}


@allure.feature("报告生成层（Report Generation）")
@allure.story("GLM-5.2 家族合并")
@allure.title("merge_glm52_family 将三形态合并为单行并正确求和")
@allure.severity(allure.severity_level.CRITICAL)
def test_merge_glm52_family_merges(report_module):
    stats = [
        _fam_row("glm-5.2", 1, 100, 0.79),
        _fam_row("glm-5.2-x", 2, 200, 1.00),
        _fam_row("glm-5.2x", 3, 300, 1.50),
        _fam_row("hy3", 5, 999, 0.0),
    ]
    out = report_module.merge_glm52_family(stats)
    models = [m["model"] for m in out]
    assert "glm-5.2" in models
    assert "glm-5.2-x" not in models and "glm-5.2x" not in models, "家族变体不应单独成行"
    merged = next(m for m in out if m["model"] == "glm-5.2")
    assert merged["calls"] == 6, f"calls 应求和=6，实得 {merged['calls']}"
    assert merged["effective_tokens"] == 600, f"tokens 应求和=600，实得 {merged['effective_tokens']}"
    assert abs(merged["effective_cost"] - 3.29) < 1e-9, f"cost 应求和=3.29，实得 {merged['effective_cost']}"
    assert any(m["model"] == "hy3" for m in out), "非家族模型 hy3 应保留"


@allure.feature("报告生成层（Report Generation）")
@allure.story("GLM-5.2 家族合并")
@allure.title("merge_glm52_family 无家族成员时原样返回")
@allure.severity(allure.severity_level.NORMAL)
def test_merge_glm52_family_no_family(report_module):
    stats = [_fam_row("hy3", 5, 999, 0.0), _fam_row("deepseek-v4-flash", 2, 100, 0.1)]
    out = report_module.merge_glm52_family(stats)
    assert [m["model"] for m in out] == ["hy3", "deepseek-v4-flash"]


@allure.feature("报告生成层（Report Generation）")
@allure.story("GLM-5.2 家族合并")
@allure.title("merge_glm52_family 在列表为空时不崩")
@allure.severity(allure.severity_level.NORMAL)
def test_merge_glm52_family_empty(report_module):
    assert report_module.merge_glm52_family([]) == []


# ── HTML 转义（XSS 防御） ───────────────────────────────────────────────────

@allure.feature("报告生成层（Report Generation）")
@allure.story("HTML 转义（XSS 防御）")
@allure.title("_esc 转义 < > & \" 防止注入")
@allure.severity(allure.severity_level.CRITICAL)
def test_esc_escapes_html(report_module):
    evil = '<script>alert(1)</script>'
    out = report_module._esc(evil)
    assert "<script>" not in out, "原始标签不得出现在转义结果中"
    assert "&lt;script&gt;" in out
    assert report_module._esc('"') == "&quot;"
    assert report_module._esc("&") == "&amp;"


# ── 图表构造器空/退化数据 ───────────────────────────────────────────────────

@allure.feature("报告生成层（Report Generation）")
@allure.story("图表构造器（空/退化数据）")
@allure.title("build_donut_chart 空数据返回空串（不崩）")
@allure.severity(allure.severity_level.NORMAL)
def test_build_donut_chart_empty(report_module):
    assert report_module.build_donut_chart([], value_key="effective_tokens") == ""


@allure.feature("报告生成层（Report Generation）")
@allure.story("图表构造器（空/退化数据）")
@allure.title("build_donut_chart 有数据时返回内联 SVG")
@allure.severity(allure.severity_level.NORMAL)
def test_build_donut_chart_with_data(report_module):
    stats = [{"task_type": "代码生成", "effective_tokens": 500000},
             {"task_type": "问答", "effective_tokens": 300000}]
    svg = report_module.build_donut_chart(stats, value_key="effective_tokens")
    assert "<svg" in svg and "</svg>" in svg


@allure.feature("报告生成层（Report Generation）")
@allure.story("图表构造器（空/退化数据）")
@allure.title("build_model_cost_chart 空数据返回空串（不崩）")
@allure.severity(allure.severity_level.NORMAL)
def test_build_model_cost_chart_empty(report_module):
    assert report_module.build_model_cost_chart([]) == ""


@allure.feature("报告生成层（Report Generation）")
@allure.story("图表构造器（空/退化数据）")
@allure.title("build_model_cost_chart 有数据时返回条形图容器")
@allure.severity(allure.severity_level.NORMAL)
def test_build_model_cost_chart_with_data(report_module):
    stats = [{"model": "glm-5.2", "effective_cost": 0.8}]
    out = report_module.build_model_cost_chart(stats)
    assert "chart-bars" in out


# ── 三格式生成器跑通 ───────────────────────────────────────────────────────

@allure.feature("报告生成层（Report Generation）")
@allure.story("三格式报告生成")
@allure.title("generate_markdown_report 在 fixture 上跑通且含关键章节")
@allure.severity(allure.severity_level.CRITICAL)
def test_generate_markdown_report_smoke(report_module):
    md = report_module.generate_markdown_report(_report_data())
    assert isinstance(md, str) and len(md) > 200
    assert "# Workbuddy使用情况报告" in md
    assert "## 一、概览统计" in md
    assert "## 二、Token 消耗可视化" in md


@allure.feature("报告生成层（Report Generation）")
@allure.story("三格式报告生成")
@allure.title("generate_html_report 在 fixture 上跑通且为合法 HTML")
@allure.severity(allure.severity_level.CRITICAL)
def test_generate_html_report_smoke(report_module):
    html = report_module.generate_html_report(_report_data())
    assert isinstance(html, str) and len(html) > 200
    assert "<html" in html.lower()
    assert "Workbuddy使用情况报告" in html


@allure.feature("报告生成层（Report Generation）")
@allure.story("三格式报告生成")
@allure.title("generate_json_report 返回可解析 JSON 且含 summary/meta")
@allure.severity(allure.severity_level.CRITICAL)
def test_generate_json_report_smoke(report_module):
    out = report_module.generate_json_report(_report_data())
    assert isinstance(out, str), "json 报告应为字符串"
    parsed = json.loads(out)
    assert "summary" in parsed and "meta" in parsed


# ── XSS 回归：恶意模型名进入 HTML 报告被转义 ───────────────────────────────

@allure.feature("报告生成层（Report Generation）")
@allure.story("XSS 回归")
@allure.title("恶意模型名进入 HTML 报告被转义，不出现原始标签")
@allure.severity(allure.severity_level.BLOCKER)
def test_html_report_escapes_malicious_model(report_module):
    data = _report_data()
    evil = '<img src=x onerror=alert(1)>'
    data["model_stats"] = [{
        "model": evil, "calls": 1, "effective_tokens": 10, "effective_cost": 0.0,
        "input_tokens": 5, "output_tokens": 5,
        "unit_price_input": None, "unit_price_output": None, "configured": False,
    }]
    html = report_module.generate_html_report(data)
    assert evil not in html, "恶意模型名必须以转义形式出现，不得保留原始标签"
    assert "&lt;img" in html.lower(), "应出现转义后的 &lt;img"
