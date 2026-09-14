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
    """meta.cost_source='official' **且**确有官方数据时，横幅切换为 L1 真值。

    v1.6.0 收紧了 v1.5.2 预留的契约：光有 cost_source 标记不够，必须真的拿到
    official_usage 数据。否则会渲染出「L1 真值（未导入官方用量导出）」这种自相
    矛盾的横幅——宁可回落 L2 的诚实估算，也不要一个假的真值。
    """
    data = _data_with_lc(LC_MAP)
    data["meta"]["cost_source"] = "official"
    data["official_usage"] = {
        "by_model": [{"name": "hy4-preview", "requests": 2, "credits": 43.4,
                      "free_requests": 1, "paid_requests": 1, "avg_credits": 21.7}],
        "totals": {"requests": 2, "credits": 43.4, "free_requests": 1,
                   "paid_requests": 1, "models": 1, "clients": 1},
        "meta": {"window": {"first": "2026-09-12", "last": "2026-09-14"}},
    }
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


# ---------------------------------------------------------------------------
# v1.7.0 · A2 机读偏差方向（low_confidence_bias → ⚠↑ / ⚠↓）
# ---------------------------------------------------------------------------

BIAS_MAP = {"hy4-preview": "over", "glm-5.3-flash": "over", "deepseek-v4.1-flash": "under"}


def _data_with_bias(low_confidence=None, bias=None):
    """在 _data_with_lc 基础上额外注入 low_confidence_bias（A2 机读方向）。"""
    data = _data_with_lc(low_confidence)
    if bias is not None:
        data["meta"]["low_confidence_bias"] = bias
    return data


@allure.feature("成本置信度")
@allure.story("偏差方向")
def test_pricing_json_has_low_confidence_bias_section():
    """pricing.json 必须含 low_confidence_bias 段（A2），且取值属于 over/under/mixed。"""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "scripts" / "pricing.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "low_confidence_bias" in data, "pricing.json 缺少 low_confidence_bias 段（v1.7.0 A2）"
    bias = data["low_confidence_bias"]
    # 与 low_confidence 同键（除 _comment 外）
    lc_keys = {k for k in data["low_confidence"] if not k.startswith("_")}
    bias_keys = {k for k in bias if not k.startswith("_")}
    assert bias_keys == lc_keys, f"low_confidence_bias 键应与 low_confidence 对齐，差异：{bias_keys ^ lc_keys}"
    for k, v in bias.items():
        if k.startswith("_"):
            continue
        assert v in ("over", "under", "mixed"), f"{k} 的偏差方向 {v!r} 不合法"
    # 实测已知方向
    assert bias["hy4-preview"] == "over", "hy4-preview 夜间免费 → 静态价表高估"
    assert bias["deepseek-v4.1-flash"] == "under", "deepseek-v4.1-flash 峰谷 → 低估"


@allure.feature("成本置信度")
@allure.story("偏差方向")
def test_collector_exposes_low_confidence_bias(collector_module):
    """采集器把 low_confidence_bias 透传到 meta（渲染层不硬编码方向）。"""
    assert hasattr(collector_module, "LOW_CONFIDENCE_BIAS"), "ca_core 未导出 LOW_CONFIDENCE_BIAS"
    assert collector_module.LOW_CONFIDENCE_BIAS.get("hy4-preview") == "over"


@allure.feature("成本置信度")
@allure.story("偏差方向")
def test_bias_arrow_in_cost_chart(report_module):
    """A2：高估模型渲染 ⚠↑，低估模型渲染 ⚠↓。"""
    stats = _data_with_lc(LC_MAP)["model_stats"]
    chart = report_module.build_model_cost_chart_md(stats, low_conf_map=LC_MAP, low_conf_bias_map=BIAS_MAP)
    assert "⚠↑ glm-5.3-flash" in chart, "高估模型应带 ⚠↑"
    assert "⚠↑ hy4-preview" in chart, "高估模型应带 ⚠↑"
    assert "箭头表示偏差方向" in chart, "图表脚注须解释箭头含义"


