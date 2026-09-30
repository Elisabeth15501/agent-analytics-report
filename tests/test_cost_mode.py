# -*- coding: utf-8 -*-
"""L0/L1 · 计价模式 cost_mode（tokens_only）— pytest + Allure 双可视化。

千问办公这类**积分订阅制、无公开单 token 刊例价**的数据源，跑出来的报告原本满屏
`¥0.00` 与「未配置」，读者极易把「没有单价可算」误读成「免费」或「采集失败」。
本层守住 `--cost-mode {auto,tokens-only,priced}` 与报告端的金额隐藏逻辑。

覆盖：
  - _decide_cost_mode 的三条边界（限免/本地合法 ¥0 不得误判、L1 真值优先、显式开关）
  - tokens_only 的 MD / HTML 渲染：金额章节与成本列缺席、章节改名为 Token 口径
  - tokens_only 不残留任何「花费 / 实际成本 / 省钱」字样（页脚等固定文案除外）
  - 历史 JSON（无 cost_mode 字段）默认按 priced 渲染 —— 零回归
  - JSON 报告落盘 meta.cost_mode

全部使用内置 fixture，不读任何真实 Agent 数据目录。
"""

import copy
import json

import pytest

import allure

pytestmark = [pytest.mark.unit, pytest.mark.contract]


# ── fixture：一份结构完整、带金额与带会话明细的报告数据 ──────────────────

def _base_data():
    return {
        "meta": {
            "period": "week", "start_date": "2026-08-10", "end_date": "2026-08-16",
            "source": "workbuddy", "cost_source": "estimate",
            "unconfigured_models": [], "timed_free": {}, "low_confidence": {},
            "low_confidence_bias": {},
        },
        "summary": {
            "active_day_count": 2, "active_days": ["2026-08-10", "2026-08-11"],
            "total_sessions": 3, "total_traces": 12,
            "total_automation_runs": 0, "successful_automation_runs": 0,
            "total_outputs": 0, "skills_used": 0,
            "total_tokens": 1_000_000, "total_effective_tokens": 900_000,
            "total_input_tokens": 600_000, "total_output_tokens": 400_000,
            "total_cached_tokens": 100_000, "cache_rate": 16.7,
            "total_cost": 12.0, "total_effective_cost": 10.0,
            "total_input_cost": 7.0, "total_output_cost": 3.0,
            "task_type_distribution": {"代码开发": 2, "问答": 1},
            "estimated_request_count": 30,
        },
        "daily_tokens": {
            "2026-08-10": {"total": 600000, "effective": 540000, "input": 400000,
                           "output": 200000, "cached": 60000, "calls": 8,
                           "effective_cost": 6.0},
            "2026-08-11": {"total": 400000, "effective": 360000, "input": 200000,
                           "output": 200000, "cached": 40000, "calls": 4,
                           "effective_cost": 4.0},
        },
        "skill_usage": {"skills": {}, "active_days": ["2026-08-10"]},
        "outputs": [], "automation_runs": [], "session_credits": [], "memory_logs": {},
        "traces": [{"exec_model": "glm-5.2", "raw_model": "glm-5.2", "channel": "gateway",
                    "input_tokens": 600000, "output_tokens": 400000, "session_id": "s1"}],
        "sessions": [{"id": "s1", "title": "写一个快排", "task_type": "代码开发",
                      "cwd": "/p", "created_date": "2026-08-10"}],
        "task_types": {"s1": "代码开发"},
        "model_stats": [
            {"model": "glm-5.2", "calls": 12, "effective_tokens": 900000,
             "effective_cost": 10.0, "input_tokens": 600000, "output_tokens": 400000,
             "configured": True, "unit_price_input": 8.0, "unit_price_output": 28.0},
        ],
        "model_exec_stats": [
            {"model": "glm-5.2", "calls": 12, "effective_tokens": 900000,
             "effective_cost": 10.0, "input_tokens": 600000, "output_tokens": 400000,
             "configured": True, "unit_price_input": 8.0, "unit_price_output": 28.0,
             "is_router": False},
        ],
        "task_token_stats": [
            {"task_type": "代码开发", "session_count": 2, "effective_tokens": 900000,
             "total_tokens": 1000000, "input_tokens": 600000, "output_tokens": 400000,
             "cached_tokens": 100000, "effective_cost": 10.0},
        ],
        "top_tasks": [
            {"title": "写一个快排", "task_type": "代码开发", "effective_tokens": 900000,
             "total_tokens": 1000000, "input_tokens": 600000, "cached_tokens": 100000,
             "effective_cost": 10.0, "calls": 12},
        ],
        "session_stats": {
            "rows": [
                {"title": "写一个快排", "task_type": "代码开发", "effective_cost": 6.0,
                 "effective_tokens": 540000, "calls": 8, "models": ["glm-5.2"]},
                {"title": "解释模型蒸馏", "task_type": "问答", "effective_cost": 4.0,
                 "effective_tokens": 360000, "calls": 4, "models": ["glm-5.2"]},
            ],
            "buckets": [{"label": "¥1-10", "count": 2, "cost": 10.0}],
        },
        "cost_anomalies": {
            "cost": {"thresholds": {"p50": 4.0, "p95": 6.0, "session_p95": 5.0},
                     "daily": [{"date": "2026-08-10", "value": 6.0, "reasons": ["超过 p95"]}],
                     "session": [{"title": "写一个快排", "value": 6.0, "models": ["glm-5.2"]}]},
            "token": {"thresholds": {"p50": 360000, "p95": 540000, "session_p95": 8},
                      "daily": [{"date": "2026-08-10", "value": 540000, "reasons": ["环比突增"]}],
                      "session": [{"title": "写一个快排", "value": 540000, "calls": 8,
                                   "models": ["glm-5.2"]}]},
        },
        "savings_insights": {
            "items": [{"model": "glm-5.2", "cost": 10.0, "cost_share": 100.0,
                       "alternative": "glm-5.2-air", "note": "轻量任务",
                       "estimated_monthly_save": 3.0}],
            "total_estimated_monthly_save": 3.0,
        },
    }


