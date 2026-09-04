# -*- coding: utf-8 -*-
"""L0 · 档位维度（快速 / 均衡 / 极致）— pytest + Allure 双可视化。

业务背景：WorkBuddy 把原单一 auto 路由器拆为三档（快速 / 均衡 / 极致），trace 中以
路由别名 fast-model / balanced-model / extreme-model 出现。本测试覆盖 v1.3.0：
  - 档位别名被识别为独立通道 "tier"，极端档 extreme-model 归一为配置缓存 id deep-model
  - pricing.json 的 mode_rates 被正确加载为 ¥ 估算单价，且官方配置缓存倍率覆盖 multiplier
  - 三档各自独立成行、档位行标记 is_router=True（未解析具体模型）
  - 无档位数据时聚合 / 报告段落安全返回空
  - 配置缓存缺失时优雅降级（仍从 pricing.json 读手工锚定单价）
  - 金额守恒：档位估算花费 = 各 trace effective_cost 之和，且与 §3.1 / §3.2 档位行金额一致
  - 补「Agent workflow」schema（b）fixture：空顶层 modelInfo、model 藏在 generation
    span 的 toolOutput 转义 JSON，还原后喂入档位别名

⚠️ 档位倍率是「积分维度」，与 ¥ 刊例价正交——其单价为倍率锚定估算值，不计入账单总额，
   但必须可被计价（否则漏计 + meta.unconfigured_models 假阳性）。
"""

import json

import pytest

import allure

pytestmark = [pytest.mark.unit, pytest.mark.whitebox]

DATE = "2026-09-02"

# 档位估算单价（pricing.json mode_rates，与采集器加载值一致）
FAST_IP, FAST_OP = 1.24, 2.47
BAL_IP, BAL_OP = 6.58, 23.04
EXT_IP, EXT_OP = 12.15, 42.51


def _mk_tier(alias, inp=100_000, out=10_000, cached=0, eff_cost=None,
             date=DATE, session_id=""):
    """构造一条最小档位 trace，字段对齐 aggregate_traces_by 的读取。

    alias 为 trace 字面量（fast-model / balanced-model / extreme-model）。
    channel 用 parse_channel 的真实返回值 "tier"。eff_cost 为 None 时按档位估算单价推算，
    模拟 collect_traces 已算好的 trace 级成本。
    """
    if eff_cost is None:
        ip, op = {
            "fast-model": (FAST_IP, FAST_OP),
            "balanced-model": (BAL_IP, BAL_OP),
            "extreme-model": (EXT_IP, EXT_OP),
            "deep-model": (EXT_IP, EXT_OP),
        }[alias]
        eff_in = max(inp - cached * 0.9, 0)
        eff_cost = (eff_in / 1_000_000) * ip + (out / 1_000_000) * op
    return {
        "session_id": session_id,
        "model_key": alias,
        "model_name": alias,
        "exec_model": alias,
        "channel": "tier",
        "total_tokens": inp + out,
        "input_tokens": inp,
        "output_tokens": out,
        "cached_tokens": cached,
        "effective_tokens": inp + out,
        "input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": 0.0,
        "effective_cost": eff_cost,
        "date": date,
        "call_count": 1,
    }


def _tier_traces():
    """三条不同档位 trace（含极端档 trace 字面量 extreme-model）。"""
    return [
        _mk_tier("fast-model", inp=400_000, out=100_000),
        _mk_tier("balanced-model", inp=600_000, out=200_000),
        _mk_tier("extreme-model", inp=800_000, out=200_000),
    ]


# ── 配置加载（本地缓存优先 + 手工映射兜底）──────────────────────────────

@allure.feature("档位维度 mode_rates")
@allure.story("配置加载")
@allure.title("TIER_ALIASES 含三档规范 id（含 extreme/deep 双拼写）")
@allure.severity(allure.severity_level.CRITICAL)
def test_tier_aliases(collector_module):
    ta = collector_module.TIER_ALIASES
    assert {"fast-model", "balanced-model", "extreme-model", "deep-model"} <= ta, (
        f"TIER_ALIASES 应含四档拼写，实得 {ta}"
    )


@allure.feature("档位维度 mode_rates")
@allure.story("别名归一化")
@allure.title("parse_channel 把三档归入 tier 通道，extreme-model 归一为 deep-model")
@allure.severity(allure.severity_level.CRITICAL)
def test_parse_channel_tier(collector_module):
    assert collector_module.parse_channel("fast-model") == ("tier", "fast-model")
    assert collector_module.parse_channel("balanced-model") == ("tier", "balanced-model")
    # 致命不一致：trace 字面量 extreme-model → 配置缓存规范 id deep-model
    assert collector_module.parse_channel("extreme-model") == ("tier", "deep-model")
    # auto 仍走 legacy router 通道，不受档位改动影响
    assert collector_module.parse_channel("auto") == ("router", "auto")