@allure.feature("成本置信度")
@allure.story("偏差方向")
def test_under_bias_renders_down_arrow(report_module):
    """A2：低估模型命中 ⚠↓（deepseek-v4.1-flash 峰谷低估）。"""
    data = _data_with_bias({"deepseek-v4.1-flash": "峰谷计价，静态价表低估"}, BIAS_MAP)
    data["model_stats"] = [
        {"model": "deepseek-v4.1-flash", "calls": 32, "effective_tokens": 30000,
         "input_tokens": 20000, "output_tokens": 10000,
         "unit_price_input": 0.8, "unit_price_output": 2.8,
         "effective_cost": 9.9, "configured": True},
    ]
    out = report_module.generate_markdown_report(data)
    assert "⚠↓" in out, "低估模型应渲染 ⚠↓"


@allure.feature("成本置信度")
@allure.story("偏差方向")
def test_no_bias_map_falls_back_to_plain_warn(report_module):
    """A2 零回归：未提供 bias 映射时，图表行内退回纯 ⚠（不画箭头）。"""
    stats = _data_with_lc(LC_MAP)["model_stats"]
    chart = report_module.build_model_cost_chart_md(stats, low_conf_map=LC_MAP)
    # 行首标记仍是纯 ⚠（无方向箭头），与 v1.6.2 行为一致
    assert "⚠ hy4-preview" in chart
    assert "⚠ glm-5.3-flash" in chart
    assert "⚠↑ " not in chart and "⚠↓ " not in chart, "无 bias 映射时行内不应出现方向箭头"
    # 但低置信度脚注本身仍应出现（那是 v1.5.2 的既有行为）
    assert "低置信度估算" in chart


# ---------------------------------------------------------------------------
# v1.7.0 · A3 低置信度最贵模型高位告警
# ---------------------------------------------------------------------------

@allure.feature("成本置信度")
@allure.story("高位告警")
def test_md_high_alert_when_low_conf_is_most_expensive(report_module):
    """A3：低置信度模型恰为最贵（Top-3）时，§3 顶部必须给出高位告警横幅。"""
    # fixture 中 glm-5.3-flash(¥15.86) 是最贵，且为低置信度
    out = report_module.generate_markdown_report(_data_with_bias(LC_MAP, BIAS_MAP))
    assert "高位告警" in out, "低置信度模型为最贵时必须出现高位告警"
    assert "不要据此切换模型" in out, "告警须明确劝阻据此切换模型"
    alert_line = next(ln for ln in out.splitlines() if "高位告警" in ln)
    assert "glm-5.3-flash" in alert_line
    assert "⚠↑" in alert_line, "告警须带偏差方向"


@allure.feature("成本置信度")
@allure.story("高位告警")
def test_html_high_alert_when_low_conf_is_most_expensive(report_module):
    """A3：HTML 版同样给出高位告警（warn-box 容器）。"""
    out = report_module.generate_html_report(_data_with_bias(LC_MAP, BIAS_MAP))
    assert "高位告警" in out
    assert "warn-box" in out


@allure.feature("成本置信度")
@allure.story("高位告警")
def test_no_high_alert_when_low_conf_is_insignificant(report_module):
    """A3 零回归：低置信度模型占比微不足道（<5%）时，不弹高位告警（避免噪音）。

    即便它排到「第 2 贵」，¥0.01 vs ¥99.00 也毫无告警价值。
    """
    data = _data_with_bias(LC_MAP, BIAS_MAP)
    data["model_stats"] = [
        {"model": "glm-5.3-flash", "calls": 1, "effective_tokens": 1000,
         "input_tokens": 800, "output_tokens": 200,
         "unit_price_input": 0.8, "unit_price_output": 2.8,
         "effective_cost": 0.01, "configured": True},
        {"model": "hy3", "calls": 900, "effective_tokens": 400000,
         "input_tokens": 300000, "output_tokens": 100000,
         "unit_price_input": 1.0, "unit_price_output": 4.0,
         "effective_cost": 99.0, "configured": True},
    ]
    alert = report_module._lc_top_alert(data)
    assert alert is None, f"占比 0.01% 的低置信度模型不应触发高位告警，实际：{alert}"