def _tokens_only_data():
    """千问办公那种「一个单价都没命中」的数据形态。"""
    d = _base_data()
    d["meta"]["source"] = "qwenwork"
    d["meta"]["cost_mode"] = "tokens_only"
    d["meta"]["tokens_source"] = "estimated"
    d["meta"]["cost_supported"] = False      # 适配器 SUPPORTS_COST=False 的落盘形态
    d["meta"]["unconfigured_models"] = ["flash"]
    d["summary"]["total_cost"] = 0.0
    d["summary"]["total_effective_cost"] = 0.0
    d["summary"]["total_input_cost"] = 0.0
    d["summary"]["total_output_cost"] = 0.0
    for stats in (d["model_stats"], d["model_exec_stats"]):
        for m in stats:
            m.update({"configured": False, "effective_cost": 0.0,
                      "unit_price_input": None, "unit_price_output": None})
    d["cost_anomalies"].pop("cost")
    d["cost_anomalies"]["cost_note"] = "本期成本为 0，成本口径不适用"
    d["savings_insights"] = {"items": [], "total_estimated_monthly_save": 0.0}
    # 千问办公专有维度（第 9 项）：真实提问轮数、单通道声明、后台自动运行摘要
    d["sessions"][0]["_human_turns"] = 5
    d["meta"]["single_channel"] = True
    d["meta"]["background_runs"] = {"count": 227, "error_count": 172,
                                    "total_duration_ms": 211597541,
                                    "types": {"reflection_memory_error": 172}}
    return d


# ── 一、判定逻辑（纯函数，边界最全）────────────────────────────────────────

@allure.feature("计价模式")
@allure.story("auto 判定")
@allure.title("无单价命中 → tokens_only")
def test_auto_switches_to_tokens_only(collector_module):
    assert collector_module._decide_cost_mode(
        requested="auto", unconfigured={"flash"}, configured_rows=[],
        total_cost=0.0, total_effective_cost=0.0, has_official=False) == "tokens_only"


@allure.feature("计价模式")
@allure.story("auto 判定")
@allure.title("连模型数据都读不到 → tokens_only（百度搭子/聚合 token 源）")
def test_no_model_data_switches_to_tokens_only(collector_module):
    # 适配器未产出任何模型统计（unconfigured 与 configured_rows 都空）：
    # 旧逻辑会因 `unconfigured` 为假值漏判、错误地走 priced（满屏 ¥0.00）；
    # 新逻辑只认「有没有可计价的模型」，故应转 tokens_only。
    assert collector_module._decide_cost_mode(
        requested="auto", unconfigured=set(), configured_rows=[],
        total_cost=0.0, total_effective_cost=0.0, has_official=False) == "tokens_only"


