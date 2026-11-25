# -*- coding: utf-8 -*-
"""adapters/dumate.py — 百度搭子（DuMate）专用数据源适配器（2026-11-25 重写）

数据归属（实证，2026-11-25）
  百度搭子桌面客户端 qianfan-desktop-app / DuMate 的真实用量数据在：
    %APPDATA%/qianfan-desktop-app/qianfan_desk_xdg/<uid-hash>/
      data/opencode/opencode.db
  实测 schema 为 OpenCode 旧代：session(77) / message(1771) / part(6952)，
  token 藏在 message.data JSON 的 tokens{total,input,output,reasoning,
  cache:{read,write}}，同条消息还带 modelID / providerID / requestID，
  是上游回传的 L2 真值（不是本地字符估算）。

  ⚠️ 此前版本把数据源默认指向 ~/.workbuddy（"归属未证实" 保守假设），实跑证明
  那条链读到的 100% 是 WorkBuddy 自己的库，与百度搭子无关，已废弃。
  本适配器与 --source generic 完全独立：自带专用 reader，不读 generic 的
  schema profile，也不复用 ca_sources 的 WorkBuddy 路径常量。

  本应用只录入本地会话数据：全链路只读本地 SQLite（mode=ro），不联网、
  不上传、不读取 credential/account/token 等敏感表。

计费口径
  SUPPORTS_COST=False：百度搭子底层模型标识（glm-5 等）是否有可核对刊例价
  尚未确认，不做任何金额推断；报告强制 tokens-only，只显示模型 / token /
  调用维度，全篇零金额。

隐私边界
  - 数据库路径由 DUMATE_DB_PATH 显式指定或按固定目录自动发现，不递归用户 home；
  - 只读打开（mode=ro + timeout），锁冲突显式告警降级，绝不静默读空；
  - 仅 SELECT session / message 两张白名单表；part / control_account /
    memory_chunk / session_share 等表一律不访问（只从 sqlite_master 读表名用于探测）；
  - 关闭符号链接跟随：发现路径若含符号链接则跳过。
"""

import contextlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ca_core import ts_to_date  # noqa: E402

__all__ = [
    "DUMATE_DB_PATH",
    "SUPPORTS_COST",
    "collect_dumate",
    "resolve_dumate_db",
    "resolve_dumate_home",
]

# 显式能力声明：不提供金额，报告走 tokens-only（见 CLI :329–335 强制口径）。
SUPPORTS_COST = False

# 环境变量名（直接指向 opencode.db 文件，或其所在目录）。
DUMATE_DB_PATH = "DUMATE_DB_PATH"

_TZ = timezone(timedelta(hours=8))
# 只读白名单：除这两张表外不访问任何表内容（含 part / control_account）。
_READ_TABLES = ("session", "message")
_SENSITIVE = (
    "credential", "credentials", "account", "accounts", "token", "tokens",
    "secret", "secrets", "auth", "auth_tokens", "api_keys", "keychain",
    "control_account",
)


def _appdata_root():
    """%APPDATA%（Windows）或 ~/.config（POSIX）下的 qianfan-desktop-app 目录。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "qianfan-desktop-app"
    return Path.home() / ".config" / "qianfan-desktop-app"


def _is_link(path):
    """路径任一环节是符号链接就跳过（隐私硬边界）。"""
    p = Path(path)
    for part in (p, *p.parents):
        try:
            if part.is_symlink():
                return True
        except OSError:
            return True
        if part == part.parent:
            break
    return False


def resolve_dumate_db():
    """定位百度搭子 opencode.db。

    优先级：
      1. DUMATE_DB_PATH —— 文件或目录，直接指向（测试 fixture / 多账号）
      2. DUMATE_HOME   —— 兼容旧变量：可指向 db 文件、或含 data/opencode 的根
      3. 自动发现 —— %APPDATA%/qianfan-desktop-app/qianfan_desk_xdg/*/
         data/opencode/opencode.db，多个账号取 mtime 最新者
      4. 未发现时返回保守默认路径（通常不存在），调用方判空走空结果

    返回 Path，可能不存在；只读打开失败时 collect_dumate 返回空结果并打 stderr。
    """
    for env_name in (DUMATE_DB_PATH, "DUMATE_HOME"):
        env = os.environ.get(env_name)
        if not env:
            continue
        p = Path(env).expanduser()
        candidates = [p]
        if p.is_dir():
            candidates = [p / "data" / "opencode" / "opencode.db", p / "opencode.db"]
        for c in candidates:
            if c.is_file() and not _is_link(c):
                return c
        # 显式指定但文件不存在：原样返回，让调用方显式失败/降级，不悄悄回退真实用户目录
        return candidates[0]

    root = _appdata_root() / "qianfan_desk_xdg"
    found = []
    if root.exists() and not _is_link(root):
        with contextlib.suppress(OSError):
            for candidate in root.glob("*/data/opencode/opencode.db"):
                if candidate.is_file() and not _is_link(candidate):
                    found.append(candidate)
    if found:
        return max(found, key=lambda p: p.stat().st_mtime)
    return root / "data" / "opencode" / "opencode.db"


