# -*- coding: utf-8 -*-
"""v1.7.0 新特性回归 — pytest + Allure 双可视化。

覆盖本轮落地的两组改动：

F17 双源对账（P1 / P2）
  - P1 请求数反推：§1 概览渲染「估算请求数（generation 反推）」，L1 下额外给
    「估算请求数 ↔ 官方请求数」倍率，消除 11.9x 粒度差被读成「用量暴涨」。
  - P2 路由别名解析：auto / fast-model / balanced-model / extreme-model 不记录落地
    底层模型，报告必须显式说明「单独成组、不计入最贵模型与省钱建议」。

低置信度最贵模型（Phase A）
  - A1：省钱杠杆过滤低置信度模型（不推荐从折扣 / 时段模型迁走）。
  - A2：pricing.json 的 low_confidence_bias 驱动 ⚠↑ / ⚠↓ 偏差方向标记。
  - A3：低置信度模型恰为最贵（Top-3 且占比 ≥5%）时，§3 / §4.4 顶部高位告警。

全部白盒 / 回归，使用内置 fixture，不依赖真实数据。
"""

import json
import sys
from pathlib import Path

import pytest

import allure

pytestmark = [pytest.mark.unit, pytest.mark.whitebox, pytest.mark.regression]

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# --------------------------------------------------------------------------
# fixture
# --------------------------------------------------------------------------

LC_MAP = {
    "hy4-preview": "夜间 23:00–08:00 免费，静态价表按全时段计费 → 严重高估",
    "glm-5.3-flash": "5 折促销 2026-09-09 到期，跨期报告会高估促销期成本",
}

LC_BIAS_MAP = {
    "hy4-preview": "over",       # 夜间免费被全时段计费 → 高估
    "glm-5.3-flash": "over",     # 促销到期 → 高估
}