@allure.feature("档位维度 mode_rates")
@allure.story("配置加载")
@allure.title("mode_rates 的 ¥ 估算单价被并入 MODEL_PRICING，档位可被计价")
@allure.severity(allure.severity_level.CRITICAL)
def test_mode_rates_loaded_into_pricing(collector_module):
    mp = collector_module.MODEL_PRICING
    assert mp.get("fast-model") == {"input": FAST_IP, "output": FAST_OP}, f"fast-model 单价缺失/不符：{mp.get('fast-model')}"
    assert mp.get("balanced-model") == {"input": BAL_IP, "output": BAL_OP}, f"balanced-model 单价缺失/不符：{mp.get('balanced-model')}"
    assert mp.get("deep-model") == {"input": EXT_IP, "output": EXT_OP}, f"deep-model 单价缺失/不符：{mp.get('deep-model')}"
    # price_of 应直接返回估算单价（不回退 None）
    assert collector_module.price_of("extreme-model") == (EXT_IP, EXT_OP)
    assert collector_module.price_of("deep-model") == (EXT_IP, EXT_OP)


@allure.feature("档位维度 mode_rates")
@allure.story("配置加载")
@allure.title("官方配置缓存倍率覆盖 mode_rates 的 multiplier")
@allure.severity(allure.severity_level.NORMAL)
def test_config_cache_overrides_multiplier(collector_module):
    """本地缓存优先：acc-product-config-v3.json 的 credits 应覆盖 pricing.json 的手工 multiplier。"""
    # 模拟本机缓存文件存在的情况，验证覆盖逻辑
    orig = collector_module.ca_core._load_acc_product_config
    collector_module.ca_core._load_acc_product_config = lambda: {
        "path": "~/.workbuddy/cache/acc-product-config-v3.json",
        "mtime": 1234567890,
        "loaded": True,
        "multipliers": {"fast-model": 0.21, "balanced-model": 0.65, "deep-model": 1.20}
    }
    try:
        cfg, _ = collector_module._load_pricing_config()
        rates = cfg["mode_rates_meta"]["rates"]
        # 缓存存在时，fast 倍率应为官方 0.21、extreme 应为 1.20（deep-model 规范 id）
        assert rates.get("fast", {}).get("multiplier") == 0.21, f"fast 倍率未被官方缓存覆盖：{rates.get('fast')}"
        assert rates.get("extreme", {}).get("multiplier") == 1.20, f"extreme 倍率未被官方缓存覆盖：{rates.get('extreme')}"
        assert cfg["mode_rates_meta"]["config_cache_loaded"] is True
    finally:
        collector_module.ca_core._load_acc_product_config = orig


@allure.feature("档位维度 mode_rates")
@allure.story("优雅降级")
@allure.title("配置缓存缺失时仍从 pricing.json 读手工锚定单价（BLOCKER）")
@allure.severity(allure.severity_level.BLOCKER)
def test_config_cache_missing_falls_back(collector_module):
    """配置缓存读不到时，mode_rates 的 ¥ 单价与 auto_estimate 仍来自 pricing.json，不崩。"""
    orig = collector_module.ca_core._load_acc_product_config
    collector_module.ca_core._load_acc_product_config = lambda: {
        "path": None, "mtime": None, "loaded": False, "multipliers": {}
    }
    try:
        cfg, _ = collector_module._load_pricing_config()
        assert "deep-model" in cfg["models"], "缓存缺失时仍应从 pricing.json 载入档位单价"
        assert cfg["mode_rates_meta"]["config_cache_loaded"] is False
        assert cfg["mode_rates_meta"]["auto_estimate"] is True
        # ¥ 单价兜底为手工锚定值
        assert cfg["models"]["deep-model"] == {"input": EXT_IP, "output": EXT_OP}
    finally:
        collector_module.ca_core._load_acc_product_config = orig


# ── 聚合 ──────────────────────────────────────────────────────────────