@allure.feature("计价模式")
@allure.story("auto 判定")
@allure.title("限免 / 本地模型：单价已配置且合法为 0，不得误判为 tokens_only")
def test_legit_zero_cost_stays_priced(collector_module):
    # 限免（hy3）与本地模型（custom-local）算出来的 ¥0.00 是真实结果，
    # 成本章节必须照常保留 —— 这是本特性最容易踩的坑。
    assert collector_module._decide_cost_mode(
        requested="auto", unconfigured=set(),
        configured_rows=[{"model": "hy3", "configured": True}],
        total_cost=0.0, total_effective_cost=0.0, has_official=False) == "priced"


@allure.feature("计价模式")
@allure.story("auto 判定")
@allure.title("有已配置模型但存在未配置模型 → 仍按 priced（WorkBuddy 常态）")
def test_mixed_configured_stays_priced(collector_module):
    assert collector_module._decide_cost_mode(
        requested="auto", unconfigured={"some-new-model"},
        configured_rows=[{"model": "glm-5.2", "configured": True}],
        total_cost=8.8, total_effective_cost=7.7, has_official=False) == "priced"


@allure.feature("计价模式")
@allure.story("auto 判定")
@allure.title("L1 官方账单在场时优先 priced")
def test_official_truth_stays_priced(collector_module):
    assert collector_module._decide_cost_mode(
        requested="auto", unconfigured={"minimax-m3"}, configured_rows=[],
        total_cost=0.0, total_effective_cost=0.0, has_official=True) == "priced"


@allure.feature("计价模式")
@allure.story("显式开关")
@allure.title("--cost-mode 显式值覆盖 auto 判定")
def test_explicit_flags_win(collector_module):
    f = collector_module._decide_cost_mode
    # 数据明明该切 tokens_only，但用户显式要 priced
    assert f(requested="priced", unconfigured={"flash"}, configured_rows=[],
             total_cost=0.0, total_effective_cost=0.0, has_official=False) == "priced"
    # 数据本该 priced，用户显式要隐藏金额
    assert f(requested="tokens-only", unconfigured=set(),
             configured_rows=[{"configured": True}],
             total_cost=9.9, total_effective_cost=8.8, has_official=True) == "tokens_only"


# ── 二、渲染层：tokens_only 的金额缺席 ────────────────────────────────────

_ALL_COST_WORDS = ("实际成本", "原始总成本", "输入成本", "输出成本", "花费速览",
                   "省钱杠杆", "省钱成就", "最贵模型", "成本货币化", "未配置",
                   "估算实际花费", "实际成本（估算）")


@allure.feature("计价模式")
@allure.story("Markdown 渲染")
@allure.title("tokens_only：成本章节改名、金额字样不残留")
def test_markdown_tokens_only_hides_cost(report_module):
    md = report_module.generate_markdown_report(_tokens_only_data())
    # 章节改口径
    assert "## 三、模型使用对比" in md
    assert "## 四、Token 与调用深度分析" in md
    assert "### 4.1 每会话 Token 消耗 Top 10" in md
    assert "### 📈 用量速览" in md
    assert "计价模式：tokens_only" in md
    # 成本专属结构（标题 / 行标签 / 洞察）不得出现。
    # 注意断言的是「结构」而非「词」——解释性文案里提到「省钱杠杆不存在」是允许的。
    for absent in ("### 2.2 成本货币化", "💰 花费速览", "### 4.4 省钱杠杆",
                   "### ✅ 省钱成就", "💸 **最贵模型", "实际成本（计费等效）",
                   "| 原始总成本（含缓存全价） |"):
        assert absent not in md, f"tokens_only 报告不应出现「{absent}」"
    # 表格列头也要撤（MD 的 Top10 任务表历史上漏过一处实际成本列）
    assert "| 排名 | 任务名称 | 任务类型 | 实际消耗 | 原始总Token | 缓存占比 | 实际成本 |" not in md
    assert "| 排名 | 任务名称 | 任务类型 | 实际消耗 | 原始总Token | 缓存占比 |" in md


@allure.feature("计价模式")
@allure.story("Markdown 渲染")
@allure.title("tokens_only：模型表退化为用量三列")
def test_markdown_model_table_compact(report_module):
    md = report_module.generate_markdown_report(_tokens_only_data())
    # 模型明细表只剩用量列（金额列整列撤掉，而不是填 ¥0.00 占位）
    assert "| 模型 | 调用次数 | 实际消耗Token |" in md
    assert "占总花费比" not in md
    # §3.1 表标题去计费口径（token-only 下没有「费用结算依据」这回事）
    assert "**Token 维度明细**" in md


