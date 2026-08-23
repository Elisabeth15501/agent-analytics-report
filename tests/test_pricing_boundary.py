# -*- coding: utf-8 -*-
"""L2 · 定价边界（Pricing Boundary）— pytest + Allure 双可视化。

在 L0 核心成本数学之上，补强「边界 / 异常 / 渠道 / 已下架」维度：
  - price_of 显式 channel 分支：router→(None,None)、openrouter-free→(0,0)、
    custom-local 命中/未命中、gateway 未知模型→(None,None)
  - 已下架模型（delisted）：历史调用仍需计价 → price_of 仍返回数值单价（非空）
  - compute_cost 边界：零 token→0、负数 token 不截断（如实反映公式）、大数 4 位舍入
  - trace_cost 边界：未配置/router 通道回退 blended 估算（DEFAULT_BLENDED_PER_MILLION=1.0）

全部白盒 / 回归，使用合成输入，不依赖真实数据库 / traces。
"""

import pytest

import allure

pytestmark = [pytest.mark.unit, pytest.mark.regression]


# ── price_of 显式 channel 分支 ───────────────────────────────────────────────

@allure.feature("定价边界（Pricing Boundary）")
@allure.story("price_of 显式 channel 分支")
@allure.title("channel='router' 无单一单价 → (None, None)")
@allure.severity(allure.severity_level.NORMAL)
@pytest.mark.parametrize("model", ["auto", "glm-5.2", "unknown-xyz"])
def test_price_of_channel_router(collector_module, model):
    assert collector_module.price_of(model, channel="router") == (None, None)


@allure.feature("定价边界（Pricing Boundary）")
@allure.story("price_of 显式 channel 分支")
@allure.title("channel='openrouter-free' 一律 0 价（无论底层模型）")
@allure.severity(allure.severity_level.NORMAL)
@pytest.mark.parametrize("model", ["openrouter/foo:free", "glm-5.2", "kimi-k3"])
def test_price_of_channel_openrouter_free(collector_module, model):
    assert collector_module.price_of(model, channel="openrouter-free") == (0.0, 0.0)


@allure.feature("定价边界（Pricing Boundary）")
@allure.story("price_of 显式 channel 分支")
@allure.title("channel='custom-local' 命中配置表返回数值单价")
@allure.severity(allure.severity_level.NORMAL)
def test_price_of_channel_custom_local_configured(collector_module):
    # glm-5.2 在 MODEL_PRICING 中存在，custom-local 通道默认对齐网关同名价
    ip, op = collector_module.price_of("glm-5.2", channel="custom-local")
    assert (ip, op) == (8.0, 28.0)


@allure.feature("定价边界（Pricing Boundary）")
@allure.story("price_of 显式 channel 分支")
@allure.title("channel='custom-local' 未知模型 → (None, None)")
@allure.severity(allure.severity_level.NORMAL)
def test_price_of_channel_custom_local_unknown(collector_module):
    assert collector_module.price_of("__zzz_unknown_model_xyz__", channel="custom-local") == (None, None)


@allure.feature("定价边界（Pricing Boundary）")
@allure.story("price_of 显式 channel 分支")
@allure.title("channel='gateway' 未知模型 → (None, None)（不瞎算）")
@allure.severity(allure.severity_level.NORMAL)
def test_price_of_channel_gateway_unknown(collector_module):
    assert collector_module.price_of("__zzz_unknown_model_xyz__", channel="gateway") == (None, None)


# ── 已下架模型（delisted）────────────────────────────────────────────────────

@allure.feature("定价边界（Pricing Boundary）")
@allure.story("已下架模型计价")
@allure.title("DELISTED_MODELS 非空，且下架模型历史调用仍能查到数值单价")
@allure.severity(allure.severity_level.NORMAL)
def test_delisted_models_priced(collector_module):
    delisted = collector_module.DELISTED_MODELS
    if not delisted:
        pytest.skip("当前 pricing 未声明已下架模型，跳过")
    model = next(iter(delisted))
    ip, op = collector_module.price_of(model)
    assert ip is not None and op is not None, \
        f"已下架模型 {model} 历史调用仍需计价，price_of 不应返回 (None, None)"
    assert ip >= 0 and op >= 0


# ── compute_cost 边界 ────────────────────────────────────────────────────────

@allure.feature("定价边界（Pricing Boundary）")
@allure.story("compute_cost 边界")
@allure.title("compute_cost(0,0,...) 对已知模型返回 0.0")
@allure.severity(allure.severity_level.NORMAL)
def test_compute_cost_zero_tokens(collector_module):
    assert collector_module.compute_cost(0, 0, "glm-5.2") == 0.0


@allure.feature("定价边界（Pricing Boundary）")
@allure.story("compute_cost 边界")
@allure.title("compute_cost 负数 token 不截断，如实反映公式（调用方须保证非负）")
@allure.severity(allure.severity_level.NORMAL)
def test_compute_cost_negative_not_clamped(collector_module):
    # (1M/1M)*8 + (-500k/1M)*28 = 8.0 - 14.0 = -6.0，负数如实返回（不 clamp 到 0）
    got = collector_module.compute_cost(1_000_000, -500_000, "glm-5.2")
    assert got == -6.0, f"负数 token 应如实返回 -6.0，实得 {got}"


@allure.feature("定价边界（Pricing Boundary）")
@allure.story("compute_cost 边界")
@allure.title("compute_cost 大数按 4 位小数舍入，与公式一致")
@allure.severity(allure.severity_level.NORMAL)
def test_compute_cost_large_rounding(collector_module):
    got = collector_module.compute_cost(123_456_789, 987_654_321, "glm-5.2")
    expected = round((123_456_789 / 1_000_000) * 8.0 + (987_654_321 / 1_000_000) * 28.0, 4)
    assert got == expected, f"compute_cost 大数舍入不符：got {got}, expected {expected}"


# ── trace_cost 边界 ──────────────────────────────────────────────────────────

@allure.feature("定价边界（Pricing Boundary）")
@allure.story("trace_cost 边界")
@allure.title("trace_cost 未配置/router 通道回退 blended 估算（DEFAULT_BLENDED_PER_MILLION=1.0）")
@allure.severity(allure.severity_level.NORMAL)
def test_trace_cost_router_blended_fallback(collector_module):
    # channel='router' → compute_cost 返 None → 回退 (in+out)/1M * 1.0
    got = collector_module.trace_cost(2_000_000, 2_000_000, "auto", channel="router")
    assert got == 4.0, f"trace_cost router 回退应为 4.0，实得 {got}"


@allure.feature("定价边界（Pricing Boundary）")
@allure.story("trace_cost 边界")
@allure.title("trace_cost 未知模型（无通道）回退 blended：与 (in+out)/1M*1.0 一致")
@allure.severity(allure.severity_level.NORMAL)
def test_trace_cost_unknown_blended(collector_module):
    got = collector_module.trace_cost(3_000_000, 2_000_000, "__zzz_unknown_model_xyz__")
    assert got == 5.0, f"trace_cost 未知模型 blended 应为 5.0，实得 {got}"
