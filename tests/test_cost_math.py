# -*- coding: utf-8 -*-
"""L0 · 成本与定价计算（Cost & Pricing Math）— pytest + Allure 双可视化。

覆盖 collect_usage_data.py 中的纯计算函数：
  - glm52_discount_multiplier   GLM-5.2 夜猫子折扣系数（硬编码常量，强回归守卫）
  - effective_tokens_of         计费等效 Token（缓存 0.9x 折扣）
  - is_timed_free              限时免费判定（含截止日当天）
  - price_of / compute_cost / trace_cost  单价查询与成本计算（含未配置回退）

全部为白盒单元 + 冒烟，不依赖真实数据库 / traces。
"""

import pytest

import allure

pytestmark = [pytest.mark.unit, pytest.mark.whitebox]


# ── 折扣系数 ────────────────────────────────────────────────────────────────

@allure.feature("成本与定价计算（Cost & Pricing Math）")
@allure.story("GLM-5.2 夜猫子折扣系数")
@allure.title("glm52_discount_multiplier 对家族/非家族模型返回正确系数")
@allure.severity(allure.severity_level.NORMAL)
@pytest.mark.parametrize("name, expected", [
    ("glm-5.2", 0.79),
    ("GLM-5.2", 0.79),           # 归一化大小写
    ("glm-5.2-x", 0.50),
    ("glm-5.2X", 0.50),          # 归一化大小写
    ("glm-5.2x", 0.50),
    ("hy3", 1.0),                # 非家族恒 1.0
    ("minimax-m3", 1.0),
    ("", 1.0),                   # 空名 → default 语义 → 1.0
])
def test_glm52_discount_multiplier(collector_module, name, expected):
    got = collector_module.glm52_discount_multiplier(name)
    assert got == expected, f"glm52_discount_multiplier({name!r}) = {got}, 期望 {expected}"


# ── 计费等效 Token ──────────────────────────────────────────────────────────

@allure.feature("成本与定价计算（Cost & Pricing Math）")
@allure.story("计费等效 Token（缓存折扣）")
@allure.title("effective_tokens_of 在缓存折扣与下限 0 下正确")
@allure.severity(allure.severity_level.NORMAL)
@pytest.mark.parametrize("total, cached, expected", [
    (1000, 200, 820),            # 1000 - 200*0.9 = 820
    (100, 10, 91),               # 100 - 9 = 91
    (0, 0, 0),
    (500, 1000, 0),              # 负值被截断为 0
    (1234567, 0, 1234567),
])
def test_effective_tokens_of(collector_module, total, cached, expected):
    got = collector_module.effective_tokens_of(total, cached)
    assert got == expected, f"effective_tokens_of({total},{cached}) = {got}, 期望 {expected}"


# ── 限时免费判定 ────────────────────────────────────────────────────────────

@allure.feature("成本与定价计算（Cost & Pricing Math）")
@allure.story("限时免费判定")
@allure.title("is_timed_free 含截止当天、越期/无配置/无日期均返 False")
@allure.severity(allure.severity_level.NORMAL)
def test_is_timed_free_logic(collector_module):
    TIMED_FREE = collector_module.TIMED_FREE
    # 选取任一限免模型（如 hy3），动态取其截止日，避免硬编码
    if TIMED_FREE:
        model, deadline = next(iter(TIMED_FREE.items()))
        assert collector_module.is_timed_free(model, deadline) is True, \
            f"{model} 在截止日 {deadline} 应判为限免"
        # 截止日之后 → 不再限免
        from datetime import datetime, timedelta
        after = (datetime.strptime(deadline, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        assert collector_module.is_timed_free(model, after) is False, \
            f"{model} 在 {after}（截止后）不应限免"
    # 非限免模型 → 始终 False
    assert collector_module.is_timed_free("glm-5.2", "2026-08-31") is False
    # 日期缺失 → 安全返回 False
    assert collector_module.is_timed_free("hy3", None) is False


# ── 单价查询 ────────────────────────────────────────────────────────────────

@allure.feature("成本与定价计算（Cost & Pricing Math）")
@allure.story("单价查询")
@allure.title("price_of 对 router(auto)/免费档/已配置模型行为正确")
@allure.severity(allure.severity_level.NORMAL)
def test_price_of_router_and_free(collector_module):
    # 智能路由别名 → 无单一单价
    assert collector_module.price_of("auto") == (None, None)
    # 任意 :free 后缀 → 价 0（无论是否套 custom-local 前缀）
    assert collector_module.price_of("openrouter/foo:free") == (0.0, 0.0)
    assert collector_module.price_of("custom-local:z.ai/glm-5.2:free") == (0.0, 0.0)


@allure.feature("成本与定价计算（Cost & Pricing Math）")
@allure.story("单价查询")
@allure.title("price_of 对发布定价表内置模型返回数值单价")
@allure.severity(allure.severity_level.NORMAL)
def test_price_of_configured_model(collector_module):
    """发布版内置模型（如 glm-5.2）必须能查到数值单价，且非负。"""
    ip, op = collector_module.price_of("glm-5.2")
    assert ip is not None and op is not None, "glm-5.2 在发布定价表中应已配置单价"
    assert ip >= 0 and op >= 0


# ── 成本计算 ────────────────────────────────────────────────────────────────

@allure.feature("成本与定价计算（Cost & Pricing Math）")
@allure.story("成本计算")
@allure.title("compute_cost 与 price_of 公式一致（含 1e6 缩放与 4 位舍入）")
@allure.severity(allure.severity_level.NORMAL)
def test_compute_cost_matches_formula(collector_module):
    ip, op = collector_module.price_of("glm-5.2")
    expected = round((1_000_000 / 1_000_000) * ip + (2_000_000 / 1_000_000) * op, 4)
    got = collector_module.compute_cost(1_000_000, 2_000_000, "glm-5.2")
    assert got == expected, f"compute_cost 公式不符：got {got}, expected {expected}"


@allure.feature("成本与定价计算（Cost & Pricing Math）")
@allure.story("成本计算")
@allure.title("compute_cost 对未配置模型返回 None（不瞎算）")
@allure.severity(allure.severity_level.NORMAL)
def test_compute_cost_unconfigured_none(collector_module):
    ptr = "__zzz_unknown_model_xyz__"
    assert collector_module.compute_cost(1_000_000, 1_000_000, ptr) is None


@allure.feature("成本与定价计算（Cost & Pricing Math）")
@allure.story("成本计算")
@allure.title("trace_cost 未配置时回退历史 blended 估算（DEFAULT_BLENDED_PER_MILLION=1.0）")
@allure.severity(allure.severity_level.NORMAL)
def test_trace_cost_blended_fallback(collector_module):
    ptr = "__zzz_unknown_model_xyz__"
    # (1M+1M)/1M * 1.0 = 2.0
    got = collector_module.trace_cost(1_000_000, 1_000_000, ptr)
    assert got == 2.0, f"trace_cost blended 回退应为 2.0，实得 {got}"


@allure.feature("成本与定价计算（Cost & Pricing Math）")
@allure.story("成本计算")
@allure.title("trace_cost 与 compute_cost 在已配置模型上一致")
@allure.severity(allure.severity_level.NORMAL)
def test_trace_cost_matches_compute_when_configured(collector_module):
    c = collector_module.compute_cost(500_000, 300_000, "glm-5.2")
    t = collector_module.trace_cost(500_000, 300_000, "glm-5.2")
    assert c is not None and t == c
