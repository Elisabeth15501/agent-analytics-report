# -*- coding: utf-8 -*-
"""C7 · L1 模式下 §4.4 省钱建议改用官方真实积分，且不再渲染低置信度标记。

覆盖：
  - build_savings_insights_from_official 用官方 by_model 真实积分作为成本基准（而非 trace 估算）
  - L1 真值模式跳过低置信度过滤（官方积分已是真值，折扣/时段模型「迁走」建议不会误导）
  - L1 按 DISPLAY_MERGE 归并变体（glm-5.2-x → glm-5.2），与 §3.5 对账口径一致
  - §4.4 渲染：L1 下不出现「折扣/时段模型提示」与「高位告警」低置信度标记（已是真值）
  - §4.4 渲染：L1 引导语切换为「官方用量导出（成本真值 L1）」，与 L2 估算口径区分

全部白盒 / 回归，使用内置 fixture，不依赖真实数据。
"""

import json

import pytest

import allure

pytestmark = [pytest.mark.unit, pytest.mark.whitebox, pytest.mark.regression]

LC_MAP = {
    "hy4-preview": "夜间 23:00–08:00 免费，静态价表按全时段计费 → 严重高估",
}


def _base_data(low_confidence=None):
    """构造可驱动报告生成的 fixture（仿 _data_with_lc）。"""
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
            # 低置信度：时段免费模型（L2 估算偏高）
            {"model": "hy4-preview", "calls": 12, "effective_tokens": 50000,
             "input_tokens": 40000, "output_tokens": 10000,
             "unit_price_input": 6.0, "unit_price_output": 18.0,
             "effective_cost": 7.68, "configured": True},
            # 可信对照组（_CHEAPER_ALT 中：glm-5.2 → minimax-m3）
            {"model": "glm-5.2", "calls": 10, "effective_tokens": 10000,
             "input_tokens": 8000, "output_tokens": 2000,
             "unit_price_input": 8.0, "unit_price_output": 28.0,
             "effective_cost": 0.8, "configured": True},
        ],
        "model_exec_stats": [
            {"model": "hy4-preview", "calls": 12, "effective_tokens": 50000,
             "input_tokens": 40000, "output_tokens": 10000,
             "unit_price_input": 6.0, "unit_price_output": 18.0,
             "effective_cost": 7.68, "configured": True},
        ],
        "traces": [],
        "skill_usage": {"skills": []},
        "outputs": [],
        "automation_runs": [],
        "session_stats": {"rows": [], "buckets": []},
        "cost_anomalies": {},
        "savings_insights": {"items": [], "total_estimated_monthly_save": 0.0},
    }


def _with_session(data):
    """补会话数据（§4.4 在 session_stats.rows 为空时直接 return []）。"""
    data["session_stats"] = {
        "rows": [{"title": "生成周报", "task_type": "文档写作", "effective_cost": 15.87,
                  "effective_tokens": 100000, "calls": 133, "session_id": "s1",
                  "models": ["glm-5.3-flash", "hy3"]}],
        "buckets": [{"label": "¥10-20", "count": 1, "cost": 15.87}],
    }
    return data


def _l1_savings_items():
    """L1 真值省钱建议 fixture（基于官方真实积分 glm-5.2=100 分）。"""
    return {
        "items": [{
            "model": "glm-5.2", "cost": 100.0, "cost_share": 100.0,
            "alternative": "minimax-m3", "note": "简单任务可迁移",
            "estimated_monthly_save": 25.0,
        }],
        "total_estimated_monthly_save": 25.0,
    }


# ── 逻辑层：build_savings_insights_from_official ──────────────────────────────

@allure.feature("C7 · L1 真值省钱")
@allure.story("成本基准")
def test_l1_savings_uses_real_credits(collector_module):
    """L1 省钱建议的成本基准必须是官方真实积分，而非 trace 估算。"""
    by_model = [{"name": "glm-5.2", "requests": 10, "credits": 100.0,
                 "free_requests": 0, "paid_requests": 10}]
    si = collector_module.build_savings_insights_from_official(by_model)
    items = si["items"]
    assert len(items) == 1, "glm-5.2 在 _CHEAPER_ALT 中，应给出替代建议"
    assert items[0]["model"] == "glm-5.2"
    assert items[0]["cost"] == 100.0, "成本基准必须是官方真实积分 credits，而非 trace effective_cost"
    assert items[0]["cost_share"] == 100.0
    assert si["total_estimated_monthly_save"] > 0


