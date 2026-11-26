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

注意：本文件是 CLI 编排入口 + facade，实际实现分布在
ca_core.py / ca_sources.py / ca_sessions.py / ca_aggregate.py，由本文件统一编排并 re-export。
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
import urllib.error
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# 技能根目录（scripts/ 的上一级），用于导入 adapters/ 下的多 Agent 数据源适配器
_SKILL_ROOT = os.path.dirname(_HERE)
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)

from ca_core import (
    ALL_CUSTOM_MODELS,
    ALL_LOCAL_MODELS,
    ALL_ROUTER_MODELS,
    CACHE_DISCOUNT,
    CUSTOM_LOCAL_PRICING,
    DB_PATH,
    DEFAULT_BLENDED_PER_MILLION,
    DEFAULT_MODEL,
    DELISTED_MODELS,
    DISCOVERED_EXTERNAL,
    DISCOVERED_LOCAL,
    DISCOVERED_ROUTER,
    DISPLAY_MERGE,
    GLM52_FAMILY,
    GLM52_RATE,
    HOME,
    LOW_CONFIDENCE,
    LOW_CONFIDENCE_BIAS,
    MEDIA_EXTS,
    MODEL_PRICING,
    MODE_RATES_META,
    ORPHAN_KEY,
    ORPHAN_LABEL,
    PERIOD_DAYS,
    PERIOD_LABELS,
    PERIOD_NEXT,
    PERIOD_SHORT,
    PER_MILLION,
    PROJECTS_DIR,
    ROUTER_ALIASES,
    ROUTER_HOSTS,
    ROUTER_VENDORS,
    SCHEDULED_PRICING,
    SESSIONS_DIR,
    SILICONFLOW_VENDOR_PREFIXES,
    SYSTEM_REMINDER_RE,
    TASK_RULES_PATH,
    TASK_TYPE_RULES,
    TIER_ALIASES,
    TIER_CANON,
    TIER_LABELS,
    TIMED_FREE,
    TRACES_DIR,
    TZ,
    UNNAMED_LABEL,
    USAGE_LOG_PATH,
    USER_CUSTOM_MODELS,
    WB_DIR,
    WORKBUDDY_SESSIONS,
    _CHEAPER_ALT,
    _PRICING,
    _PRICING_LOCAL_LOADED,
    _build_sid_to_title,
    _load_acc_product_config,
    _load_pricing_config,
    _to_num,
    call_time_of,
    canonical_tier,
    compute_cost,
    discover_custom_models,
    effective_tokens_of,
    glm52_discount_multiplier,
    is_entry_tier,
    is_router_like,
    is_scheduled_free,
    is_timed_free,
    iso_to_date,
    iso_to_dt,
    load_task_rules,
    low_confidence_reason,
    low_confidence_bias,
    merge_display_key,
    normalize_model,
    parse_channel,
    parse_date_range,
    price_of,
    resolve_date_range,
    resolve_model,
    score_task_types,
    token_active_session_ids,
    trace_cost,
    ts_to_date,
    ts_to_dt,
    _router_avg_unit_price,
)
from ca_sources import (
    _extract_text_from_parts,
    _find_session_jsonl,
    _recover_model_info_from_spans,
    _transform_cwd,
    collect_db_data,
    collect_session_outputs,
    collect_skill_usage,
    collect_traces,
    get_session_artifact_fingerprint,
    get_session_content,
)
from ca_sessions import (
    aggregate_task_token_stats,
    aggregate_top_tasks,
    classify_task,
    collect_task_types,
)
from ca_aggregate import (
    _detect_daily_anomalies,
    _detect_session_anomalies,
    _fmt_anom_val,
    _normalize_model_key,
    _percentile,
    aggregate_by_exec_model,
    aggregate_by_model,
    aggregate_by_session,
    aggregate_by_tier,
    aggregate_traces_by,
    build_savings_insights,
    build_savings_insights_from_official,
    detect_cost_anomalies,
)

# 绑定子模块名，便于测试按「真实定义模块」定位 monkeypatch（拆分后需要）
import ca_core
import ca_sources
import ca_sessions
import ca_aggregate

# 批次2 re-export：函数已搬入新模块，门面保留旧名字供 CLI/main/测试访问
from ca_billing import (
    _decide_cost_mode,
    fetch_remote_pricing,
    import_official_l1,
    collect_unconfigured_models,
    tier_pricing_basis_meta,
    apply_cost_mode,
    decorate_pricing_meta,
    apply_pricing_lookup,
)
from ca_cli import (
    build_parser,
    validate_args,
    resolve_period_window,
    build_task_classifier_with_consent,
)
# 批次5 re-export：采集层已搬入 ca_collect，门面保留旧名字供 main/测试访问
from ca_collect import collect_from_source, collect_workbuddy_bundle
from ca_analytics import (
    estimate_request_count,
    build_result_skeleton,
    compute_totals,
    compute_transparency_counts,
    build_summary,
    build_task_distribution,
    attach_aggregates,
    enrich_analysis,
)


def emit(result, output):
    """唯一写盘点：stdout 打印或写文件（末尾原有行为逐字保留）。"""
    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(output_json, encoding="utf-8")
        print(f"[OK] 数据已保存到 {output}", file=sys.stderr)
    else:
        print(output_json)


def main():
    args = build_parser()
    validate_args(args)
    window = resolve_period_window(args)
    task_classifier = build_task_classifier_with_consent(args)
    bundle = collect_from_source(args, window.start, window.end, task_classifier)
    result = build_result_skeleton(args, window, bundle)
    totals = compute_totals(bundle)
    counts = compute_transparency_counts(bundle, totals, args.source)
    req_count = estimate_request_count(bundle.traces)
    build_summary(result, bundle, totals, req_count, counts)
    build_task_distribution(result, bundle)
    attach_aggregates(result, bundle)
    import_official_l1(result, args, window)
    unconfigured = collect_unconfigured_models(result)
    tier_pricing_basis_meta(result, counts)
    apply_cost_mode(result, args, unconfigured, totals,
                    str(result["meta"].get("cost_source") or "") == "official")
    decorate_pricing_meta(result)
    apply_pricing_lookup(result, args, unconfigured)
    enrich_analysis(result, bundle)
    emit(result, args.output)


if __name__ == "__main__":
    main()