def _data(low_confidence=None, low_confidence_bias=None):
    """含低置信度模型的报告 fixture；传 None 表示未配置（零噪音基线）。"""
    meta = {"period": "week", "start_date": "2026-09-08", "end_date": "2026-09-14"}
    if low_confidence is not None:
        meta["low_confidence"] = low_confidence
    if low_confidence_bias is not None:
        meta["low_confidence_bias"] = low_confidence_bias
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
            "total_cost": 25.0, "total_effective_cost": 25.06,
        },
        "daily_tokens": {},
        "model_stats": [
            {"model": "hy4-preview", "calls": 12, "effective_tokens": 50000,
             "input_tokens": 40000, "output_tokens": 10000,
             "unit_price_input": 6.0, "unit_price_output": 18.0,
             "effective_cost": 7.68, "configured": True},
            # 低置信度且金额最高 → A3 高位告警应命中（第 1 贵，占比 ~63%）
            {"model": "glm-5.3-flash", "calls": 31, "effective_tokens": 40000,
             "input_tokens": 30000, "output_tokens": 10000,
             "unit_price_input": 0.8, "unit_price_output": 2.8,
             "effective_cost": 15.86, "configured": True},
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


def _data_with_router():
    """加一个路由别名模型，用于 F17 · P2 的别名说明断言。"""
    d = _data(LC_MAP, LC_BIAS_MAP)
    d["model_stats"].append({
        "model": "auto", "calls": 5, "effective_tokens": 5000,
        "input_tokens": 4000, "output_tokens": 1000,
        "unit_price_input": 1.0, "unit_price_output": 4.0,
        "effective_cost": 0.5, "configured": True, "is_router": True,
    })
    return d


# --------------------------------------------------------------------------
# A2 · 机读偏差方向
# --------------------------------------------------------------------------

@allure.feature("低置信度最贵模型")
@allure.story("A2 偏差方向")
def test_pricing_json_has_low_confidence_bias_section():
    """pricing.json 必须含 low_confidence_bias 段，且方向值与 low_confidence 同键。"""
    p = SCRIPTS_DIR / "pricing.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "low_confidence_bias" in data, "pricing.json 缺少 low_confidence_bias 段（v1.7.0 A2）"
    lc = data["low_confidence"]
    bias = data["low_confidence_bias"]
    # 方向段应覆盖（至少不遗漏）最典型的两个方向
    assert bias.get("hy4-preview") == "over", "hy4-preview 夜间免费被全时段计费，方向应为 over"
    assert bias.get("deepseek-v4.1-flash") == "under", "峰谷取空闲价 → 高峰被低估，方向应为 under"
    # 与 low_confidence 同键（允许 bias 段有 _comment 元数据键）
    missing = [k for k in lc if not k.startswith("_") and k not in bias]
    assert not missing, f"这些低置信度模型缺偏差方向：{missing}"


@allure.feature("低置信度最贵模型")
@allure.story("A2 偏差方向")
def test_bias_is_loaded_from_pricing_json():
    """ca_core 必须真的从 pricing.json 载入 bias（而非停在空种子值）。

    回归点：_load_pricing_config() 在模块早期就用 LOW_CONFIDENCE_BIAS 初始化 cfg，
    若只在 _PRICING 生成后才赋值会 NameError；本用例同时守住「值确实被合并进来」。
    """
    import ca_core
    assert ca_core.low_confidence_bias("hy4-preview") == "over"
    assert ca_core.low_confidence_bias("deepseek-v4.1-flash") == "under"
    assert ca_core.low_confidence_bias("hy3") == "", "可信模型不应有偏差方向"


@allure.feature("低置信度最贵模型")
@allure.story("A2 偏差方向")
def test_arrow_mapping(report_module):
    """⚠↑ = 高估 / ⚠↓ = 低估 / ⚠ = 方向不明。"""
    assert report_module._lc_arrow("over") == "⚠↑"
    assert report_module._lc_arrow("under") == "⚠↓"
    assert report_module._lc_arrow("mixed") == "⚠"
    assert report_module._lc_arrow("") == "⚠"


@allure.feature("低置信度最贵模型")
@allure.story("A2 偏差方向")
def test_report_renders_bias_arrow(report_module):
    """报告里低置信度模型带方向箭头，并附图例说明。"""
    out = report_module.generate_markdown_report(_data(LC_MAP, LC_BIAS_MAP))
    assert "⚠↑" in out, "over（高估）应渲染 ⚠↑"
    assert "箭头表示偏差方向" in out, "必须给出图例，否则 ⚠↑/⚠↓ 无法理解"


@allure.feature("低置信度最贵模型")
@allure.story("A2 偏差方向")
def test_under_bias_renders_down_arrow(report_module):
    """低估方向渲染 ⚠↓（与高估区分开）。"""
    d = _data(LC_MAP, LC_BIAS_MAP)
    d["model_stats"].append({
        "model": "deepseek-v4.1-flash", "calls": 8, "effective_tokens": 9000,
        "input_tokens": 7000, "output_tokens": 2000,
        "unit_price_input": 1.0, "unit_price_output": 4.0,
        "effective_cost": 0.9, "configured": True,
    })
    d["meta"]["low_confidence"]["deepseek-v4.1-flash"] = "峰谷双档取空闲价 → 高峰被低估"
    out = report_module.generate_markdown_report(d)
    assert "⚠↓" in out, "under（低估）应渲染 ⚠↓"


# --------------------------------------------------------------------------
# A3 · 低置信度最贵模型高位告警
# --------------------------------------------------------------------------

@allure.feature("低置信度最贵模型")
@allure.story("A3 高位告警")
def test_lc_top_alert_fires_for_top1_low_confidence(report_module):
    """低置信度模型是最贵且占比够大时，_lc_top_alert 返回告警信息。"""
    alert = report_module._lc_top_alert(_data(LC_MAP, LC_BIAS_MAP))
    assert alert is not None, "glm-5.3-flash 是第 1 贵的低置信度模型，应触发告警"
    assert alert["model"] == "glm-5.3-flash"
    assert alert["rank"] == 1
    assert alert["arrow"] == "⚠↑"
    assert alert["share"] > 0.5


@allure.feature("低置信度最贵模型")
@allure.story("A3 高位告警")
def test_md_high_alert_banner(report_module):
    """MD 报告 §3 顶部出现高位告警，且明确「不要据此切换模型」。"""
    out = report_module.generate_markdown_report(_data(LC_MAP, LC_BIAS_MAP))
    assert "高位告警" in out
    assert "不要据此切换模型" in out, "告警必须给出行动建议，而不只是报数字"
    assert "glm-5.3-flash" in out


@allure.feature("低置信度最贵模型")
@allure.story("A3 高位告警")
def test_html_high_alert_banner(report_module):
    """HTML 报告用 warn-box 承载同一条告警（MD / HTML 双通道一致）。"""
    out = report_module.generate_html_report(_data(LC_MAP, LC_BIAS_MAP))
    assert "高位告警" in out
    assert "warn-box" in out


@allure.feature("低置信度最贵模型")
@allure.story("零回归")
def test_no_alert_without_low_confidence(report_module):
    """未配置 low_confidence 时不得凭空出现高位告警（零噪音）。"""
    out = report_module.generate_markdown_report(_data(None, None))
    assert "高位告警" not in out
    assert "⚠↑" not in out and "⚠↓" not in out


# --------------------------------------------------------------------------
# A1 · 省钱杠杆过滤低置信度模型
# --------------------------------------------------------------------------

@allure.feature("低置信度最贵模型")
@allure.story("A1 省钱建议过滤")
def test_savings_insights_excludes_low_confidence_model():
    """低置信度模型不进省钱建议（避免建议从折扣模型迁走反而多花钱）。

    glm-4.7 在 _CHEAPER_ALT 里有更便宜的替代，正常情况下会被建议；
    一旦把它标成低置信度，就必须从建议里消失。这是 A1 的核心契约。
    """
    import ca_aggregate

    stats = [{"model": "glm-4.7", "calls": 50, "effective_tokens": 100000,
              "input_tokens": 80000, "output_tokens": 20000,
              "effective_cost": 100.0, "configured": True}]

    # 控制组：未标低置信度时，glm-4.7 应被建议（证明下面的消失不是因为没替代项）
    base = ca_aggregate.build_savings_insights(stats)
    assert "glm-4.7" in [i["model"] for i in base["items"]], \
        "前提不成立：glm-4.7 本应被建议（有更便宜替代），请检查 _CHEAPER_ALT / pricing.json"

    # 实验组：标记为低置信度后应从建议中剔除
    original = ca_aggregate.low_confidence_reason
    ca_aggregate.low_confidence_reason = lambda name: "测试：低置信度" if name == "glm-4.7" else ""
    try:
        filtered = ca_aggregate.build_savings_insights(stats)
    finally:
        ca_aggregate.low_confidence_reason = original

    assert "glm-4.7" not in [i["model"] for i in filtered["items"]], \
        "低置信度模型不应出现在省钱建议里（A1）"


# --------------------------------------------------------------------------
# F17 · P1 请求数反推
# --------------------------------------------------------------------------

@allure.feature("F17 双源对账")
@allure.story("P1 请求数反推")
def test_md_overview_shows_estimated_request_count(report_module):
    """§1 概览渲染「估算请求数（generation 反推）」并声明是估算值。"""
    d = _data(None, None)
    d["summary"]["estimated_request_count"] = 137
    d["summary"]["request_estimate_gap_minutes"] = 15
    out = report_module.generate_markdown_report(d)
    assert "估算请求数（generation 反推）" in out
    assert "137" in out
    assert "口径与官方「请求数」不同" in out, "必须声明口径差异，否则会被当成真实请求数"


@allure.feature("F17 双源对账")
@allure.story("P1 请求数反推")
def test_md_overview_ratio_vs_official_in_l1(report_module):
    """L1（导入官方导出）下额外给出「估算 ↔ 官方」倍率，解释 11.9x 粒度差。"""
    d = _data(None, None)
    d["summary"]["estimated_request_count"] = 238
    d["summary"]["official_requests"] = 20
    d["meta"]["cost_source"] = "official"
    d["official_usage"] = {
        "by_model": [{"name": "hy3", "requests": 20, "credits": 100.0,
                      "free_requests": 0, "paid_requests": 20, "avg_credits": 5.0}],
        "totals": {"requests": 20, "credits": 100.0, "free_requests": 0,
                   "paid_requests": 20, "models": 1, "clients": 1},
        "meta": {"window": {"first": "2026-09-12", "last": "2026-09-14"}},
    }
    out = report_module.generate_markdown_report(d)
    assert "估算请求数 ↔ 官方请求数" in out
    assert "11.9x" in out, "238 / 20 = 11.9x，应渲染倍率"


@allure.feature("F17 双源对账")
@allure.story("P1 请求数反推")
def test_no_estimated_request_row_when_absent(report_module):
    """没有估算请求数时（旧数据）不渲染该行 —— 零回归。"""
    out = report_module.generate_markdown_report(_data(None, None))
    assert "估算请求数（generation 反推）" not in out


# --------------------------------------------------------------------------
# F17 · P2 路由别名解析
# --------------------------------------------------------------------------

@allure.feature("F17 双源对账")
@allure.story("P2 路由别名")
def test_router_alias_note_lists_aliases(report_module):
    """出现路由别名时，显式列出各别名并说明「不计入最贵模型与省钱建议」。"""
    out = report_module.generate_markdown_report(_data_with_router())
    for alias in ("auto", "fast-model", "balanced-model", "extreme-model"):
        assert f"`{alias}`" in out, f"应显式列出路由别名 {alias}"
    assert "不记录落到哪个底层模型" in out
    assert "不计入「最贵模型」与省钱建议等成本结论" in out
    assert "均价估算值" in out, "须说明其单价只是均价估算"


@allure.feature("F17 双源对账")
@allure.story("P2 路由别名")
def test_no_router_note_without_router(report_module):
    """没有路由别名时不渲染该说明 —— 零回归。"""
    out = report_module.generate_markdown_report(_data(None, None))
    assert "不记录落到哪个底层模型" not in out
