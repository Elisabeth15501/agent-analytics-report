#!/usr/bin/env python3
"""collect_usage_data.py — WorkBuddy Agent 使用数据采集器

从多个数据源采集 Agent 使用情况：
  1. traces/       — token 消耗、模型信息、会话时长
  2. workbuddy.db  — 会话元数据、自动化运行记录、信用消耗
  3. usage-log.json — 技能使用记录、活跃天数
  4. WorkBuddy/    — 会话产出文件、记忆日志
  5. automation API — 自动化任务配置

用法:
  python collect_usage_data.py [--days N] [--output data.json]
  默认采集最近 7 天数据，输出到 stdout

注意：本文件自 v1.3.0 工程债治理（Phase 1）起仅为 facade，实际实现分布在
ca_core.py / ca_sources.py / ca_sessions.py / ca_aggregate.py，由本文件统一 re-export。
"""
import argparse
import calendar
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ca_core import *
from ca_sources import *
from ca_sessions import *
from ca_aggregate import *

# 绑定子模块名，便于测试按「真实定义模块」定位 monkeypatch（Phase 1 拆分后需要）
import ca_core
import ca_sources
import ca_sessions
import ca_aggregate

"""
collect_usage_data.py — WorkBuddy Agent 使用数据采集器

从多个数据源采集 Agent 使用情况：
  1. traces/       — token 消耗、模型信息、会话时长
  2. workbuddy.db  — 会话元数据、自动化运行记录、信用消耗
  3. usage-log.json — 技能使用记录、活跃天数
  4. WorkBuddy/    — 会话产出文件、记忆日志
  5. automation API — 自动化任务配置

用法:
  python collect_usage_data.py [--days N] [--output data.json]
  默认采集最近 7 天数据，输出到 stdout

功能增强：
  - 添加成本货币化计算（基于 token 消耗）
  - 添加模型使用状况统计
  - 支持实时数据采集（通过 --realtime 参数）
"""