@allure.feature("计价模式")
@allure.story("HTML 渲染")
@allure.title("tokens_only：统计卡与表头不含金额")
def test_html_tokens_only_hides_cost(report_module):
    html = report_module.generate_html_report(_tokens_only_data())
    assert "三、模型使用对比" in html
    assert "四、Token 与调用深度分析" in html
    assert "计价模式（不计价）" in html
    assert "实际成本（估算）" not in html
    assert "<th>实际成本</th>" not in html
    assert "💰 花费速览" not in html
    assert "📈 用量速览" in html


@allure.feature("计价模式")
@allure.story("零回归")
@allure.title("历史 JSON 无 cost_mode 字段 → 仍按 priced 渲染")
def test_absent_cost_mode_defaults_priced(report_module):
    d = _base_data()          # 不带 cost_mode，模拟 v1.7.x 之前采集的数据
    md = report_module.generate_markdown_report(d)
    assert "## 三、模型使用与成本对比" in md
    assert "## 四、成本深度分析（每会话 / 异常 / 省钱）" in md
    assert "### 2.2 成本货币化" in md
    assert "实际成本" in md and "¥" in md
    html = report_module.generate_html_report(copy.deepcopy(d))
    assert "实际成本（估算）" in html


@allure.feature("计价模式")
@allure.story("零回归")
@allure.title("priced 数据加显式 priced → 与默认渲染逐字一致")
def test_priced_mode_identical_to_default(report_module):
    a = report_module.generate_markdown_report(_base_data())
    b = _base_data()
    b["meta"]["cost_mode"] = "priced"
    assert a == report_module.generate_markdown_report(b)


@allure.feature("计价模式")
@allure.story("JSON 输出")
@allure.title("JSON 报告落盘 meta.cost_mode")
def test_json_report_carries_cost_mode(report_module):
    js = json.loads(report_module.generate_json_report(_tokens_only_data()))
    assert js["meta"]["cost_mode"] == "tokens_only"
    js2 = json.loads(report_module.generate_json_report(_base_data()))
    assert js2["meta"]["cost_mode"] == "priced"


@allure.feature("计价模式")
@allure.story("页脚署名")
@allure.title("外部源页脚标数据来源与计价模式，workbuddy 沿用历史文案")
def test_footer_is_source_aware(report_module):
    assert "本报告基于 WorkBuddy 数据自动生成。" in \
        report_module.generate_markdown_report(_base_data())
    foot = report_module._footer_text(_tokens_only_data())
    assert "千问办公" in foot and "tokens_only" in foot


# ── 三、token-only 措辞一致性（用户实跑反馈的 5 处残留）──────────────────

@allure.feature("计价模式")
@allure.story("措辞")
@allure.title("tokens_only 下「计费等效 / 含估算成本 / 1/10 价计费」字样清零")
def test_no_billing_wording_in_tokens_only(report_module):
    for fmt, fn in (("md", report_module.generate_markdown_report),
                    ("html", report_module.generate_html_report)):
        out = fn(_tokens_only_data())
        for word in ("计费等效", "含估算成本", "1/10 价计费", "费用结算依据", "计费维度明细"):
            assert word not in out, f"{fmt} 版 tokens_only 报告残留「{word}」"


@allure.feature("计价模式")
@allure.story("措辞")
@allure.title("priced 下这些措辞原样保留（不得顺手改掉 WorkBuddy 口径）")
def test_billing_wording_kept_when_priced(report_module):
    md = report_module.generate_markdown_report(_base_data())
    assert "实际消耗 Token（计费等效）" in md
    assert "计费维度明细（费用结算依据）" in md
    assert "（含估算成本）" in md
    assert "1/10 价计费" in md


@allure.feature("计价模式")
@allure.story("会话数口径")
@allure.title("§一 会话总数补口径说明，与后文「N 个会话进入统计」自洽")
def test_session_count_note(report_module):
    md = report_module.generate_markdown_report(_tokens_only_data())
    # fixture：total_sessions=3，session_stats.rows=2 → 必须交代差的那 1 个是空会话
    assert "| 会话总数 | 3 个（含空会话；其中 2 个有 token 活动，其余为无调用的空会话） |" in md
    # 全部会话都有活动时不加括号，免得给正常窗口添噪音
    d = _tokens_only_data()
    d["summary"]["total_sessions"] = 2
    assert "含空会话" not in report_module.generate_markdown_report(d)