@allure.feature("档位维度 聚合")
@allure.story("独立成行")
@allure.title("三档各自独立成行，且极端档按规范 id deep-model 归并")
@allure.severity(allure.severity_level.CRITICAL)
def test_three_tiers_separate_rows(collector_module):
    rows = collector_module.aggregate_by_tier(_tier_traces())
    models = {r["model"] for r in rows}
    assert models == {"fast-model", "balanced-model", "deep-model"}, (
        f"三档应各自独立成行（极端档归 deep-model），实得 {models}"
    )
    # 档位行标记 is_router（未解析具体模型），且已配置单价
    for r in rows:
        assert r.get("is_router") is True, f"{r['model']} 应标记 is_router"
        assert r.get("configured") is True, f"{r['model']} 应已配置估算单价"


@allure.feature("档位维度 聚合")
@allure.story("空数据")
@allure.title("无档位数据时聚合返回空、报告段落返回空")
@allure.severity(allure.severity_level.NORMAL)
def test_no_tier_data(collector_module, report_module):
    # 仅含普通模型 trace，无档位
    plain_traces = [
        {"session_id": "s1", "model_key": "glm-5.2", "model_name": "glm-5.2",
         "exec_model": "glm-5.2", "channel": "gateway", "total_tokens": 1000,
         "input_tokens": 800, "output_tokens": 200, "cached_tokens": 0,
         "effective_tokens": 1000, "input_cost": 0.0, "output_cost": 0.0,
         "total_cost": 0.0, "effective_cost": 0.01, "date": DATE, "call_count": 1},
    ]
    assert collector_module.aggregate_by_tier(plain_traces) == [], "无档位数据应返回空列表"
    assert report_module.build_tier_section_html({"tier_stats": [], "meta": {}}) == []
    assert report_module.build_tier_section_md({"tier_stats": [], "meta": {}}) == []


@allure.feature("档位维度 聚合")
@allure.story("金额守恒")
@allure.title("档位估算花费 = 各 trace effective_cost 之和（BLOCKER）")
@allure.severity(allure.severity_level.BLOCKER)
def test_tier_amount_conservation(collector_module):
    traces = _tier_traces()
    rows = {r["model"]: r for r in collector_module.aggregate_by_tier(traces)}
    for alias, canon in (("fast-model", "fast-model"), ("balanced-model", "balanced-model"),
                         ("extreme-model", "deep-model")):
        expected = sum(t["effective_cost"] for t in traces if t["exec_model"] == alias)
        got = rows[canon]["effective_cost"]
        assert abs(got - expected) < 1e-6, (
            f"{alias} 档位估算花费应等于各 trace 之和：期望 {expected:.6f}，实得 {got:.6f}"
        )


@allure.feature("档位维度 聚合")
@allure.story("金额守恒")
@allure.title("§3.1 / §3.2 / §3.4 档位行金额一致（BLOCKER）")
@allure.severity(allure.severity_level.BLOCKER)
def test_tier_consistent_across_sections(collector_module):
    """档位行在 §3.1(账单口径) / §3.2(入口视图) 与 §3.4(档位维度) 金额必须一致。

    避免「报告自己矛盾」：极端档在 §3.1/§3.2 以 trace 字面量 extreme-model 出现，
    在 §3.4 以规范 id deep-model 出现，二者金额应相等。
    """
    traces = _tier_traces()
    exec_rows = {r["model"]: r for r in collector_module.aggregate_by_exec_model(traces)}
    model_rows = {r["model"]: r for r in collector_module.aggregate_by_model(traces)}
    tier_rows = {r["model"]: r for r in collector_module.aggregate_by_tier(traces)}

    # 极端档：§3.1/§3.2 的 extreme-model 行 vs §3.4 的 deep-model 行
    sec31 = exec_rows.get("extreme-model")
    sec32 = model_rows.get("extreme-model")
    sec34 = tier_rows.get("deep-model")
    assert sec31 and sec32 and sec34, "极端档应同时出现在三个章节"
    assert abs(sec31["effective_cost"] - sec34["effective_cost"]) < 1e-6, (
        f"§3.1 与 §3.4 极端档金额不符：{sec31['effective_cost']} vs {sec34['effective_cost']}"
    )
    assert abs(sec32["effective_cost"] - sec34["effective_cost"]) < 1e-6, (
        f"§3.2 与 §3.4 极端档金额不符：{sec32['effective_cost']} vs {sec34['effective_cost']}"
    )
    # 快速档同样一致
    assert abs(exec_rows["fast-model"]["effective_cost"] - tier_rows["fast-model"]["effective_cost"]) < 1e-6


