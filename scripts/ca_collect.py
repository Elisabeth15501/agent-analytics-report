#!/usr/bin/env python3
"""ca_collect.py — 采集层（批次 5）：6 路数据源分发 + WorkBuddy 三步时序。

从 collect_usage_data.py::main() 内联段（原 :398–:612）整体搬出，逻辑逐字保留：
  - claude-code / codex / qwenwork / dumate / generic / WorkBuddy(else) 六路分发；
  - WorkBuddy 三步时序「补全 → setdefault → 重采」不重排（契约 7）；
  - SQLite 补全连接 mode=ro（契约 8）；
  - dumate / generic 无条件直采 collect_task_types，不收口预分类（契约 9）。

对外入口（与 docs/ADAPTERS.md 契约一致）：
  collect_from_source(args, start_date, end_date, task_classifier) -> CollectionBundle
  collect_workbuddy_bundle(start_date, end_date, task_classifier) -> CollectionBundle

设计红线：
  * 只采集、不写盘、不联网（唯一写盘点仍在门面 emit）；
  * auto 探测与 tokens-only 强制留在 main（:323–335），本模块收到的 args.source 必为具体源名；
  * result JSON 的键顺序不受影响——CollectionBundle 只在步骤函数间传递，不进 JSON。
"""

from __future__ import annotations

import sqlite3
import sys

from ca_core import DB_PATH, ts_to_date
from ca_sessions import collect_task_types
from ca_sources import (
    collect_db_data,
    collect_session_outputs,
    collect_skill_usage,
    collect_traces,
)
from ca_types import CollectionBundle

__all__ = ["collect_from_source", "collect_workbuddy_bundle"]


def _task_types_from_adapter(db_data, task_classifier):
    """claude-code / codex / qwenwork 共用：有 LLM 分类器则重分类，否则收口预分类。

    适配器已基于对话文本预分类 task_type（复用 classify_task，与 WorkBuddy 同源）；
    仅当显式启用 LLM 分类器时才基于 _dialogue_text 重分类。
    """
    if task_classifier is not None:
        return collect_task_types(db_data["sessions"], classifier=task_classifier)
    return {
        s["id"]: s.get("task_type", "其他") for s in db_data["sessions"]
    }


