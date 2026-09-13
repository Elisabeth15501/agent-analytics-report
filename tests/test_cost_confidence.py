# -*- coding: utf-8 -*-
"""L1 · 成本置信度（v1.5.2）— pytest + Allure 双可视化。

背景：静态价表（pricing.json）在数学上无法表达「服务端时段减免 / 用户免费额度」，
导致 hy4-preview（夜间免费）等模型的估算严重高于实际账单（实测 12 次中 11 次积分 0）。
v1.5.2 不试图让价表变准，而是把「你在看哪一级成本」显式化：
  - L2 估算（默认，未导入官方导出）→ 报告顶部警告 + 模型标 ⚠ + 剔除出「最贵模型」结论
  - L1 真值（导入官方导出后）→ 横幅自动切换（v1.6.0 落地，此处先锁定渲染契约）

覆盖：
  - pricing.json 的 low_confidence 段被采集层读入 meta
  - 报告顶部横幅（MD / HTML）出现且标注 L2 估算
  - 成本图表对低置信度模型标 ⚠ 并附脚注
  - 低置信度模型不进「最贵模型」，但被剔除者必须显式列出（不能悄悄消失）
  - 无 low_confidence 配置时零噪音（不新增任何标记）——零回归保障

全部白盒 / 回归，使用内置 fixture，不依赖真实数据。
"""

import json

import pytest

import allure

pytestmark = [pytest.mark.unit, pytest.mark.whitebox, pytest.mark.regression]

SKILL_SCRIPTS = None  # 由 conftest 的 report_module / collector_module 提供


def _data_with_lc(low_confidence=None):
    """构造含低置信度模型的 fixture；low_confidence=None 表示未配置。"""
    meta = {"period": "week", "start_date": "2026-09-08", "end_date": "2026-09-14"}
    if low_confidence is not None:
        meta["low_confidence"] = low_confidence
    return {
        "meta": meta,
        "summary": {
            "active_day_count": 2, "active_days": ["2026-09-12", "2026-09-13"],
            "total_sessions": 3, "total_traces": 20,
            "total_automation_runs": 0, "successful_automation_runs": 0,
            "total_outputs": 0, "skills_used": 1,
            "total_tokens": 100_000, "total_effective_tokens": 90_000,
            "total_input_tokens": 60_000, "total_output_tokens": 40_000,
            "total_cached_tokens": 10_000,
            "total_cost": 20.0, "total_effective_cost": 16.0,
        },
        "daily_tokens": {},
        "model_stats": [
            # 低置信度：估算偏差大（如 hy4-preview 夜间免费）
            {"model": "hy4-preview", "calls": 12, "effective_tokens": 50000,
             "input_tokens": 40000, "output_tokens": 10000,
             "unit_price_input": 6.0, "unit_price_output": 18.0,
             "effective_cost": 7.68, "configured": True},
            # 低置信度且金额最高
            {"model": "glm-5.3-flash", "calls": 31, "effective_tokens": 40000,
             "input_tokens": 30000, "output_tokens": 10000,
             "unit_price_input": 0.8, "unit_price_output": 2.8,
             "effective_cost": 15.86, "configured": True},
            # 可信模型（对照组）
            {"model": "hy3", "calls": 90, "effective_tokens": 10000,
             "input_tokens": 8000, "output_tokens": 2000,
             "unit_price_input": 1.0, "unit_price_output": 4.0,
             "effective_cost": 1.52, "configured": True},
        ],
        "model_exec_stats": [],
        "traces": [],
        "skill_usage": {"skills": []},
        "outputs": [],
        "automation_runs": [],
    }


LC_MAP = {
    "hy4-preview": "夜间 23:00–08:00 免费，静态价表按全时段计费 → 严重高估",
    "glm-5.3-flash": "5 折促销 2026-09-09 到期，跨期报告会高估促销期成本",
}


@allure.feature("成本置信度")
@allure.story("定价配置加载")
def test_pricing_json_has_low_confidence_section():
    """pricing.json 必须含 low_confidence 段，且 hy4-preview 被标注（实测高估最严重）。"""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "scripts" / "pricing.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "low_confidence" in data, "pricing.json 缺少 low_confidence 段（v1.5.2）"
    lc = data["low_confidence"]
    assert "hy4-preview" in lc, "hy4-preview 夜间免费导致估算严重偏高，必须标为低置信度"
    assert "夜间" in lc["hy4-preview"], "原因说明应写清「夜间免费」这一根因，便于用户理解"