@allure.feature("成本置信度")
@allure.story("高位告警")
def test_no_high_alert_when_low_conf_is_cheap_but_meaningful(report_module):
    """A3 边界：低置信度模型有分量但排名掉到 Top-3 之外时，同样不告警。"""
    data = _data_with_bias(LC_MAP, BIAS_MAP)
    data["model_stats"] = [
        # 3 个可信模型都比低置信度模型贵 → 低置信度掉到第 4 名
        {"model": "hy3", "calls": 900, "effective_tokens": 400000,
         "input_tokens": 300000, "output_tokens": 100000,
         "unit_price_input": 1.0, "unit_price_output": 4.0,
         "effective_cost": 99.0, "configured": True},
        {"model": "claude-sonnet-4-20250514", "calls": 100, "effective_tokens": 50000,
         "input_tokens": 40000, "output_tokens": 10000,
         "unit_price_input": 3.0, "unit_price_output": 15.0,
         "effective_cost": 60.0, "configured": True},
        {"model": "gpt-5", "calls": 80, "effective_tokens": 40000,
         "input_tokens": 30000, "output_tokens": 10000,
         "unit_price_input": 2.0, "unit_price_output": 8.0,
         "effective_cost": 40.0, "configured": True},
        {"model": "glm-5.3-flash", "calls": 31, "effective_tokens": 40000,
         "input_tokens": 30000, "output_tokens": 10000,
         "unit_price_input": 0.8, "unit_price_output": 2.8,
         "effective_cost": 10.0, "configured": True},
    ]
    alert = report_module._lc_top_alert(data)
    assert alert is None, f"第 4 贵的低置信度模型不在 Top-3，不应告警，实际：{alert}"


# ---------------------------------------------------------------------------
# v1.7.0 · A1 省钱杠杆过滤低置信度模型
# ---------------------------------------------------------------------------

@allure.feature("成本置信度")
@allure.story("省钱杠杆")
def test_savings_insights_excludes_low_confidence(collector_module):
    """A1：低置信度模型不得进入「可省钱」杠杆（否则会诱导用户从折扣模型迁走）。

    build_savings_insights 读的是 model_exec_stats（执行维度）且依赖模块级
    LOW_CONFIDENCE，因此用 collector_module 里导出的同一份配置构造输入。
    """
    assert hasattr(collector_module, "build_savings_insights"), "ca_aggregate 未导出 build_savings_insights"
    exec_stats = [
        # 低置信度：促销模型，单价高到会命中「换更便宜模型」
        {"model": "glm-5.3-flash", "calls": 31, "effective_tokens": 40000,
         "input_tokens": 30000, "output_tokens": 10000,
         "unit_price_input": 0.8, "unit_price_output": 2.8,
         "effective_cost": 15.86, "configured": True},
        # 低置信度：时段免费模型
        {"model": "hy4-preview", "calls": 12, "effective_tokens": 50000,
         "input_tokens": 40000, "output_tokens": 10000,
         "unit_price_input": 6.0, "unit_price_output": 18.0,
         "effective_cost": 7.68, "configured": True},
        # 可信对照组
        {"model": "hy3", "calls": 90, "effective_tokens": 10000,
         "input_tokens": 8000, "output_tokens": 2000,
         "unit_price_input": 1.0, "unit_price_output": 4.0,
         "effective_cost": 1.52, "configured": True},
    ]
    insights = collector_module.build_savings_insights(exec_stats)
    blob = json.dumps(insights, ensure_ascii=False) if not isinstance(insights, str) else insights
    assert "hy4-preview" not in blob, "hy4-preview 为时段免费低置信度模型，不应出现在省钱建议里"
    assert "glm-5.3-flash" not in blob, "glm-5.3-flash 为促销低置信度模型，不应出现在省钱建议里"


@allure.feature("成本置信度")
@allure.story("省钱杠杆")
def test_savings_section_mentions_discount_hint(report_module):
    """A1：§4.4 顶部提示折扣/时段模型，替代原来的「迁走」建议。

    注：§4.4 由 build_cost_analysis_section 渲染，该函数在 session_stats.rows 为空时
    直接 return []（无会话就不渲染成本深剖章节），所以 fixture 必须带上会话数据。
    """
    data = _data_with_bias(LC_MAP, BIAS_MAP)
    data["session_stats"] = {
        "rows": [{"title": "生成周报", "task_type": "文档写作", "effective_cost": 15.87,
                  "effective_tokens": 100000, "calls": 133, "models": ["glm-5.3-flash", "hy3"]}],
        "buckets": [{"label": "¥10-20", "count": 1, "cost": 15.87}],
    }
    data["savings_insights"] = {"items": [], "total_estimated_monthly_save": 0.0}
    out = report_module.generate_markdown_report(data)
    assert "折扣 / 时段模型提示" in out, "省钱章节须提示折扣/时段模型"
    assert "不就此给出「迁走」建议" in out, "须明确不推荐迁走"