def collect_workbuddy_bundle(start_date, end_date, task_classifier):
    """WorkBuddy 源：会话元数据 + trace 两阶段关联，三步时序内部禁止重排。

    输入:
        start_date, end_date: str (YYYY-MM-DD)
        task_classifier: callable | None（None=启发式，非 None=LLM 增强）
    输出:
        CollectionBundle（traces/sessions/automation_runs/session_credits/
        sid_to_rawmodel/skill_usage/outputs/memory_logs/task_types 已填，
        cost_supported=True，source_meta={}）
    执行流程（顺序即契约，重排会静默导致通道误判）:
        1. collect_db_data → sessions + sid_to_rawmodel
        2. collect_traces → 初采
        3. 补全窗口外创建但有窗内 trace 的会话（mode=ro 只读连接）
        4. setdefault：把补全会话并入 sid_to_rawmodel
        5. 重采 trace（用补全后的映射，修正 raw_model 通道归属）
        6. 技能使用 / 会话产出 / 记忆日志 / 任务类型
    """
    db_data = collect_db_data(start_date, end_date)
    sid_to_rawmodel = {
        s["id"]: (s.get("model") or "default") for s in db_data["sessions"]
    }
    traces = collect_traces(start_date, end_date, sid_to_rawmodel)

    # ── 步骤 1：补全会话 ──
    # 窗口内有 trace 但创建于窗口外的会话，确保 token 统计能关联到任务类型。
    try:
        trace_sids = {t.get("session_id") for t in traces if t.get("session_id")}
        existing_ids = {s["id"] for s in db_data["sessions"]}
        missing = trace_sids - existing_ids
        if missing:
            # 只读打开：采集器只读 sessions 表，不需要写权限——可写连接会因 WAL
            # 模式生成/持有 -wal -shm 文件，客户端持锁时读数会抛 SQLITE_BUSY。
            cdb = sqlite3.connect(
                f"file:{DB_PATH.as_posix()}?mode=ro", uri=True
            )
            try:
                cdb.row_factory = sqlite3.Row
                ph = ",".join("?" * len(missing))
                for r in cdb.execute(
                    f"SELECT * FROM sessions WHERE id IN ({ph}) AND deleted_at IS NULL",
                    list(missing),
                ).fetchall():
                    db_data["sessions"].append(
                        {
                            "id": r["id"],
                            "cwd": r["cwd"],
                            "title": r["title"] or "",
                            "custom_title": r["custom_title"] or "",
                            "status": r["status"],
                            "created_at": r["created_at"],
                            "created_date": ts_to_date(r["created_at"]),
                            "updated_at": r["updated_at"],
                            "mode": r["mode"],
                            "model": r["model"],
                            "is_background_automation": bool(
                                r["is_background_automation"]
                            ),
                        }
                    )
            finally:
                cdb.close()
    except (sqlite3.Error, OSError) as e:
        print(f"[WARN] supplementary sessions query: {e}", file=sys.stderr)

    # ── 步骤 2：setdefault + 步骤 3：重采 ──
    # 补全会话后把跨窗口长会话并入 sid_to_rawmodel 并重采 trace——
    # 否则这些会话的 sid 在映射里缺失，raw_model 会退化成 trace 执行模型名，
    # 导致通道误判（如 hy3 网关会话里的 deepseek-v4-pro 被错归、或反之）。
    for s in db_data["sessions"]:
        sid_to_rawmodel.setdefault(s["id"], s.get("model") or "default")
    traces = collect_traces(start_date, end_date, sid_to_rawmodel)

    skill_usage = collect_skill_usage(start_date, end_date)
    outputs, memory_logs = collect_session_outputs(start_date, end_date)
    task_types = collect_task_types(db_data["sessions"], classifier=task_classifier)

    return CollectionBundle(
        traces=traces,
        sessions=db_data["sessions"],
        automation_runs=db_data["automation_runs"],
        session_credits=db_data["session_credits"],
        sid_to_rawmodel=sid_to_rawmodel,
        skill_usage=skill_usage,
        outputs=outputs,
        memory_logs=memory_logs,
        task_types=task_types,
        cost_supported=True,
        source_meta={},
    )


