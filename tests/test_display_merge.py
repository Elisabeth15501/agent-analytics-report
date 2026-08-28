# -*- coding: utf-8 -*-
"""L0 · display_merge 显示层合并（免费额度版 / 收费版）— pytest + Allure 双可视化。

业务背景：WorkBuddy 对同一模型会提供两个入口 ——「免费额度版」（trace 记为
hy4-preview / hy3）与「免费额度用尽后的收费版」（trace 记为 hy4-preview-x / hy3-x）。
报告里用户只想看到一个模型条目（hy4-preview / hy3），但**费用必须只计收费版那部分**。

覆盖 collect_usage_data.py：
  - pricing.json 的 display_merge 配置被正确加载
  - aggregate_by_model（入口视图）与 aggregate_by_exec_model（账单口径）均合并显示为一行
  - 合并**不改变金额**：费用仍按各 trace 的 exec_model 单独计算
    （核心回归点：若合并时误用显示键计价，收费版用量会被限免价算成 ¥0）
  - 含收费调用的合并行不得被整体标注为「限时免费」
  - 未配置合并的模型（如 glm-5.2）行为不受影响
"""

import pytest

import allure

pytestmark = [pytest.mark.unit, pytest.mark.whitebox]

# 测试日期落在 hy4-preview（截至 2026-09-10）与 hy3（截至 2026-09-30）的限免期内
DATE = "2026-08-29"

# hy4-preview 刊例价（pricing.json）：输入 6 / 输出 18 元每百万 tokens
IN_PRICE, OUT_PRICE = 6.0, 18.0