# ── 四、「未配置单价模型」整块的条件压制 ──────────────────────────────────

@allure.feature("计价模式")
@allure.story("缺失单价块")
@allure.title("tokens_only → 整块（含 pricing.local.json stub）压掉")
def test_unconfigured_block_hidden_in_tokens_only(report_module):
    d = _tokens_only_data()
    d["meta"]["unconfigured_models"] = ["flash"]
    for fmt, fn in (("md", report_module.generate_markdown_report),
                    ("html", report_module.generate_html_report)):
        out = fn(d)
        assert "本期有未配置单价的模型" not in out, f"{fmt} 版仍输出了缺失单价块"


@allure.feature("计价模式")
@allure.story("缺失单价块")
@allure.title("源不支持计费（SUPPORTS_COST=False）→ priced 也不给补价 stub")
def test_unconfigured_block_hidden_when_source_unsupported(report_module):
    d = _base_data()
    d["meta"]["cost_supported"] = False
    d["meta"]["unconfigured_models"] = ["flash"]
    md = report_module.generate_markdown_report(d)
    assert "本期有未配置单价的模型" not in md
    # 但成本章节照常（用户显式要 priced）
    assert "### 2.2 成本货币化" in md


@allure.feature("计价模式")
@allure.story("缺失单价块")
@allure.title("priced + 支持计费 + 只是部分模型缺价 → 该块保留（它这时真有用）")
def test_unconfigured_block_shown_when_priced_and_supported(report_module):
    d = _base_data()
    d["meta"]["unconfigured_models"] = ["brand-new-model"]
    md = report_module.generate_markdown_report(d)
    assert "本期有未配置单价的模型" in md
    assert "brand-new-model" in md
    assert "pricing.local.json" in md


# ── 五、第二批修正：口径措辞 / 真实计数 / 单通道 / 符号（含成对回归断言）────
#
# 每条都配一条 WorkBuddy 侧的「不许变」断言 —— 这些措辞是数据源专属的，
# 改 A 源不能顺手把 B 源的口径也改了（历史上已经串过味一次）。

@allure.feature("计价模式")
@allure.story("调用粒度")
@allure.title("千问办公=逐次模型调用，WorkBuddy 仍=generation 粒度")
def test_calls_granularity_is_source_aware(report_module):
    qw = report_module.generate_markdown_report(_tokens_only_data())
    assert "逐次模型调用粒度" in qw
    assert "generation 粒度" not in qw
    wb = report_module.generate_markdown_report(_base_data())
    assert "generation 粒度" in wb
    assert "逐次模型调用" not in wb
    html_qw = report_module.generate_html_report(_tokens_only_data())
    assert "调用次数·逐次模型调用" in html_qw
    assert "调用次数·generation 粒度" not in html_qw
    assert "调用次数·generation 粒度" in report_module.generate_html_report(_base_data())


@allure.feature("计价模式")
@allure.story("用户提问轮数")
@allure.title("有真实计数时不再摆聚类估算值；WorkBuddy 保留估算行")
def test_user_turn_count_preferred_over_estimate(report_module):
    d = _tokens_only_data()
    assert report_module._user_turn_count(d) == 5
    md = report_module.generate_markdown_report(d)
    assert "用户提问轮数（真实计数）" in md
    assert "估算请求数" not in md
    # WorkBuddy 形态没有 _human_turns → 回退估算行，一字不动
    wb = _base_data()
    assert report_module._user_turn_count(wb) is None
    wb_md = report_module.generate_markdown_report(wb)
    assert "估算请求数（generation 反推）" in wb_md
    assert "用户提问轮数" not in wb_md


@allure.feature("计价模式")
@allure.story("后台自动运行")
@allure.title("千问办公补一行后台运行，WorkBuddy 不出现该行")
def test_background_runs_row_only_for_qwenwork(report_module):
    md = report_module.generate_markdown_report(_tokens_only_data())
    assert "后台自动运行" in md and "227 次" in md and "失败 172 次" in md
    assert "后台自动运行" not in report_module.generate_markdown_report(_base_data())