@allure.feature("成本置信度")
@allure.story("采集层透传")
def test_collector_exposes_low_confidence_in_meta(collector_module):
    """采集器把 pricing.json 的 low_confidence 透传到 meta，供渲染层使用（渲染层不硬编码）。"""
    assert hasattr(collector_module, "LOW_CONFIDENCE"), "ca_core 未导出 LOW_CONFIDENCE"
    # 来自真实 pricing.json：至少含 hy4-preview
    assert "hy4-preview" in collector_module.LOW_CONFIDENCE


@allure.feature("成本置信度")
@allure.story("报告横幅")
def test_md_banner_shows_l2_estimate(report_module):
    """未导入官方导出时，MD 报告顶部必须出现「L2 估算」横幅。"""
    out = report_module.generate_markdown_report(_data_with_lc(LC_MAP))
    assert "成本口径：L2 估算" in out
    assert "未导入官方用量导出" in out
    # 低置信度模型须逐条列出原因
    assert "hy4-preview" in out and "夜间" in out


@allure.feature("成本置信度")
@allure.story("报告横幅")
def test_html_banner_shows_l2_estimate(report_module):
    """HTML 报告同样出现横幅，且用 disclaimer-box 容器承载。"""
    out = report_module.generate_html_report(_data_with_lc(LC_MAP))
    assert "成本口径：L2 估算" in out
    assert "disclaimer-box" in out


@allure.feature("成本置信度")
@allure.story("L1 真值切换")
def test_banner_switches_to_l1_when_official(report_module):
    """meta.cost_source='official' 时横幅应切换为 L1 真值（为 v1.6.0 预留渲染契约）。"""
    data = _data_with_lc(LC_MAP)
    data["meta"]["cost_source"] = "official"
    out = report_module.generate_markdown_report(data)
    assert "成本口径：L1 真值" in out
    assert "L2 估算" not in out


@allure.feature("成本置信度")
@allure.story("图表标记")
def test_cost_chart_marks_low_confidence(report_module):
    """成本图表给低置信度模型标 ⚠，并附图下脚注。"""
    stats = _data_with_lc(LC_MAP)["model_stats"]
    chart = report_module.build_model_cost_chart_md(stats, low_conf_map=LC_MAP)
    assert "⚠ glm-5.3-flash" in chart, "低置信度模型应在行首标 ⚠"
    assert "  hy3" in chart, "可信模型不应带 ⚠ 标记"
    assert "低置信度估算" in chart, "图表必须附免责脚注"


@allure.feature("成本置信度")
@allure.story("最贵模型结论")
def test_low_confidence_excluded_from_top_cost_but_disclosed(report_module):
    """低置信度模型不进「最贵模型」结论，但被剔除者必须显式列出（不能悄悄消失）。"""
    out = report_module.generate_markdown_report(_data_with_lc(LC_MAP))
    # 结论行以 💸 **最贵模型** 开头，指向可信模型（hy3 是唯一可信的 priced 模型）
    top_line = next(ln for ln in out.splitlines() if "💸 **最贵模型**" in ln)
    assert "`hy3`" in top_line, f"结论应落在可信模型上，实际：{top_line}"
    assert "glm-5.3-flash" not in top_line, "低置信度模型不应出现在结论标题里"
    # 被剔除的低置信度模型不得消失
    assert "未计入本结论的低置信度模型" in out
    assert "glm-5.3-flash" in out and "¥15.86" in out, "剔除项须带金额显式披露"


@allure.feature("成本置信度")
@allure.story("零回归")
def test_no_low_confidence_config_renders_no_noise(report_module):
    """未配置 low_confidence 时，报告不得出现任何 ⚠ 低置信度标记（零噪音）。"""
    out = report_module.generate_markdown_report(_data_with_lc(None))
    assert "低置信度" not in out, "无配置时不应凭空产生低置信度提示"
    assert "成本口径：L2 估算" in out, "L2 横幅本身仍应出现（默认路径就是估算）"


@allure.feature("成本置信度")
@allure.story("零回归")
def test_cost_chart_without_map_is_unchanged(report_module):
    """不传 low_conf_map 时图表行为与 v1.5.1 一致（无标记、无脚注）。"""
    stats = _data_with_lc(LC_MAP)["model_stats"]
    chart = report_module.build_model_cost_chart_md(stats)
    assert "⚠" not in chart
    assert "低置信度" not in chart