def resolve_dumate_home():
    """兼容旧 API：返回 opencode.db 所在目录（data/opencode）。"""
    return resolve_dumate_db().parent


# 保留旧常量名供外部引用（CLI 已改用 resolve_dumate_db()）。
DUMATE_HOME = resolve_dumate_db().parent


def _connect_ro(path):
    """只读打开 opencode.db；锁冲突 / 路径异常返回 None 并显式告警。"""
    if not path or not Path(path).exists():
        return None
    if _is_link(path):
        print(f"[WARN] dumate 数据源为符号链接，跳过：{path}", file=sys.stderr)
        return None
    uri = "file:" + str(path).replace("\\", "/") + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        # 只读校验：到 sqlite_master 取表名（不读表内容），顺便探测 schema。
        tables = {
            r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not all(t in tables for t in _READ_TABLES):
            print(
                f"[WARN] dumate opencode.db schema 不含 session/message，跳过（tables={sorted(tables)[:8]}）",
                file=sys.stderr,
            )
            connection.close()
            return None
        if any(t.lower() in _SENSITIVE for t in tables):
            # 敏感表存在并不读取：此处仅提示本适配器不会访问它们。
            pass
        return connection
    except (sqlite3.Error, OSError) as exc:
        print(f"[WARN] dumate opencode.db open(read-only): {exc}", file=sys.stderr)
        return None


def _date_of_ms(value):
    """毫秒时间戳 → YYYY-MM-DD（东八区）；无法解析返回空串。"""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=_TZ).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def _to_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _load_sessions(conn):
    """从 session 表读全部未删除会话（窗口过滤在 collect_dumate 里做）。

    窗口外创建的会话仍可能在窗口内产生 trace，不能在这里提前过滤，否则大量
    token 会被静默丢弃（旧版 dumate 用「补全会话」修复过同类问题）。
    """
    rows = conn.execute(
        "SELECT id, title, directory, time_created, time_updated, "
        "model_settings, source, channel, type FROM session "
        "WHERE time_deleted IS NULL"
    ).fetchall()
    sessions = []
    for r in rows:
        date = _date_of_ms(r["time_created"])
        model = ""
        raw_settings = r["model_settings"]
        if raw_settings:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                settings = json.loads(raw_settings) if isinstance(raw_settings, str) else raw_settings
                if isinstance(settings, dict):
                    model = str(
                        settings.get("modelID") or settings.get("model")
                        or settings.get("model_id") or ""
                    )
        sessions.append({
            "id": str(r["id"]),
            "cwd": str(r["directory"] or ""),
            "title": str(r["title"] or ""),
            "custom_title": "",
            "status": "active",
            "created_at": _to_int(r["time_created"]),
            "created_date": date,
            "updated_at": _to_int(r["time_updated"]),
            "mode": str(r["type"] or r["channel"] or "default"),
            "model": model or str(r["channel"] or "dumate"),
            "is_background_automation": False,
            # 只录入本地会话数据：不携带任何云端归属字段。
            "_source": "dumate",
        })
    return sessions