@allure.feature("档位维度 聚合")
@allure.story("不污染 auto 均价")
@allure.title("档位不进入 auto 路由均价 paid 集合（auto 估算均价不含档位）")
@allure.severity(allure.severity_level.CRITICAL)
def test_tiers_excluded_from_auto_avg(collector_module):
    """采集器计算 auto 均价时排除路由类别名（含三档），否则会抬高 auto 估算均价。

    auto 的均价回填只在入口视图（§3.2 / aggregate_by_model）发生，故在此视图断言；
    本期付费模型仅 glm-5.2(8/28)，档位被排除后 auto 均价应等于 8/28。
    """
    traces = _tier_traces() + [
        {"session_id": "s1", "model_key": "glm-5.2", "model_name": "glm-5.2",
         "exec_model": "glm-5.2", "channel": "gateway", "total_tokens": 1000,
         "input_tokens": 800, "output_tokens": 200, "cached_tokens": 0,
         "effective_tokens": 1000, "input_cost": 0.0, "output_cost": 0.0,
         "total_cost": 0.0, "effective_cost": 0.01, "date": DATE, "call_count": 1},
        {"session_id": "s2", "model_key": "auto", "model_name": "auto",
         "exec_model": "auto", "channel": "router", "total_tokens": 1000,
         "input_tokens": 800, "output_tokens": 200, "cached_tokens": 0,
         "effective_tokens": 1000, "input_cost": 0.0, "output_cost": 0.0,
         "total_cost": 0.0, "effective_cost": 0.0, "date": DATE, "call_count": 1},
    ]
    model_rows = {r["model"]: r for r in collector_module.aggregate_by_model(traces)}
    auto = model_rows.get("auto")
    assert auto and auto.get("configured"), "入口视图下 auto 应被均价估算配置"
    assert abs(auto["unit_price_input"] - 8.0) < 1e-6, (
        f"auto 输入均价应等于 glm-5.2=8（不含档位），实得 {auto['unit_price_input']}"
    )


# ── Agent workflow schema（b）fixture ──────────────────────────────────

