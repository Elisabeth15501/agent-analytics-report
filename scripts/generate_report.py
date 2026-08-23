#!/usr/bin/env python3
"""
generate_report.py — Agent 使用情况周报生成器

整合所有数据源，生成多种格式的报告，包含：
  一、概览统计
  二、Token 消耗可视化（含成本货币化）
  三、任务类型统计
  四、任务 Token 消耗统计
  五、技能使用统计
  六、自动化任务运行情况
  七、产出物清单
  八、核心洞察与建议
  九、下周展望

支持输出格式：markdown（默认）、html、json

用法:
  python generate_report.py [data.json] [--days N] [--output report.xxx] [--format markdown|html|json]
"""

import argparse
import html
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))


def _esc(value):
    """HTML 转义：防止会话标题 / 模型名 / 自动化名等用户派生文本注入 HTML（XSS）。"""
    return html.escape(str(value), quote=True)


def _fmt_generated_at(dt=None):
    """生成时间展示格式：YYYY/MM/DD HH:MM:SS (UTC+08:00)，便于人类阅读。

    旧版曾用 ISO 8601 全精度（含微秒与 +08:00 后缀），对用户不友好；此处改回
    紧凑可读格式，时区以 UTC 偏移呈现（如 UTC+08:00）。
    """
    dt = dt or datetime.now(TZ)
    off = dt.utcoffset() or timedelta(0)
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hh, mm = divmod(total // 60, 60)
    tz = f"UTC{sign}{hh:02d}:{mm:02d}"
    return f"{dt.strftime('%Y/%m/%d %H:%M:%S')} ({tz})"

# 时间窗口标签（与 collect_usage_data.PERIOD_* 保持一致）
_PERIOD_LABEL = {"day": "日报", "week": "周报", "month": "月报", "year": "年报", "custom": "自定义报告"}
_PERIOD_SHORT = {"day": "当日", "week": "本周", "month": "本月", "year": "本年", "custom": "本期"}
_PERIOD_NEXT = {"day": "次日", "week": "下周", "month": "下月", "year": "明年", "custom": "下期"}


def _period_labels(meta):
    """根据 meta.period 返回 (整期标签, 本期短标签, 下期短标签)。回退兼容旧数据（无 period 字段）。"""
    key = (meta or {}).get("period", "week")
    return (_PERIOD_LABEL.get(key, "周报"),
            _PERIOD_SHORT.get(key, "本周"),
            _PERIOD_NEXT.get(key, "下周"))


def _calendar_period(meta):
    """将报告周期识别为 ISO 8601 日历日期标签。

      - 日报 · 2026-08-01
      - 周报 · 2026-W31（2026-07-27/2026-08-02）
      - 月报 · 2026-08
      - 年报 · 2026
      - 自定义报告 · 2026-07-26/2026-08-01
    """
    meta = meta or {}
    pk = meta.get("period", "week")
    start = meta.get("start_date", "")
    end = meta.get("end_date", "")
    try:
        if pk == "day":
            return f"日报 · {start}" if start else "日报"
        if pk == "week":
            d = datetime.strptime(end, "%Y-%m-%d")
            iso_year, iso_week, _ = d.isocalendar()
            return f"周报 · {iso_year}-W{iso_week:02d}（{start}/{end}）"
        if pk == "month":
            d = datetime.strptime(end, "%Y-%m-%d")
            return f"月报 · {d.year}-{d.month:02d}"
        if pk == "year":
            d = datetime.strptime(end, "%Y-%m-%d")
            return f"年报 · {d.year}"
    except Exception:
        pass
    return f"自定义报告 · {start}/{end}" if (start or end) else "自定义报告"


def format_number(n):
    """数字格式化：K/M/G 后缀"""
    if n is None:
        return "0"
    n = float(n)
    if n >= 1_000_000_000:
        return f"{n / 1e9:.2f}G"
    elif n >= 1_000_000:
        return f"{n / 1e6:.2f}M"
    elif n >= 1_000:
        return f"{n / 1e3:.1f}K"
    return str(int(n)) if n == int(n) else f"{n:.1f}"


def format_file_size(bytes_val):
    """文件大小格式化：B / KB / MB"""
    if bytes_val is None:
        return "0B"
    bytes_val = float(bytes_val)
    if bytes_val >= 1_000_000:
        return f"{bytes_val / 1_000_000:.1f}MB"
    elif bytes_val >= 1_000:
        return f"{bytes_val / 1_000:.1f}KB"
    return f"{int(bytes_val)}B"


def _disp_width(s):
    """等宽字体下的显示宽度：CJK / 全角字符计 2，其余（含 ASCII）计 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(s))


def _pad_label(s, width):
    """按显示宽度右侧补空格，使等宽字体下中文 / 英文混排的标签列对齐。"""
    return str(s) + " " * max(0, width - _disp_width(s))


# 环形图调色板（与表格配色协调）
_DONUT_PALETTE = [
    "#3498db", "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6",
    "#1abc9c", "#e67e22", "#34495e", "#16a085", "#c0392b",
    "#8e44ad", "#27ae60",
]


def build_donut_chart(stats, title="实际消耗 Token 占比（按任务类型，计费等效）",
                      center_label="实际消耗", value_key="effective_tokens", unit=""):
    """生成自包含内联 SVG 环形图（双主题兼容，currentColor + CSS 变量）。

    stats: 含 task_type 与各数值字段的列表。value_key 指定扇形取值字段，
    unit / center_label 控制图例单位与中心文字，使同一函数既能画「任务类型
    Token 占比」（默认），也能画「每会话成本分布」（value_key="count"、
    unit=" 会话"、center_label="会话数"）。无数据时返回空串；不依赖外部 CDN。
    """
    items = [s for s in stats if s.get(value_key, 0) > 0]
    total = sum(s.get(value_key, 0) for s in items)
    if total <= 0:
        return ""
    # 按占比降序，保证配色稳定
    items = sorted(items, key=lambda x: x.get(value_key, 0), reverse=True)

    cx, cy, r, sw = 110, 110, 80, 34
    circ = 2 * math.pi * r
    cum = 0.0
    arcs = []
    legend = []
    for i, s in enumerate(items):
        frac = s.get(value_key, 0) / total
        seg_len = frac * circ
        color = _DONUT_PALETTE[i % len(_DONUT_PALETTE)]
        pct = frac * 100
        # 用 stroke-dasharray 画出圆环段；dashoffset 让各段首尾相接
        arcs.append(
            f'        <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{color}" stroke-width="{sw}" '
            f'stroke-dasharray="{seg_len:.2f} {circ - seg_len:.2f}" '
            f'stroke-dashoffset="{-cum:.2f}" />'
        )
        legend.append(
            f'            <div class="legend-item">'
            f'<span class="swatch" style="background:{color}"></span>'
            f'{_esc(s["task_type"])}：{pct:.1f}%'
            f'<span class="pct">（{format_number(s.get(value_key, 0))}{unit}）</span></div>'
        )
        cum += seg_len

    svg = f"""    <div class="chart-pie">
        <svg width="220" height="220" viewBox="0 0 220 220" role="img" aria-label="{title}">
            <g transform="rotate(-90 {cx} {cy})">
                <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" style="stroke:var(--table-border)" stroke-width="{sw}" />
{chr(10).join(arcs)}
            </g>
            <text x="{cx}" y="{cy - 4}" text-anchor="middle" font-size="15" font-weight="bold" style="fill:var(--accent-fg)">{format_number(total)}</text>
            <text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="11" style="fill:var(--muted)">{center_label}</text>
        </svg>
        <div class="legend">
{chr(10).join(legend)}
        </div>
    </div>"""
    return svg


def build_session_cost_bar_md(buckets, title="每会话成本分布（按会话数）"):
    """Markdown 横向条形图（fenced ``` 代码块）：每会话成本分布，按会话数。

    与 HTML 版环形图数据同源：成本区间 → 会话数。纯文本代码块随查看器浅/深主题
    自适应，各区间独立一行绝不重叠，窄区间只是短条不会糊；行尾标注会话数。
    """
    items = [b for b in buckets if b.get("count", 0) > 0]
    if not items:
        return ""
    maxc = max(b["count"] for b in items) or 1
    bar_w = 20
    label_w = max((_disp_width(b["label"]) for b in items), default=10)
    rows = [f"**{title}**", "", f"{_pad_label('成本区间', label_w)} | {'会话数'}"]
    for b in items:
        n = max(int(bar_w * b["count"] / maxc), 1)
        bar = "█" * n
        rows.append(f"{_pad_label(b['label'], label_w)} | {bar} {b['count']}")
    return "```\n" + "\n".join(rows) + "\n```"


def build_task_type_chart_md(stats, title="各任务类型 实际消耗 Token 占比"):
    """Markdown 横向条形图：各任务类型 实际消耗 Token 占比。

    替换原先的 mermaid 饼图——因为 mermaid 饼图在 .md 预览里无法稳定满足：
    (1) 浅/深外观下文字可能消失（强制 theme 不可靠）；
    (2) 窄扇区标签会糊在一起（mermaid 无「仅图例」模式，标签直接画在扇区上）。

    横向条形图用 fenced ``` 纯文本代码块，与 3.1/3.2 模型条形图同风格：
    - 主题安全：文字取查看器代码块前景色，切换浅/深外观绝不消失；
    - 永不重叠：每个任务类型独立一行；
    - 窄项只是短条，不会糊；标签用 _pad_label 按显示宽度对齐（兼容中英文混排）。
    """
    items = [s for s in stats
             if s.get("effective_tokens", 0) > 0]
    total = sum(s.get("effective_tokens", 0) for s in items)
    if total <= 0:
        return ""
    # 合并窄项，保持行数清爽
    MERGE_THRESHOLD = 2.5  # 百分比
    rows_data = []
    other_pct = 0.0
    other_tok = 0
    for s in items:
        pct = s.get("effective_tokens", 0) / total * 100
        if pct < MERGE_THRESHOLD and s["task_type"] != "其他":
            other_pct += pct
            other_tok += s.get("effective_tokens", 0)
        else:
            rows_data.append([s["task_type"], pct, s.get("effective_tokens", 0)])
    if other_pct > 0:
        rows_data.append(["其他", other_pct, other_tok])
    rows_data.sort(key=lambda x: x[1], reverse=True)
    rows_data = rows_data[:10]
    max_pct = max(r[1] for r in rows_data) or 1
    bar_w = 32
    label_w = 16
    out = []
    for label, pct, toks in rows_data:
        n = max(int(bar_w * pct / max_pct), 1)
        bar = "█" * n
        lab = _pad_label(label, label_w)
        out.append(f"{lab} | {bar} {pct:.1f}% ({format_number(toks)})")
    return "```\n" + "\n".join(out) + "\n```"


# ── 模型使用与成本对比（新章节）────────────────────────────
def build_model_cost_chart_md(model_stats, title="各模型估算实际花费对比"):
    """Markdown 横向条形图：各模型估算实际花费对比（与 HTML 条形图对齐）。

    用 fenced ``` 代码块承载 ASCII 横向条：查看器按代码块主题自适应明暗，
    且对超长模型名（如 custom-local:GLM-4.5-air）无渲染问题。条形长度按
    最大花费线性映射，行尾标注金额，与 HTML `build_model_cost_chart` 同源。
    """
    items = [m for m in model_stats if (m.get("effective_cost", 0) or 0) > 0]
    if not items:
        return ""
    items = sorted(items, key=lambda x: x.get("effective_cost", 0), reverse=True)
    maxc = max(m.get("effective_cost", 0) for m in items) or 1
    bar_w = 32          # ASCII 条最大长度
    label_w = 28        # 模型名列宽
    rows = [f"**{title}**", ""]
    for m in items:
        c = m.get("effective_cost", 0)
        n = max(int(bar_w * c / maxc), 1)
        bar = "█" * n
        label = str(m["model"])
        if len(label) > label_w:
            label = label[:label_w - 1] + "…"
        else:
            label = label.ljust(label_w)
        rows.append(f"{label} | {bar} ¥{c:.2f}")
    return "```\n" + "\n".join(rows) + "\n```"


def build_model_cost_chart(model_stats, title="各模型估算实际花费对比"):
    """HTML 内联条形图：各模型估算实际花费对比（仅含已配置单价模型）。"""
    items = [m for m in model_stats if (m.get("effective_cost", 0) or 0) > 0]
    if not items:
        return ""
    items = sorted(items, key=lambda x: x.get("effective_cost", 0), reverse=True)
    maxc = max(m.get("effective_cost", 0) for m in items) or 1
    rows = []
    for m in items:
        c = m.get("effective_cost", 0)
        w = max(int(220 * c / maxc), 1)
        rows.append(
            f'        <div class="bar-row"><span class="bar-label" title="{m["model"]}">{m["model"]}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{w}px"></span></span>'
            f'<span class="bar-val">¥{c:.2f}</span></div>'
        )
    return (
        f'    <div class="chart-bars">\n      <p><strong>{title}</strong></p>\n'
        + "\n".join(rows)
        + "\n    </div>"
    )


def _timed_free_label(model, timed_free_map):
    """限免标签：优先用 pricing.json 里的真实截止日，缺失时退化为不带日期的通用说法。"""
    deadline = (timed_free_map or {}).get(model)
    return f"（限时免费至 {deadline}）" if deadline else "（限时免费）"


# GLM-5.2 家族裸模型名集合（与 collect_usage_data.py 保持一致）：用于 §3.1 合并为「glm-5.2」一行
GLM52_FAMILY = {"glm-5.2", "glm-5.2-x", "glm-5.2x"}


def merge_glm52_family(model_stats):
    """将 GLM-5.2 家族（glm-5.2 / glm-5.2-x / glm-5.2x）合并为「glm-5.2」一行。

    三者是同一官方模型在不同时段的计费形态（白天 0.79x、夜间 0.5x），合并后调用次数与
    计费均为二者之和。保留原排序位置（不再加粗）。
    """
    _fam = [m for m in model_stats if m["model"].strip().lower() in GLM52_FAMILY]
    if not _fam:
        return model_stats
    _idx = next((i for i, m in enumerate(model_stats)
                 if m["model"].strip().lower() in GLM52_FAMILY), 0)
    _merged = {
        "model": "glm-5.2",
        "calls": sum(m.get("calls", 0) for m in _fam),
        "effective_tokens": sum(m.get("effective_tokens", 0) for m in _fam),
        "effective_cost": sum((m.get("effective_cost", 0) or 0) for m in _fam),
        "input_tokens": sum(m.get("input_tokens", 0) for m in _fam),
        "output_tokens": sum(m.get("output_tokens", 0) for m in _fam),
        "unit_price_input": (_fam[0].get("unit_price_input") or 8.0),
        "unit_price_output": (_fam[0].get("unit_price_output") or 28.0),
        "configured": _fam[0].get("configured", False),
    }
    model_stats = [m for m in model_stats if m["model"].strip().lower() not in GLM52_FAMILY]
    model_stats.insert(_idx, _merged)
    return model_stats


def _build_model_block_md(model_stats, dim_label, is_exec=False, timed_free_map=None, compact=False):
    """生成单一维度的模型表格 + 洞察 Markdown 行（不含小节标题）。

    compact=True 时仅渲染「模型 / 调用次数 / 实际消耗Token」三列（用于本地模型使用统计，
    因本地推理零成本，单价/花费/占比列无意义）。
    GLM-5.2 家族合并已在调用方通过 merge_glm52_family() 完成。
    """
    lines = []
    configured = [m for m in model_stats if m.get("configured")]
    # 实际产生计费的行（含未配置单价的 auto 路由，其成本来自 trace 级汇总）——用于占比分母与图表
    priced = [m for m in model_stats if (m.get("effective_cost", 0) or 0) > 0]
    total_billable = sum(m.get("effective_cost", 0) for m in priced) or 1
    cost_header = "估算花费＊" if is_exec else "估算实际花费"
    lines.append(f"**{dim_label}**")
    lines.append("")
    if compact:
        lines.append("| 模型 | 调用次数 | 实际消耗Token |")
        lines.append("|------|---------|-------------|")
    else:
        lines.append(f"| 模型 | 调用次数 | 实际消耗Token | 输入单价(元/1M) | 输出单价(元/1M) | {cost_header} | 占总花费比 |")
        lines.append("|------|---------|-------------|--------------|--------------|------------|-----------|")
    for m in model_stats:
        cfg = m.get("configured")
        mname = m["model"]
        if m.get("is_delisted"):
            mname = f"{m['model']} 🗄️"
        elif m.get("is_local"):
            mname = f"{m['model']} 🔧🏠"
        elif m.get("is_router_api"):
            mname = f"{m['model']} 🔀"
        elif m.get("is_custom"):
            mname = f"{m['model']} 🔧"
        elif is_exec and m.get("is_router"):
            mname = f"{m['model']}（未解析具体模型）"
        elif m.get("timed_free"):
            mname = f"{m['model']}{_timed_free_label(m['model'], timed_free_map)}"
        eff = m.get("effective_cost", 0) or 0.0
        is_free = cfg and m.get("unit_price_input") == 0 and m.get("unit_price_output") == 0
        if m.get("timed_free"):
            # 限免期内实付 ¥0。若仍保留刊例单价（账单口径），一并显示以便看清「原价 vs 实付」。
            ip = f"{m['unit_price_input']:.2f}" if cfg and m.get("unit_price_input") else "限时免费"
            op = f"{m['unit_price_output']:.2f}" if cfg and m.get("unit_price_output") else "限时免费"
            cost = "¥0.00"
        elif is_free:
            ip, op, cost = "免费", "免费", "¥0.00"
        elif eff > 0:
            # 真实产生计费（含未配置单价的 auto 路由，成本来自 trace 级汇总）——如实显示金额
            ip = f"{m['unit_price_input']:.2f}" if cfg else "—"
            op = f"{m['unit_price_output']:.2f}" if cfg else "—"
            cost = f"¥{eff:.2f}"
        elif m.get("is_delisted") and cfg:
            # 下架但已知单价：trace 级未计成本时（如历史数据重算前），用单价×token 补算估算花费，
            # 保证「已知单价≠未知」，避免有价的下架模型被误标为「已下架·未知」。
            ep = m.get("unit_price_input") or 0.0
            eo = m.get("unit_price_output") or 0.0
            est = (m.get("input_tokens", 0) / 1_000_000) * ep + (m.get("output_tokens", 0) / 1_000_000) * eo
            eff = est
            ip = f"{ep:.2f}"
            op = f"{eo:.2f}"
            cost = f"¥{est:.2f}"
        else:
            ip = f"{m['unit_price_input']:.2f}" if cfg else "—"
            op = f"{m['unit_price_output']:.2f}" if cfg else "—"
            cost = "已下架·未知" if m.get("is_delisted") else "未配置"
        share = (eff / total_billable * 100) if eff > 0 else 0.0
        share_txt = f"{share:.1f}%" if eff > 0 else "—"
        if compact:
            lines.append(
                f"| {mname} | {m['calls']} | {format_number(m.get('effective_tokens', 0))} |"
            )
        else:
            lines.append(
                f"| {mname} | {m['calls']} | {format_number(m.get('effective_tokens', 0))} "
                f"| {ip} | {op} | {cost} | {share_txt} |"
            )
    lines.append("")
    if is_exec:
        lines.append("> ＊本表为**使用量分布估算（非计费口径）**：已解析具体模型的调用按其真实单价估算；"
                     "未能解析具体模型的调用（路由别名 `auto`）按本周期计费模型均价估算。"
                     "**本表总额不代表真实账单，且不可与 3.1 相加**。")
    if any(m.get("is_router") for m in model_stats):
        lines.append("> ℹ️ `auto` 为智能路由别名（执行时自动调配最适合模型，类似 openrouter/free），无单一单价；"
                     "其「单价 / 花费」为所有计费模型（单价>0）的**均价估算值**，仅供横向对比参考。")
    if any(m.get("is_delisted") for m in model_stats):
        lines.append("> 🗄️ = 曾在 WorkBuddy 提供、现已下架的官方模型；历史调用仍正常统计与计价，单价未知者不计入成本。")
    if any(m.get("is_local") for m in model_stats):
        lines.append("> 🔧🏠 = 你通过 Ollama 本地推理运行的模型（零 API 成本），**不计入账单总额**；其「花费」恒为 ¥0.00，仅作本地使用量统计。")
    if any(m.get("is_custom") and not m.get("is_local") for m in model_stats):
        lines.append("> 🔧 = 你通过外部 API 接口自建 / 接入的自定义模型（非 WorkBuddy 官方模型），与 🗄️ 官方已下架模型**分开统计**；单价仅作粗略参考，实际账单请往对应接口查看。")
    if any(m.get("is_router_api") for m in model_stats):
        lines.append("> 🔀 = 智能路由 / 聚合网关（外部 API，如 OpenRouter 免费档、Groq 等）：一次调用可能落到不同底层模型或上游 host，单价 / 花费为粗略参考，实际账单请往对应接口查看。")
    if model_stats:
        top_calls = max(model_stats, key=lambda x: x["calls"])
        lines.append(f"> 🏆 **最常使用模型**：`{top_calls['model']}` —— 调用 {top_calls['calls']} 次。")
    if priced:
        concrete = [m for m in priced if not m.get("is_router")]
        top_cost = max(concrete or priced, key=lambda x: x.get("effective_cost", 0))
        share = top_cost.get("effective_cost", 0) / total_billable * 100 if total_billable else 0
        lines.append(f"> 💸 **最贵模型**（按花费，不含路由别名）：`{top_cost['model']}` —— 估算花费 ¥{top_cost.get('effective_cost', 0):.2f}，"
                     f"占总花费 {share:.1f}%。")
    elif configured:
        lines.append("> 💸 当前已配置单价的模型均为免费模型（花费 ¥0.00）；填入付费模型单价后此处显示最贵模型。")
    lines.append("")
    return lines


def build_model_section_md(data):
    """Markdown 章节：模型使用与成本对比（双维度：接口/通道 + 实际执行模型）。"""
    meta = data.get("meta", {})
    model_stats = data.get("model_stats", [])
    exec_stats = data.get("model_exec_stats", [])
    if not model_stats and not exec_stats:
        return []
    lines = []
    lines.append("## 三、模型使用与成本对比")
    lines.append("")
    lines.append("按模型统计调用次数、实际消耗 Token 与单价（元 / 1M tokens，输入 / 输出分别计价）。"
                 "本章节提供**两个维度**：")
    lines.append("")
    lines.append("- **3.1 按实际计费模型（账单口径）**：按 API 实际计费的模型（即 trace 的 `exec_model`，含经 `auto`/限免入口路由到的付费模型）聚合，是费用结算依据；其各模型花费合计 = 报告概览「实际成本（计费等效）」总额。")
    lines.append("- **3.2 按入口 / 配置模型（使用维度）**：按你配置的入口 / 通道（如 `auto` 路由、`hy3`、`custom-local`）聚合，反映你实际请求 / 配置了哪些入口、各多少次——属「使用分布」而非「账单」；本维度总额不代表真实账单，且不可与 3.1 相加。")
    lines.append("")
    tf_map = meta.get("timed_free", {}) or {}
    if tf_map:
        _tf_txt = "、".join(f"`{k}`（至 **{v}**）" for k, v in sorted(tf_map.items()))
        lines.append(f"> 🎁 **限时免费**：{_tf_txt} 在限免活动期间免费，相关调用花费记为 ¥0.00；"
                     "表格中以「限时免费」标注，以区别于永久免费模型（`:free` 后缀）。"
                     "若该模型有公开刊例价，表中仍会显示原单价，方便对比「原价 vs 实付」。")
        lines.append("")
    lines.append("> ⚠️ 以上计算只供参考，如果是外部自建接口（custom-local），请往接口相关网站查看账单。")
    lines.append("")
    # 3.1 接口 / 通道（计费维度）
    lines.append("### 3.1 按实际计费模型（账单口径）")
    lines.append("")
    lines.append("> 备注：WorkBuddy 的 GLM-5.2 夜猫子计划折扣（2026 年 7 月 16 日开始）已计入 `glm-5.2` 的花费中。")
    lines.append("")
    model_stats = merge_glm52_family(model_stats)
    lines += _build_model_block_md(model_stats, "计费维度明细（费用结算依据）", timed_free_map=tf_map)
    chart = build_model_cost_chart_md([m for m in model_stats if m.get("configured")])
    if chart:
        lines.append(chart)
        lines.append("")
    # 3.2 按入口 / 配置模型（使用维度 · 非计费口径）
    lines.append("### 3.2 按入口 / 配置模型（使用维度 · 非计费口径）")
    lines.append("")
    lines.append("> 本维度按你配置的**入口 / 通道模型名**聚合（如 `auto` 路由、`hy3`、`custom-local`），"
                 "反映你实际请求 / 配置了哪些入口、各多少次——是「使用分布」而非「账单」。"
                 "经由 `auto` 路由或限免入口（如 `hy3`）实际执行的底层付费模型，其花费已计入 3.1 对应执行模型行，"
                 "本表不直接展开；**本维度总额不代表真实账单，且不可与 3.1 相加**；自建接口（custom-local）实际单价请往接口网站查看。")
    lines.append("")
    if exec_stats:
        official_exec = [m for m in exec_stats if not m.get("is_custom")]
        local_exec = [m for m in exec_stats if m.get("is_local")]
        external_exec = [m for m in exec_stats if m.get("is_custom") and not m.get("is_local")]
        if official_exec:
            lines.append("#### 3.2.1 官方 / 网关入口模型")
            lines.append("")
            lines += _build_model_block_md(official_exec, "官方入口维度明细", is_exec=True, timed_free_map=tf_map)
            chart2 = build_model_cost_chart_md([m for m in official_exec if m.get("configured")])
            if chart2:
                lines.append(chart2)
                lines.append("")
        if local_exec:
            lines.append("#### 3.2.2 本地模型（Ollama 本地推理）🔧🏠")
            lines.append("")
            lines += _build_model_block_md(local_exec, "本地模型维度明细", is_exec=True, timed_free_map=tf_map, compact=True)
            chart3 = build_model_cost_chart_md([m for m in local_exec if m.get("configured")])
            if chart3:
                lines.append(chart3)
                lines.append("")
        if external_exec:
            lines.append("#### 3.2.3 外部 API 接口接入模型 🔧")
            lines.append("")
            lines += _build_model_block_md(external_exec, "外部API入口维度明细", is_exec=True, timed_free_map=tf_map)
            chart4 = build_model_cost_chart_md([m for m in external_exec if m.get("configured")])
            if chart4:
                lines.append(chart4)
                lines.append("")
    else:
        lines.append("（本期无模型调用数据）")
        lines.append("")
    # 3.3 缺失单价模型（数据驱动，仅当存在时显示）
    lines += _build_unconfigured_models_section_md(meta)
    return lines


def _build_unconfigured_models_section_md(meta):
    """Markdown 渲染「缺失单价模型」提示块：列出未配置单价的模型 + 可复制的 pricing.json 补写片段 + 可选网络估算/搜索链接。"""
    lines = []
    unconfigured = meta.get("unconfigured_models") or []
    if not unconfigured:
        return lines
    pricing_lookup = meta.get("pricing_lookup") or {}
    network_estimates = pricing_lookup.get("network_estimates") or {}
    search_links = pricing_lookup.get("search_links") or {}
    mode = pricing_lookup.get("mode", "offline")

    lines.append("> ⚠️ **本期有未配置单价的模型**（未计入账单总额，但调用仍发生）：")
    lines.append("")
    lines.append("| 模型 | 建议输入单价(元/1M) | 建议输出单价(元/1M) | 来源 |")
    lines.append("|------|---------------------|---------------------|------|")
    for m in unconfigured:
        est = network_estimates.get(m)
        link = search_links.get(m)
        if est:
            src = f"🌐 网络估算价，[搜索验证]({link})" if link else "🌐 网络估算价"
            ip, op = f"{est['input']:.2f}", f"{est['output']:.2f}"
        elif link:
            src = f"[搜索定价]({link})"
            ip, op = "—", "—"
        else:
            src = "未配置"
            ip, op = "—", "—"
        lines.append(f"| `{m}` | {ip} | {op} | {src} |")
    lines.append("")

    # 可复制的 pricing.local.json 补写片段（按模型生成 4 空格缩进示例）
    lines.append("<details>")
    lines.append("<summary>📝 复制下面片段到 <code>scripts/pricing.local.json</code> 的 <code>models</code> 节点补全单价（本地覆盖文件，<code>skillhub upgrade</code> 升级时不会被覆盖；仅供参考，请按实际账单核对）</summary>")
    lines.append("")
    lines.append("```json")
    for m in unconfigured:
        lines.append(f'    "{m}": {{')
        lines.append(f'        "input": 0,   // 填入实际输入单价（元/1M tokens）')
        lines.append(f'        "output": 0   // 填入实际输出单价（元/1M tokens）')
        lines.append(f'    }},')
    lines.append("```")
    lines.append("</details>")
    lines.append("")
    if mode == "online":
        lines.append("> ℹ️ 已联网检索（`--lookup-pricing online`），上表「网络估算价」仅作参考，**不计入**报告任何成本总额，请以你实际账单为准。")
        lines.append("")
    return lines


def _build_unconfigured_models_section_html(meta):
    """HTML 版：列出未配置单价模型 + 可复制补写片段 + 可选网络估算。"""
    unconfigured = meta.get("unconfigured_models") or []
    if not unconfigured:
        return []
    pricing_lookup = meta.get("pricing_lookup") or {}
    network_estimates = pricing_lookup.get("network_estimates") or {}
    search_links = pricing_lookup.get("search_links") or {}
    mode = pricing_lookup.get("mode", "offline")

    block = []
    block.append('        <h3>3.3 缺失单价模型（未计入账单总额）</h3>')
    block.append('        <p class="disclaimer">⚠️ 以下模型本期被调用，但未在 <code>pricing.json</code>（或本地覆盖 <code>pricing.local.json</code>）中找到单价，因此未计入成本总额。请把单价补进 <code>scripts/pricing.local.json</code> 的 <code>models</code> 节点后重跑采集（该文件升级 Skill 时不会被覆盖）。</p>')
    block.append("        <table>")
    block.append("            <tr><th>模型</th><th>建议输入单价(元/1M)</th><th>建议输出单价(元/1M)</th><th>来源</th></tr>")
    for m in unconfigured:
        est = network_estimates.get(m)
        link = search_links.get(m)
        if est:
            src = f'<a href="{_esc(link)}" target="_blank">🌐 网络估算价（点击验证）</a>' if link else "🌐 网络估算价"
            ip, op = f"{est['input']:.2f}", f"{est['output']:.2f}"
        elif link:
            src = f'<a href="{_esc(link)}" target="_blank">搜索定价</a>'
            ip, op = "—", "—"
        else:
            src = "未配置"
            ip, op = "—", "—"
        block.append(
            f"            <tr><td><code>{_esc(m)}</code></td><td>{ip}</td><td>{op}</td><td>{src}</td></tr>"
        )
    block.append("        </table>")

    # 可复制补写片段
    block.append("        <details>")
    block.append("            <summary>📝 点击展开：复制以下片段到 <code>scripts/pricing.local.json</code> 的 <code>models</code> 节点补全单价（本地覆盖，升级 Skill 时不丢失）</summary>")
    block.append('            <pre><code class="language-json">')
    for m in unconfigured:
        block.append(f'    "{m}": {{')
        block.append(f'        "input": 0,   // 填入实际输入单价（元/1M tokens）')
        block.append(f'        "output": 0   // 填入实际输出单价（元/1M tokens）')
        block.append(f'    }},')
    block.append("</code></pre>")
    block.append("        </details>")
    if mode == "online":
        block.append('        <p class="disclaimer">ℹ️ 已联网检索（<code>--lookup-pricing online</code>），上表「网络估算价」仅作参考，<b>不计入</b>报告任何成本总额，请以你实际账单为准。</p>')
    return block


def _build_model_block_html(model_stats, is_exec=False, timed_free_map=None, compact=False):
    """生成单一维度的模型表格 + 洞察 HTML（不含小节标题）。

    compact=True 时仅渲染「模型 / 调用次数 / 实际消耗Token」三列（用于本地模型使用统计）。
    GLM-5.2 家族合并已在调用方通过 merge_glm52_family() 完成。
    """
    configured = [m for m in model_stats if m.get("configured")]
    # 实际产生计费的行（含未配置单价的 auto 路由，其成本来自 trace 级汇总）——用于占比分母与图表
    priced = [m for m in model_stats if (m.get("effective_cost", 0) or 0) > 0]
    total_billable = sum(m.get("effective_cost", 0) for m in priced) or 1
    cost_header = "估算花费＊" if is_exec else "估算实际花费"
    block = []
    block.append("        <table>")
    if compact:
        block.append("            <tr><th>模型</th><th>调用次数</th><th>实际消耗Token</th></tr>")
    else:
        block.append(f"            <tr><th>模型</th><th>调用次数</th><th>实际消耗Token</th>"
                     f"<th>输入单价(元/1M)</th><th>输出单价(元/1M)</th><th>{cost_header}</th><th>占总花费比</th></tr>")
    for m in model_stats:
        cfg = m.get("configured")
        mname = m["model"]
        if m.get("is_delisted"):
            mname = f"{m['model']} 🗄️"
        elif m.get("is_local"):
            mname = f"{m['model']} 🔧🏠"
        elif m.get("is_router_api"):
            mname = f"{m['model']} 🔀"
        elif m.get("is_custom"):
            mname = f"{m['model']} 🔧"
        elif is_exec and m.get("is_router"):
            mname = f"{m['model']}（未解析具体模型）"
        elif m.get("timed_free"):
            mname = f"{m['model']}{_timed_free_label(m['model'], timed_free_map)}"
        eff = m.get("effective_cost", 0) or 0.0
        is_free = cfg and m.get("unit_price_input") == 0 and m.get("unit_price_output") == 0
        if m.get("timed_free"):
            # 限免期内实付 ¥0。若仍保留刊例单价（账单口径），一并显示以便看清「原价 vs 实付」。
            ip = f"{m['unit_price_input']:.2f}" if cfg and m.get("unit_price_input") else "限时免费"
            op = f"{m['unit_price_output']:.2f}" if cfg and m.get("unit_price_output") else "限时免费"
            cost = "¥0.00"
        elif is_free:
            ip, op, cost = "免费", "免费", "¥0.00"
        elif eff > 0:
            # 真实产生计费（含未配置单价的 auto 路由，成本来自 trace 级汇总）——如实显示金额
            ip = f"{m['unit_price_input']:.2f}" if cfg else "—"
            op = f"{m['unit_price_output']:.2f}" if cfg else "—"
            cost = f"¥{eff:.2f}"
        elif m.get("is_delisted") and cfg:
            # 下架但已知单价：trace 级未计成本时（如历史数据重算前），用单价×token 补算估算花费，
            # 保证「已知单价≠未知」，避免有价的下架模型被误标为「已下架·未知」。
            ep = m.get("unit_price_input") or 0.0
            eo = m.get("unit_price_output") or 0.0
            est = (m.get("input_tokens", 0) / 1_000_000) * ep + (m.get("output_tokens", 0) / 1_000_000) * eo
            eff = est
            ip = f"{ep:.2f}"
            op = f"{eo:.2f}"
            cost = f"¥{est:.2f}"
        else:
            ip = f"{m['unit_price_input']:.2f}" if cfg else "—"
            op = f"{m['unit_price_output']:.2f}" if cfg else "—"
            cost = "已下架·未知" if m.get("is_delisted") else "未配置"
        share = (eff / total_billable * 100) if eff > 0 else 0.0
        share_txt = f"{share:.1f}%" if eff > 0 else "—"
        if compact:
            block.append(
                f"            <tr><td>{_esc(mname)}</td><td>{m['calls']}</td>"
                f"<td>{format_number(m.get('effective_tokens', 0))}</td></tr>"
            )
        else:
            block.append(
                f"            <tr><td>{_esc(mname)}</td><td>{m['calls']}</td>"
                f"<td>{format_number(m.get('effective_tokens', 0))}</td><td>{ip}</td><td>{op}</td>"
                f"<td>{cost}</td><td>{share_txt}</td></tr>"
            )
    block.append("        </table>")
    if is_exec:
        block.append('        <p class="disclaimer">＊本表为<b>使用量分布估算（非计费口径）</b>：已解析具体模型的调用按其真实单价估算；'
                     '未能解析具体模型的调用（路由别名 <code>auto</code>）按本周期计费模型均价估算。'
                     '<b>本表总额不代表真实账单，且不可与 3.1 相加</b>。</p>')
    if any(m.get("is_router") for m in model_stats):
        block.append('        <p>ℹ️ <code>auto</code> 为智能路由别名（执行时自动调配最适合模型，类似 openrouter/free），'
                     '无单一单价；其「单价 / 花费」为所有计费模型（单价&gt;0）的<b>均价估算值</b>，仅供横向对比参考。</p>')
    if any(m.get("is_delisted") for m in model_stats):
        block.append('        <p>🗄️ = 曾在 WorkBuddy 提供、现已下架的官方模型；历史调用仍正常统计与计价，单价未知者不计入成本。</p>')
    if any(m.get("is_local") for m in model_stats):
        block.append('        <p>🔧🏠 = 你通过 Ollama 本地推理运行的模型（零 API 成本），<b>不计入账单总额</b>；其「花费」恒为 ¥0.00，仅作本地使用量统计。</p>')
    if any(m.get("is_custom") and not m.get("is_local") for m in model_stats):
        block.append('        <p>🔧 = 你通过外部 API 接口自建 / 接入的自定义模型（非 WorkBuddy 官方模型），与 🗄️ 官方已下架模型<b>分开统计</b>；单价仅作粗略参考，实际账单请往对应接口查看。</p>')
    if any(m.get("is_router_api") for m in model_stats):
        block.append('        <p>🔀 = 智能路由 / 聚合网关（外部 API，如 OpenRouter 免费档、Groq 等）：一次调用可能落到不同底层模型或上游 host，单价 / 花费为粗略参考，实际账单请往对应接口查看。</p>')
    if configured:
        chart = build_model_cost_chart(configured)
        if chart:
            block.append(chart)
        top_calls = max(model_stats, key=lambda x: x["calls"])
        if priced:
            concrete = [m for m in priced if not m.get("is_router")]
            top_cost = max(concrete or priced, key=lambda x: x.get("effective_cost", 0))
            share = top_cost.get("effective_cost", 0) / total_billable * 100 if total_billable else 0
            block.append(
                f'        <p>🏆 <strong>最常使用模型</strong>：<code>{_esc(top_calls["model"])}</code>'
                f'（调用 {top_calls["calls"]} 次）；'
                f'💸 <strong>最贵模型</strong>（按花费，不含路由别名）：<code>{_esc(top_cost["model"])}</code>'
                f'（估算 ¥{top_cost.get("effective_cost", 0):.2f}，占 {share:.1f}%）。</p>'
            )
        else:
            block.append(
                f'        <p>🏆 <strong>最常使用模型</strong>：<code>{_esc(top_calls["model"])}</code>'
                f'（调用 {top_calls["calls"]} 次）。当前已配置单价的模型均为免费模型，'
                f'填入付费模型单价后显示最贵模型对比。</p>'
            )
    return block


def build_model_section_html(data):
    """HTML 章节：模型使用与成本对比（双维度：接口/通道 + 实际执行模型）。"""
    meta = data.get("meta", {})
    model_stats = data.get("model_stats", [])
    exec_stats = data.get("model_exec_stats", [])
    if not model_stats and not exec_stats:
        return []
    tf_map = meta.get("timed_free", {}) or {}
    lines = []
    lines.append('    <div class="section">')
    lines.append('        <h2 class="section-title">三、模型使用与成本对比</h2>')
    lines.append("        <p>按模型统计调用次数、实际消耗 Token 与单价（元 / 1M tokens，输入 / 输出分别计价）。"
                 "本章节提供<b>两个维度</b>：<b>3.1 按实际计费模型</b>（账单口径，与概览总额一致）与 <b>3.2 按入口 / 配置模型</b>（使用分布）。"
                 "未配置单价的模型以「未配置」标注——在 <code>scripts/pricing.local.json</code> 的 <code>models</code> 里补上单价即可（无需改 Python 代码；该本地文件升级 Skill 时不会被覆盖）。</p>")
    if tf_map:
        _tf_txt = "、".join(f"<code>{_esc(k)}</code>（至 <b>{_esc(v)}</b>）" for k, v in sorted(tf_map.items()))
        lines.append(f'        <p class="disclaimer">🎁 <b>限时免费</b>：{_tf_txt} 在限免活动期间免费，'
                     '相关调用花费记为 ¥0.00，以区别于永久免费模型（<code>:free</code> 后缀）。'
                     '若该模型有公开刊例价，表中仍显示原单价，方便对比「原价 vs 实付」。</p>')
    lines.append('        <h3>3.1 按实际计费模型（账单口径）</h3>')
    lines.append('        <p class="disclaimer">备注：WorkBuddy 的 GLM-5.2 夜猫子计划折扣（2026 年 7 月 16 日开始）已计入 <code>glm-5.2</code> 的花费中。</p>')
    model_stats = merge_glm52_family(model_stats)
    lines += _build_model_block_html(model_stats, timed_free_map=tf_map)
    lines.append('        <p class="disclaimer">⚠️ 以上计算只供参考，如果是外部自建接口（custom-local），请往接口相关网站查看账单。'
                 '3.1 各模型花费合计 = 报告概览「实际成本（计费等效）」总额。</p>')
    lines.append('        <h3>3.2 按入口 / 配置模型（使用维度 · 非计费口径）</h3>')
    lines.append('        <p>本维度按你配置的<b>入口 / 通道模型名</b>（如 <code>auto</code> 路由、<code>hy3</code>、<code>custom-local</code>）聚合，'
                 '反映实际请求 / 配置的入口分布（非账单）。经由 <code>auto</code> 或限免入口实际执行的底层付费模型，'
                 '其花费已计入 3.1 对应执行模型行，本表不直接展开。'
                 '<b>本维度总额不代表真实账单，且不可与 3.1 相加</b>。</p>')
    if exec_stats:
        official_exec = [m for m in exec_stats if not m.get("is_custom")]
        local_exec = [m for m in exec_stats if m.get("is_local")]
        external_exec = [m for m in exec_stats if m.get("is_custom") and not m.get("is_local")]
        if official_exec:
            lines.append('        <h4>3.2.1 官方 / 网关入口模型</h4>')
            lines += _build_model_block_html(official_exec, is_exec=True, timed_free_map=tf_map)
        if local_exec:
            lines.append('        <h4>3.2.2 本地模型（Ollama 本地推理）🔧🏠</h4>')
            lines += _build_model_block_html(local_exec, is_exec=True, timed_free_map=tf_map, compact=True)
        if external_exec:
            lines.append('        <h4>3.2.3 外部 API 接口接入模型 🔧</h4>')
            lines += _build_model_block_html(external_exec, is_exec=True, timed_free_map=tf_map)
    else:
        lines.append('        <p>（本期无模型调用数据）</p>')
    lines.append('        <p class="disclaimer">⚠️ 以上计算只供参考，如果是外部自建接口（custom-local），请往接口相关网站查看账单。</p>')
    # 3.3 缺失单价模型（数据驱动，仅当存在时显示）
    lines += _build_unconfigured_models_section_html(data.get("meta", {}))
    lines.append("    </div>")
    return lines


def _auto_is_active(run):
    """该运行所属自动化是否处于执行中（ACTIVE）。PAUSED/DELETED/UNKNOWN 均视为已停止。"""
    return (run.get("auto_status") or "UNKNOWN") == "ACTIVE"


def _auto_state_label(status):
    """把 automation 定义状态映射为中文标签（用于自动化详情表）。"""
    return {
        "ACTIVE": "执行中",
        "PAUSED": "已暂停",
        "DELETED": "已删除",
    }.get(status or "UNKNOWN", "未知")


def build_next_week_outlook(summary, daily_tokens, automation_runs, session_credits, period_key="week"):
    """基于本期实际数据，生成动态的下期用量预测 + 优先级行动建议。

    所有结论均由报告内真实数据驱动：花费预测来自日均花费，额度预测来自
    当前额度占用与本期 token 消耗，自动化告警带样本量门槛避免小样本误报。
    不再重复「十、核心洞察」已给出的最大成本来源（高峰日）。

    自动化相关建议**仅考虑「正在执行」的自动化（auto_status == ACTIVE）**：
    已停止（已暂停 PAUSED / 已删除 DELETED）的自动化任务不计入，其运行记录
    即便处于 PENDING_REVIEW 也不再视为需要人工处理的「待审核」项。
    """
    _short = _PERIOD_SHORT.get(period_key, "本周")
    _next = _PERIOD_NEXT.get(period_key, "下周")
    items = []

    # 0. 下期用量预测（真·展望，数据驱动）
    eff_cost = summary.get("total_effective_cost", 0)
    active_days = summary.get("active_day_count", 0)
    if daily_tokens and active_days > 0 and eff_cost > 0:
        avg_daily_cost = eff_cost / active_days
        dates = sorted(daily_tokens.keys())
        try:
            d0 = datetime.strptime(dates[0], "%Y-%m-%d")
            d1 = datetime.strptime(dates[-1], "%Y-%m-%d")
            period_days = max((d1 - d0).days + 1, 1)
        except Exception:
            period_days = PERIOD_DAYS.get(period_key, 7)
        peak_day_cost = max((v.get("effective_cost", 0) for v in daily_tokens.values()), default=0)
        forecast_low = avg_daily_cost * period_days
        forecast_high = (avg_daily_cost + max(peak_day_cost - avg_daily_cost, 0)) * period_days
        items.append(
            f"- 📈 **下期用量预测**：按本期日均 ¥{avg_daily_cost:.2f} 推算，{_next}（约 {period_days} 天）"
            f"预计花费 **¥{forecast_low:.2f} ~ ¥{forecast_high:.2f}**。"
        )

    # 自动化相关建议：仅统计「正在执行（ACTIVE）」的自动化。
    # 已停止（PAUSED/DELETED）的自动化不计入——其 PENDING_REVIEW 运行不再视为待处理项。
    active_runs = [r for r in automation_runs if _auto_is_active(r)]
    total_runs = len(active_runs)

    # 1. 待审核任务 → P0（仅统计正在执行的自动化）
    pending = sum(1 for r in active_runs if r.get("status") == "PENDING_REVIEW")
    if pending:
        share = pending / total_runs * 100 if total_runs else 0
        items.append(
            f"- 🔔 **P0 处理待审核任务**：{pending} 个自动化运行处于 PENDING_REVIEW"
            f"（占本期执行中自动化 {share:.0f}%），卡在人工确认环节持续占用额度与资源，建议尽快审核或配置自动放行。"
        )

    # 2. 自动化稳定性 → P1（样本 ≥10 才告警，否则仅观察）
    if total_runs:
        success = sum(1 for r in active_runs if r.get("result_success"))
        fail = total_runs - success
        fail_rate = fail / total_runs * 100
        if total_runs >= 10 and fail_rate >= 30:
            items.append(
                f"- 🛠️ **P1 自动化成功率偏低**：{success}/{total_runs}（失败率 {fail_rate:.0f}%），"
                f"失败重试会重复消耗 token，建议检查任务配置与依赖环境。"
            )
        elif fail > 0:
            items.append(
                f"- 🛠️ **P1 自动化稳定性**：本期 {fail}/{total_runs} 次失败（失败率 {fail_rate:.0f}%），"
                f"样本偏小暂不作为告警，建议持续观察。"
            )

    # 3. Token 额度 → P2（数据驱动）
    if session_credits:
        latest = max(session_credits, key=lambda x: x.get("updated_at", 0))
        used = latest.get("used", 0); size = latest.get("size", 0)
        if size > 0:
            ratio = used / size * 100
            if ratio >= 70:
                items.append(
                    f"- 💳 **P2 检查 Token 额度**：已用约 {ratio:.0f}%，接近上限，"
                    f"建议规划{_next}用量避免高峰任务因额度不足中断。"
                )
            else:
                items.append(
                    f"- 💳 **P2 关注 Token 额度**：已用约 {ratio:.0f}%，"
                    f"建议定期核对防止高峰期任务因额度不足而失败。"
                )

    if not items:
        items.append("- 使用趋势平稳，建议继续保持并关注高峰时段任务调度。")

    return items


# ─────────────────────────────────────────────────────────────
# Markdown 格式
# ─────────────────────────────────────────────────────────────
def generate_markdown_report(data):
    meta = data.get("meta", {})
    summary = data.get("summary", {})
    daily_tokens = data.get("daily_tokens", {})
    task_dist = summary.get("task_type_distribution", {})
    skills = data.get("skill_usage", {}).get("skills", {})
    outputs = data.get("outputs", [])
    automation_runs = data.get("automation_runs", [])

    total_input = summary.get("total_input_tokens", 0)
    total_cached = summary.get("total_cached_tokens", 0)
    cache_rate = (total_cached / total_input * 100) if total_input else 0

    lines = []
    _key = meta.get("period", "week")
    _label, _short, _next = _period_labels(meta)
    lines.append("# Workbuddy使用情况报告")
    lines.append("")
    lines.append(f"> **报告类型**：{_calendar_period(meta)}")
    lines.append(f"> **报告周期**：{meta.get('start_date', '')} 至 {meta.get('end_date', '')}")
    lines.append(f"> **生成时间**：{_fmt_generated_at()}")
    lines.append(f"> **数据来源**：WorkBuddy 会话历史、Traces、workbuddy.db、技能使用记录、自动化配置")
    lines.append("")

    # 一、概览统计
    lines.append("## 一、概览统计")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 活跃天数 | {summary.get('active_day_count', 0)} 天（{', '.join(summary.get('active_days', []))}）|")
    lines.append(f"| 会话总数 | {summary.get('total_sessions', 0)} 个 |")
    _gt = data.get("traces", [])
    _unresolved_calls = sum(1 for t in _gt
                            if (t.get("exec_model") or t.get("raw_model") or "") == "default"
                            or ((t.get("input_tokens", 0) or 0) + (t.get("output_tokens", 0) or 0)) == 0)
    _billable_calls = len(_gt) - _unresolved_calls
    lines.append(f"| 调用次数 | {_billable_calls} 次（{_unresolved_calls} 次未解析/幽灵，不计费）|")
    lines.append(f"| 使用技能 | {summary.get('skills_used', 0)} 个 |")
    lines.append(f"| 自动化任务运行 | {summary.get('total_automation_runs', 0)} 次（成功 {summary.get('successful_automation_runs', 0)} 次）|")
    lines.append(f"| 产出文件 | {summary.get('total_outputs', 0)} 个 |")
    lines.append(f"| 实际消耗 Token（计费等效） | {format_number(summary.get('total_effective_tokens', 0))}（原始 {format_number(summary.get('total_tokens', 0))}）|")
    lines.append(f"| 缓存占比 | {cache_rate:.1f}%（缓存命中 token 占输入 token 的比例）|")
    lines.append(f"| 实际成本（计费等效） | ¥{summary.get('total_effective_cost', 0):.2f}（原始口径 ¥{summary.get('total_cost', 0):.2f}）|")
    if any(m.get("timed_free") for m in data.get("model_stats", []) + data.get("model_exec_stats", [])):
        tf_map = meta.get("timed_free", {}) or {}
        if tf_map:
            tf_desc = "、".join(f"{k} 至 {v} 免费" for k, v in tf_map.items())
            lines.append(f"> 🎁 实际成本已计「限时免费」模型（{tf_desc}），相关花费记为 ¥0.00。")
        else:
            lines.append("> 🎁 实际成本已计「限时免费」模型，相关花费记为 ¥0.00。")
    _warn = _unresolved_warning_md(_unresolved_call_stats(data))
    if _warn:
        lines.append(_warn)
    lines.append("")

    # 二、Token 消耗可视化
    lines.append("## 二、Token 消耗可视化")
    lines.append("")
    lines.append("### 2.1 总体消耗")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 原始总 Token（含缓存命中） | {format_number(summary.get('total_tokens', 0))} |")
    lines.append(f"| 实际消耗 Token（计费等效） | {format_number(summary.get('total_effective_tokens', 0))} |")
    lines.append(f"| 输入 Token | {format_number(summary.get('total_input_tokens', 0))} |")
    lines.append(f"| 输出 Token | {format_number(summary.get('total_output_tokens', 0))} |")
    lines.append(f"| 缓存命中 Token | {format_number(summary.get('total_cached_tokens', 0))} |")
    lines.append(f"| 缓存占比 | {cache_rate:.1f}%（缓存命中按约 1/10 价计费，不计入实际消耗全价）|")
    lines.append("")

    lines.append("### 2.2 成本货币化")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 实际成本（计费等效） | ¥{summary.get('total_effective_cost', 0):.2f} |")
    lines.append(f"| 原始总成本（含缓存全价） | ¥{summary.get('total_cost', 0):.2f} |")
    lines.append(f"| 输入成本 | ¥{summary.get('total_input_cost', 0):.2f} |")
    lines.append(f"| 输出成本 | ¥{summary.get('total_output_cost', 0):.2f} |")
    lines.append("")

    lines.append("### 2.3 每日 Token 消耗趋势")
    lines.append("")
    if daily_tokens:
        max_tokens = max((v.get("total", 0) for v in daily_tokens.values()), default=1)
        lines.append("```")
        for date in sorted(daily_tokens.keys()):
            stats = daily_tokens[date]
            bar_len = int(40 * stats.get("total", 0) / max_tokens) if max_tokens > 0 else 0
            bar = "█" * bar_len
            lines.append(f"{date} |{bar} {format_number(stats.get('total', 0))}")
        lines.append("```")
        lines.append("")
        lines.append("| 日期 | 原始总Token | 实际消耗 | 输入 | 输出 | 缓存 | 调用 | 实际成本 |")
        lines.append("|------|-----------|---------|------|------|------|------|---------|")
        for date in sorted(daily_tokens.keys()):
            stats = daily_tokens[date]
            lines.append(
                f"| {date} | {format_number(stats.get('total', 0))} | "
                f"{format_number(stats.get('effective', 0))} | "
                f"{format_number(stats.get('input', 0))} | {format_number(stats.get('output', 0))} | "
                f"{format_number(stats.get('cached', 0))} | {stats.get('calls', 0)} | "
                f"¥{stats.get('effective_cost', 0):.2f} |"
            )
        lines.append("")

    # （新增）三、模型使用与成本对比
    lines.extend(build_model_section_md(data))

    # （新增）四、成本深度分析（每会话 / 异常 / 省钱）
    lines.extend(build_cost_analysis_section_md(data))

    # 五、任务类型统计
    lines.append("## 五、任务类型统计")
    lines.append("")
    lines.append("> 统计口径：仅含本期有 trace（token 活动）的会话；历史空会话（无 token 活动）不计入，避免虚高。")
    lines.append("")
    if task_dist:
        lines.append("| 任务类型 | 会话数 | 占比 |")
        lines.append("|----------|--------|------|")
        for task_type, count in sorted(task_dist.items(), key=lambda x: x[1], reverse=True):
            pct = count / sum(task_dist.values()) * 100 if task_dist else 0
            lines.append(f"| {task_type} | {count} | {pct:.1f}% |")
        lines.append("")
        lines.append("> ℹ️ 「其他」= 无法自动归类到已知任务类型的会话（含无标题且对话内容缺失的会话）。")
        lines.append("")
        lines.append("### 任务类型分布图")
        lines.append("")
        lines.append("```")
        max_val = max(task_dist.values()) if task_dist else 1
        label_w = max((_disp_width(t) for t in task_dist), default=10)
        for task_type, count in sorted(task_dist.items(), key=lambda x: x[1], reverse=True):
            bar = "█" * int(20 * count / max_val) if max_val > 0 else ""
            lines.append(f"{_pad_label(task_type, label_w)} |{bar} {count}")
        lines.append("```")
        lines.append("")

    # 五、任务 Token 消耗统计
    lines.append("## 六、任务 Token 消耗统计")
    lines.append("")
    lines.append("按任务类型聚合的 Token 消耗，反映哪类任务最吃 token（含估算成本）：")
    lines.append("")
    task_token_stats = data.get("task_token_stats", [])
    # 过滤掉 0 token 的行（避免表格出现无意义的 0.0% 行）
    display_stats = [s for s in task_token_stats
                     if s.get("effective_tokens", 0) > 0]
    if display_stats:
        total_eff = sum(s.get("effective_tokens", 0) for s in display_stats)
        total_tok = sum(s["total_tokens"] for s in display_stats)
        lines.append("> 排名按「实际消耗（计费等效）」排序：缓存命中 token 按约 1/10 价计费，不按全价，"
                     "故不虚高；「缓存占比」高说明该任务大量复用同一段上下文（如连续多轮生成），"
                     "看起来 token 多但实际便宜。")
        lines.append("")
        lines.append("| 任务类型 | 会话数 | 实际消耗 | 原始总Token | 输入Token | 输出Token | 缓存占比 | 实际成本 | 占比 |")
        lines.append("|---------|------|---------|-----------|----------|----------|---------|---------|------|")
        for s in display_stats:
            eff = s.get("effective_tokens", 0)
            pct = (eff / total_eff * 100) if total_eff else 0
            c_ratio = (s["cached_tokens"] / s["input_tokens"] * 100) if s.get("input_tokens") else 0
            lines.append(
                f"| {s['task_type']} | {s['session_count']} | {format_number(eff)} "
                f"| {format_number(s['total_tokens'])} "
                f"| {format_number(s['input_tokens'])} | {format_number(s['output_tokens'])} "
                f"| {c_ratio:.0f}% | ¥{s.get('effective_cost', 0):.2f} | {pct:.1f}% |"
            )
        lines.append("")
        lines.append("> ℹ️ 「其他」含无法关联会话记录（trace 的会话 ID 在本地会话库找不到）或自动分类失败的 token；")
        lines.append("> 「未命名会话」= 本地会话库中无标题记录的会话。")
        lines.append("")
        top = task_token_stats[0]
        top_pct = (top.get("effective_tokens", 0) / total_eff * 100) if total_eff else 0
        lines.append(
            f"> 🔥 **实际消耗最高的任务类型**：{top['task_type']} —— "
            f"{format_number(top.get('effective_tokens', 0))} token（原始 {format_number(top['total_tokens'])}），"
            f"占 {top_pct:.1f}%，实际成本 ¥{top.get('effective_cost', 0):.2f}。"
        )
        # 占比图：各任务类型 实际消耗 token 占比（横向条形图，主题安全、不重叠）
        donut = build_task_type_chart_md(display_stats, title="实际消耗 Token 占比（按任务类型，计费等效）")
        if donut:
            lines.append("")
            lines.append("**各任务类型 实际消耗 Token 占比**：")
            lines.append("")
            lines.append(donut)
            lines.append("")
        # Top 10 最吃 token 的任务对话框（含自动化任务会话）
        top_tasks = data.get("top_tasks", [])
        if top_tasks:
            lines.append("")
            lines.append("**实际消耗最高的 10 个任务对话框**（含自动化任务，按会话实际消耗排序）：")
            lines.append("")
            lines.append("| 排名 | 任务名称 | 任务类型 | 实际消耗 | 原始总Token | 缓存占比 | 实际成本 |")
            lines.append("|------|----------|----------|---------|-----------|---------|---------|")
            for i, tk in enumerate(top_tasks, 1):
                c_ratio = (tk.get("cached_tokens", 0) / tk["input_tokens"] * 100) if tk.get("input_tokens") else 0
                lines.append(
                    f"| {i} | {tk.get('title', '-')} | {tk.get('task_type', '-')} "
                    f"| {format_number(tk.get('effective_tokens', 0))} "
                    f"| {format_number(tk.get('total_tokens', 0))} "
                    f"| {c_ratio:.0f}% | ¥{tk.get('effective_cost', 0):.2f} |"
                )
            lines.append("")
    else:
        lines.append(f"_{_short}无任务 Token 消耗数据_")
    lines.append("")

    # 六、技能使用统计
    lines.append("## 七、技能使用统计")
    lines.append("")
    if skills:
        lines.append("| 技能名称 | 使用次数 | 最近使用 |")
        lines.append("|----------|---------|---------|")
        for sid, sdata in sorted(skills.items(), key=lambda x: x[1].get("usage_count_in_range", 0), reverse=True):
            lines.append(
                f"| {sid} | {sdata.get('usage_count_in_range', 0)} | {sdata.get('last_used', '-')} |"
            )
        lines.append("")
    else:
        lines.append(f"_{_short}未使用技能_")
        lines.append("")

    # 七、自动化任务运行情况
    lines.append("## 八、自动化任务运行情况")
    lines.append("")
    if automation_runs:
        auto_groups = defaultdict(list)
        for run in automation_runs:
            auto_groups[run.get("automation_id", "unknown")].append(run)
        lines.append("| 任务名称 | 状态 | 运行次数 | 成功 | 失败 | 最近一次结果 | 最近一次运行日期 |")
        lines.append("|---------|------|---------|------|------|----------|----------------|")
        for auto_id, runs in sorted(auto_groups.items(), key=lambda x: len(x[1]), reverse=True):
            success = sum(1 for r in runs if r.get("result_success"))
            fail = len(runs) - success
            if runs:
                latest_result = "成功" if runs[-1].get("result_success") else "失败"
            else:
                latest_result = "-"
            label = runs[0].get("automation_name") or auto_id
            auto_state = _auto_state_label(runs[0].get("auto_status"))
            dates = [r.get("created_date", "") for r in runs if r.get("created_date")]
            last_run_date = max(dates) if dates else "-"
            lines.append(f"| {label} | {auto_state} | {len(runs)} | {success} | {fail} | {latest_result} | {last_run_date} |")
        lines.append("")
    else:
        lines.append(f"_{_short}无自动化任务运行记录_")
        lines.append("")

    # 八、产出物清单
    lines.append("## 九、产出物清单")
    lines.append("")
    if outputs:
        lines.append("| 文件 | 类型 | 日期 | 大小 |")
        lines.append("|------|------|------|------|")
        for out in outputs[:20]:
            ext = out.get("extension", "").lstrip(".")
            size = format_file_size(out.get("size_bytes", 0))
            lines.append(f"| {out.get('file_name', '-')} | {ext or '-'} | {out.get('date', '-')} | {size} |")
        lines.append("")
    else:
        lines.append(f"_{_short}无产出文件_")
        lines.append("")

    # 十、核心洞察与建议（数据驱动：钱优先 + 判断 + 行动）
    lines.append("## 十、核心洞察与建议")
    lines.append("")
    eff_cost = summary.get("total_effective_cost", 0)
    raw_cost = summary.get("total_cost", 0)
    eff_tokens = summary.get("total_effective_tokens", 0)
    cache_saving = max(raw_cost - eff_cost, 0)

    # 💰 花费速览（钱头条，数据驱动）
    lines.append("### 💰 花费速览")
    lines.append("")
    lines.append(f"- 本期实际花费 **¥{eff_cost:.2f}**（原始口径 ¥{raw_cost:.2f}），缓存复用为你节省约 **¥{cache_saving:.2f}**。")
    session_credits = data.get("session_credits", [])
    if session_credits:
        latest = max(session_credits, key=lambda x: x.get("updated_at", 0))
        used = latest.get("used", 0); size = latest.get("size", 0)
        if size > 0:
            lines.append(f"- 当前会话额度已用 **{used / size * 100:.0f}%**（{format_number(used)} / {format_number(size)}）。")
    lines.append("")

    # 🔍 最大成本来源（替代"高峰日"，带数据驱动判断）
    if daily_tokens and eff_tokens:
        peak_day, peak_stats = max(daily_tokens.items(), key=lambda x: x[1].get("effective", 0))
        peak_cost = peak_stats.get("effective_cost", 0)
        peak_tok = peak_stats.get("effective", 0)
        peak_share = peak_tok / eff_tokens * 100
        verdict = "占本期实际消耗比重偏高，建议复盘当日是否存在可精简的批量/重复任务" \
            if peak_share >= 25 else "占本期实际消耗比重正常，属单日波动"
        lines.append("### 🔍 最大成本来源")
        lines.append("")
        lines.append(f"- **高峰日 {peak_day}**：实际花费 ¥{peak_cost:.2f}（{format_number(peak_tok)} token），"
                     f"占本期实际消耗 **{peak_share:.1f}%**——{verdict}。")
        lines.append("")

    # 📊 任务类型洞察（加分布形态判断）
    if task_dist:
        total_count = sum(task_dist.values())
        top_task, top_n = max(task_dist.items(), key=lambda x: x[1])
        top_pct = top_n / total_count * 100
        if top_pct >= 50:
            shape = "高度集中于单一类型，建议确认是否为预期工作流"
        elif top_pct >= 25:
            shape = "相对集中，为本期主力任务"
        else:
            shape = "分布较均衡，未见单一类型主导"
        lines.append("### 📊 任务类型洞察")
        lines.append("")
        lines.append(f"- **主要任务类型**：{top_task}（{top_n} 次，{top_pct:.1f}%）——{shape}。")
        lines.append("")

    # ✅ 省钱成就（替代"缓存效率优秀"，翻成钱）
    if cache_rate > 0:
        lines.append("### ✅ 省钱成就")
        lines.append("")
        lines.append(f"- 缓存命中率达 **{cache_rate:.1f}%**，按原始全价计费你本应付约 ¥{raw_cost:.2f}，"
                     f"实际仅 ¥{eff_cost:.2f}——**缓存为你省下 ¥{cache_saving:.2f}**，"
                     f"是实际成本远低于原始账单的主因。")
        lines.append("")

    # 十、下期展望（基于实际数据动态生成）
    lines.append(f"## 十一、{_next}展望")
    lines.append("")
    lines.append(f"基于{_short}实际数据，先预测下期用量，再按优先级给出行动建议：")
    lines.append("")
    for item in build_next_week_outlook(summary, daily_tokens, automation_runs,
                                        data.get("session_credits", []), period_key=_key):
        lines.append(item)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*本报告基于 WorkBuddy 数据自动生成。*")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# HTML 格式
# ─────────────────────────────────────────────────────────────


# ── 四、成本深度分析（每会话 / 异常 / 省钱）────────────────
def _build_session_cwd_maps(data):
    """构建两个 session 映射：
      - sessions_by_cwd: cwd -> [session_id, ...]
      - session_cost_by_id: session_id -> effective_cost
    用于 §4.5 失败自动化成本归因（automation_runs 通过 source_cwd ↔ sessions.cwd 关联）。
    """
    sessions_by_cwd = {}
    for s in (data.get("sessions") or []):
        cwd = s.get("cwd")
        if not cwd:
            continue
        sessions_by_cwd.setdefault(cwd, []).append(s.get("id"))
    session_cost_by_id = {}
    for r in ((data.get("session_stats") or {}).get("rows") or []):
        sid = r.get("session_id")
        if sid:
            session_cost_by_id[sid] = r.get("effective_cost", 0.0)
    return sessions_by_cwd, session_cost_by_id


def _compute_failed_automation_cost(automation_runs, sessions_by_cwd, session_cost_by_id):
    """O1 v1.5 增强：按 automation_id 聚合失败自动化的浪费成本。

    数据模型说明：automation_runs.thread_id 是「run-{ts}-{n}」格式的内部 run ID，
    与 sessions.id（UUID）不是同一套标识系统，因此无法直接 1:1 关联。本函数采用
    cwd 作为桥接键：每个 automation 涉及一组唯一 source_cwd，匹配 sessions.cwd 后
    累加这些会话的有效成本，再除以运行次数得到「平均单次成本」。

    估算口径：
      - 单 cwd 单会话（如手动测试）：归因准确。
      - 多 cwd / 单 cwd 多会话：可能高估（混入了同工作区的交互会话成本）。
      - 失败重试场景下，实际失败成本可能略高于平均；本估算取平均属保守口径。
    只读，不写任何配置；apply 动作（F8/v2）不触发。
    """
    if not automation_runs or not sessions_by_cwd or not session_cost_by_id:
        return []
    groups = {}
    for r in automation_runs:
        groups.setdefault(r.get("automation_id") or "unknown", []).append(r)
    out = []
    for auto_id, runs in groups.items():
        fail = sum(1 for x in runs if not x.get("result_success"))
        if fail == 0:
            continue
        unique_cwds = {x.get("source_cwd") for x in runs if x.get("source_cwd")}
        total_cost = 0.0
        matched_session_count = 0
        matched_cwd_count = 0
        for cwd in unique_cwds:
            sids = sessions_by_cwd.get(cwd) or []
            if not sids:
                continue
            matched_cwd_count += 1
            matched_session_count += len(sids)
            for sid in sids:
                total_cost += session_cost_by_id.get(sid, 0.0)
        total_runs = len(runs)
        avg_cost = total_cost / total_runs if total_runs else 0.0
        waste = fail * avg_cost
        fail_rate = fail / total_runs * 100 if total_runs else 0
        # D3：匹配到会话但其成本全为 0（多为幽灵 / 空 trace）时，标记「成本未解析」，
        # 渲染层据此显示「无法估算」而非误导性的 ¥0.00。
        unresolved = (matched_session_count > 0 and total_cost == 0)
        out.append({
            "name": runs[0].get("automation_name") or auto_id,
            "state": _auto_state_label(runs[0].get("auto_status")),
            "total_runs": total_runs,
            "fail": fail,
            "fail_rate": fail_rate,
            "avg_cost": avg_cost,
            "waste": waste,
            "cwd_count": len(unique_cwds),
            "matched_cwd_count": matched_cwd_count,
            "matched_session_count": matched_session_count,
            "unresolved": unresolved,
        })
    out.sort(key=lambda x: x["waste"], reverse=True)
    return out


def _render_failed_automation_md(items):
    if not items:
        return []
    lines = []
    lines.append("### 4.5 失败自动化成本归因")
    lines.append("")
    lines.append("> 💸 失败运行 = 消耗 token 但无产出。下列自动化本月存在失败运行，"
                 "按「失败次数 × 该任务所在工作区平均单次成本」估算白白烧掉的成本。")
    lines.append("> ⚠️ 估算口径：通过 `automation.source_cwd ↔ sessions.cwd` 关联；"
                 "若同一工作区下含多个会话（含交互），平均成本可能略高估。")
    lines.append("")
    lines.append("| 自动化任务 | 状态 | 运行 | 失败 | 失败率 | 平均单次成本 | 估算浪费 |")
    lines.append("|-----------|------|------|------|--------|-------------|---------|")
    for r in items:
        warn = " ⚠️" if r["fail_rate"] >= 30 else ""
        if r.get("unresolved"):
            avg_disp = "¥—（成本未解析）"
            waste_disp = "¥—（成本未解析）"
        else:
            avg_disp = f"¥{r['avg_cost']:.2f}"
            waste_disp = f"¥{r['waste']:.2f}"
        lines.append(
            f"| {r['name']}{warn} | {r['state']} | {r['total_runs']} | {r['fail']} | "
            f"{r['fail_rate']:.0f}% | {avg_disp} | {waste_disp} |"
        )
    lines.append("")
    total_waste = sum(r["waste"] for r in items)
    lines.append(f"> 💸 **本月失败自动化总浪费估算：¥{total_waste:.2f}**"
                 "（按失败次数 × 该任务所在工作区平均单次成本推算）。")
    if any(r.get("unresolved") for r in items):
        lines.append("> ⚠️ 部分失败任务的成本未解析（会话无 token 或模型未解析），其浪费无法估算，"
                     "上表「总浪费」为可解析部分的下限，实际可能更高。")
    lines.append("> 建议：失败率 ≥ 30% 的任务优先排查依赖环境与配置，或暂停直至修复。"
                 "（apply 动作属 F8/v2，本节仅提供洞察。）")
    lines.append("")
    return lines


def _render_failed_automation_html(items):
    if not items:
        return []
    L = []
    L.append('        <h3>4.5 失败自动化成本归因</h3>')
    L.append('        <p>💸 失败运行 = 消耗 token 但无产出。'
             '按「失败次数 × 该任务所在工作区平均单次成本」估算白白烧掉的成本。</p>')
    L.append('        <p>⚠️ 估算口径：通过 automation.source_cwd ↔ sessions.cwd 关联；'
             '若同一工作区下含多个会话（含交互），平均成本可能略高估。</p>')
    L.append('        <table>')
    L.append('            <tr><th>自动化任务</th><th>状态</th><th>运行</th><th>失败</th>'
             '<th>失败率</th><th>平均单次成本</th><th>估算浪费</th></tr>')
    for r in items:
        warn = " ⚠️" if r["fail_rate"] >= 30 else ""
        if r.get("unresolved"):
            avg_disp = "¥—（成本未解析）"
            waste_disp = "¥—（成本未解析）"
        else:
            avg_disp = f"¥{r['avg_cost']:.2f}"
            waste_disp = f"¥{r['waste']:.2f}"
        L.append(f"            <tr><td>{_esc(r['name'])}{warn}</td><td>{r['state']}</td>"
                 f"<td>{r['total_runs']}</td><td>{r['fail']}</td>"
                 f"<td>{r['fail_rate']:.0f}%</td><td>{avg_disp}</td>"
                 f"<td>{waste_disp}</td></tr>")
    L.append('        </table>')
    total_waste = sum(r["waste"] for r in items)
    L.append(f'        <p>💸 <b>本月失败自动化总浪费估算：¥{total_waste:.2f}</b>'
             f'（按失败次数 × 该任务所在工作区平均单次成本推算）。</p>')
    if any(r.get("unresolved") for r in items):
        L.append('        <p style="color:var(--disclaimer-fg)">⚠️ 部分失败任务的成本未解析（会话无 token 或模型未解析），'
                 '其浪费无法估算，上表「总浪费」为可解析部分的下限，实际可能更高。</p>')
    L.append('        <p>建议：失败率 ≥ 30% 的任务优先排查依赖环境与配置，或暂停直至修复。'
             '（apply 动作属 F8/v2，本节仅洞察。）</p>')
    return L


def _compute_session_size_anomalies(rows):
    """O2 v1.5：会话规模（调用次数）异常检测。纯只读。

    threshold = max(p95(calls), 200)，top 5 按 calls 降序。
    """
    if not rows:
        return None
    calls_sorted = sorted([r.get("calls", 0) for r in rows if r.get("calls", 0) > 0])
    if not calls_sorted:
        return None
    n = len(calls_sorted)
    p95_idx = max(0, int(n * 0.95) - 1)
    p95_calls = calls_sorted[p95_idx]
    threshold = max(p95_calls, 200)
    items = sorted(
        [r for r in rows if r.get("calls", 0) > threshold],
        key=lambda x: x.get("calls", 0), reverse=True,
    )
    return {"threshold": threshold, "p95": p95_calls, "items": items[:5]}


def _compute_cache_and_untitled(data, threshold_pct=60.0):
    """O4+O6 v1.5：缓存健康度卡 + 未命名高成本会话提示。纯只读。

    O4：从 traces 聚合每会话 cache_rate，找出 < threshold 的会话，
       按「(全局缓存率 - 当前缓存率) × 当前成本 × 0.5」估算可省（保守口径）。
    O6：未命名 / 标题为「未命名会话」且成本 ≥ 会话级 p95 的会话。
    """
    summary = data.get("summary", {})
    total_cached = summary.get("total_cached_tokens", 0)
    total_input = summary.get("total_input_tokens", 0)
    global_cache_rate = (total_cached / total_input * 100) if total_input else 0

    traces = data.get("traces") or []
    sessions = data.get("sessions") or []
    rows = (data.get("session_stats") or {}).get("rows", [])
    if not rows:
        return None

    sess_cache = defaultdict(lambda: {"cached": 0, "input": 0})
    for t in traces:
        sid = t.get("session_id")
        if not sid:
            continue
        sess_cache[sid]["cached"] += t.get("cached_tokens", 0) or 0
        sess_cache[sid]["input"] += t.get("input_tokens", 0) or 0

    sid_to_meta = {}
    for s in sessions:
        sid = s.get("id")
        if sid:
            sid_to_meta[sid] = {"title": s.get("title") or ""}
    for r in rows:
        sid = r.get("session_id")
        if sid:
            sid_to_meta.setdefault(sid, {"title": ""})
            sid_to_meta[sid].update({
                "cost": r.get("effective_cost", 0.0),
                "calls": r.get("calls", 0),
                "task_type": r.get("task_type", "其他"),
                "first_date": r.get("first_date", ""),
            })

    # O4：缓存健康度
    cache_items = []
    for sid, st in sess_cache.items():
        if st["input"] <= 0:
            continue
        cr = st["cached"] / st["input"] * 100
        if cr < threshold_pct:
            meta = sid_to_meta.get(sid, {})
            cost = meta.get("cost", 0.0)
            gap = max(global_cache_rate - cr, 0)
            saving = cost * (gap / 100) * 0.5  # 保守估算：差距一半可补
            cache_items.append({
                "title": (meta.get("title") or "未命名会话")[:30],
                "task_type": meta.get("task_type", "其他"),
                "cache_rate": cr,
                "calls": meta.get("calls", 0),
                "cost": cost,
                "potential_saving": saving,
            })
    cache_items.sort(key=lambda x: x["potential_saving"], reverse=True)
    cache_health = {
        "global_rate": global_cache_rate,
        "threshold": threshold_pct,
        "items": cache_items[:5],
        "total_potential": sum(x["potential_saving"] for x in cache_items),
    }

    # O6：未命名高成本会话
    costs = sorted([r.get("effective_cost", 0) for r in rows])
    n = len(costs)
    cost_p95 = costs[max(0, int(n * 0.95) - 1)] if costs else 0
    untitled = []
    for r in rows:
        title = r.get("title", "")
        cost = r.get("effective_cost", 0)
        if (not title or title == "未命名会话") and cost >= cost_p95 and cost > 0:
            untitled.append({
                "title": title or "未命名会话",
                "task_type": r.get("task_type", "其他"),
                "cost": cost,
                "calls": r.get("calls", 0),
                "first_date": r.get("first_date", ""),
            })
    untitled.sort(key=lambda x: x["cost"], reverse=True)

    # D4：本期成本整体未解析（多为幽灵 / 空 trace）时，缓存可省金额无法量化，标记之。
    cost_unresolved = (summary.get("total_effective_cost", 0) == 0)
    return {"cache_health": cache_health, "untitled": untitled[:5], "cost_p95": cost_p95,
            "cost_unresolved": cost_unresolved}


def _render_cache_untitled_md(payload):
    if not payload:
        return []
    ch = payload["cache_health"]
    lines = []
    lines.append("### 4.6 缓存健康度与未命名高成本会话")
    lines.append("")
    lines.append(f"> 💡 全局缓存命中率 **{ch['global_rate']:.1f}%**——"
                 f"提升缓存复用是「零质量风险」的直接省钱杠杆。"
                 f"下列会话缓存率 < {ch['threshold']}%。")
    lines.append("")
    if ch["items"]:
        lines.append("**📉 缓存健康度卡**（保守估算「差距 × 0.5 × 当前成本」为可省空间）：")
        lines.append("")
        lines.append("| 任务名称 | 任务类型 | 缓存率 | 调用 | 当前成本 | 估算可省 |")
        lines.append("|---------|---------|--------|------|---------|---------|")
        for r in ch["items"]:
            lines.append(
                f"| {r['title']} | {r['task_type']} | {r['cache_rate']:.0f}% | "
                f"{r['calls']} | ¥{r['cost']:.2f} | ¥{r['potential_saving']:.2f} |"
            )
        lines.append("")
        lines.append(f"> 💰 **缓存潜力总省：¥{ch['total_potential']:.2f}**"
                     "（F8 prompt-caching 可承接）。")
        lines.append("")
    else:
        lines.append(f"- 当前所有会话缓存率均 ≥ {ch['threshold']}%，暂无明显缓存优化空间。")
        lines.append("")

    if payload.get("cost_unresolved"):
        lines.append("> ⚠️ 缓存潜力估算不可用：本期成本未解析（多为幽灵 / 空 trace），无法量化可省金额；"
                     "请先解决 §4 顶部的数据缺失提示。")
        lines.append("")

    if payload["untitled"]:
        lines.append(f"**⚠️ 未命名高成本会话**（成本 ≥ ¥{payload['cost_p95']:.2f}"
                     " = 会话级 p95，建议补标题便于归因）：")
        lines.append("")
        lines.append("| 标题 | 任务类型 | 实际成本 | 调用 | 日期 |")
        lines.append("|------|---------|---------|------|------|")
        for r in payload["untitled"]:
            lines.append(
                f"| ⚠️ {r['title']} | {r['task_type']} | ¥{r['cost']:.2f} | "
                f"{r['calls']} | {r['first_date']} |"
            )
        lines.append("")
    return lines


def _render_cache_untitled_html(payload):
    if not payload:
        return []
    ch = payload["cache_health"]
    L = []
    L.append('        <h3>4.6 缓存健康度与未命名高成本会话</h3>')
    L.append(f'        <p>💡 全局缓存命中率 <b>{ch["global_rate"]:.1f}%</b>——'
             '提升缓存复用是「零质量风险」的直接省钱杠杆。'
             f'下列会话缓存率 &lt; {ch["threshold"]}%。</p>')
    if ch["items"]:
        L.append('        <p><b>📉 缓存健康度卡</b>（保守估算「差距 × 0.5 × 当前成本」为可省空间）：</p>')
        L.append('        <table>')
        L.append('            <tr><th>任务名称</th><th>任务类型</th><th>缓存率</th>'
                 '<th>调用</th><th>当前成本</th><th>估算可省</th></tr>')
        for r in ch["items"]:
            L.append(f"            <tr><td>{_esc(r['title'])}</td><td>{_esc(r['task_type'])}</td>"
                     f"<td>{r['cache_rate']:.0f}%</td><td>{r['calls']}</td>"
                     f"<td>¥{r['cost']:.2f}</td><td>¥{r['potential_saving']:.2f}</td></tr>")
        L.append('        </table>')
        L.append(f'        <p>💰 <b>缓存潜力总省：¥{ch["total_potential"]:.2f}</b>'
                 '（F8 prompt-caching 可承接）。</p>')
    else:
        L.append(f'        <p>当前所有会话缓存率均 ≥ {ch["threshold"]}%，'
                 '暂无明显缓存优化空间。</p>')

    if payload.get("cost_unresolved"):
        L.append('        <p style="color:var(--disclaimer-fg)">⚠️ 缓存潜力估算不可用：本期成本未解析'
                 '（多为幽灵 / 空 trace），无法量化可省金额；请先解决 §4 顶部的数据缺失提示。</p>')

    if payload["untitled"]:
        L.append(f'        <p><b>⚠️ 未命名高成本会话</b>（成本 ≥ ¥{payload["cost_p95"]:.2f}'
                 ' = 会话级 p95，建议补标题便于归因）：</p>')
        L.append('        <table>')
        L.append('            <tr><th>标题</th><th>任务类型</th><th>实际成本</th>'
                 '<th>调用</th><th>日期</th></tr>')
        for r in payload["untitled"]:
            L.append(f"            <tr><td>⚠️ {_esc(r['title'])}</td><td>{_esc(r['task_type'])}</td>"
                     f"<td>¥{r['cost']:.2f}</td><td>{r['calls']}</td>"
                     f"<td>{r['first_date']}</td></tr>")
        L.append('        </table>')
    return L


def _unresolved_call_stats(data):
    """统计「未解析 / 幽灵」调用，供 §1 / §4 警告卡使用。

    早期 WorkBuddy trace 可能同时缺失 sessionId、modelInfo（被 collect 兜底记为字面量
    'default' 且 token 全 0），这类调用无法归属到模型 / 会话 / 成本——须显式提示，
    而非静默记为 ¥0.00。
    """
    traces = data.get("traces", [])
    n_default = sum(1 for t in traces if (t.get("raw_model") or "") == "default")
    n_no_session = sum(1 for t in traces if not t.get("session_id"))
    n_no_tokens = sum(1 for t in traces
                      if (t.get("input_tokens", 0) or 0) + (t.get("output_tokens", 0) or 0) == 0)
    return {
        "total": len(traces),
        "default": n_default,
        "no_session": n_no_session,
        "no_tokens": n_no_tokens,
    }


def _unresolved_warning_md(stats):
    """Markdown 警告卡：列出未解析 / 幽灵调用的占比与影响。无异常时返回空串。"""
    if stats["default"] == 0 and stats["no_session"] == 0 and stats["no_tokens"] == 0:
        return ""
    try:
        from collect_usage_data import DEFAULT_MODEL
    except Exception:
        DEFAULT_MODEL = "（未配置）"
    parts = []
    if stats["default"]:
        parts.append(
            f"{stats['default']} 次调用模型未解析（记为 `default`，缺 modelInfo 无真实模型名），"
            f"已按默认模型 `{DEFAULT_MODEL}` 口径估算；若不符请改 collect 脚本的 DEFAULT_MODEL 常量"
        )
    if stats["no_session"]:
        parts.append(f"{stats['no_session']} 次调用缺 sessionId，未计入「每会话成本」分析")
    if stats["no_tokens"]:
        parts.append(f"{stats['no_tokens']} 次调用无 token 数据，未计入成本")
    pct = ""
    if stats["total"]:
        unresolved = stats["default"] or stats["no_tokens"]
        if unresolved:
            pct = f"（占本期 {stats['total']} 次调用的约 {round(unresolved / stats['total'] * 100)}%）"
    return ("> ⚠️ **未解析 / 幽灵调用提示**" + pct + "：" + "；".join(parts) + "。\n"
            "> 这些调用并非「免费」，而是数据缺失导致无法计费——请结合上方各章节交叉核对。\n")


def _fmt_anom_val(v, is_cost):
    return f"¥{v:.2f}" if is_cost else f"{v:,.0f} token"


def _compute_free_token_share(data):
    """估算本期「有效 Token 中来自零成本（免费 / 限时免费 / 未计费）调用」的比例。

    用于判断本期是否为免费主导期，从而给出 §4 免责声明。优先用逐 trace 数据（精确）；
    退化到每日统计（日级，免费+付费可能同日记一日，略粗略）。
    """
    traces = data.get("traces") or []
    if traces:
        total_eff = sum(t.get("effective_tokens", 0) for t in traces)
        if not total_eff:
            return 1.0
        free_eff = sum(t.get("effective_tokens", 0) for t in traces if t.get("effective_cost", 0) == 0)
        return free_eff / total_eff
    daily = data.get("daily_tokens", {}) or {}
    total_eff = sum(d.get("effective", 0) for d in daily.values())
    if not total_eff:
        return 1.0
    free_eff = sum(d.get("effective", 0) for d in daily.values() if d.get("effective_cost", 0) == 0)
    return free_eff / total_eff


def _free_period_disclaimer(data):
    """若本期为免费 / 限时免费主导期，返回免责声明 md 段落（含末尾空行）；否则返回 []。"""
    free_share = _compute_free_token_share(data)
    total_eff_cost = (data.get("summary", {}) or {}).get("total_effective_cost", 0) or 0
    free_dominant = (free_share >= 0.8) or (total_eff_cost == 0)
    if not free_dominant:
        return []
    pct = round(free_share * 100)
    return [
        f"> 🎁 **本期以免费 / 限时免费模型为主**（约 {pct}% 的有效 Token 来自零成本调用）。",
        ">",
        "> 成本相关洞察（§4.4 省钱杠杆、§4.5 失败自动化成本归因、§4.6 未命名高成本会话）在免费期参考意义有限；",
        "> 请重点参考 **§4.3 Token 口径异常**（捕捉免费日的高 Token 峰值）与 **§4.6 缓存健康度**（与计费无关，始终有效）。",
        "",
    ]


def _free_period_disclaimer_html(data):
    """免费 / 限时免费主导期的免责声明（HTML 版），返回 html 行列表。"""
    free_share = _compute_free_token_share(data)
    total_eff_cost = (data.get("summary", {}) or {}).get("total_effective_cost", 0) or 0
    free_dominant = (free_share >= 0.8) or (total_eff_cost == 0)
    if not free_dominant:
        return []
    pct = round(free_share * 100)
    return [
        '        <div class="disclaimer-box">',
        f'            <b>🎁 本期以免费 / 限时免费模型为主</b>（约 {pct}% 的有效 Token 来自零成本调用）。',
        '            <p>成本相关洞察（§4.4 省钱杠杆、§4.5 失败自动化成本归因、§4.6 未命名高成本会话）在免费期参考意义有限；'
        '请重点参考 <b>§4.3 Token 口径异常</b>（捕捉免费日的高 Token 峰值）与 <b>§4.6 缓存健康度</b>（与计费无关，始终有效）。</p>',
        '        </div>',
    ]


def _render_anomaly_block_md(title, block, kind):
    """渲染单口径（cost / token）异常块，返回 md 行列表。"""
    lines = []
    thr = block.get("thresholds", {})
    if kind == "cost":
        lines.append(f"**💰 {title}**：日级成本阈值 p50 = ¥{thr.get('p50', 0):.2f}、"
                     f"p95 = ¥{thr.get('p95', 0):.2f}；会话级 p95 = ¥{thr.get('session_p95', 0):.2f}。")
    else:
        lines.append(f"**📊 {title}**：日级有效 Token 阈值 p50 = {thr.get('p50', 0):,.0f}、"
                     f"p95 = {thr.get('p95', 0):,.0f}；会话级 p95（调用次数）= {thr.get('session_p95', 0):,.0f}。")
    lines.append("")
    daily = block.get("daily", [])
    if daily:
        lines.append("**异常日**：")
        lines.append("")
        for a in daily:
            v = _fmt_anom_val(a["value"], kind == "cost")
            lines.append(f"- 🔺 **{a['date']}**：{v} —— {'；'.join(a['reasons'])}")
        lines.append("")
    else:
        label = "成本" if kind == "cost" else "Token 消耗"
        lines.append(f"- 日级{label}平稳，无超过 p95 或环比突增的异常日。")
        lines.append("")
    sess = block.get("session", [])
    if sess:
        if kind == "cost":
            lines.append("**异常高成本会话（超过会话级 p95）**：")
        else:
            lines.append("**异常高调用会话（调用次数超过 p95）**：")
        lines.append("")
        for a in sess:
            models = "、".join(a.get("models", [])[:3]) or "—"
            if kind == "cost":
                lines.append(f"- 💰 **{a['title']}**：¥{a['value']:.2f}（主要模型：{models}）")
            else:
                lines.append(f"- 💰 **{a['title']}**：实际消耗 {a['value']:,.0f} token、"
                             f"调用 {a.get('calls', 0)} 次（主要模型：{models}）")
        lines.append("")
    return lines


def _render_anomaly_block_html(title, block, kind):
    """渲染单口径（cost / token）异常块，返回 html 行列表。"""
    L = []
    thr = block.get("thresholds", {})
    if kind == "cost":
        L.append(f"        <p><b>💰 {title}</b>：日级成本阈值 p50 = ¥{thr.get('p50', 0):.2f}、"
                 f"p95 = ¥{thr.get('p95', 0):.2f}；会话级 p95 = ¥{thr.get('session_p95', 0):.2f}。</p>")
    else:
        L.append(f"        <p><b>📊 {title}</b>：日级有效 Token 阈值 p50 = {thr.get('p50', 0):,.0f}、"
                 f"p95 = {thr.get('p95', 0):,.0f}；会话级 p95（调用次数）= {thr.get('session_p95', 0):,.0f}。</p>")
    daily = block.get("daily", [])
    if daily:
        L.append("        <p><b>异常日</b>：</p><ul>")
        for a in daily:
            v = _fmt_anom_val(a["value"], kind == "cost")
            L.append(f"            <li>🔺 <b>{a['date']}</b>：{v} —— {'；'.join(a['reasons'])}</li>")
        L.append("        </ul>")
    else:
        label = "成本" if kind == "cost" else "Token 消耗"
        L.append(f"        <p>日级{label}平稳，无超过 p95 或环比突增的异常日。</p>")
    sess = block.get("session", [])
    if sess:
        if kind == "cost":
            L.append("        <p><b>异常高成本会话（超过会话级 p95）</b>：</p><ul>")
        else:
            L.append("        <p><b>异常高调用会话（调用次数超过 p95）</b>：</p><ul>")
        for a in sess:
            models = "、".join(a.get("models", [])[:3]) or "—"
            if kind == "cost":
                L.append(f"            <li>💰 <b>{_esc(a['title'])}</b>：¥{a['value']:.2f}（主要模型：{models}）</li>")
            else:
                L.append(f"            <li>💰 <b>{_esc(a['title'])}</b>：实际消耗 {a['value']:,.0f} token、"
                         f"调用 {a.get('calls', 0)} 次（主要模型：{models}）</li>")
        L.append("        </ul>")
    return L


def build_cost_analysis_section_md(data):
    """Markdown 章节：四、成本深度分析（每会话 / 异常 / 省钱）。

    覆盖行业调研头号诉求「每任务 / 每会话成本」：token 成本往往只占 AI Agent 总成本的
    30–70%，真正烧钱的是「每个会话跑几次调用、几次重试」。另含成本异常检测与省钱杠杆洞察。
    """
    session_stats = data.get("session_stats") or {}
    rows = session_stats.get("rows", [])
    buckets = session_stats.get("buckets", [])
    ca = data.get("cost_anomalies") or {}
    si = data.get("savings_insights") or {}
    automation_runs = data.get("automation_runs") or []
    if not rows:
        return []
    lines = []
    lines.append("## 四、成本深度分析（每会话 / 异常 / 省钱）")
    lines.append("")
    lines.append("> 行业调研显示：Agent 使用者最关心「**每任务 / 每会话成本**」——token 成本往往只占 AI Agent 总成本的 30–70%，"
                 "真正烧钱的是「每个会话跑几次调用、几次重试」。以下从会话维度拆解你的花费。")
    lines.append("")
    # 未解析 / 幽灵调用警告卡（D1：替代静默 ¥0.00，显式提示数据缺失）
    _warn = _unresolved_warning_md(_unresolved_call_stats(data))
    if _warn:
        lines.append(_warn)
        lines.append("")

    # 免费 / 限时免费主导期免责声明（P1：避免 §4.4/4.5/4.6 成本洞察在免费期误导）
    _disc = _free_period_disclaimer(data)
    if _disc:
        lines.extend(_disc)

    # 4.1 每会话成本 Top 10
    lines.append("### 4.1 每会话成本 Top 10")
    lines.append("")
    lines.append("| 排名 | 任务名称 | 任务类型 | 实际成本 | 实际消耗 | 调用 | 主要模型 |")
    lines.append("|------|---------|---------|---------|---------|------|---------|")
    for i, r in enumerate(rows[:10], 1):
        models = "、".join(r.get("models", [])[:3]) or "—"
        lines.append(
            f"| {i} | {r['title']} | {r['task_type']} | ¥{r['effective_cost']:.2f} | "
            f"{format_number(r['effective_tokens'])} | {r['calls']} | {models} |"
        )
    lines.append("")

    # 4.2 成本分布
    lines.append("### 4.2 每会话成本分布")
    lines.append("")
    total_cost = sum(b["cost"] for b in buckets) or 0
    lines.append(f"本期共 {len(rows)} 个会话，合计实际成本 ¥{total_cost:.2f}。按单会话成本分桶（图表按会话数，明细含合计成本）：")
    lines.append("")
    bar = build_session_cost_bar_md(buckets)
    if bar:
        lines.append(bar)
        lines.append("")
    lines.append("**明细**（成本区间 | 会话数 | 合计成本 | 占成本比）：")
    lines.append("")
    lines.append("| 成本区间 | 会话数 | 合计成本 | 占比 |")
    lines.append("|---------|--------|---------|------|")
    for b in buckets:
        pct = (b["cost"] / total_cost * 100) if total_cost else 0
        lines.append(f"| {b['label']} | {b['count']} | ¥{b['cost']:.2f} | {pct:.1f}% |")
    lines.append("")

    # 4.3 异常检测（双口径：成本 + Token）
    lines.append("### 4.3 成本 / Token 异常与飙升检测（双口径）")
    lines.append("")
    lines.append("> 本节同时以「成本」与「Token 消耗」两个**独立**口径检测异常日，"
                 "避免免费 / 限时免费模型拉低成本口径而漏报 Token 峰值。")
    lines.append("")
    cost_block = ca.get("cost")
    if cost_block is None:
        lines.append(f"> ⚠️ {ca.get('cost_note', '')}")
        lines.append("")
    else:
        lines.extend(_render_anomaly_block_md("成本口径", cost_block, "cost"))
    lines.extend(_render_anomaly_block_md("Token 口径", ca["token"], "token"))

    # 4.3.1 会话规模异常（O2 v1.5 增强，纯只读）
    size_anom = _compute_session_size_anomalies(rows)
    if size_anom and size_anom["items"]:
        lines.append(f"**📊 会话规模异常**（调用次数 > {size_anom['threshold']}，"
                     f"取 max(会话 p95={size_anom['p95']}, 200)，疑似 fan-out / 长链路）：")
        lines.append("")
        lines.append("| 任务名称 | 任务类型 | 调用 | 实际成本 |")
        lines.append("|---------|---------|------|---------|")
        for r in size_anom["items"]:
            lines.append(
                f"| {r['title'][:30]} | {r['task_type']} | {r['calls']} | ¥{r['effective_cost']:.2f} |"
            )
        lines.append("")

    # 4.4 省钱杠杆
    lines.append("### 4.4 省钱杠杆（自动洞察）")
    lines.append("")
    items = si.get("items", [])
    if items:
        lines.append("基于「实际执行模型」维度分析，以下高占比付费模型存在更便宜的替代方案"
                     "（假设 30% 的简单任务可迁移，估算口径，仅供参考）：")
        lines.append("")
        for it in items:
            lines.append(
                f"- **{it['model']}** 当前花费 ¥{it['cost']:.2f}（占付费成本 {it['cost_share']:.1f}%）；"
                f"若简单任务迁移至 **{it['alternative']}**（{it['note']}），"
                f"预计月省 **¥{it['estimated_monthly_save']:.2f}**。"
            )
        lines.append("")
        lines.append(f"> 💡 **合计预计月省 ¥{si.get('total_estimated_monthly_save', 0):.2f}**"
                     f"（保守估算，实际取决于你可迁移的任务比例）。")
    else:
        lines.append("- 当前付费模型均已是最优性价比，暂无明确可迁移的更便宜替代；"
                     "后续若引入更便宜模型或提升缓存复用率，可进一步降本。")
    lines.append("")

    # 4.5 失败自动化成本归因（O1 v1.5 增强，纯只读）
    sessions_by_cwd, session_cost_by_id = _build_session_cwd_maps(data)
    failed_items = _compute_failed_automation_cost(automation_runs, sessions_by_cwd, session_cost_by_id)
    lines.extend(_render_failed_automation_md(failed_items))

    # 4.6 缓存健康度与未命名高成本会话（O4+O6 v1.5 增强，纯只读）
    cache_untitled = _compute_cache_and_untitled(data, threshold_pct=60.0)
    lines.extend(_render_cache_untitled_md(cache_untitled))

    return lines


def build_cost_analysis_section_html(data):
    """HTML 章节：四、成本深度分析（对应 MD 同名章节）。"""
    session_stats = data.get("session_stats") or {}
    rows = session_stats.get("rows", [])
    buckets = session_stats.get("buckets", [])
    ca = data.get("cost_anomalies") or {}
    si = data.get("savings_insights") or {}
    automation_runs = data.get("automation_runs") or []
    if not rows:
        return []
    L = []
    L.append('    <div class="section">')
    L.append('        <h2 class="section-title">四、成本深度分析（每会话 / 异常 / 省钱）</h2>')
    L.append('        <p>行业调研显示：Agent 使用者最关心「<b>每任务 / 每会话成本</b>」——token 成本往往只占 '
             'AI Agent 总成本的 30–70%，真正烧钱的是「每个会话跑几次调用、几次重试」。以下从会话维度拆解你的花费。</p>')
    _warn = _unresolved_warning_md(_unresolved_call_stats(data))
    if _warn:
        _warn_html = _warn.replace("\n", " ").replace("⚠️", "").strip()
        L.append(f'        <div class="warn-box"><b>⚠️ 未解析 / 幽灵调用提示</b>：{_esc(_warn_html)}</div>')

    # 免费 / 限时免费主导期免责声明（P1）
    L.extend(_free_period_disclaimer_html(data))

    # 4.1
    L.append('        <h3>4.1 每会话成本 Top 10</h3>')
    L.append('        <table>')
    L.append('            <tr><th>排名</th><th>任务名称</th><th>任务类型</th><th>实际成本</th>'
             '<th>实际消耗</th><th>调用</th><th>主要模型</th></tr>')
    for i, r in enumerate(rows[:10], 1):
        models = "、".join(r.get("models", [])[:3]) or "—"
        L.append(f"            <tr><td>{i}</td><td>{_esc(r['title'])}</td><td>{_esc(r['task_type'])}</td>"
                 f"<td>¥{r['effective_cost']:.2f}</td><td>{format_number(r['effective_tokens'])}</td>"
                 f"<td>{r['calls']}</td><td>{models}</td></tr>")
    L.append('        </table>')

    # 4.2
    L.append('        <h3>4.2 每会话成本分布</h3>')
    total_cost = sum(b["cost"] for b in buckets) or 0
    L.append(f"        <p>本期共 {len(rows)} 个会话，合计实际成本 ¥{total_cost:.2f}。按单会话成本分桶（环形图按会话数）：</p>")
    # 环形图：每会话成本分布（按会话数，复用 build_donut_chart 并指定 value_key）
    bucket_stats = [{"task_type": b["label"], "count": b["count"], "cost": b["cost"]} for b in buckets]
    donut = build_donut_chart(bucket_stats, title="每会话成本分布（按会话数）",
                              center_label="会话数", value_key="count", unit=" 会话")
    if donut:
        L.append(donut)
    L.append('        <table>')
    L.append('            <tr><th>成本区间</th><th>会话数</th><th>合计成本</th><th>占比</th></tr>')
    for b in buckets:
        pct = (b["cost"] / total_cost * 100) if total_cost else 0
        L.append(f"            <tr><td>{b['label']}</td><td>{b['count']}</td>"
                 f"<td>¥{b['cost']:.2f}</td><td>{pct:.1f}%</td></tr>")
    L.append('        </table>')

    # 4.3 异常检测（双口径：成本 + Token）
    L.append('        <h3>4.3 成本 / Token 异常与飙升检测（双口径）</h3>')
    L.append('        <p>本节同时以「成本」与「Token 消耗」两个<b>独立</b>口径检测异常日，'
             '避免免费 / 限时免费模型拉低成本口径而漏报 Token 峰值。</p>')
    cost_block = ca.get("cost")
    if cost_block is None:
        L.append(f'        <p style="color:var(--disclaimer-fg)">⚠️ {ca.get("cost_note", "")}</p>')
    else:
        L.extend(_render_anomaly_block_html("成本口径", cost_block, "cost"))
    L.extend(_render_anomaly_block_html("Token 口径", ca["token"], "token"))

    # 4.3.1 会话规模异常（O2 v1.5 增强，纯只读）
    size_anom = _compute_session_size_anomalies(rows)
    if size_anom and size_anom["items"]:
        L.append(f'        <p><b>📊 会话规模异常</b>（调用次数 &gt; {size_anom["threshold"]}，'
                 f'取 max(会话 p95={size_anom["p95"]}, 200)，疑似 fan-out / 长链路）：</p>')
        L.append('        <table>')
        L.append('            <tr><th>任务名称</th><th>任务类型</th><th>调用</th><th>实际成本</th></tr>')
        for r in size_anom["items"]:
            L.append(f"            <tr><td>{_esc(r['title'][:30])}</td><td>{_esc(r['task_type'])}</td>"
                     f"<td>{r['calls']}</td><td>¥{r['effective_cost']:.2f}</td></tr>")
        L.append('        </table>')

    # 4.4
    L.append('        <h3>4.4 省钱杠杆（自动洞察）</h3>')
    items = si.get("items", [])
    if items:
        L.append("        <p>基于「实际执行模型」维度分析，以下高占比付费模型存在更便宜的替代方案"
                 "（假设 30% 的简单任务可迁移，估算口径，仅供参考）：</p><ul>")
        for it in items:
            L.append(
                f"            <li><b>{it['model']}</b> 当前花费 ¥{it['cost']:.2f}（占付费成本 {it['cost_share']:.1f}%）；"
                f"若简单任务迁移至 <b>{it['alternative']}</b>（{it['note']}），"
                f"预计月省 <b>¥{it['estimated_monthly_save']:.2f}</b>。</li>"
            )
        L.append("        </ul>")
        L.append(f'        <p>💡 <b>合计预计月省 ¥{si.get("total_estimated_monthly_save", 0):.2f}</b>'
                 f'（保守估算，实际取决于你可迁移的任务比例）。</p>')
    else:
        L.append("        <p>当前付费模型均已是最优性价比，暂无明确可迁移的更便宜替代；"
                 "后续若引入更便宜模型或提升缓存复用率，可进一步降本。</p>")

    # 4.5 失败自动化成本归因（O1 v1.5 增强，纯只读）
    sessions_by_cwd, session_cost_by_id = _build_session_cwd_maps(data)
    failed_items = _compute_failed_automation_cost(automation_runs, sessions_by_cwd, session_cost_by_id)
    L.extend(_render_failed_automation_html(failed_items))

    # 4.6 缓存健康度与未命名高成本会话（O4+O6 v1.5 增强，纯只读）
    cache_untitled = _compute_cache_and_untitled(data, threshold_pct=60.0)
    L.extend(_render_cache_untitled_html(cache_untitled))

    L.append('    </div>')
    return L


def generate_html_report(data):
    meta = data.get("meta", {})
    summary = data.get("summary", {})
    daily_tokens = data.get("daily_tokens", {})
    task_dist = summary.get("task_type_distribution", {})
    skills = data.get("skill_usage", {}).get("skills", {})
    outputs = data.get("outputs", [])
    automation_runs = data.get("automation_runs", [])

    total_input = summary.get("total_input_tokens", 0)
    total_cached = summary.get("total_cached_tokens", 0)
    cache_rate = (total_cached / total_input * 100) if total_input else 0

    # CSS 作为普通字符串，花括号即字面量，无需转义
    css = """
        :root {
            --bg: #ffffff; --fg: #333333; --muted: #6c757d;
            --card-bg: #f8f9fa; --card-border: #dee2e6; --table-border: #dee2e6;
            --th-bg: #f8f9fa; --accent: #3498db; --accent-fg: #2c3e50;
            --chart-bg: #f8f9fa; --bar-track: #e9ecef; --bar-fill: #3498db;
            --link: #3498db;
            --disclaimer-bg: #fff8e6; --disclaimer-border: #e0a800; --disclaimer-fg: #b8860b;
        }
        /* 显式深色：通过 <html data-theme="dark"> 强制 */
        :root[data-theme="dark"] {
            --bg: #16181d; --fg: #e6e6e6; --muted: #9aa0a6;
            --card-bg: #21242b; --card-border: #343a45; --table-border: #343a45;
            --th-bg: #262a32; --accent: #58b6ec; --accent-fg: #9fd3f3;
            --chart-bg: #21242b; --bar-track: #343a45; --bar-fill: #58b6ec;
            --link: #58b6ec;
            --disclaimer-bg: #3a2f12; --disclaimer-border: #c79a2e; --disclaimer-fg: #f0c75a;
        }
        /* 系统深色：未显式选择「浅色」时跟随系统配色 */
        @media (prefers-color-scheme: dark) {
            :root:not([data-theme="light"]) {
                --bg: #16181d; --fg: #e6e6e6; --muted: #9aa0a6;
                --card-bg: #21242b; --card-border: #343a45; --table-border: #343a45;
                --th-bg: #262a32; --accent: #58b6ec; --accent-fg: #9fd3f3;
                --chart-bg: #21242b; --bar-track: #343a45; --bar-fill: #58b6ec;
                --link: #58b6ec;
                --disclaimer-bg: #3a2f12; --disclaimer-border: #c79a2e; --disclaimer-fg: #f0c75a;
            }
        }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: var(--fg); background: var(--bg); max-width: 1200px; margin: 0 auto; padding: 20px; transition: background .3s ease, color .3s ease; }
        .header { background: var(--card-bg); padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid var(--card-border); transition: background .3s ease, border-color .3s ease; }
        .section { margin-bottom: 30px; }
        .section-title { color: var(--accent-fg); border-bottom: 2px solid var(--accent); padding-bottom: 10px; margin-bottom: 15px; }
        table { width: 100%; border-collapse: collapse; margin: 10px 0; }
        th, td { border: 1px solid var(--table-border); padding: 8px 12px; text-align: left; }
        th { background-color: var(--th-bg); font-weight: 600; }
        .chart-pie { display: flex; align-items: center; gap: 24px; flex-wrap: wrap; background: var(--chart-bg); padding: 15px; border-radius: 5px; }
        .chart-pie svg { flex-shrink: 0; }
        .chart-pie .legend { font-size: 13px; line-height: 1.5; color: var(--fg); }
        .chart-pie .legend-item { display: flex; align-items: center; margin: 4px 0; }
        .chart-pie .swatch { width: 12px; height: 12px; border-radius: 3px; margin-right: 8px; display: inline-block; }
        .chart-pie .pct { color: var(--muted); margin-left: 4px; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin: 15px 0; }
        .stat-card { background: var(--card-bg); padding: 15px; border-radius: 8px; border: 1px solid var(--card-border); }
        .stat-value { font-size: 24px; font-weight: bold; color: var(--accent-fg); }
        .stat-label { color: var(--muted); font-size: 14px; }
        .chart-bars { background: var(--chart-bg); padding: 15px; border-radius: 5px; margin: 10px 0; }
        .bar-row { display: flex; align-items: center; gap: 10px; margin: 6px 0; font-size: 13px; }
        .bar-label { width: 220px; text-align: right; font-family: monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .bar-track { flex: 1; background: var(--bar-track); border-radius: 4px; height: 14px; overflow: hidden; min-width: 60px; }
        .bar-fill { display: block; height: 100%; background: var(--bar-fill); }
        .bar-val { width: 80px; text-align: right; font-family: monospace; }
        .disclaimer { color: var(--disclaimer-fg); font-size: 13px; background: var(--disclaimer-bg); border-left: 4px solid var(--disclaimer-border); padding: 8px 12px; border-radius: 4px; margin: 12px 0; }
        .warn-box { color: var(--disclaimer-fg); background: var(--disclaimer-bg); border: 1px solid var(--disclaimer-border); border-left: 4px solid var(--disclaimer-border); padding: 10px 14px; border-radius: 6px; margin: 12px 0; font-size: 13px; }
        .disclaimer-box { color: var(--disclaimer-fg); background: var(--disclaimer-bg); border: 1px solid var(--disclaimer-border); border-left: 4px solid var(--disclaimer-border); padding: 10px 14px; border-radius: 6px; margin: 12px 0; font-size: 13px; }
        .disclaimer-box p { margin: 6px 0 0; }
        a { color: var(--link); }
        /* 主题切换控件 */
        .theme-toggle { display: inline-flex; gap: 6px; margin-top: 14px; flex-wrap: wrap; }
        .theme-toggle button {
            font-size: 13px; padding: 6px 14px; border-radius: 999px;
            border: 1px solid var(--table-border); background: var(--bg);
            color: var(--fg); cursor: pointer; transition: all .2s ease;
        }
        .theme-toggle button:hover { border-color: var(--accent); color: var(--accent-fg); }
        .theme-toggle button.active { background: var(--accent); color: #fff; border-color: var(--accent); }
    """

    lines = []
    _key = meta.get("period", "week")
    _label, _short, _next = _period_labels(meta)
    lines.append("<!DOCTYPE html>")
    lines.append('<html lang="zh-CN">')
    lines.append("<head>")
    lines.append('    <meta charset="UTF-8">')
    lines.append('    <meta name="viewport" content="width=device-width, initial-scale=1.0">')
    lines.append('    <script>')
    lines.append('        (function(){try{var t=localStorage.getItem("aurs-theme")||"system";if(t&&t!=="system"){document.documentElement.setAttribute("data-theme",t);}}catch(e){}})();')
    lines.append('    </script>')
    lines.append("    <title>Workbuddy使用情况报告</title>")
    lines.append("    <style>")
    lines.append(css)
    lines.append("    </style>")
    lines.append("</head>")
    lines.append("<body>")
    lines.append('    <div class="header">')
    lines.append("        <h1>Workbuddy使用情况报告</h1>")
    lines.append(f"        <p><strong>报告类型</strong>：{_calendar_period(meta)}</p>")
    lines.append(f"        <p><strong>报告周期</strong>：{meta.get('start_date', '')} 至 {meta.get('end_date', '')}</p>")
    lines.append(f"        <p><strong>生成时间</strong>：{_fmt_generated_at()}</p>")
    lines.append("        <p><strong>数据来源</strong>：WorkBuddy 会话历史、Traces、workbuddy.db、技能使用记录、自动化配置</p>")
    lines.append('        <div class="theme-toggle" role="group" aria-label="主题切换">')
    lines.append('            <button type="button" data-set-theme="light">☀ 浅色</button>')
    lines.append('            <button type="button" data-set-theme="dark">🌙 深色</button>')
    lines.append('            <button type="button" data-set-theme="system">🖥 系统</button>')
    lines.append('        </div>')
    lines.append("    </div>")

    # 一、概览统计
    lines.append('    <div class="section">')
    lines.append('        <h2 class="section-title">一、概览统计</h2>')
    lines.append('        <div class="stats-grid">')
    _gt_h = data.get("traces", [])
    _unresolved_calls_h = sum(1 for t in _gt_h
                               if (t.get("exec_model") or t.get("raw_model") or "") == "default"
                               or ((t.get("input_tokens", 0) or 0) + (t.get("output_tokens", 0) or 0)) == 0)
    _billable_calls_h = len(_gt_h) - _unresolved_calls_h
    stat_cards = [
        (summary.get("active_day_count", 0), "活跃天数"),
        (summary.get("total_sessions", 0), "会话总数"),
        (_billable_calls_h, f"调用次数（{_unresolved_calls_h} 未解析）"),
        (summary.get("skills_used", 0), "使用技能"),
        (summary.get("total_automation_runs", 0), "自动化任务运行"),
        (format_number(summary.get("total_effective_tokens", 0)), "实际消耗 Token"),
        (f"¥{summary.get('total_effective_cost', 0):.2f}", "实际成本"),
    ]
    for val, label in stat_cards:
        lines.append('            <div class="stat-card">')
        lines.append(f"                <div class=\"stat-value\">{val}</div>")
        lines.append(f"                <div class=\"stat-label\">{label}</div>")
        lines.append("            </div>")
    lines.append("        </div>")
    lines.append("    </div>")

    # 二、Token 消耗可视化
    lines.append('    <div class="section">')
    lines.append('        <h2 class="section-title">二、Token 消耗可视化</h2>')
    lines.append("        <table>")
    lines.append("            <tr><th>指标</th><th>数值</th></tr>")
    token_rows = [
        ("原始总 Token（含缓存命中）", format_number(summary.get("total_tokens", 0))),
        ("实际消耗 Token（计费等效）", format_number(summary.get("total_effective_tokens", 0))),
        ("输入 Token", format_number(summary.get("total_input_tokens", 0))),
        ("输出 Token", format_number(summary.get("total_output_tokens", 0))),
        ("缓存命中 Token", format_number(summary.get("total_cached_tokens", 0))),
        ("缓存占比", f"{cache_rate:.1f}%"),
        ("实际成本（计费等效）", f"¥{summary.get('total_effective_cost', 0):.2f}"),
        ("原始总成本（含缓存全价）", f"¥{summary.get('total_cost', 0):.2f}"),
    ]
    for name, val in token_rows:
        lines.append(f"            <tr><td>{name}</td><td>{val}</td></tr>")
    lines.append("        </table>")
    lines.append("    </div>")

    # 每日趋势（横向条形图，与报告内其他横条图表一致）
    if daily_tokens:
        max_tokens = max((v.get("total", 0) for v in daily_tokens.values()), default=1) or 1
        rows = []
        for date in sorted(daily_tokens.keys()):
            stats = daily_tokens[date]
            tok = stats.get("total", 0)
            w = max(int(220 * tok / max_tokens), 1)
            rows.append(
                f'        <div class="bar-row"><span class="bar-label" title="{date}">{date}</span>'
                f'<span class="bar-track"><span class="bar-fill" style="width:{w}px"></span></span>'
                f'<span class="bar-val">{format_number(tok)}</span></div>'
            )
        lines.append('    <div class="section">')
        lines.append('        <h2 class="section-title">每日 Token 消耗趋势</h2>')
        lines.append('        <div class="chart-bars">')
        lines.append('            <p><strong>每日 Token 消耗（原始总计）</strong></p>')
        lines.extend(rows)
        lines.append('        </div>')
        lines.append('    </div>')

    # （新增）三、模型使用与成本对比
    lines.extend(build_model_section_html(data))

    # （新增）四、成本深度分析（每会话 / 异常 / 省钱）
    lines.extend(build_cost_analysis_section_html(data))

    # 五、任务类型统计
    lines.append('    <div class="section">')
    lines.append('        <h2 class="section-title">五、任务类型统计</h2>')
    lines.append('        <p style="font-size:.85em;opacity:.75;margin:.4em 0">统计口径：仅含本期有 trace（token 活动）的会话；历史空会话（无 token 活动）不计入，避免虚高。</p>')
    lines.append("        <table>")
    lines.append("            <tr><th>任务类型</th><th>会话数</th><th>占比</th></tr>")
    if task_dist:
        total_tasks = sum(task_dist.values())
        for task_type, count in sorted(task_dist.items(), key=lambda x: x[1], reverse=True):
            pct = count / total_tasks * 100 if total_tasks else 0
            lines.append(f"            <tr><td>{_esc(task_type)}</td><td>{count}</td><td>{pct:.1f}%</td></tr>")
    else:
        lines.append("            <tr><td colspan='3'>暂无任务数据</td></tr>")
    lines.append("        </table>")
    lines.append('        <p style="font-size:.85em;opacity:.75;margin:.4em 0">ℹ️ 「其他」= 无法自动归类到已知任务类型的会话（含无标题且对话内容缺失的会话）。</p>')
    lines.append("    </div>")

    # 五、任务 Token 消耗统计
    lines.append('    <div class="section">')
    lines.append('        <h2 class="section-title">六、任务 Token 消耗统计</h2>')
    task_token_stats = data.get("task_token_stats", [])
    # 过滤掉 0 token 的行（避免表格出现无意义的 0.0% 行）
    display_stats = [s for s in task_token_stats
                     if s.get("effective_tokens", 0) > 0]
    lines.append("        <p>按任务类型聚合的 Token 消耗，排名按「实际消耗（计费等效）」排序；"
                 "缓存命中 token 按约 1/10 价计费，不虚高。缓存占比高说明该任务大量复用同一段上下文。</p>")
    if display_stats:
        total_eff = sum(s.get("effective_tokens", 0) for s in display_stats)
        total_tok = sum(s["total_tokens"] for s in display_stats)
        lines.append("        <table>")
        lines.append("            <tr><th>任务类型</th><th>会话数</th><th>实际消耗</th><th>原始总Token</th><th>输入</th><th>输出</th><th>缓存占比</th><th>实际成本</th><th>占比</th></tr>")
        for s in display_stats:
            eff = s.get("effective_tokens", 0)
            pct = (eff / total_eff * 100) if total_eff else 0
            c_ratio = (s["cached_tokens"] / s["input_tokens"] * 100) if s.get("input_tokens") else 0
            lines.append(
                f"            <tr><td>{_esc(s['task_type'])}</td><td>{s['session_count']}</td>"
                f"<td>{format_number(eff)}</td><td>{format_number(s['total_tokens'])}</td>"
                f"<td>{format_number(s['input_tokens'])}</td><td>{format_number(s['output_tokens'])}</td>"
                f"<td>{c_ratio:.0f}%</td><td>¥{s.get('effective_cost', 0):.2f}</td><td>{pct:.1f}%</td></tr>"
            )
        lines.append("        </table>")
        lines.append('        <p style="font-size:.85em;opacity:.75;margin:.4em 0">ℹ️ 「其他」含无法关联会话记录（trace 的会话 ID 在本地会话库找不到）或自动分类失败的 token；「未命名会话」= 本地会话库中无标题记录的会话。</p>')
        top = task_token_stats[0]
        top_pct = (top.get("effective_tokens", 0) / total_eff * 100) if total_eff else 0
        lines.append(
            f'        <p>🔥 <strong>实际消耗最高的任务类型</strong>：{top["task_type"]} —— '
            f'{format_number(top.get("effective_tokens", 0))} token（原始 {format_number(top["total_tokens"])}），'
            f'占 {top_pct:.1f}%，实际成本 ¥{top.get("effective_cost", 0):.2f}。</p>'
        )
        # 环形图：各任务类型 实际消耗 token 占比
        donut = build_donut_chart(display_stats, title="实际消耗 Token 占比（按任务类型，计费等效）")
        if donut:
            lines.append('        <p><strong>各任务类型 实际消耗 Token 占比</strong>：</p>')
            lines.append(donut)
        # Top 10 最吃 token 的任务对话框（含自动化任务会话）
        top_tasks = data.get("top_tasks", [])
        if top_tasks:
            lines.append(
                '        <p><strong>实际消耗最高的 10 个任务对话框</strong>（含自动化任务，按会话实际消耗排序）：</p>'
            )
            lines.append("        <table>")
            lines.append("            <tr><th>排名</th><th>任务名称</th><th>任务类型</th><th>实际消耗</th><th>原始总Token</th><th>缓存占比</th><th>实际成本</th></tr>")
            for i, tk in enumerate(top_tasks, 1):
                c_ratio = (tk.get("cached_tokens", 0) / tk["input_tokens"] * 100) if tk.get("input_tokens") else 0
                lines.append(
                    f"            <tr><td>{i}</td><td>{_esc(tk.get('title', '-'))}</td>"
                    f"<td>{_esc(tk.get('task_type', '-'))}</td>"
                    f"<td>{format_number(tk.get('effective_tokens', 0))}</td>"
                    f"<td>{format_number(tk.get('total_tokens', 0))}</td>"
                    f"<td>{c_ratio:.0f}%</td><td>¥{tk.get('effective_cost', 0):.2f}</td></tr>"
                )
            lines.append("        </table>")
    else:
        lines.append(f"        <p>{_short}无任务 Token 消耗数据</p>")
    lines.append("    </div>")

    # 六、技能使用统计
    lines.append('    <div class="section">')
    lines.append('        <h2 class="section-title">七、技能使用统计</h2>')
    lines.append("        <table>")
    lines.append("            <tr><th>技能名称</th><th>使用次数</th><th>最近使用</th></tr>")
    if skills:
        for sid, sdata in sorted(skills.items(), key=lambda x: x[1].get("usage_count_in_range", 0), reverse=True):
            lines.append(f"            <tr><td>{_esc(sid)}</td><td>{sdata.get('usage_count_in_range', 0)}</td><td>{sdata.get('last_used', '-')}</td></tr>")
    else:
        lines.append(f"            <tr><td colspan='3'>{_short}未使用技能</td></tr>")
    lines.append("        </table>")
    lines.append("    </div>")

    # 七、自动化任务运行情况
    lines.append('    <div class="section">')
    lines.append('        <h2 class="section-title">八、自动化任务运行情况</h2>')
    lines.append("        <table>")
    lines.append("            <tr><th>任务名称</th><th>状态</th><th>运行次数</th><th>成功</th><th>失败</th><th>最近一次结果</th><th>最近一次运行日期</th></tr>")
    if automation_runs:
        auto_groups = defaultdict(list)
        for run in automation_runs:
            auto_groups[run.get("automation_id", "unknown")].append(run)
        for auto_id, runs in sorted(auto_groups.items(), key=lambda x: len(x[1]), reverse=True):
            success = sum(1 for r in runs if r.get("result_success"))
            fail = len(runs) - success
            if runs:
                latest_result = "成功" if runs[-1].get("result_success") else "失败"
            else:
                latest_result = "-"
            label = runs[0].get("automation_name") or auto_id
            auto_state = _auto_state_label(runs[0].get("auto_status"))
            dates = [r.get("created_date", "") for r in runs if r.get("created_date")]
            last_run_date = max(dates) if dates else "-"
            lines.append(f"            <tr><td>{_esc(label)}</td><td>{auto_state}</td><td>{len(runs)}</td><td>{success}</td><td>{fail}</td><td>{latest_result}</td><td>{last_run_date}</td></tr>")
    else:
        lines.append(f"            <tr><td colspan='7'>{_short}无自动化任务运行记录</td></tr>")
    lines.append("        </table>")
    lines.append("    </div>")

    # 八、产出物清单
    lines.append('    <div class="section">')
    lines.append('        <h2 class="section-title">九、产出物清单</h2>')
    lines.append("        <table>")
    lines.append("            <tr><th>文件</th><th>类型</th><th>日期</th><th>大小</th></tr>")
    if outputs:
        for out in outputs[:20]:
            ext = out.get("extension", "").lstrip(".")
            size = format_file_size(out.get("size_bytes", 0))
            fname = out.get("file_name", "-")
            lines.append(f"            <tr><td>{_esc(fname)}</td><td>{_esc(ext or '-')}</td><td>{out.get('date', '-')}</td><td>{size}</td></tr>")
    else:
        lines.append(f"            <tr><td colspan='4'>{_short}无产出文件</td></tr>")
    lines.append("        </table>")
    lines.append("    </div>")

    # 十、核心洞察与建议（数据驱动：钱优先 + 判断 + 行动）
    lines.append('    <div class="section">')
    lines.append('        <h2 class="section-title">十、核心洞察与建议</h2>')
    eff_cost = summary.get("total_effective_cost", 0)
    raw_cost = summary.get("total_cost", 0)
    eff_tokens = summary.get("total_effective_tokens", 0)
    cache_saving = max(raw_cost - eff_cost, 0)
    session_credits = data.get("session_credits", [])

    # 💰 花费速览
    lines.append("        <h3>💰 花费速览</h3>")
    lines.append(f"        <p>本期实际花费 <strong>¥{eff_cost:.2f}</strong>（原始口径 ¥{raw_cost:.2f}），"
                 f"缓存复用为你节省约 <strong>¥{cache_saving:.2f}</strong>。</p>")
    if session_credits:
        latest = max(session_credits, key=lambda x: x.get("updated_at", 0))
        used = latest.get("used", 0); size = latest.get("size", 0)
        if size > 0:
            lines.append(f"        <p>当前会话额度已用 <strong>{used / size * 100:.0f}%</strong>"
                         f"（{format_number(used)} / {format_number(size)}）。</p>")

    # 🔍 最大成本来源
    if daily_tokens and eff_tokens:
        peak_day, peak_stats = max(daily_tokens.items(), key=lambda x: x[1].get("effective", 0))
        peak_cost = peak_stats.get("effective_cost", 0)
        peak_tok = peak_stats.get("effective", 0)
        peak_share = peak_tok / eff_tokens * 100
        verdict = "占本期实际消耗比重偏高，建议复盘当日是否存在可精简的批量/重复任务" \
            if peak_share >= 25 else "占本期实际消耗比重正常，属单日波动"
        lines.append("        <h3>🔍 最大成本来源</h3>")
        lines.append(f"        <p><strong>高峰日 {peak_day}</strong>：实际花费 ¥{peak_cost:.2f}"
                     f"（{format_number(peak_tok)} token），占本期实际消耗 <strong>{peak_share:.1f}%</strong>——{verdict}。</p>")

    # 📊 任务类型洞察
    if task_dist:
        total_count = sum(task_dist.values())
        top_task, top_n = max(task_dist.items(), key=lambda x: x[1])
        top_pct = top_n / total_count * 100
        if top_pct >= 50:
            shape = "高度集中于单一类型，建议确认是否为预期工作流"
        elif top_pct >= 25:
            shape = "相对集中，为本期主力任务"
        else:
            shape = "分布较均衡，未见单一类型主导"
        lines.append("        <h3>📊 任务类型洞察</h3>")
        lines.append(f"        <p><strong>主要任务类型</strong>：{_esc(top_task)}（{top_n} 次，{top_pct:.1f}%）——{shape}。</p>")

    # ✅ 省钱成就
    if cache_rate > 0:
        lines.append("        <h3>✅ 省钱成就</h3>")
        lines.append(f"        <p>缓存命中率达 <strong>{cache_rate:.1f}%</strong>，按原始全价计费你本应付约 ¥{raw_cost:.2f}，"
                     f"实际仅 ¥{eff_cost:.2f}——<strong>缓存为你省下 ¥{cache_saving:.2f}</strong>，"
                     f"是实际成本远低于原始账单的主因。</p>")
    lines.append("    </div>")

    # 十、下期展望（基于实际数据动态生成）
    lines.append('    <div class="section">')
    lines.append(f'        <h2 class="section-title">十一、{_next}展望</h2>')
    lines.append(f"        <p>基于{_short}实际数据，先预测下期用量，再按优先级给出行动建议：</p>")
    lines.append("        <ul>")
    for item in build_next_week_outlook(summary, daily_tokens, automation_runs,
                                        data.get("session_credits", []), period_key=_key):
        raw = item[2:].strip() if item.startswith("- ") else item
        escaped = _esc(raw)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        lines.append(f"            <li>{text}</li>")
    lines.append("        </ul>")
    lines.append("    </div>")

    lines.append('    <footer>')
    lines.append('        <p style="text-align: center; color: var(--muted); margin-top: 40px;">*本报告基于 WorkBuddy 数据自动生成。</p>')
    lines.append("    </footer>")
    lines.append('    <script>')
    lines.append('        (function(){')
    lines.append('            var root = document.documentElement;')
    lines.append('            var btns = document.querySelectorAll(".theme-toggle button");')
    lines.append('            function applyTheme(t){')
    lines.append('                if(t==="system"){ root.removeAttribute("data-theme"); }')
    lines.append('                else { root.setAttribute("data-theme", t); }')
    lines.append('                try { localStorage.setItem("aurs-theme", t); } catch(e){}')
    lines.append('                btns.forEach(function(b){ b.classList.toggle("active", b.getAttribute("data-set-theme")===t); });')
    lines.append('            }')
    lines.append('            btns.forEach(function(b){ b.addEventListener("click", function(){ applyTheme(b.getAttribute("data-set-theme")); }); });')
    lines.append('            var saved = "system";')
    lines.append('            try { saved = localStorage.getItem("aurs-theme") || "system"; } catch(e){}')
    lines.append('            applyTheme(saved);')
    lines.append('        })();')
    lines.append('    </script>')
    lines.append("</body>")
    lines.append("</html>")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# JSON 格式
# ─────────────────────────────────────────────────────────────
def generate_json_report(data):
    meta = data.get("meta", {})
    summary = data.get("summary", {})
    traces = data.get("traces", [])
    sessions = data.get("sessions", [])
    automation_runs = data.get("automation_runs", [])
    session_credits = data.get("session_credits", [])
    skill_usage = data.get("skill_usage", {})
    outputs = data.get("outputs", [])
    daily_tokens = data.get("daily_tokens", {})

    task_dist = summary.get("task_type_distribution", {})
    total_input = summary.get("total_input_tokens", 0)
    total_cached = summary.get("total_cached_tokens", 0)
    cache_rate = (total_cached / total_input * 100) if total_input else 0

    model_stats = data.get("model_stats", [])
    exec_stats = data.get("model_exec_stats", [])
    # 最常使用模型：基于「实际执行模型」维度，且排除路由别名（auto），取最具体的真实模型
    _calls_src = [m for m in exec_stats if not m.get("is_router")] or \
                 [m for m in model_stats if not m.get("is_router")] or \
                 (exec_stats or model_stats)
    top_model_by_calls = max(_calls_src, key=lambda x: x["calls"])["model"] if _calls_src else None
    configured = [m for m in model_stats if m.get("configured")]
    priced_models = [m for m in configured if m.get("effective_cost", 0) > 0]
    concrete_priced = [m for m in priced_models if not m.get("is_router")]
    _cost_src = concrete_priced or priced_models
    top_model_by_cost = max(_cost_src, key=lambda x: x.get("effective_cost", 0))["model"] if _cost_src else None

    report = {
        "meta": {
            "report_title": "Workbuddy使用情况报告",
            "start_date": meta.get("start_date", ""),
            "end_date": meta.get("end_date", ""),
            "generated_at": _fmt_generated_at(),
            "data_sources": ["WorkBuddy 会话历史", "Traces", "workbuddy.db", "技能使用记录", "自动化配置"],
        },
        "summary": {
            "overview": {
                "active_days": summary.get("active_days", []),
                "active_day_count": summary.get("active_day_count", 0),
                "total_sessions": summary.get("total_sessions", 0),
                "total_traces": summary.get("total_traces", 0),
                "total_automation_runs": summary.get("total_automation_runs", 0),
                "successful_automation_runs": summary.get("successful_automation_runs", 0),
                "total_outputs": summary.get("total_outputs", 0),
                "skills_used": summary.get("skills_used", 0),
                "total_cost": summary.get("total_cost", 0),
                "total_input_cost": summary.get("total_input_cost", 0),
                "total_output_cost": summary.get("total_output_cost", 0),
            },
            "token_usage": {
                "total_tokens": summary.get("total_tokens", 0),
                "total_effective_tokens": summary.get("total_effective_tokens", 0),
                "total_input_tokens": summary.get("total_input_tokens", 0),
                "total_output_tokens": summary.get("total_output_tokens", 0),
                "total_cached_tokens": summary.get("total_cached_tokens", 0),
                "cache_rate": round(cache_rate, 1),
                "total_cost": summary.get("total_cost", 0),
                "total_effective_cost": summary.get("total_effective_cost", 0),
            },
            "task_types": task_dist,
            "daily_tokens": daily_tokens,
            "skill_usage": skill_usage.get("skills", {}),
            "automation_summary": {
                "total_runs": len(automation_runs),
                "successful_runs": sum(1 for r in automation_runs if r.get("result_success")),
                "failed_runs": len(automation_runs) - sum(1 for r in automation_runs if r.get("result_success")),
            },
            "model_breakdown": model_stats,
            "model_exec_breakdown": exec_stats,
        },
        "details": {
            "sessions": sessions,
            "automation_runs": automation_runs,
            "outputs": outputs,
            "session_credits": session_credits,
            "full_traces": traces,
        },
        "insights": {
            "peak_usage": {
                "day": max(daily_tokens.items(), key=lambda x: x[1].get("total", 0))[0] if daily_tokens else None,
                "tokens": max((v.get("total", 0) for v in daily_tokens.values()), default=0) if daily_tokens else 0,
            },
            "dominant_task_type": max(task_dist.items(), key=lambda x: x[1]) if task_dist else None,
            "cache_efficiency": "优秀" if cache_rate > 80 else "良好" if cache_rate > 60 else "一般",
            "top_model_by_calls": top_model_by_calls,
            "top_model_by_cost": top_model_by_cost,
            "cost_analysis": {
                "top_sessions": [
                    {"title": r["title"], "task_type": r["task_type"],
                     "effective_cost": r["effective_cost"], "effective_tokens": r["effective_tokens"],
                     "calls": r["calls"], "models": r["models"]}
                    for r in (data.get("session_stats") or {}).get("rows", [])[:10]
                ],
                "distribution": (data.get("session_stats") or {}).get("buckets", []),
                "anomalies": data.get("cost_anomalies", {}),
                "savings": data.get("savings_insights", {}),
            },
        },
    }

    return json.dumps(report, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Agent 使用情况报告生成器")
    parser.add_argument("data_file", nargs="?", help="数据 JSON 文件路径（未提供则实时采集）")
    parser.add_argument("--period", choices=["day", "week", "month", "year"], default="week",
                        help="时间窗口预设（实时采集时使用，默认 week）："
                             "day=今天 / week=当前日历周(YYYY年第WW周，周一至周日，不论生成日在周中或周末) / "
                             "month=当前自然月(MM月，1日至月底) / year=当前自然年(YYYY年全年)")
    parser.add_argument("--days", type=int, help="自定义滚动天数，覆盖 --period（实时采集时使用）")
    parser.add_argument("--start", type=str, help="绝对起始日期 YYYY-MM-DD（实时采集时使用）")
    parser.add_argument("--end", type=str, help="绝对结束日期 YYYY-MM-DD（实时采集时使用）")
    parser.add_argument("--output", "-o", type=str, help="输出文件路径")
    parser.add_argument("--format", type=str, default="markdown",
                        choices=["markdown", "html", "json"], help="输出格式")
    args = parser.parse_args()

    if args.data_file:
        data = json.loads(Path(args.data_file).read_text(encoding="utf-8"))
    else:
        import collect_usage_data as collector
        start_date, end_date, period_key, period_label = collector.resolve_date_range(
            period=args.period, days=args.days, start=args.start, end=args.end)
        db_data = collector.collect_db_data(start_date, end_date)
        sid_to_rawmodel = {s["id"]: (s.get("model") or "default") for s in db_data["sessions"]}
        traces = collector.collect_traces(start_date, end_date, sid_to_rawmodel)
        skill_usage = collector.collect_skill_usage(start_date, end_date)
        outputs, memory_logs = collector.collect_session_outputs(start_date, end_date)
        collector.collect_task_types(db_data["sessions"])

        total_tokens = sum(t["total_tokens"] for t in traces)
        total_input = sum(t["input_tokens"] for t in traces)
        total_output = sum(t["output_tokens"] for t in traces)
        total_cached = sum(t["cached_tokens"] for t in traces)
        total_effective = sum(t.get("effective_tokens", 0) for t in traces)
        total_effective_cost = round(sum(t.get("effective_cost", 0.0) for t in traces), 2)
        cache_rate = (total_cached / total_input * 100) if total_input else 0

        daily_tokens = {}
        for t in traces:
            d = t["date"]
            if d not in daily_tokens:
                daily_tokens[d] = {"total": 0, "input": 0, "output": 0, "cached": 0, "calls": 0,
                                   "total_cost": 0.0, "effective": 0, "effective_cost": 0}
            daily_tokens[d]["total"] += t["total_tokens"]
            daily_tokens[d]["input"] += t["input_tokens"]
            daily_tokens[d]["output"] += t["output_tokens"]
            daily_tokens[d]["cached"] += t["cached_tokens"]
            daily_tokens[d]["calls"] += t["call_count"]
            daily_tokens[d]["total_cost"] += t.get("cost", 0.0)
            daily_tokens[d]["effective"] += t.get("effective_tokens", 0)
            daily_tokens[d]["effective_cost"] += t.get("effective_cost", 0.0)

        # 活跃天数 = 窗口内产生 token 活动的日期（仅 trace 日期，避免会话创建日虚高）
        active_days = set(t["date"] for t in traces)

        # §5 任务类型分布（D5：仅统计本期有 trace 的会话，避免历史空会话虚高）
        traced_session_ids = {t.get("session_id") for t in traces if t.get("session_id")}
        task_dist = {}
        for s in db_data["sessions"]:
            if s.get("id") not in traced_session_ids:
                continue
            tt = s.get("task_type", "其他")
            task_dist[tt] = task_dist.get(tt, 0) + 1

        _session_stats = collector.aggregate_by_session(traces, db_data["sessions"])
        data = {
            "meta": {"start_date": start_date, "end_date": end_date, "period": period_key,
                     "period_label": period_label, "days": args.days if args.days is not None else collector.PERIOD_DAYS.get(period_key, 7)},
            "traces": traces,
            "sessions": db_data["sessions"],
            "automation_runs": db_data["automation_runs"],
            "session_credits": db_data["session_credits"],
            "skill_usage": skill_usage,
            "outputs": outputs,
            "daily_tokens": daily_tokens,
            "summary": {
                "total_traces": len(traces),
                "total_sessions": len(db_data["sessions"]),
                "total_tokens": total_tokens,
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "total_cached_tokens": total_cached,
                "total_effective_tokens": total_effective,
                "cache_rate": round(cache_rate, 1),
                "active_days": sorted(active_days),
                "active_day_count": len(active_days),
                "total_automation_runs": len(db_data["automation_runs"]),
                "successful_automation_runs": sum(1 for r in db_data["automation_runs"] if r["result_success"]),
                "total_outputs": len(outputs),
                "skills_used": len(skill_usage.get("skills", {})),
                "task_type_distribution": task_dist,
                "total_cost": sum(t.get("total_cost", 0.0) for t in traces),
                "total_input_cost": sum(t.get("input_cost", 0.0) for t in traces),
                "total_output_cost": sum(t.get("output_cost", 0.0) for t in traces),
                "total_effective_cost": round(total_effective_cost, 2),
            },
            "task_token_stats": collector.aggregate_task_token_stats(traces, db_data["sessions"]),
            "top_tasks": collector.aggregate_top_tasks(traces, db_data["sessions"], top_n=10),
            # P0-1：§3.1 改为「实际计费模型」维度（exec_model，与头条/账单一致），
            # §3.2 改为「入口/配置」维度（model_key，使用分布）。对调两节数据源以消除 §3.1 与头条矛盾。
            "model_stats": collector.aggregate_by_exec_model(traces),
            "model_exec_stats": collector.aggregate_by_model(traces),
            "session_stats": _session_stats,
            "cost_anomalies": collector.detect_cost_anomalies(daily_tokens, _session_stats),
            "savings_insights": collector.build_savings_insights(collector.aggregate_by_exec_model(traces)),
        }

    if args.format == "markdown":
        report = generate_markdown_report(data)
    elif args.format == "html":
        report = generate_html_report(data)
    else:
        report = generate_json_report(data)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        _pl = _PERIOD_LABEL.get((data.get("meta", {}).get("period", "week")), "周报")
        print(f"[OK] {_pl}已保存到 {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
