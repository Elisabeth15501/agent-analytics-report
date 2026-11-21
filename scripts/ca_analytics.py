"""ca_analytics.py — 统计、组装与聚合（main() 拆分 · 批次 2 建立，批次 3 扩充）。

批次 2 收纳（自 main 局部函数原样抽出，语义不变）：
  - estimate_request_count : 请求数反推（原 _parse_started_at + _cluster_requests）

批次 3 收纳（自 main 内联代码块抽出，语义不变；阶段 I 仅建新、main 不调用）：
  - build_result_skeleton       : ⑥⑦⑧ result 骨架 + tokens_source 标记
  - compute_totals              : ⑨ token/成本/积分/活跃天数汇总
  - compute_transparency_counts : ⑪ 未知模型/档位/失败调用 + 缓存率
  - build_summary               : ⑫ result["summary"] 24 字段
  - build_task_distribution     : ⑬ task_type_distribution
  - attach_aggregates           : ⑭ 5 类聚合
  - enrich_analysis             : ⑳ session_stats / savings(L2保护) / daily / 异常

阶段 II（批次 6）main 切流后这些函数才会被调用；golden diff 在那时做一次性验证。
"""

from datetime import datetime, timedelta, timezone

from ca_core import (
    TZ,
    PERIOD_DAYS,
    token_active_session_ids,
    ts_to_dt,
)
from ca_sessions import aggregate_task_token_stats, aggregate_top_tasks
from ca_aggregate import (
    aggregate_by_exec_model,
    aggregate_by_model,
    aggregate_by_tier,
    aggregate_by_session,
    build_savings_insights,
    detect_cost_anomalies,
)
from ca_types import Totals, TransparencyCounts


# ── 批次 2：请求数反推 ───────────────────────────────────────────