@allure.feature("档位维度 还原")
@allure.story("Agent workflow schema")
@allure.title("空顶层 modelInfo 的档位 trace 从 generation span toolOutput 还原出别名")
@allure.severity(allure.severity_level.CRITICAL)
def test_workflow_schema_recovers_tier_alias(collector_module):
    """所有真实档位数据都走 schema (b)：顶层 modelInfo 为空，model 在 generation
    span 的 toolOutput（转义 JSON 字符串）里。验证还原路径能喂入 extreme-model 别名。"""
    tool_output = json.dumps({
        "model": "extreme-model",
        "usage": {
            "prompt_tokens": 800,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    })
    data = {
        "trace": {"startedAt": "2026-09-02T01:22:44+08:00"},
        "spans": [{"name": "generation", "toolOutput": tool_output}],
    }
    rec = collector_module._recover_model_info_from_spans(data)
    assert rec is not None, "schema (b) 档位 trace 应可还原"
    assert rec["models"] == ["extreme-model"], f"还原出的模型应为 extreme-model，实得 {rec['models']}"
    assert rec["input"] == 800 and rec["output"] == 200, "usage 解析错误"
    assert rec["calls"] == 1


@allure.feature("档位维度 还原")
@allure.story("Agent workflow schema")
@allure.title("缺少 usage 字段的辅助 span（如 terminalTitleGenerator）不虚增调用次数")
@allure.severity(allure.severity_level.NORMAL)
def test_workflow_schema_no_usage_span(collector_module):
    """后台辅助调用（terminalTitleGenerator）有 model 但 toolOutput 不含 usage 字段，
    还原时不应被计为一次有量调用，避免调用次数虚高。"""
    data = {
        "trace": {"startedAt": "2026-09-02T00:37:00+08:00"},
        "spans": [
            # 主对话：有 usage
            {"name": "generation", "toolOutput": json.dumps({
                "model": "fast-model",
                "usage": {"prompt_tokens": 40380, "completion_tokens": 611,
                          "prompt_tokens_details": {"cached_tokens": 0}},
            })},
            # 后台辅助：有 model 但 toolOutput 无 usage 字段（terminalTitleGenerator 类）
            {"name": "generation", "toolOutput": json.dumps({
                "model": "fast-model",
            })},
        ],
    }
    rec = collector_module._recover_model_info_from_spans(data)
    # 调用次数只计「含 usage 的 completion」（此处 1 条有量），无 usage 的辅助 span 不计入
    assert rec["calls"] == 1, f"无 usage 字段的辅助 span 不应虚增调用次数，实得 {rec['calls']}"


@allure.feature("档位维度 报告")
@allure.story("段落渲染")
@allure.title("档位报告段落含 §3.4 标题、估算免责声明与可配置折叠块")
@allure.severity(allure.severity_level.NORMAL)
def test_tier_report_section(collector_module, report_module):
    traces = _tier_traces()
    tier = collector_module.aggregate_by_tier(traces)
    data = {
        "tier_stats": tier,
        "meta": {
            "mode_cost_estimated": True,
            "mode_config_cache_loaded": True,
            "mode_rates": collector_module.MODE_RATES_META.get("rates", {}),
        },
    }
    html = report_module.build_tier_section_html(data)
    md = report_module.build_tier_section_md(data)
    assert any("3.4" in l for l in html), "HTML 应含 §3.4 标题"
    assert any("估算" in l for l in html), "HTML 应含「估算」免责说明"
    assert any("mode_rates" in l for l in html), "HTML 应含可配置 mode_rates 折叠块"
    assert any("3.4" in l for l in md)


@allure.feature("档位维度 报告")
@allure.story("估算标记")
@allure.title("meta 标注 mode_cost_estimated，档位行不落入未配置假阳性")
@allure.severity(allure.severity_level.NORMAL)
def test_tier_not_unconfigured_false_positive(collector_module):
    """档位已配置估算单价，不应在 meta.unconfigured_models 里出现（避免假阳性告警）。"""
    traces = _tier_traces()
    exec_rows = collector_module.aggregate_by_exec_model(traces)
    unconfigured = {m["model"] for m in exec_rows if not m.get("configured")}
    assert "extreme-model" not in unconfigured, "极端档不应被判为未配置"
    assert "fast-model" not in unconfigured
    assert collector_module.MODE_RATES_META.get("auto_estimate") is True


@allure.feature("档位维度 报告")
@allure.story("main() 接线回归")
@allure.title("main() 必须注入 tier_stats 与 mode meta，否则 §3.4 永不渲染")
@allure.severity(allure.severity_level.CRITICAL)
def test_main_wires_tier_section_into_report(collector_module, report_module):
    """回归守卫：曾出现 generate_report.main() 本地组装 data 时漏掉 tier_stats / mode meta，
    导致 §3.4 在所有真实报告里被静默省略。此处完整模拟 main() 的 data 组装并断言 §3.4 渲染。"""
    traces = _tier_traces()
    tier = collector_module.aggregate_by_tier(traces)
    mode = collector_module.MODE_RATES_META
    data = {
        "meta": {"start_date": "2026-08-31", "end_date": "2026-09-06", "period": "week",
                 "period_label": "周报", "days": 7,
                 "mode_rates": dict(mode.get("rates", {}) or {}),
                 "mode_cost_estimated": bool(mode.get("auto_estimate", False)),
                 "mode_config_cache_loaded": bool(mode.get("config_cache_loaded", False)),
                 "mode_config_cache_path": mode.get("config_cache_path"),
                 "mode_config_cache_mtime": mode.get("config_cache_mtime")},
        "tier_stats": tier, "model_stats": [], "model_exec_stats": [], "sessions": [],
        "automation_runs": [], "session_credits": [], "skill_usage": {"skills": {}}, "outputs": [],
        "daily_tokens": {},
        "summary": {"total_traces": 0, "total_sessions": 0, "total_tokens": 0, "total_input_tokens": 0,
                    "total_output_tokens": 0, "total_cached_tokens": 0, "total_effective_tokens": 0,
                    "cache_rate": 0.0, "active_days": [], "active_day_count": 0, "total_automation_runs": 0,
                    "successful_automation_runs": 0, "total_outputs": 0, "skills_used": 0,
                    "task_type_distribution": {}, "total_cost": 0.0, "total_input_cost": 0.0,
                    "total_output_cost": 0.0, "total_effective_cost": 0.0},
        "task_token_stats": [], "top_tasks": [], "session_stats": [], "cost_anomalies": [],
        "savings_insights": [],
    }
    md = report_module.generate_markdown_report(data)
    assert "3.4 档位维度" in md, "main() 必须注入 tier_stats，否则 §3.4 永不渲染"
    if mode.get("config_cache_loaded"):
        assert "服务端配置缓存校准" in md, "配置缓存已加载时 §3.4 应显示校准提示"
    j = json.loads(report_module.generate_json_report(data))
    assert j["meta"].get("mode_config_cache_loaded") == bool(mode.get("config_cache_loaded"))