@allure.feature("计价模式")
@allure.story("下期预测")
@allure.title("tokens_only 按日均 token 预测；priced 的 ¥ 文案逐字不变")
def test_outlook_forecast_token_variant(report_module):
    items = report_module.build_next_week_outlook(
        _tokens_only_data()["summary"], _tokens_only_data()["daily_tokens"], [], [],
        period_key="week", cost_hidden=True)
    assert any("下期用量预测（Token 口径）" in i for i in items)
    assert not any("¥" in i for i in items)
    d = _base_data()
    wb_items = report_module.build_next_week_outlook(
        d["summary"], d["daily_tokens"], [], [], period_key="week")
    assert any("下期用量预测**：按本期日均 ¥" in i or "按本期日均 ¥" in i for i in wb_items)
    # 展望必须承接 §4.3 的峰值日，不能再退化成「使用趋势平稳」
    tok = _tokens_only_data()
    with_anom = report_module.build_next_week_outlook(
        tok["summary"], tok["daily_tokens"], [], [], period_key="week", cost_hidden=True,
        anomaly_days=tok["cost_anomalies"]["token"]["daily"],
        background_runs=tok["meta"]["background_runs"])
    assert any("复盘 Token 峰值日" in i for i in with_anom)
    assert any("关注后台自动运行" in i for i in with_anom)
    assert "使用趋势平稳" not in "\n".join(with_anom)
    # 整份报告里也要看得到
    md = report_module.generate_markdown_report(tok)
    assert "下期用量预测（Token 口径）" in md and "复盘 Token 峰值日" in md


@allure.feature("计价模式")
@allure.story("单通道")
@allure.title("meta.single_channel → §3.2 不重复输出；未声明时照旧")
def test_single_channel_skips_section_32(report_module):
    md = report_module.generate_markdown_report(_tokens_only_data())
    assert "### 3.2" not in md
    assert "仅一层通道" in md
    html = report_module.generate_html_report(_tokens_only_data())
    assert "<h3>3.2" not in html
    wb = report_module.generate_markdown_report(_base_data())
    assert "### 3.2 按入口 / 配置模型" in wb
    assert "仅一层通道" not in wb


@allure.feature("计价模式")
@allure.story("异常符号")
@allure.title("token 口径条目用 📈，cost 口径仍是 💰")
def test_anomaly_emoji_matches_metric(report_module):
    qw = report_module.generate_markdown_report(_tokens_only_data())
    assert "- 📈 **写一个快排**" in qw
    assert "💰 **写一个快排**" not in qw
    wb = report_module.generate_markdown_report(_base_data())
    assert "💰 **写一个快排**" in wb          # 成本口径条目保持原样
    html_qw = report_module.generate_html_report(_tokens_only_data())
    assert "📈 <b>写一个快排</b>" in html_qw
    # token 口径条目在任何源都该是 📈（这是通用修复，不是千问专属）；
    # WorkBuddy 的 HTML 里同时有 💰（成本口径）与 📈（token 口径）两条，各自正确
    wb_html = report_module.generate_html_report(_base_data())
    assert "💰 <b>写一个快排</b>" in wb_html and "📈 <b>写一个快排</b>" in wb_html


@allure.feature("计价模式")
@allure.story("分母口径")
@allure.title("任务类型洞察交代「仅统计有 token 活动的会话」，WorkBuddy 相等时不加")
def test_task_insight_scope_note(report_module):
    d = _tokens_only_data()
    d["summary"]["total_sessions"] = 155          # 远大于有 token 活动的 3 个会话
    md = report_module.generate_markdown_report(d)
    assert "仅统计本期有 token 活动的 3 个会话" in md
    w = _base_data()
    # total_sessions 与 task_type_distribution 合计相等 → 不该冒出口径补充
    w["summary"]["total_sessions"] = sum(w["summary"]["task_type_distribution"].values())
    assert "仅统计本期有 token 活动的" not in report_module.generate_markdown_report(w)


@allure.feature("计价模式")
@allure.story("页脚署名")
@allure.title("中文标签两侧不留空格，ASCII 标签仍留空格")
def test_footer_spacing_by_script(report_module):
    assert report_module._footer_text(_tokens_only_data()) == \
        "本报告基于千问办公本机数据自动生成（计价模式 tokens_only）。"
    d = _base_data()
    d["meta"]["source"] = "codex"
    assert " Codex CLI 本机数据" in report_module._footer_text(d)
    d2 = _base_data()
    assert report_module._footer_text(d2) == "本报告基于 WorkBuddy 数据自动生成。"