def _mk(exec_model, model_key=None, inp=100_000, out=10_000, cached=0,
        eff_cost=None, date=DATE, session_id="s1"):
    """构造一条最小 trace，字段对齐 aggregate_traces_by 的读取。

    eff_cost 为 None 时，按 (inp, out, cached) 与刊例价推算，模拟 collect_traces
    已算好的 trace 级成本（账单口径 §3.1 会直接累加它）。
    """
    if eff_cost is None:
        eff_in = max(inp - cached * 0.9, 0)
        eff_cost = (eff_in / 1_000_000) * IN_PRICE + (out / 1_000_000) * OUT_PRICE
    return {
        "session_id": session_id,
        "model_key": model_key or exec_model,
        "model_name": exec_model,
        "exec_model": exec_model,
        "channel": "gateway",
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


def _hy4_traces():
    """一条限免调用 + 一条收费调用（model_key 与 exec_model 各自不同）。"""
    return [
        # 免费额度版：限免期内，计费 ¥0
        _mk("hy4-preview", model_key="hy4-preview", eff_cost=0.0, session_id="free"),
        # 收费版：按刊例价 6/18 计费
        _mk("hy4-preview-x", model_key="hy4-preview-x", session_id="paid"),
    ]


@allure.feature("display_merge 显示层合并")
@allure.story("配置加载")
@allure.title("pricing.json 的 display_merge 被正确加载")
@allure.severity(allure.severity_level.CRITICAL)
def test_display_merge_loaded(collector_module):
    dm = collector_module.DISPLAY_MERGE
    assert dm.get("hy4-preview-x") == "hy4-preview", f"hy4-preview-x 应合并到 hy4-preview，实得 {dm}"
    assert dm.get("hy3-x") == "hy3", f"hy3-x 应合并到 hy3，实得 {dm}"


@allure.feature("display_merge 显示层合并")
@allure.story("报告只显示一个模型条目")
@allure.title("免费额度版与收费版在报告里合并为一行（两个维度均合并）")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("fn_name", ["aggregate_by_model", "aggregate_by_exec_model"])
def test_merge_into_single_row(collector_module, fn_name):
    rows = collector_module.__dict__[fn_name](_hy4_traces())
    hy_rows = [r for r in rows if "hy4" in r["model"]]
    assert len(hy_rows) == 1, f"hy4 应合并为一行，实得 {[r['model'] for r in hy_rows]}"
    assert hy_rows[0]["model"] == "hy4-preview", f"合并后应显示 hy4-preview，实得 {hy_rows[0]['model']}"
    assert hy_rows[0]["calls"] == 2, f"calls 应为 2（两条 trace 合并），实得 {hy_rows[0]['calls']}"


@allure.feature("display_merge 显示层合并")
@allure.story("费用只计收费版用量")
@allure.title("合并行的费用只来自收费版，不被限免价吃掉（核心回归）")
@allure.severity(allure.severity_level.BLOCKER)
@pytest.mark.parametrize("fn_name", ["aggregate_by_model", "aggregate_by_exec_model"])
def test_merged_cost_counts_only_paid_variant(collector_module, fn_name):
    """若合并时误用「显示键」计价，两条 trace 都会按 hy4-preview 的限免价算成 ¥0。

    正确行为：按各 trace 的 exec_model 计价 —— 限免那条 ¥0、收费那条按 6/18，
    故合并行花费 = 0.78（仅收费版那部分），既不是 0，也不是 1.56（两边都计费）。
    """
    rows = collector_module.__dict__[fn_name](_hy4_traces())
    row = next(r for r in rows if r["model"] == "hy4-preview")
    expected = (100_000 / 1_000_000) * IN_PRICE + (10_000 / 1_000_000) * OUT_PRICE  # 0.78
    assert abs(row["effective_cost"] - expected) < 1e-6, (
        f"合并行花费应只计收费版 = {expected:.4f}，实得 {row['effective_cost']:.4f}"
    )
    assert row["effective_cost"] > 0, "合并行花费被限免价吃成 ¥0 —— 显示键/计费键未分离"


@allure.feature("display_merge 显示层合并")
@allure.story("费用只计收费版用量")
@allure.title("合并前后总金额守恒（合并只改分组，不碰钱）")
@allure.severity(allure.severity_level.BLOCKER)
@pytest.mark.parametrize("fn_name", ["aggregate_by_model", "aggregate_by_exec_model"])
def test_merge_preserves_total_amount(collector_module, fn_name):
    fn = collector_module.__dict__[fn_name]
    traces = _hy4_traces()

    merged_total = sum(r["effective_cost"] for r in fn(traces))

    saved = dict(collector_module.DISPLAY_MERGE)
    try:
        collector_module.DISPLAY_MERGE.clear()
        split_total = sum(r["effective_cost"] for r in fn(traces))
    finally:
        collector_module.DISPLAY_MERGE.update(saved)

    assert abs(merged_total - split_total) < 1e-9, (
        f"合并前后总金额应一致：合并后 {merged_total:.6f} vs 合并前 {split_total:.6f}"
    )


@allure.feature("display_merge 显示层合并")
@allure.story("限免标注")
@allure.title("含收费调用的合并行不得被整体标注为「限时免费」")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("fn_name", ["aggregate_by_model", "aggregate_by_exec_model"])
def test_merged_row_not_marked_timed_free(collector_module, fn_name):
    """合并行里夹着一条收费调用，若整行被标 timed_free，报告会显示「限免」却挂着花费。"""
    rows = collector_module.__dict__[fn_name](_hy4_traces())
    row = next(r for r in rows if r["model"] == "hy4-preview")
    assert row.get("timed_free") is False, "合并行含收费调用，不应被标注为限时免费"


@allure.feature("display_merge 显示层合并")
@allure.story("限免标注")
@allure.title("纯限免调用仍正确标注为「限时免费」")
@allure.severity(allure.severity_level.NORMAL)
@pytest.mark.parametrize("fn_name", ["aggregate_by_model", "aggregate_by_exec_model"])
def test_all_free_row_still_marked_timed_free(collector_module, fn_name):
    traces = [_mk("hy3", model_key="hy3", eff_cost=0.0)]
    rows = collector_module.__dict__[fn_name](traces)
    row = next(r for r in rows if r["model"] == "hy3")
    assert row.get("timed_free") is True, "全部落在限免期内的调用，应标注为限时免费"


@allure.feature("display_merge 显示层合并")
@allure.story("hy3 系列")
@allure.title("hy3 / hy3-x 同样合并为 hy3，且费用只计 hy3-x")
@allure.severity(allure.severity_level.CRITICAL)
def test_hy3_family_merge(collector_module):
    """hy3 刊例价 1/4（pricing.json），与 hy4 不同，验证合并不写死单价。"""
    traces = [
        _mk("hy3", model_key="hy3", inp=1_000_000, out=100_000, eff_cost=0.0, session_id="free"),
        _mk("hy3-x", model_key="hy3-x", inp=1_000_000, out=100_000, eff_cost=1.4, session_id="paid"),
    ]
    for fn_name in ("aggregate_by_model", "aggregate_by_exec_model"):
        rows = collector_module.__dict__[fn_name](traces)
        hy_rows = [r for r in rows if "hy3" in r["model"]]
        assert len(hy_rows) == 1, f"{fn_name}: hy3 应合并为一行，实得 {[r['model'] for r in hy_rows]}"
        assert hy_rows[0]["model"] == "hy3"
        # 入口视图按 1/4 重算：1M in ×1 + 100k out ×4 = 1.0 + 0.4 = 1.4
        assert abs(hy_rows[0]["effective_cost"] - 1.4) < 1e-6, (
            f"{fn_name}: 费用应只计 hy3-x = 1.4，实得 {hy_rows[0]['effective_cost']}"
        )


@allure.feature("display_merge 显示层合并")
@allure.story("未配置合并的模型")
@allure.title("未配置 display_merge 的模型不受影响（不同接口仍分两行）")
@allure.severity(allure.severity_level.NORMAL)
def test_unmerged_model_unaffected(collector_module):
    traces = [
        _mk("glm-5.2", model_key="glm-5.2", eff_cost=0.0, session_id="a"),
        _mk("glm-5.2", model_key="custom-local:glm-5.2", eff_cost=0.0, session_id="b"),
    ]
    rows = collector_module.aggregate_by_model(traces)
    keys = {r["model"] for r in rows}
    assert keys == {"glm-5.2", "custom-local:glm-5.2"}, f"未配置合并的模型应仍分两行，实得 {keys}"