def _load_traces(conn, start_date, end_date, sessions):
    """从 message.data JSON 抽 assistant 调用的 L2 token，归一成统一 trace。"""
    known = {s["id"] for s in sessions}
    rows = conn.execute(
        "SELECT id, session_id, time_created, data FROM message WHERE time_deleted IS NULL"
    ).fetchall()
    traces, seen = [], set()
    for r in rows:
        sid = str(r["session_id"] or "")
        if sid not in known:
            continue
        raw = r["data"]
        try:
            msg = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        tokens = msg.get("tokens")
        if not isinstance(tokens, dict):
            continue
        total = _to_int(tokens.get("total"))
        inp = _to_int(tokens.get("input"))
        out = _to_int(tokens.get("output"))
        if not (total or inp or out):
            continue
        # 真实数据 data.time 是 {'created': ms, 'completed': ms} 字典，不是标量。
        timing = msg.get("time") if isinstance(msg.get("time"), dict) else {}
        created_ms = timing.get("created") or r["time_created"]
        completed_ms = timing.get("completed") or created_ms
        date = _date_of_ms(created_ms)
        if not date or not (start_date <= date <= end_date):
            continue
        # 百度搭子同一个 requestID 会复用于多条 usage 消息（流式/多轮上报），
        # 按 requestID 去重会误删真实调用；改用 message.id（库主键）作唯一键。
        # requestID 只作为元数据保留在 _request_id，不参与去重。
        req = str(r["id"] or "")
        if req in seen:
            continue
        seen.add(req)
        model = str(msg.get("modelID") or msg.get("model") or "unknown")
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        cached = _to_int(cache.get("read"))
        reasoning = _to_int(tokens.get("reasoning"))
        # 与 ca_core.CACHE_DISCOUNT = 0.1 保持一致，但本源不计价。
        effective = max(int(round(total - cached * 0.1)), 0)
        def _iso(ms):
            try:
                return datetime.fromtimestamp(float(ms) / 1000, tz=_TZ).isoformat()
            except (TypeError, ValueError, OSError, OverflowError):
                return date
        traces.append({
            "trace_id": f"dumate:{sid}:{req}",
            "pid": f"dumate:{sid}",
            "date": date,
            "started_at": _iso(created_ms),
            "ended_at": _iso(completed_ms),
            "duration_ms": max(_to_int(completed_ms) - _to_int(created_ms), 0),
            "status": "error" if str(msg.get("finish") or "").lower() in ("error", "failed") else "success",
            "session_id": sid,
            "total_tokens": total,
            "token_source": "reported",
            "input_tokens": inp,
            "output_tokens": out,
            "cached_tokens": cached,
            "reasoning_tokens": reasoning,
            "call_count": 1,
            "models": [model],
            "model_name": model,
            "exec_model": model,
            "model_key": model,
            # 不冒充 WorkBuddy 默认网关：显式标记 dumate 通道。
            "channel": "dumate",
            "raw_model": model,
            # 金额恒 0：SUPPORTS_COST=False + CLI 强制 tokens-only 双保险。
            "total_cost": 0.0,
            "input_cost": 0.0,
            "output_cost": 0.0,
            "effective_tokens": effective,
            "effective_cost": 0.0,
            "is_free": False,
            "is_error": str(msg.get("finish") or "").lower() in ("error", "failed"),
            "is_unknown_model": model == "unknown",
            "is_tier_name": False,
            "alt_hy3_cost": None,
            # token 分层：上游 L2 真值，非估算、非缺失。
            "tokens_source": "reported",
            "tokens_source_layer": "L2",
            "_tokens_estimated": False,
            "_tokens_missing": False,
            "_tokens_note": "百度搭子 message.data.tokens 上游回传真值",
            "_request_id": str(msg.get("requestID") or ""),
            "_dialogue_text": "",
        })
    traces.sort(key=lambda t: t["started_at"])
    return traces


def collect_dumate(start_date, end_date):
    """返回 (traces, db_data)，schema 与采集层下游兼容。

    仅录入本地会话数据：只读取本地 opencode.db 的 session / message 两张表；
    automation_runs / session_credits 留空（该库无对应表）；成本维度恒 0。
    """
    db_path = resolve_dumate_db()
    conn = _connect_ro(db_path)
    if conn is None:
        return [], {
            "sessions": [], "automation_runs": [], "session_credits": [],
            "skill_usage": {"skills": {}, "active_days": []},
            "outputs": [], "memory_logs": {},
            "_meta": {"dumate_db": str(db_path), "local-only": True},
        }
    try:
        all_sessions = _load_sessions(conn)
        traces = _load_traces(conn, start_date, end_date, all_sessions)
    finally:
        conn.close()

    # 纳入「窗口内创建」或「窗口内有 trace」的会话（后者可能创建于窗口外）。
    traced_sids = {t["session_id"] for t in traces}
    sessions = [
        s for s in all_sessions
        if (start_date <= s["created_date"] <= end_date) or s["id"] in traced_sids
    ]
    sessions.sort(key=lambda s: s["created_at"])

    # session.model_settings 只记档位（如 autoModelLevel=L0），没有真实模型名；
    # 从本窗口 trace 反推每个会话实际执行的模型，供报告模型维度使用。
    sid_model = {}
    for t in traces:
        sid_model.setdefault(t["session_id"], t["model_name"])
    for s in sessions:
        if s.get("model") in ("", "dumate") and s["id"] in sid_model:
            s["model"] = sid_model[s["id"]]

    db_data = {
        "sessions": sessions,
        "automation_runs": [],   # 百度搭子库无自动化表
        "session_credits": [],   # 百度搭子库无信用消耗表
        "skill_usage": {"skills": {}, "active_days": sorted({t["date"] for t in traces})},
        "outputs": [],
        "memory_logs": {},
        "_meta": {
            "dumate_db": str(db_path),
            "local-only": True,
            "source_notice": "本应用只录入本地会话数据",
            "tokens_source_layer_summary": {"L2": {"traces": len(traces),
                                                    "tokens": sum(t["total_tokens"] for t in traces)}},
        },
    }
    return traces, db_data
