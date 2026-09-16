# -*- coding: utf-8 -*-
"""v1.7.1 时段定价回归 — pytest + Allure 双可视化。

覆盖 Phase B 三项：

B4 按时间戳应用 timed_free：hy4-preview 夜间 23:00–08:00 免费（2026-09-11 起）。
  此前只传日期级 timed_free，夜间调用被当作白天计费，是低置信度模型被严重高估的根因。
B5 按时间戳应用 mode_rates 峰谷双档：deepseek-v4.1-flash 周一至周五 09:00–12:00 /
  14:00–18:00 用 peak 价（input=2.0/output=8.0），其余时段用空闲价（1.0/4.0）。
B6 促销跨期（手动段）：glm-5.3 / glm-5.3-flash 发布期 5 折（至 2026-09-09）。

全部基于 pricing.json 的真实 scheduled_pricing 配置 + 真实模型刊例价，白盒验证。
"""

import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.whitebox, pytest.mark.regression]

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ca_core as c  # noqa: E402


# --------------------------------------------------------------------------
# call_time_of 解析
# --------------------------------------------------------------------------

def test_call_time_of_ms_timestamp():
    from datetime import datetime, timezone, timedelta
    _tz = timezone(timedelta(hours=8))
    _dt = datetime(2026, 9, 12, 11, 30, 0, tzinfo=_tz)
    _ms = int(_dt.timestamp() * 1000)
    t = c.call_time_of(_ms)
    assert t["as_of_date"] == "2026-09-12"
    assert t["as_of_hour"] == 11
    assert t["as_of_dow"] == 6  # 周六


def test_call_time_of_iso_space():
    t = c.call_time_of("2026-09-12 23:30:00")
    assert t["as_of_date"] == "2026-09-12"
    assert t["as_of_hour"] == 23
    assert t["as_of_dow"] == 6


def test_call_time_of_iso_tz():
    t = c.call_time_of("2026-09-13T03:00:00+08:00")
    assert t["as_of_date"] == "2026-09-13"
    assert t["as_of_hour"] == 3
    assert t["as_of_dow"] == 7  # 周日


def test_call_time_of_none_falls_back_to_date():
    t = c.call_time_of(None, date_str="2026-09-16")
    assert t["as_of_date"] == "2026-09-16"
    assert t["as_of_hour"] is None
    assert t["as_of_dow"] is None


# --------------------------------------------------------------------------
# B4 · hy4-preview 夜间免费
# --------------------------------------------------------------------------

def test_b4_hy4_preview_night_free():
    ip, op = c.price_of("hy4-preview", **c.call_time_of("2026-09-12 23:30:00"))
    assert (ip, op) == (0.0, 0.0)
    assert c.is_scheduled_free("hy4-preview", **c.call_time_of("2026-09-12 23:30:00"))


def test_b4_hy4_preview_night_window_edge():
    # 23:00 起始、08:00 结束（含 07:59，不含 08:00）
    assert c.is_scheduled_free("hy4-preview", **c.call_time_of("2026-09-13 23:00:00"))
    assert c.is_scheduled_free("hy4-preview", **c.call_time_of("2026-09-13 07:59:00"))
    assert not c.is_scheduled_free("hy4-preview", **c.call_time_of("2026-09-13 08:00:00"))


def test_b4_hy4_preview_daytime_charged():
    ip, op = c.price_of("hy4-preview", **c.call_time_of("2026-09-12 11:30:00"))
    assert ip is not None and ip > 0
    assert not c.is_scheduled_free("hy4-preview", **c.call_time_of("2026-09-12 11:30:00"))


def test_b4_hy4_preview_no_hour_is_conservative():
    # 只有日期、没有小时：夜间免费规则（声明了 hours）不生效，宁可白天价也不乱免
    ip, op = c.price_of("hy4-preview", as_of_date="2026-09-16")
    assert ip is not None and ip > 0


# --------------------------------------------------------------------------
# B5 · deepseek-v4.1-flash 峰谷双档
# --------------------------------------------------------------------------

def test_b5_peak_window_weekday_morning():
    # 周一 10:00 → 高峰（注意 09:00-12:00 含 10:00）
    ip, op = c.price_of("deepseek-v4.1-flash", **c.call_time_of("2026-09-14 10:00:00"))
    assert (ip, op) == (2.0, 8.0)


def test_b5_offpeak_weekday_noon():
    # 周一 13:00 → 午休空闲档
    ip, op = c.price_of("deepseek-v4.1-flash", **c.call_time_of("2026-09-14 13:00:00"))
    assert (ip, op) == (1.0, 4.0)


def test_b5_offpeak_weekend():
    # 周六（dow 不在 1-5）→ 空闲档
    ip, op = c.price_of("deepseek-v4.1-flash", **c.call_time_of("2026-09-12 10:00:00"))
    assert (ip, op) == (1.0, 4.0)


def test_b5_peak_window_evening():
    # 周一 17:30 → 14:00-18:00 高峰尾段
    ip, op = c.price_of("deepseek-v4.1-flash", **c.call_time_of("2026-09-14 17:30:00"))
    assert (ip, op) == (2.0, 8.0)


# --------------------------------------------------------------------------
# B6 · glm-5.3 / glm-5.3-flash 促销跨期 5 折
# --------------------------------------------------------------------------

def test_b6_glm53_promo_active():
    base_ip = c.MODEL_PRICING["glm-5.3"]["input"]
    ip, op = c.price_of("glm-5.3", **c.call_time_of("2026-09-05 10:00:00"))
    assert ip == pytest.approx(base_ip * 0.5)


def test_b6_glm53_promo_expired():
    base_ip = c.MODEL_PRICING["glm-5.3"]["input"]
    ip, op = c.price_of("glm-5.3", **c.call_time_of("2026-09-16 10:00:00"))
    assert ip == pytest.approx(base_ip)  # 恢复全价


def test_b6_glm53_flash_promo_active():
    ip, op = c.price_of("glm-5.3-flash", **c.call_time_of("2026-09-05 10:00:00"))
    assert ip == pytest.approx(c.MODEL_PRICING["glm-5.3-flash"]["input"] * 0.5)


# --------------------------------------------------------------------------
# 不受影响模型
# --------------------------------------------------------------------------

def test_unaffected_model_untouched():
    ip, op = c.price_of("glm-5.2", **c.call_time_of("2026-09-16 10:00:00"))
    assert (ip, op) == (c.MODEL_PRICING["glm-5.2"]["input"],
                         c.MODEL_PRICING["glm-5.2"]["output"])
