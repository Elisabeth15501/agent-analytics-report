# -*- coding: utf-8 -*-
"""L0 · 聚合与异常检测（Aggregation & Anomaly）— pytest + Allure 双可视化。

覆盖 collect_usage_data.py 中的聚合 / 异常纯函数（不依赖真实 DB / traces）：
  - aggregate_by_model / aggregate_by_exec_model   按接口维度 / 实际执行模型维度聚合求和
  - _detect_daily_anomalies                       日级阈值 / 环比突增检测
  - _detect_session_anomalies                     会话级异常检测
  - detect_cost_anomalies                         双口径（成本 / Token）异常检测，含全免费→cost_all_zero
"""

import pytest

import allure

pytestmark = [pytest.mark.unit, pytest.mark.whitebox]


def _make_trace(exec_model, model_key=None, **kw):
    """构造一条最小 trace 字典，字段对齐 aggregate_traces_by 的读取。"""
    mk = model_key or exec_model
    return {
        "session_id": kw.get("session_id", "s1"),
        "model_key": mk,
        "model_name": exec_model,
        "exec_model": exec_model,
        "total_tokens": kw.get("total_tokens", 100),
        "input_tokens": kw.get("input_tokens", 60),
        "output_tokens": kw.get("output_tokens", 40),
        "cached_tokens": kw.get("cached_tokens", 0),
        "effective_tokens": kw.get("effective_tokens", 100),
        "input_cost": kw.get("input_cost", 0.0),
        "output_cost": kw.get("output_cost", 0.0),
        "total_cost": kw.get("total_cost", 0.0),
        "effective_cost": kw.get("effective_cost", 0.0),
        "date": kw.get("date", "2026-08-01"),
        "call_count": kw.get("call_count", 1),
    }


@allure.feature("聚合与异常检测（Aggregation & Anomaly）")
@allure.story("模型维度聚合求和")
@allure.title("aggregate_by_exec_model 对同模型多次调用合并为一行并正确求和")
@allure.severity(allure.severity_level.CRITICAL)
def test_aggregate_by_exec_model_sums(collector_module):
    traces = [
        _make_trace("glm-5.2", session_id="a", effective_tokens=100, effective_cost=0.5),
        _make_trace("glm-5.2", session_id="b", effective_tokens=200, effective_cost=0.7),
    ]
    rows = collector_module.aggregate_by_exec_model(traces)
    glm = next((r for r in rows if r["model"] == "glm-5.2"), None)
    assert glm is not None, "应存在 glm-5.2 聚合行"
    assert glm["calls"] == 2, f"calls 应为 2，实得 {glm['calls']}"
    assert glm["effective_tokens"] == 300, f"effective_tokens 应为 300，实得 {glm['effective_tokens']}"
    assert abs(glm["effective_cost"] - 1.2) < 1e-9, f"effective_cost 应为 1.2，实得 {glm['effective_cost']}"


@allure.feature("聚合与异常检测（Aggregation & Anomaly）")
@allure.title("aggregate_by_model 对两个不同接口拆分为两行")
@allure.severity(allure.severity_level.CRITICAL)
def test_aggregate_by_model_two_keys(collector_module):
    traces = [
        _make_trace("glm-5.2", model_key="glm-5.2", session_id="a", call_count=1),
        _make_trace("glm-5.2", model_key="custom-local:glm-4.6v", session_id="b", call_count=1),
    ]
    rows = collector_module.aggregate_by_model(traces)
    keys = {r["model"] for r in rows}
    assert "glm-5.2" in keys, "应含 gateway 侧 glm-5.2 行"
    assert "custom-local:glm-4.6v" in keys, "应含 custom-local 侧行（两接口拆分）"
    assert len(rows) == 2, f"应恰两行，实得 {len(rows)}"