@allure.feature("C7 · L1 真值省钱")
@allure.story("低置信度不过滤")
def test_l1_skips_low_confidence_filter(collector_module, monkeypatch):
    """L1 真值模式跳过低置信度过滤：官方积分已是真值，折扣/时段模型「迁走」建议不会误导。"""
    # 把 glm-5.2（在 _CHEAPER_ALT 中有替代）临时标为低置信度，制造「低置信度 + 有替代」场景
    lc = dict(collector_module.ca_core.LOW_CONFIDENCE)
    lc["glm-5.2"] = "测试：临时标记为低置信度"
    monkeypatch.setattr(collector_module.ca_core, "LOW_CONFIDENCE", lc)

    by_model = [{"name": "glm-5.2", "requests": 10, "credits": 100.0,
                 "free_requests": 0, "paid_requests": 10}]
    si = collector_module.build_savings_insights_from_official(by_model)
    blob = json.dumps(si, ensure_ascii=False)
    assert "glm-5.2" in blob, "L1 真值模式下低置信度模型不应被过滤"

    # 对照：L2 默认（low_confidence_filter=True）仍应剔除低置信度模型
    l2 = collector_module.build_savings_insights(
        [{"model": "glm-5.2", "effective_cost": 100.0, "configured": True, "is_router": False}],
        low_confidence_filter=True)
    assert "glm-5.2" not in json.dumps(l2, ensure_ascii=False), \
        "L2 默认仍应过滤低置信度（v1.7.0 · A1 回归保障）"


@allure.feature("C7 · L1 真值省钱")
@allure.story("变体归并")
def test_l1_collapse_display_aliases(collector_module):
    """L1 按 DISPLAY_MERGE 归并变体（glm-5.2-x → glm-5.2），与 §3.5 对账口径一致。"""
    by_model = [{"name": "glm-5.2-x", "requests": 3, "credits": 40.0,
                 "free_requests": 0, "paid_requests": 3}]
    si = collector_module.build_savings_insights_from_official(
        by_model, alias_map={"glm-5.2-x": "glm-5.2"})
    items = si["items"]
    assert any(i["model"] == "glm-5.2" for i in items), "变体应归并为 glm-5.2 并参与分析"
    assert not any(i["model"] == "glm-5.2-x" for i in items), "变体名不应单独成行"


@allure.feature("C7 · L1 真值省钱")
@allure.story("默认 L2 仍过滤（回归）")
def test_l2_default_still_filters(collector_module):
    """回归保障：未指定官方积分时，build_savings_insights 默认仍过滤低置信度（A1 行为不变）。"""
    exec_stats = [
        {"model": "glm-5.3-flash", "effective_cost": 15.86, "configured": True, "is_router": False},
        {"model": "hy4-preview", "effective_cost": 7.68, "configured": True, "is_router": False},
    ]
    si = collector_module.build_savings_insights(exec_stats)
    blob = json.dumps(si, ensure_ascii=False)
    assert "glm-5.3-flash" not in blob
    assert "hy4-preview" not in blob


# ── 渲染层：§4.4 低置信度标记在 L1 下隐藏 ────────────────────────────────────

@allure.feature("C7 · L1 真值省钱")
@allure.story("§4.4 渲染")
def test_l2_section44_shows_discount_hint(report_module):
    """L2（估算）模式：§4.4 顶部仍提示折扣/时段模型（低置信度标记，v1.7.0 · A1）。"""
    data = _with_session(_base_data(low_confidence=LC_MAP))
    data["savings_insights"] = _l1_savings_items()
    out = report_module.generate_markdown_report(data)
    assert "折扣 / 时段模型提示" in out, "L2 应提示折扣/时段模型"
    assert "不就此给出「迁走」建议" in out
    assert "官方用量导出（成本真值 L1）" not in out


@allure.feature("C7 · L1 真值省钱")
@allure.story("§4.4 渲染")
def test_l1_section44_hides_discount_hint(report_module):
    """C7：L1（官方真值）模式下 §4.4 不再渲染低置信度标记（已是真值）。"""
    data = _with_session(_base_data(low_confidence=LC_MAP))
    data["meta"]["cost_source"] = "official"
    data["official_usage"] = {
        "by_model": [{"name": "glm-5.2", "requests": 10, "credits": 100.0,
                      "free_requests": 0, "paid_requests": 10}],
    }
    data["savings_insights"] = _l1_savings_items()
    out = report_module.generate_markdown_report(data)
    assert "折扣 / 时段模型提示" not in out, "L1 真值下不应渲染低置信度标记"
    assert "不就此给出「迁走」建议" not in out
    assert "官方用量导出（成本真值 L1）" in out, "L1 引导语应体现真值口径"


@allure.feature("C7 · L1 真值省钱")
@allure.story("§4.4 渲染（HTML）")
def test_l1_section44_html_hides_discount_hint(report_module):
    """C7：HTML 报告同样在 L1 下隐藏低置信度标记，并切换真值引导语。"""
    data = _with_session(_base_data(low_confidence=LC_MAP))
    data["meta"]["cost_source"] = "official"
    data["official_usage"] = {
        "by_model": [{"name": "glm-5.2", "requests": 10, "credits": 100.0,
                      "free_requests": 0, "paid_requests": 10}],
    }
    data["savings_insights"] = _l1_savings_items()
    out = report_module.generate_html_report(data)
    assert "折扣 / 时段模型提示" not in out, "L1 HTML 不应渲染低置信度标记"
    assert "官方用量导出（成本真值 L1）" in out