def main():
    parser = argparse.ArgumentParser(description="WorkBuddy Agent 使用数据采集器")
    parser.add_argument("--period", choices=["day", "week", "month", "year"], default="week",
                        help="时间窗口预设：day=今天 / week=最近7天 / month=最近30天 / year=最近365天（默认 week）")
    parser.add_argument("--days", type=int, help="自定义滚动天数，覆盖 --period（如 --days 14）")
    parser.add_argument("--start", type=str, help="绝对起始日期 YYYY-MM-DD（与 --end 搭配）")
    parser.add_argument("--end", type=str, help="绝对结束日期 YYYY-MM-DD（与 --start 搭配）")
    parser.add_argument("--output", "-o", type=str, help="输出 JSON 文件路径（默认 stdout）")
    parser.add_argument("--realtime", action="store_true", help="实时采集模式（立即采集最新数据）")
    parser.add_argument("--lookup-pricing", choices=["offline", "online"], default="offline",
                        help="缺失单价模型的处理：offline=仅提示如何补写（默认，纯本地）；"
                             "online=尝试联网检索（需 --pricing-api 指向你自己的定价镜像，"
                             "否则仅生成可点击的搜索链接）。联网结果一律标注「网络估算价，仅供参考」")
    parser.add_argument("--pricing-api", type=str, default=None,
                        help="online 模式可选：指向一个返回 {\"models\": {模型名: {input,output}}} 的 JSON 端点，"
                             "用于补全缺失模型单价（取自你自己的定价镜像，避免抓第三方页面）")
    args = parser.parse_args()

    if args.realtime:
        # 实时模式：立即采集最新数据（今天）
        start_date, end_date, period_key, period_label = resolve_date_range(period="day")
        print(f"[INFO] 实时模式：采集范围 {start_date} ~ {end_date}（当日）", file=sys.stderr)
    else:
        # 记录显式指定的参数，用于提示用户实际生效的参数
        explicit_params = []
        if args.start and args.end:
            explicit_params.append("--start/--end")
        if args.days is not None:
            explicit_params.append(f"--days {args.days}")
        if args.period != "week" or explicit_params:  # 只有显式指定了非默认值，或其他参数覆盖时才提示
            if args.period != "week" and not (args.days or args.start):
                explicit_params.append(f"--period {args.period}")
        
        try:
            start_date, end_date, period_key, period_label = resolve_date_range(
                period=args.period, days=args.days, start=args.start, end=args.end)
        except ValueError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(2)

        # 详细提示：显式指定参数 vs 默认值
        if explicit_params:
            print(f"[INFO] 采集范围[{period_label}]：{start_date} ~ {end_date}（生效参数：{', '.join(explicit_params)}）", file=sys.stderr)
        else:
            print(f"[INFO] 采集范围[{period_label}]：{start_date} ~ {end_date}（默认一周，可用 --period/--days/--start/--end 自定义）", file=sys.stderr)

    # 采集各数据源：先取会话（sessions.model 含带通道前缀的原始模型标识符，
    # 是「接口通道」的真相源），据其构建 session_id→raw_model 映射，再采集 trace 并关联通道。
    db_data = collect_db_data(start_date, end_date)
    sid_to_rawmodel = {s["id"]: (s.get("model") or "default") for s in db_data["sessions"]}
    traces = collect_traces(start_date, end_date, sid_to_rawmodel)

    # 补全会话：窗口内有 trace 但创建于窗口外的会话，确保 token 统计能关联到任务类型
    try:
        trace_sids = {t.get("session_id") for t in traces if t.get("session_id")}
        existing_ids = {s["id"] for s in db_data["sessions"]}
        missing = trace_sids - existing_ids
        if missing:
            cdb = sqlite3.connect(str(DB_PATH))
            cdb.row_factory = sqlite3.Row
            ph = ",".join("?" * len(missing))
            for r in cdb.execute(
                f"SELECT * FROM sessions WHERE id IN ({ph}) AND deleted_at IS NULL", list(missing)
            ).fetchall():
                db_data["sessions"].append({
                    "id": r["id"], "cwd": r["cwd"], "title": r["title"] or "",
                    "custom_title": r["custom_title"] or "", "status": r["status"],
                    "created_at": r["created_at"], "created_date": ts_to_date(r["created_at"]),
                    "updated_at": r["updated_at"], "mode": r["mode"], "model": r["model"],
                    "is_background_automation": bool(r["is_background_automation"]),
                })
            cdb.close()
    except (sqlite3.Error, OSError) as e:
        print(f"[WARN] supplementary sessions query: {e}", file=sys.stderr)

    # 修复：补全会话后，把跨窗口长会话并入 sid_to_rawmodel 并重采 trace——
    # 否则这些会话的 sid 在映射里缺失，raw_model 会退化成 trace 执行模型名，
    # 导致通道误判（如 hy3 网关会话里的 deepseek-v4-pro 被错归、或反之）。
    for s in db_data["sessions"]:
        sid_to_rawmodel.setdefault(s["id"], s.get("model") or "default")
    traces = collect_traces(start_date, end_date, sid_to_rawmodel)

    skill_usage = collect_skill_usage(start_date, end_date)
    outputs, memory_logs = collect_session_outputs(start_date, end_date)

    # 任务类型分类（基于对话内容）
    task_types = collect_task_types(db_data["sessions"])

    # 汇总
    if args.days is not None:
        meta_days = args.days
    elif args.start and args.end:
        try:
            meta_days = (datetime.strptime(args.end, "%Y-%m-%d")
                         - datetime.strptime(args.start, "%Y-%m-%d")).days + 1
        except (ValueError, TypeError):
            meta_days = PERIOD_DAYS.get(period_key, 7)
    else:
        meta_days = PERIOD_DAYS.get(period_key, 7)

    result = {
        "meta": {
            "start_date": start_date,
            "end_date": end_date,
            "period": period_key,
            "period_label": period_label,
            "days": meta_days,
            "generated_at": datetime.now(TZ).isoformat(),
            "is_realtime": args.realtime,
        },
        "traces": traces,
        "sessions": db_data["sessions"],
        "automation_runs": db_data["automation_runs"],
        "session_credits": db_data["session_credits"],
        "skill_usage": skill_usage,
        "outputs": outputs,
        "memory_logs": {k: [{"file": v["file"], "session_dir": v["session_dir"]} for v in vals]
                        for k, vals in memory_logs.items()},
        "task_types": task_types,
    }

    # 统计摘要
    total_tokens = sum(t["total_tokens"] for t in traces)
    total_input = sum(t["input_tokens"] for t in traces)
    total_output = sum(t["output_tokens"] for t in traces)
    total_cached = sum(t["cached_tokens"] for t in traces)
    total_effective = sum(t.get("effective_tokens", 0) for t in traces)
    total_credits = sum(c["used"] for c in db_data["session_credits"])
    # 活跃天数 = 窗口内产生 token 活动的日期（仅统计 trace 日期）。
    # 不计入会话创建日：窗口外创建、但窗口内有 trace 的会话已被纳入 token 聚合，
    # 若把其创建日也算进 active_days 会虚高"活跃天数"（如 --days 7 却显示 12 天）。
    active_days = set(t["date"] for t in traces)

    # 计算总成本
    total_cost = sum(t["total_cost"] for t in traces)
    total_input_cost = sum(t["input_cost"] for t in traces)
    total_output_cost = sum(t["output_cost"] for t in traces)
    total_effective_cost = round(sum(t.get("effective_cost", 0.0) for t in traces), 2)
    # 缓存占比：缓存命中 token 占输入 token 的比例（越高说明越多重复上下文被廉价复用）
    cache_rate = (total_cached / total_input * 100) if total_input else 0

    result["summary"] = {
        "total_traces": len(traces),
        "total_sessions": len(db_data["sessions"]),
        "total_tokens": total_tokens,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cached_tokens": total_cached,
        "total_effective_tokens": total_effective,
        "cache_rate": round(cache_rate, 1),
        "total_credits_used": total_credits,
        "active_days": sorted(active_days),
        "active_day_count": len(active_days),
        "total_automation_runs": len(db_data["automation_runs"]),
        "successful_automation_runs": sum(1 for r in db_data["automation_runs"] if r["result_success"]),
        "total_outputs": len(outputs),
        "skills_used": len(skill_usage["skills"]),
        "task_type_distribution": {},
        "total_cost": total_cost,
        "total_input_cost": total_input_cost,
        "total_output_cost": total_output_cost,
        "total_effective_cost": round(total_effective_cost, 2),
    }

    # 任务类型分布（D5：仅统计本期有 trace 的会话，避免历史空会话虚高 §5）
    _traced_ids = {t.get("session_id") for t in traces if t.get("session_id")}
    for s in db_data["sessions"]:
        if s.get("id") not in _traced_ids:
            continue
        tt = s.get("task_type", "其他")
        result["summary"]["task_type_distribution"][tt] = result["summary"]["task_type_distribution"].get(tt, 0) + 1

    # 任务 token 消耗统计（按任务类型聚合 traces）
    result["task_token_stats"] = aggregate_task_token_stats(traces, db_data["sessions"])

    # Top N 最吃 token 的任务对话框（按会话聚合）
    result["top_tasks"] = aggregate_top_tasks(traces, db_data["sessions"], top_n=10)

    # 按实际执行模型聚合（计费维度 / 账单口径：hy3 路由到的付费模型在此体现，与概览总额一致）→ §3.1
    result["model_stats"] = aggregate_by_exec_model(traces)
    # 按入口 / 配置模型聚合（使用分布维度：auto / hy3 / custom-local 等入口）→ §3.2
    result["model_exec_stats"] = aggregate_by_model(traces)
    # 按档位（路由三档）聚合（v1.3.0 分析维度）→ §3.4
    result["tier_stats"] = aggregate_by_tier(traces)

    # 收集本期「未配置单价」的模型名（排除路由别名 auto，其本就无单一单价），供报告给出可补写片段
    unconfigured = set()
    for stats in (result["model_stats"], result["model_exec_stats"]):
        for m in stats:
            # 已下架官方模型不算「缺失单价」——它们本就无需用户补写，仅标注即可
            if not m.get("configured") and m.get("model") not in ROUTER_ALIASES and not m.get("is_delisted"):
                unconfigured.add(m["model"])
    result["meta"]["unconfigured_models"] = sorted(unconfigured)
    # 限时免费截止日（来自 pricing.json 的 timed_free），供报告渲染「限时免费至 X」标签，
    # 避免在渲染器里硬编码日期——用户改了 pricing.json 后标签会自动跟随。
    result["meta"]["timed_free"] = dict(TIMED_FREE)
    # 是否加载了本地定价覆盖（pricing.local.json），供报告透明提示。
    result["meta"]["pricing_local_loaded"] = bool(_PRICING_LOCAL_LOADED)
    # 档位维度元信息（v1.3.0）：档位估算标记、官方倍率缓存是否生效、最终档位单价表。
    # 供报告 §3.4 透明标注「估算值」并提示可配置。
    result["meta"]["mode_rates"] = dict(MODE_RATES_META.get("rates", {}))
    result["meta"]["mode_cost_estimated"] = bool(MODE_RATES_META.get("auto_estimate", False))
    result["meta"]["mode_config_cache_loaded"] = bool(MODE_RATES_META.get("config_cache_loaded", False))
    result["meta"]["mode_config_cache_path"] = MODE_RATES_META.get("config_cache_path")
    result["meta"]["mode_config_cache_mtime"] = MODE_RATES_META.get("config_cache_mtime")

    # 可选联网检索（--lookup-pricing online）：仅生成搜索链接，或拉取用户自有定价镜像。
    # ⚠️ 联网拿到的单价一律视为「网络估算价，仅供参考」，绝不用于权威成本总额。
    pricing_lookup = {
        "mode": args.lookup_pricing,
        "api": args.pricing_api,
        "network_estimates": {},
        "search_links": {},
    }
    if args.lookup_pricing == "online" and unconfigured:
        for model in sorted(unconfigured):
            pricing_lookup["search_links"][model] = (
                "https://duckduckgo.com/html/?q=" + urllib.parse.quote(f"{model} API pricing")
            )
            if args.pricing_api:
                try:
                    with urllib.request.urlopen(args.pricing_api, timeout=10) as resp:
                        remote = json.loads(resp.read().decode("utf-8"))
                    rm = (remote.get("models", {}).get(normalize_model(model))
                          or remote.get("models", {}).get(model))
                    if rm and "input" in rm and "output" in rm:
                        pricing_lookup["network_estimates"][model] = {
                            "input": float(rm["input"]), "output": float(rm["output"]),
                        }
                except (requests.RequestException, ValueError, KeyError) as e:
                    print(f"[WARN] 联网检索 {model} 失败：{e}", file=sys.stderr)
    result["meta"]["pricing_lookup"] = pricing_lookup

    # P1 成本深度分析：每会话成本 / 省钱杠杆（cost_anomalies 依赖 daily_tokens，在下方构建后计算）
    result["session_stats"] = aggregate_by_session(traces, db_data["sessions"])
    # 省钱洞察基于计费维度（exec_model）找真实付费贵模型，给出更便宜替代与预计月省
    result["savings_insights"] = build_savings_insights(result["model_stats"])

    # 每日 token 统计
    daily_tokens = {}
    for t in traces:
        d = t["date"]
        if d not in daily_tokens:
            daily_tokens[d] = {
                "total": 0, "input": 0, "output": 0, "cached": 0, "calls": 0,
                "total_cost": 0, "input_cost": 0, "output_cost": 0,
                "effective": 0, "effective_cost": 0,
            }
        daily_tokens[d]["total"] += t["total_tokens"]
        daily_tokens[d]["input"] += t["input_tokens"]
        daily_tokens[d]["output"] += t["output_tokens"]
        daily_tokens[d]["cached"] += t["cached_tokens"]
        daily_tokens[d]["calls"] += t["call_count"]
        daily_tokens[d]["total_cost"] += t["total_cost"]
        daily_tokens[d]["input_cost"] += t["input_cost"]
        daily_tokens[d]["output_cost"] += t["output_cost"]
        daily_tokens[d]["effective"] += t.get("effective_tokens", 0)
        daily_tokens[d]["effective_cost"] += t.get("effective_cost", 0.0)
    result["daily_tokens"] = daily_tokens

    # P1 成本异常检测（依赖 daily_tokens 与 session_stats，故置于每日统计之后）
    result["cost_anomalies"] = detect_cost_anomalies(result["daily_tokens"], result["session_stats"])

    output_json = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"[OK] 数据已保存到 {args.output}", file=sys.stderr)
    else:
        print(output_json)

if __name__ == "__main__":
    main()