def collect_from_source(args, start_date, end_date, task_classifier):
    """按 args.source 分发到 6 路采集器，统一返回 CollectionBundle。

    输入:
        args: Namespace —— 已由 main 完成 detect_source 写回，source 必为具体源名
              （claude-code/codex/qwenwork/dumate/generic/workbuddy）；
              generic 用 GENERIC_HOME/GENERIC_PROJECTS_DIR，dumate 用 DUMATE_DB_PATH。
        start_date, end_date: str (YYYY-MM-DD)
        task_classifier: callable | None
    输出:
        CollectionBundle —— 11 字段全部填好，交给下游统计层（ca_analytics/ca_billing）。
    副作用:
        仅 stderr 的 [INFO] 采集摘要；不写盘、不联网。
    调用关系:
        main → collect_from_source → (各 adapters.collect_<agent> |
        collect_workbuddy_bundle) → CollectionBundle → build_result_skeleton。
    分发规则:
        claude-code/codex/qwenwork → 收口适配器预分类（LLM 分类器时重分类）；
        dumate/generic             → 无条件直采 collect_task_types（无预分类键）；
        else(workbuddy)            → collect_workbuddy_bundle 三步时序。
    """
    # cost_supported：该数据源是否在语义上支持计费维度（默认支持；qwenwork 等置 False）
    cost_supported = True
    # 数据源自报的 meta 补充（single_channel / background_runs / 采集来源标注等）
    source_meta = {}

    if args.source == "claude-code":
        # ── Claude Code 数据源（P2-1 适配器 MVP）──
        # 无需 WorkBuddy 环境，直接读 ~/.claude/projects/ 下的会话 JSONL；
        # 适配器产出与 WorkBuddy 同源的 trace / sessions，任务类型已预分类。
        from adapters.claude_code import collect_claude_code

        traces, db_data = collect_claude_code(start_date, end_date)
        sid_to_rawmodel = {
            s["id"]: (s.get("model") or "default") for s in db_data["sessions"]
        }
        skill_usage = {"skills": {}, "active_days": sorted({t["date"] for t in traces})}
        outputs, memory_logs = ([], {})
        task_types = _task_types_from_adapter(db_data, task_classifier)
        print(
            f"[INFO] Claude Code 数据源：{len(traces)} 条 trace / {len(db_data['sessions'])} 个会话",
            file=sys.stderr,
        )
    elif args.source == "codex":
        # ── OpenAI Codex CLI 数据源 ──
        # 无需 WorkBuddy 环境，直接读 ~/.codex/sessions/ 下的 rollout JSONL（按日期分目录）；
        # 适配器产出与 WorkBuddy 同源的 trace / sessions，任务类型已预分类。
        from adapters.codex import collect_codex

        traces, db_data = collect_codex(start_date, end_date)
        sid_to_rawmodel = {
            s["id"]: (s.get("model") or "default") for s in db_data["sessions"]
        }
        skill_usage = {"skills": {}, "active_days": sorted({t["date"] for t in traces})}
        outputs, memory_logs = ([], {})
        task_types = _task_types_from_adapter(db_data, task_classifier)
        print(
            f"[INFO] Codex CLI 数据源：{len(traces)} 条 trace / {len(db_data['sessions'])} 个会话",
            file=sys.stderr,
        )
    elif args.source == "qwenwork":
        # ── 千问办公（QwenWork）数据源 ──
        # 无需 WorkBuddy 环境：读 ~/.qwenworkcn/projects 转录 + logs/runs 调用日志 +
        # agents.db 会话元信息。千问办公上游不回传 token（实测 1167 条
        # model.response.completed 的四个 token 字段全为 0），故 token 为字符估算、
        # 档位无公开刊例价 → 成本不计价；调用次数 / 耗时 / 标题 / 档位为真实值。
        from adapters.qwenwork import collect_qwenwork, SUPPORTS_COST

        # 能力声明（不是「这次恰好没单价」）：千问办公在语义上不支持计费维度，
        # 报告端据此压制「缺失单价 + 补价 stub」整块。见 generate_report._show_unconfigured_block
        cost_supported = SUPPORTS_COST
        traces, db_data = collect_qwenwork(start_date, end_date)
        sid_to_rawmodel = {
            s["id"]: (s.get("model") or "default") for s in db_data["sessions"]
        }
        skill_usage = db_data.get("skill_usage") or {
            "skills": {},
            "active_days": sorted({t["date"] for t in traces}),
        }
        outputs = db_data.get("outputs") or []
        # memory_logs 保持空：千问办公的记忆写进 awareness/*.md（由 file-history 快照承载），
        # 没有 WorkBuddy 那种「按日期归集的会话记忆日志目录」可映射，硬凑会造假结构。
        memory_logs = {}
        source_meta.update(db_data.get("_meta") or {})
        task_types = _task_types_from_adapter(db_data, task_classifier)
        _est = sum(1 for t in traces if t.get("_tokens_estimated"))
        print(
            f"[INFO] 千问办公数据源：{len(traces)} 条 trace / "
            f"{len(db_data['sessions'])} 个会话"
            f"（{_est}/{len(traces)} 条 token 为字符估算，档位无刊例价故未计价）",
            file=sys.stderr,
        )
    elif args.source == "dumate":
        # ── 百度搭子（DuMate）数据源（2026-11-25 专用 reader）──
        # 真实数据在 %APPDATA%/qianfan-desktop-app/qianfan_desk_xdg/<hash>/
        # data/opencode/opencode.db；不再读 ~/.workbuddy（那是 WorkBuddy 自己的库）。
        # 适配器自带专用 reader，不复用 generic 的 profile 机制。
        # 能力声明：SUPPORTS_COST=False，报告只显示模型/Token/调用维度，零金额。
        from adapters.dumate import (
            collect_dumate, resolve_dumate_db, SUPPORTS_COST,
        )

        cost_supported = SUPPORTS_COST
        traces, db_data = collect_dumate(start_date, end_date)
        sid_to_rawmodel = {
            s["id"]: (s.get("model") or "default") for s in db_data["sessions"]
        }
        skill_usage = db_data.get("skill_usage") or {
            "skills": {},
            "active_days": sorted({t["date"] for t in traces}),
        }
        outputs = db_data.get("outputs") or []
        memory_logs = db_data.get("memory_logs") or {}
        # 任务类型：与 WorkBuddy 源同一条路径（collect_task_types，默认启发式加权），
        # 有 LLM 分类器时当作 classifier 传入，没有就不传。
        # ⚠️ 不要再走「收口适配器预分类结果」的老路：dumate 适配器不做任何预分类，
        # 会话 dict 里也没有 task_type 键，直接收口会把全部会话塌成「其他」。
        task_types = collect_task_types(db_data["sessions"], classifier=task_classifier)
        source_meta.update(
            {
                "dumate_db": str(resolve_dumate_db()),
                "local-only": True,
                "source_notice": "本应用只录入本地会话数据",
            }
        )
        print(
            f"[INFO] 百度搭子数据源：{len(traces)} 条 trace / "
            f"{len(db_data['sessions'])} 个会话"
            f"（本地会话数据，只读 {resolve_dumate_db()}）",
            file=sys.stderr,
        )
    elif args.source == "generic":
        # ── 通用数据源（阶段 1 / P0）──
        # 只读显式根目录，JSONL/SQLite 双 reader；金额恒为 0，
        # token 逐条带 tokens_source 分层标记，报告端走 tokens_only 口径。
        from adapters.generic import collect_generic, SUPPORTS_COST

        # 能力显式声明：不靠「本期单价全 0」隐式推断（红线 1）
        cost_supported = SUPPORTS_COST
        traces, db_data = collect_generic(start_date, end_date)
        sid_to_rawmodel = {
            s["id"]: (s.get("model") or "default") for s in db_data["sessions"]
        }
        skill_usage = db_data.get("skill_usage") or {
            "skills": {}, "active_days": sorted({t["date"] for t in traces})
        }
        outputs = db_data.get("outputs") or []
        # 红线 6：通用源不构造 memory_logs，避免把用户目录内容带进报告
        memory_logs = {}
        source_meta.update(db_data.get("_meta") or {})
        # 适配器不做预分类；与 dumate 同理必须无条件直采，
        # 否则没有 task_type 键的会话会整列塌成「其他」。
        task_types = collect_task_types(db_data["sessions"], classifier=task_classifier)
    else:
        # ── WorkBuddy 默认数据源 ──
        # 三步时序整体委托 collect_workbuddy_bundle（补全→setdefault→重采，内部不重排）
        return collect_workbuddy_bundle(start_date, end_date, task_classifier)

    return CollectionBundle(
        traces=traces,
        sessions=db_data["sessions"],
        automation_runs=db_data["automation_runs"],
        session_credits=db_data["session_credits"],
        sid_to_rawmodel=sid_to_rawmodel,
        skill_usage=skill_usage,
        outputs=outputs,
        memory_logs=memory_logs,
        task_types=task_types,
        cost_supported=cost_supported,
        source_meta=source_meta,
    )