@allure.feature("聚合与异常检测（Aggregation & Anomaly）")
@allure.story("模型维度聚合求和")
@allure.title("聚合对空输入返回空列表")
@allure.severity(allure.severity_level.NORMAL)
@pytest.mark.parametrize("fn", ["aggregate_by_model", "aggregate_by_exec_model"])
def test_aggregate_empty(collector_module, fn):
    assert collector_module.__dict__[fn]([]) == []


@allure.feature("聚合与异常检测（Aggregation & Anomaly）")
@allure.story("日级异常检测")
@allure.title("_detect_daily_anomalies 空序列安全返回")
@allure.severity(allure.severity_level.NORMAL)
def test_detect_daily_empty(collector_module):
    daily, thr = collector_module._detect_daily_anomalies([], is_cost=False)
    assert daily == [] and thr == {"p50": 0, "p95": 0}


@allure.feature("聚合与异常检测（Aggregation & Anomaly）")
@allure.story("日级异常检测")
@allure.title("_detect_daily_anomalies 标出超 p95 与环比突增")
@allure.severity(allure.severity_level.NORMAL)
def test_detect_daily_threshold_and_spike(collector_module):
    # 平稳基线 10，最后一天突增到 100（>2x 且 >p50）→ 应被标出
    series = [(f"2026-08-0{i}", 10) for i in range(1, 9)] + [("2026-08-09", 100)]
    daily, thr = collector_module._detect_daily_anomalies(series, is_cost=False)
    flagged = {d["date"] for d in daily}
    assert "2026-08-09" in flagged, "突增日应被标为异常"
    assert thr["p95"] > 0


@allure.feature("聚合与异常检测（Aggregation & Anomaly）")
@allure.story("会话级异常检测")
@allure.title("_detect_session_anomalies 标出成本 p95 之外的会话")
@allure.severity(allure.severity_level.NORMAL)
def test_detect_session_anomalies_cost(collector_module):
    rows = [
        {"session_id": f"s{i}", "title": f"t{i}", "effective_cost": 1.0 + i * 0.1,
         "models": ["glm-5.2"], "effective_tokens": 100, "calls": 1}
        for i in range(10)
    ]
    rows.append({"session_id": "big", "title": "big", "effective_cost": 100.0,
                 "models": ["glm-5.2"], "effective_tokens": 100, "calls": 1})
    anom, sp95 = collector_module._detect_session_anomalies({"rows": rows}, metric="cost")
    assert sp95 > 0
    assert any(a["session_id"] == "big" for a in anom), "明显离群会话应被标出"


@allure.feature("聚合与异常检测（Aggregation & Anomaly）")
@allure.story("双口径异常检测")
@allure.title("detect_cost_anomalies 全免费（成本全 0）→ cost_all_zero 且 cost=None")
@allure.severity(allure.severity_level.CRITICAL)
def test_detect_cost_anomalies_all_zero(collector_module):
    daily_tokens = {
        "2026-08-01": {"effective_cost": 0.0, "effective": 50},
        "2026-08-02": {"effective_cost": 0.0, "effective": 80},
    }
    res = collector_module.detect_cost_anomalies(daily_tokens, {"rows": []})
    assert res["cost_all_zero"] is True
    assert res["cost"] is None, "成本全 0 时成本口径不应启用"
    assert "token" in res and res["token"], "Token 口径必须始终存在"


@allure.feature("聚合与异常检测（Aggregation & Anomaly）")
@allure.story("双口径异常检测")
@allure.title("detect_cost_anomalies 有真实成本 → cost 块存在且 cost_all_zero=False")
@allure.severity(allure.severity_level.NORMAL)
def test_detect_cost_anomalies_with_cost(collector_module):
    daily_tokens = {
        "2026-08-01": {"effective_cost": 1.0, "effective": 50},
        "2026-08-02": {"effective_cost": 5.0, "effective": 80},
    }
    res = collector_module.detect_cost_anomalies(daily_tokens, {"rows": []})
    assert res["cost_all_zero"] is False
    assert res["cost"] is not None, "存在真实成本时成本口径应启用"
    assert res["token"] is not None
