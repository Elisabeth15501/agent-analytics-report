# -*- coding: utf-8 -*-
"""L0 · 时间窗口与日历对齐（Calendar Alignment）— pytest + Allure 双可视化。

覆盖：
  - collect_usage_data.resolve_date_range  预设周期（day/week/month/year）日历对齐
  - collect_usage_data.resolve_date_range  绝对日期 → 报告类型识别（day/week/month/year/custom）
  - generate_report._calendar_period      报告标题的 ISO 日历标签

全部白盒单元，使用“今天”动态计算，不依赖真实数据。
"""

from datetime import date, datetime, timedelta

import pytest

import allure

pytestmark = [pytest.mark.unit, pytest.mark.whitebox]


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("resolve_date_range 预设周期")
@allure.title("day 周期返回今天（起止相同）")
@allure.severity(allure.severity_level.NORMAL)
def test_resolve_day(collector_module):
    s, e, pk, label = collector_module.resolve_date_range(period="day")
    today = date.today().strftime("%Y-%m-%d")
    assert s == e == today
    assert pk == "day" and label == "日报"


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("resolve_date_range 预设周期")
@allure.title("week 周期对齐到当前 ISO 周（周一~周日，7 天）")
@allure.severity(allure.severity_level.CRITICAL)
def test_resolve_week(collector_module):
    s, e, pk, label = collector_module.resolve_date_range(period="week")
    sd = datetime.strptime(s, "%Y-%m-%d").date()
    ed = datetime.strptime(e, "%Y-%m-%d").date()
    assert pk == "week" and label == "周报"
    assert sd.weekday() == 0, f"周起始应为周一，实得 {sd}"
    assert (ed - sd).days == 6, f"周跨度应为 6 天（7 天含两端），实得 {ed-sd}"


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("resolve_date_range 预设周期")
@allure.title("month 周期对齐到当前自然月（1 日~月底）")
@allure.severity(allure.severity_level.NORMAL)
def test_resolve_month(collector_module):
    s, e, pk, label = collector_module.resolve_date_range(period="month")
    sd = datetime.strptime(s, "%Y-%m-%d").date()
    ed = datetime.strptime(e, "%Y-%m-%d").date()
    assert pk == "month" and label == "月报"
    assert sd.day == 1, f"月起始应为 1 日，实得 {sd}"
    # 月底
    import calendar
    last = calendar.monthrange(sd.year, sd.month)[1]
    assert ed.day == last, f"月结束应为月底 {last} 日，实得 {ed}"
    assert ed >= sd


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("resolve_date_range 预设周期")
@allure.title("year 周期对齐到当前自然年（1/1~12/31）")
@allure.severity(allure.severity_level.NORMAL)
def test_resolve_year(collector_module):
    s, e, pk, label = collector_module.resolve_date_range(period="year")
    assert pk == "year" and label == "年报"
    assert s == f"{date.today().year}-01-01"
    assert e == f"{date.today().year}-12-31"


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("resolve_date_range 绝对日期分类")
@allure.title("绝对单日 → 日报")
@allure.severity(allure.severity_level.NORMAL)
def test_absolute_single_day(collector_module):
    s, e, pk, label = collector_module.resolve_date_range(start="2026-03-15", end="2026-03-15")
    assert pk == "day" and label == "日报"


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("resolve_date_range 绝对日期分类")
@allure.title("绝对完整 7 天 → 周报")
@allure.severity(allure.severity_level.NORMAL)
def test_absolute_full_week(collector_module):
    # 2026-03-02(周一) ~ 2026-03-08(周日) 为完整周
    s, e, pk, label = collector_module.resolve_date_range(start="2026-03-02", end="2026-03-08")
    assert pk == "week" and label == "周报"


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("resolve_date_range 绝对日期分类")
@allure.title("绝对完整自然月 → 月报")
@allure.severity(allure.severity_level.NORMAL)
def test_absolute_full_month(collector_module):
    s, e, pk, label = collector_module.resolve_date_range(start="2026-02-01", end="2026-02-28")
    assert pk == "month" and label == "月报"


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("resolve_date_range 绝对日期分类")
@allure.title("绝对完整自然年 → 年报")
@allure.severity(allure.severity_level.NORMAL)
def test_absolute_full_year(collector_module):
    s, e, pk, label = collector_module.resolve_date_range(start="2026-01-01", end="2026-12-31")
    assert pk == "year" and label == "年报"


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("resolve_date_range 绝对日期分类")
@allure.title("任意非标准跨度 → 自定义报告")
@allure.severity(allure.severity_level.NORMAL)
def test_absolute_custom(collector_module):
    s, e, pk, label = collector_module.resolve_date_range(start="2026-03-10", end="2026-03-20")
    assert pk == "custom" and "自定义" in label


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("resolve_date_range 绝对日期分类")
@allure.title("无效日期格式抛 ValueError")
@allure.severity(allure.severity_level.NORMAL)
def test_absolute_invalid_date_raises(collector_module):
    with pytest.raises(ValueError):
        collector_module.resolve_date_range(start="not-a-date", end="2026-03-20")


# ── _calendar_period 标签 ───────────────────────────────────────────────────

@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("_calendar_period 标签")
@allure.title("周报标签含 周报 / W周次 / 起止")
@allure.severity(allure.severity_level.NORMAL)
def test_calendar_period_week(report_module):
    label = report_module._calendar_period({
        "period": "week", "start_date": "2026-03-02", "end_date": "2026-03-08"})
    assert "周报" in label and "W" in label
    assert "2026-03-02" in label and "2026-03-08" in label


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("_calendar_period 标签")
@allure.title("月报标签含 月报 与 YYYY-MM")
@allure.severity(allure.severity_level.NORMAL)
def test_calendar_period_month(report_module):
    label = report_module._calendar_period({
        "period": "month", "start_date": "2026-03-01", "end_date": "2026-03-31"})
    assert "月报" in label and "2026-03" in label


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("_calendar_period 标签")
@allure.title("年报标签含 年报 与年份")
@allure.severity(allure.severity_level.NORMAL)
def test_calendar_period_year(report_module):
    label = report_module._calendar_period({
        "period": "year", "start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert "年报" in label and "2026" in label


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("_calendar_period 标签")
@allure.title("日报标签含 日报 与日期")
@allure.severity(allure.severity_level.NORMAL)
def test_calendar_period_day(report_module):
    label = report_module._calendar_period({
        "period": "day", "start_date": "2026-03-15", "end_date": "2026-03-15"})
    assert "日报" in label and "2026-03-15" in label


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("_calendar_period 标签")
@allure.title("自定义报告标签含 自定义报告 与起止")
@allure.severity(allure.severity_level.NORMAL)
def test_calendar_period_custom(report_module):
    label = report_module._calendar_period({
        "period": "custom", "start_date": "2026-03-10", "end_date": "2026-03-20"})
    assert "自定义报告" in label and "2026-03-10" in label and "2026-03-20" in label


@allure.feature("时间窗口与日历对齐（Calendar Alignment）")
@allure.story("_calendar_period 标签")
@allure.title("_calendar_period 对空 meta 不崩")
@allure.severity(allure.severity_level.NORMAL)
def test_calendar_period_empty_meta(report_module):
    assert "自定义报告" in report_module._calendar_period({})