def _parse_started_at(v):
    """started_at 在多层数据源里格式不统一：WorkBuddy trace 是 Unix 毫秒时间戳，
    claude-code / codex 等适配器是 ISO 8601 字符串。统一解析为带时区 datetime。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return ts_to_dt(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return ts_to_dt(int(s))  # 数值毫秒时间戳
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(
                timezone(timedelta(hours=8))
            )
        except ValueError:
            return None
    return None


def _cluster_requests(trs, gap_min=15):
    by_sid = {}
    solo = 0
    for t in trs:
        sid = (t.get("session_id") or "").strip()
        dt = _parse_started_at(t.get("started_at"))
        if not sid or dt is None:
            solo += 1
            continue
        by_sid.setdefault(sid, []).append(dt)
    req = solo
    for dts in by_sid.values():
        dts.sort()
        prev = None
        for d in dts:
            if prev is None or (d - prev).total_seconds() > gap_min * 60:
                req += 1
            prev = d
    return req


def estimate_request_count(traces, gap_min=15):
    """按 session_id + 时间窗聚类反推估算请求数（口径与官方「请求数」不同，仅对照用）。"""
    return _cluster_requests(traces, gap_min)


# ── 批次 3：统计组装 ─────────────────────────────────────────────

def build_result_skeleton(args, window, bundle):
    """⑥⑦⑧：组装 result 骨架（meta + 原始采集数据），含 qwenwork token 口径标记。"""
    result = {
        "meta": {
            "start_date": window.start,
            "end_date": window.end,
            "period": window.key,
            "period_label": window.label,
            "days": window.days,
            "generated_at": datetime.now(TZ).isoformat(),
            "is_realtime": args.realtime,
            "source": args.source,
            # 成本口径："estimate"=静态价表估算（L2，默认）；
            # 传入 --import-official 后置为 "official"（L1 真值，取官方导出「积分」字段）。
            "cost_source": "estimate",
            # 数据源能力声明（由适配器显式给出）：False 时报告端压制
            # 「缺失单价模型 + pricing.local.json 补价 stub」整块
            "cost_supported": bundle.cost_supported,
            # 适配器自行声明的补充 meta（single_channel / background_runs / 采集来源）
            **bundle.source_meta,
        },
        "traces": bundle.traces,
        "sessions": bundle.sessions,
        "automation_runs": bundle.automation_runs,
        "session_credits": bundle.session_credits,
        "skill_usage": bundle.skill_usage,
        "outputs": bundle.outputs,
        "memory_logs": {
            k: [{"file": v["file"], "session_dir": v["session_dir"]} for v in vals]
            for k, vals in bundle.memory_logs.items()
        },
        "task_types": bundle.task_types,
    }
    # token 口径标记：qwenwork 连 token 都是字符估算，显式落 meta 位
    if args.source == "qwenwork":
        result["meta"]["tokens_source"] = "estimated"
    return result


def compute_totals(bundle):
    """⑨：token / 成本 / 积分 / 活跃天数汇总（对应原 677–694 行）。"""
    traces = bundle.traces
    # 活跃天数 = 窗口内产生 token 活动的日期（仅统计 trace 日期）。
    # 不计入会话创建日：窗口外创建、但窗口内有 trace 的会话若把创建日也算进
    # active_days 会虚高"活跃天数"（如 --days 7 却显示 12 天）。
    active_days = set(t["date"] for t in traces)
    return Totals(
        total_tokens=sum(t["total_tokens"] for t in traces),
        total_input=sum(t["input_tokens"] for t in traces),
        total_output=sum(t["output_tokens"] for t in traces),
        total_cached=sum(t["cached_tokens"] for t in traces),
        total_effective=sum(t.get("effective_tokens", 0) for t in traces),
        total_credits=sum(c["used"] for c in bundle.session_credits),
        total_cost=sum(t["total_cost"] for t in traces),
        total_input_cost=sum(t["input_cost"] for t in traces),
        total_output_cost=sum(t["output_cost"] for t in traces),
        total_effective_cost=round(sum(t.get("effective_cost", 0.0) for t in traces), 2),
        active_days=sorted(active_days),
    )


def compute_transparency_counts(bundle, totals):
    """⑪：未知模型 / 档位 / 失败调用计数 + 缓存率（对应原 744、746–758 行）。"""
    traces = bundle.traces
    # 1) 未知模型：raw_model 与 exec_model 都是字面量 "default"，采集器不知道真实模型
    unknown_model_traces = sum(1 for t in traces if t.get("is_unknown_model"))
    # 2) 档位名（fast-model 等）：路由档位非真模型名，单价为倍率锚定估算
    tier_traces = [t for t in traces if t.get("is_tier_name")]
    tier_name_cost = round(sum(t.get("total_cost", 0.0) for t in tier_traces), 4)
    tier_alt_hy3_cost = round(
        sum(t.get("alt_hy3_cost") or 0.0 for t in tier_traces), 4)
    # 3) 失败调用：单独成列而非剔除——同样消耗 token / 可能计费
    error_traces = sum(1 for t in traces if t.get("is_error"))
    # 缓存占比：缓存命中 token 占输入 token 的比例
    cache_rate = (totals.total_cached / totals.total_input * 100) if totals.total_input else 0
    return TransparencyCounts(
        unknown_model_traces=unknown_model_traces,
        tier_traces=tier_traces,
        tier_name_cost=tier_name_cost,
        tier_alt_hy3_cost=tier_alt_hy3_cost,
        error_traces=error_traces,
        cache_rate=cache_rate,
    )


def build_summary(result, bundle, totals, req_count, counts):
    """⑫：写 result["summary"] 24 字段（对应原 760–791 行）。"""
    result["summary"] = {
        "total_traces": len(bundle.traces),
        # 有效调用 = 总 trace - 失败调用。total_traces 保留原样（历史口径不动）
        "total_effective_traces": len(bundle.traces) - counts.error_traces,
        "error_traces": counts.error_traces,
        "unknown_model_traces": counts.unknown_model_traces,
        "total_sessions": len(bundle.sessions),
        "total_tokens": totals.total_tokens,
        "total_input_tokens": totals.total_input,
        "total_output_tokens": totals.total_output,
        "total_cached_tokens": totals.total_cached,
        "total_effective_tokens": totals.total_effective,
        "cache_rate": round(counts.cache_rate, 1),
        "total_credits_used": totals.total_credits,
        "active_days": totals.active_days,
        "active_day_count": len(totals.active_days),
        "total_automation_runs": len(bundle.automation_runs),
        "successful_automation_runs": sum(
            1 for r in bundle.automation_runs if r["result_success"]
        ),
        "total_outputs": len(bundle.outputs),
        "skills_used": len(bundle.skill_usage["skills"]),
        "task_type_distribution": {},
        "total_cost": totals.total_cost,
        "total_input_cost": totals.total_input_cost,
        "total_output_cost": totals.total_output_cost,
        "total_effective_cost": round(totals.total_effective_cost, 2),
        # generation 聚类反推的「估算请求数」（口径与官方请求数不同，仅对照用）
        "estimated_request_count": req_count,
        "request_estimate_gap_minutes": 15,
    }


def build_task_distribution(result, bundle):
    """⑬：仅统计本期有 token 活动的会话，避免历史空会话虚高（对应原 793–802 行）。"""
    _traced_ids = token_active_session_ids(bundle.traces)
    for s in bundle.sessions:
        if s.get("id") not in _traced_ids:
            continue
        tt = s.get("task_type", "其他")
        result["summary"]["task_type_distribution"][tt] = (
            result["summary"]["task_type_distribution"].get(tt, 0) + 1
        )


def attach_aggregates(result, bundle):
    """⑭：5 类聚合（对应原 804–815 行）。"""
    result["task_token_stats"] = aggregate_task_token_stats(bundle.traces, bundle.sessions)
    result["top_tasks"] = aggregate_top_tasks(bundle.traces, bundle.sessions, top_n=10)
    # 按实际执行模型聚合（计费维度 / 账单口径）→ §3.1
    result["model_stats"] = aggregate_by_exec_model(bundle.traces)
    # 按入口 / 配置模型聚合（使用分布维度）→ §3.2
    result["model_exec_stats"] = aggregate_by_model(bundle.traces)
    # 按档位（路由三档）聚合 → §3.4
    result["tier_stats"] = aggregate_by_tier(bundle.traces)


def enrich_analysis(result, bundle):
    """⑳：session_stats / savings(L2 补算) / daily_tokens / cost_anomalies（对应原 992–1032 行）。"""
    traces = bundle.traces
    result["session_stats"] = aggregate_by_session(traces, bundle.sessions)
    # 省钱洞察基于计费维度（exec_model）找真实付费贵模型。
    # L1 真值模式已在 import_official_l1 里用官方真实积分构造并写入 savings_insights；
    # 此处仅当尚未构造（L2 估算模式）时补算，避免覆盖 L1 真值版。
    if "savings_insights" not in result:
        result["savings_insights"] = build_savings_insights(result["model_stats"])

    # 每日 token 统计
    daily_tokens = {}
    for t in traces:
        d = t["date"]
        if d not in daily_tokens:
            daily_tokens[d] = {
                "total": 0,
                "input": 0,
                "output": 0,
                "cached": 0,
                "calls": 0,
                "total_cost": 0,
                "input_cost": 0,
                "output_cost": 0,
                "effective": 0,
                "effective_cost": 0,
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

    # 成本异常检测（依赖 daily_tokens 与 session_stats，故置于每日统计之后）
    result["cost_anomalies"] = detect_cost_anomalies(
        result["daily_tokens"], result["session_stats"]
    )
