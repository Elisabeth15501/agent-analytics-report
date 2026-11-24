#!/usr/bin/env python3
"""adapters/generic.py — 通用 Agent 数据源适配器（阶段 1 / P0）。

定位：为尚未写专用适配器的 Agent 提供**显式** ``--source generic`` 数据源，
以及 ``--source auto`` 全不命中时的兜底源。只读、离线、零金额。

设计红线（路线图 §0.3 / V1–V4 实测结论）：
  * 隐私：只扫显式根目录（GENERIC_HOME / GENERIC_PROJECTS_DIR），不递归用户 home、
    不读 ``memory/``、不跟随符号链接；SQLite 只读 ``mode=ro``，表/列白名单，
    永不 SELECT ``credential`` / ``account`` / ``token`` 类表，不整库复制。
  * 计费：SUPPORTS_COST=False；本适配器产生的 trace 金额恒为 0，cost_mode 由 CLI
    强制为 tokens-only（不靠「恰好没单价」隐式推断）。
  * token：L1 账单 > L2 上游回传 > L3 tokenizer > L4 字符启发式，逐条标
    ``tokens_source`` / ``tokens_source_layer``；缺失标 ``missing``，不静默写 0；
    中文 L4 带偏差提示；跨层不合并成单一口径。
  * OTel：属性名走旧→新别名层，pin 到注册表提交 afd3ff6b，不把旧名当主契约。

对外入口：``collect_generic(start_date, end_date, **kw) -> (traces, db_data)``，
与 docs/ADAPTERS.md §7.1 的 ``collect_<agent>`` 契约一致。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

__all__ = [
    "SUPPORTS_COST", "CHANNEL", "OTEL_PIN_COMMIT", "OTEL_ATTR_ALIASES",
    "resolve_generic_home", "resolve_generic_projects_root", "detect_hints",
    "detect_openclaw_layout", "detect_sqlite_profile", "collect_generic",
    "map_otel_attrs", "estimate_tokens",
]

SUPPORTS_COST = False
CHANNEL = "generic"

GENERIC_HOME_ENV = "GENERIC_HOME"
GENERIC_PROJECTS_DIR_ENV = "GENERIC_PROJECTS_DIR"
OPENCLAW_HOME_ENV = "OPENCLAW_HOME"

# V4 实测：近 100 次提交 11 次 rename/replace/deprecate，属性名按别名层处理。
OTEL_PIN_COMMIT = "afd3ff6b"
OTEL_ATTR_ALIASES = {
    "cache_read_input_tokens": "cache_read.input_tokens",
    "cache_creation_input_tokens": "cache_write.input_tokens",
    "gen_ai.usage.cache_read_input_tokens": "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_creation_input_tokens": "gen_ai.usage.cache_write.input_tokens",
}

# 敏感表：表名出现在 sqlite_master 是允许的（只读名字），但绝不 SELECT 其内容。
SENSITIVE_TABLES = frozenset({
    "credential", "credentials", "account", "accounts", "token", "tokens",
    "secret", "secrets", "auth", "auth_tokens", "api_keys", "keychain",
})

SQLITE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3", ".vscdb"})
JSONL_SUFFIXES = frozenset({".jsonl", ".ndjson"})
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def map_otel_attrs(obj):
    """把上游 OTel 旧属性名映射到当前名（别名层，唯一允许出现旧名的位置）。"""
    if not isinstance(obj, dict):
        return obj
    return {OTEL_ATTR_ALIASES.get(k, k): v for k, v in obj.items()}


# ── 路径解析与布局探测 ─────────────────────────────────────────────────────

def resolve_generic_home(env=None):
    """通用源根目录：环境变量优先；默认一个**保守且不存在的**目录。

    默认不指向 ~/.workbuddy / ~/.claude 等已覆盖目录，避免通用源与专用源重复计数。
    """
    environ = os.environ if env is None else env
    raw = environ.get(GENERIC_HOME_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".generic-agent"


def resolve_generic_projects_root(home=None, env=None):
    environ = os.environ if env is None else env
    raw = environ.get(GENERIC_PROJECTS_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    return (home or resolve_generic_home(environ)) / "projects"


def _walk_files(root: Path, suffixes, max_depth=3):
    """受控遍历：限制深度、跳过符号链接与 memory/ 目录，不跟随任何链接。"""
    if not root or not root.is_dir() or root.is_symlink():
        return
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            print(f"[WARN] generic source: 无法读取 {current}: {exc}", file=sys.stderr)
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                p = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() == "memory" or depth >= max_depth:
                        continue
                    stack.append((p, depth + 1))
                elif entry.is_file(follow_symlinks=False) and p.suffix.lower() in suffixes:
                    yield p
            except OSError:
                continue


def detect_openclaw_layout(home=None, env=None):
    """OpenClaw 双路径探测**扩展点**：只判存在性，不打开库、不解析内容。

    Gateway 2026.9.4 实测为 SQLite only（transcript_events）；旧版本可能留 JSONL。
    这里只返回探测特征，不注册 source、不建 openclaw 适配器（路线图 S2.1b / D3 / D4）。
    """
    environ = os.environ if env is None else env
    base = Path(environ[OPENCLAW_HOME_ENV]).expanduser() if environ.get(OPENCLAW_HOME_ENV) \
        else (home or Path.home()) / ".openclaw"
    agent_dir = base / "agents" / "main" / "agent"
    sqlite_path = agent_dir / "openclaw-agent.sqlite"
    jsonl_paths = sorted(agent_dir.glob("*.jsonl")) if agent_dir.is_dir() and not agent_dir.is_symlink() else []
    preferred = "sqlite" if sqlite_path.is_file() and not sqlite_path.is_symlink() else (
        "jsonl" if jsonl_paths else None
    )
    return {
        "found": preferred is not None,
        "preferred": preferred,
        "sqlite": str(sqlite_path) if preferred == "sqlite" else None,
        "jsonl": [str(p) for p in jsonl_paths] if preferred == "jsonl" else [],
        "note": "探测扩展点；未实现 OpenClaw 解析器，不注册 --source openclaw",
    }


def detect_hints(home=None, env=None):
    """识别通用源布局特征；**只识别不解析**，供 auto / 调用方决策。"""
    root = home or resolve_generic_home(env)
    projects = resolve_generic_projects_root(root, env)
    jsonl_files = sorted(_walk_files(projects, JSONL_SUFFIXES))
    sqlite_files = sorted(_walk_files(root, SQLITE_SUFFIXES))
    return {
        "home": str(root),
        "projects": str(projects),
        "jsonl": [str(p) for p in jsonl_files],
        "sqlite": [str(p) for p in sqlite_files],
        "layout": "jsonl" if jsonl_files and not sqlite_files else (
            "sqlite" if sqlite_files and not jsonl_files else (
                "mixed" if jsonl_files or sqlite_files else "empty"
            )
        ),
        "openclaw": detect_openclaw_layout(env=env),
    }


# ── SQLite schema profile（V1/V2 实测）────────────────────────────────────

def detect_sqlite_profile(conn: sqlite3.Connection):
    """读 sqlite_master 的**表名**判定 profile；不访问任何表内容。"""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()}
    if "session_v2" in tables and "session_message" in tables:
        return "opencode_current"
    if "session" in tables and "message" in tables:
        return "opencode_legacy"
    if "message_nodes" in tables and "sessions" in tables:
        return "devin"
    if "transcript_events" in tables:
        return "openclaw_gateway"
    if "cursorDiskKV" in tables:
        return "cursor"
    return None


def _date_of(value):
    """毫秒 / 秒时间戳、ISO 字符串统一成 YYYY-MM-DD；无法解析返回空串。"""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        try:
            v = float(value)
            if v > 10_000_000_000:
                v /= 1000
            if v <= 0:
                return ""
            return datetime.fromtimestamp(v, tz=timezone(timedelta(hours=8))).date().isoformat()
        except (ValueError, OverflowError, OSError):
            return ""
    if isinstance(value, str):
        return value.strip()[:10]
    return ""


def _iso_of(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            v = float(value)
            if v > 10_000_000_000:
                v /= 1000
            return datetime.fromtimestamp(v, tz=timezone(timedelta(hours=8))).isoformat()
        except (ValueError, OverflowError, OSError):
            return ""
    if isinstance(value, str):
        return value
    return ""


def _to_int(value):
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(float(value.strip())))
        except ValueError:
            return 0
    return 0


def _bare_model(model):
    if not model:
        return "unknown"
    text = str(model).strip()
    return text.split(":", 1)[-1] if ":" in text and text.split(":", 1)[0] in {
        "generic", "openai", "anthropic", "qwenwork", "claude-code", "codex"
    } else text


def estimate_tokens(text: str) -> int:
    """L4 字符启发式：CJK 逐字计数，其余按 4 字符≈1 token 折算。

    V3 实测中文字符÷4 平均绝对误差 68.3%，故调用方必须标
    ``estimated_heuristic`` + ``_tokens_estimated`` 并输出偏差提示；本估算**不可**当账单。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    rest = len(text) - cjk
    return cjk + int(rest / 4)


def _text_of(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                part = item.get("text") or item.get("content")
                if isinstance(part, str):
                    parts.append(part)
        return " ".join(parts)
    if isinstance(value, dict):
        return _text_of(value.get("content") or value.get("text"))
    return ""


def _usage_number(usage, *keys):
    mapped = map_otel_attrs(usage)
    for key in keys:
        if key in mapped and mapped[key] not in (None, ""):
            return _to_int(mapped[key])
    return None


def _token_fields(usage, text="", tokenizer=None):
    """返回 (input, output, cached, tokens_source, layer, estimated, missing)。

    tokenizer 为可选 callable(text) -> int；未提供时不声称有 L3 能力。
    """
    usage = map_otel_attrs(usage) if isinstance(usage, dict) else {}
    inp = _usage_number(usage, "input_tokens", "prompt_tokens", "gen_ai.usage.input_tokens")
    out = _usage_number(usage, "output_tokens", "completion_tokens", "gen_ai.usage.output_tokens")
    cached = _usage_number(
        usage, "cache_read.input_tokens", "cache_read_tokens", "cached_tokens",
        "prompt_cache_hit_tokens",
    )
    if inp is not None or out is not None:
        source = "reported" if cached is not None else "reported_partial"
        return inp or 0, out or 0, cached or 0, source, "L2", False, False
    if tokenizer is not None and text:
        tokens = _to_int(tokenizer(text))
        return tokens, 0, 0, "estimated_tokenizer", "L3", True, False
    if text:
        return estimate_tokens(text), 0, 0, "estimated_heuristic", "L4", True, False
    return 0, 0, 0, "missing", "missing", False, True


# ── trace 归一 ──────────────────────────────────────────────────────────────

def _normalize_trace(*, session_id, date, timestamp="", model=None, inp=0, out=0,
                     cached=0, tokens_source="missing", layer="missing", estimated=False,
                     missing=False, text="", status="success", request_id="",
                     duration_ms=0, seq=0, tokenizer_note=""):
    bare = _bare_model(model)
    total = (inp or 0) + (out or 0)
    # 缓存折扣与 ca_core.CACHE_DISCOUNT 保持一致，但不 import 门面以免循环依赖。
    effective = max(total - (cached or 0) * 0.1, 0)
    is_error = str(status).lower() in {"error", "failed", "failure"}
    trace = {
        "trace_id": f"generic:{session_id}:{seq}:{request_id}",
        "pid": f"generic:{session_id}",
        "date": date,
        "started_at": _iso_of(timestamp) or date,
        "ended_at": _iso_of(timestamp) or date,
        "duration_ms": _to_int(duration_ms),
        "status": "error" if is_error else "success",
        "session_id": session_id or "generic-unknown",
        "total_tokens": total,
        "input_tokens": inp or 0,
        "output_tokens": out or 0,
        "cached_tokens": cached or 0,
        "call_count": 1,
        "models": [bare],
        "model_name": bare,
        "exec_model": bare,
        "model_key": f"{CHANNEL}:{bare}",
        "channel": CHANNEL,
        "raw_model": f"{CHANNEL}:{bare}",
        "total_cost": 0.0,
        "input_cost": 0.0,
        "output_cost": 0.0,
        "effective_tokens": int(effective),
        "effective_cost": 0.0,
        "is_free": False,
        "is_error": is_error,
        # ── token 分层（A11/A12/A13）──
        "tokens_source": tokens_source,
        "tokens_source_layer": layer,
        "_tokens_estimated": bool(estimated),
        "_tokens_missing": bool(missing),
        "_tokens_note": tokenizer_note,
        "_dialogue_text": text or "",
    }
    if request_id:
        trace["_request_id"] = request_id
    return trace


# ── JSONL reader ────────────────────────────────────────────────────────────

def _jsonl_record(obj):
    """从一条通用 JSONL 事件里抽出最小字段；不认识的事件返回 None。"""
    if not isinstance(obj, dict):
        return None
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    usage = message.get("usage") or obj.get("usage") or obj.get("usage_metadata") or obj.get("metrics")
    text = _text_of(message.get("content") or obj.get("content") or obj.get("text"))
    if not isinstance(usage, dict) and not text:
        return None
    if message and message.get("role") not in (None, "assistant"):
        return None
    timestamp = (obj.get("timestamp") or obj.get("created_at") or obj.get("time")
                 or message.get("timestamp"))
    model = (message.get("model") or obj.get("model") or obj.get("modelID")
             or message.get("modelID"))
    session_id = (obj.get("session_id") or obj.get("sessionId")
                  or obj.get("conversation_id") or obj.get("sessionID") or "")
    return {
        "timestamp": timestamp,
        "date": _date_of(timestamp),
        "model": model,
        "usage": usage if isinstance(usage, dict) else {},
        "text": text,
        "session_id": str(session_id),
        "request_id": str(obj.get("request_id") or obj.get("requestId") or obj.get("id") or ""),
        "status": obj.get("status") or obj.get("type") or "success",
        "duration_ms": obj.get("duration_ms") or obj.get("latency_ms") or 0,
    }


def _collect_jsonl(paths, start_date, end_date, tokenizer=None):
    records = []
    skipped_bad = 0
    seen_request_ids = set()
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        skipped_bad += 1
                        continue
                    rec = _jsonl_record(obj)
                    if rec is None or not rec["date"]:
                        continue
                    if rec["date"] < start_date or rec["date"] > end_date:
                        continue
                    if rec["request_id"] and rec["request_id"] in seen_request_ids:
                        continue
                    if rec["request_id"]:
                        seen_request_ids.add(rec["request_id"])
                    records.append(rec)
        except OSError as exc:
            print(f"[WARN] generic source: 跳过不可读文件 {path}: {exc}", file=sys.stderr)
    traces = []
    for rec in records:
        inp, out, cached, source, layer, estimated, missing = _token_fields(
            rec["usage"], rec["text"], tokenizer
        )
        traces.append(_normalize_trace(
            session_id=rec["session_id"], date=rec["date"], timestamp=rec["timestamp"],
            model=rec["model"], inp=inp, out=out, cached=cached,
            tokens_source=source, layer=layer, estimated=estimated, missing=missing,
            text=rec["text"], status=rec["status"], request_id=rec["request_id"],
            duration_ms=rec["duration_ms"], seq=len(traces),
        ))
    return traces, skipped_bad


# ── SQLite reader（五 profile，白名单列，只读，锁冲突显式降级）────────────

def _read_opencode_current(conn, start_date, end_date, tokenizer=None):
    rows = conn.execute(
        "SELECT s.id, s.tokens_input, s.tokens_output, s.tokens_reasoning, "
        "s.tokens_cache_read, s.tokens_cache_write, m.time_created, "
        "json_extract(m.data,'$.modelID') FROM session_v2 s "
        "JOIN session_message m ON m.session_id = s.id"
    ).fetchall()
    traces = []
    for sid, ti, to, _reasoning, cr, _cw, ts, model in rows:
        date = _date_of(ts)
        if not date or not (start_date <= date <= end_date):
            continue
        traces.append(_normalize_trace(
            session_id=sid, date=date, timestamp=ts, model=model,
            inp=_to_int(ti), out=_to_int(to), cached=_to_int(cr),
            tokens_source="reported", layer="L2", seq=len(traces),
        ))
    return traces


def _read_opencode_legacy(conn, start_date, end_date, tokenizer=None):
    rows = conn.execute(
        "SELECT s.id, s.prompt_tokens, s.completion_tokens, s.time_created, "
        "json_extract(m.data,'$.modelID') FROM session s "
        "JOIN message m ON m.session_id = s.id"
    ).fetchall()
    traces = []
    for sid, ti, to, ts, model in rows:
        date = _date_of(ts)
        if not date or not (start_date <= date <= end_date):
            continue
        traces.append(_normalize_trace(
            session_id=sid, date=date, timestamp=ts, model=model,
            inp=_to_int(ti), out=_to_int(to), tokens_source="reported",
            layer="L2", seq=len(traces),
        ))
    return traces


def _read_devin(conn, start_date, end_date, tokenizer=None):
    rows = conn.execute(
        "SELECT s.id, json_extract(n.chat_message,'$.metadata.request_id'), "
        "json_extract(n.chat_message,'$.metadata.generation_model'), "
        "json_extract(n.chat_message,'$.metadata.created_at'), "
        "json_extract(n.chat_message,'$.metadata.metrics.input_tokens'), "
        "json_extract(n.chat_message,'$.metadata.metrics.output_tokens'), "
        "json_extract(n.chat_message,'$.metadata.metrics.cache_read_tokens') "
        "FROM sessions s JOIN message_nodes n ON n.session_id = s.id "
        "WHERE s.hidden = 0 AND json_extract(n.chat_message,'$.role') = 'assistant'"
    ).fetchall()
    traces, seen = [], set()
    for sid, req, model, ts, ti, to, cr in rows:
        key = req or f"{sid}:{ts}"
        if key in seen:
            continue  # requestId 全局去重（红线 4）
        seen.add(key)
        date = _date_of(ts)
        if not date or not (start_date <= date <= end_date):
            continue
        traces.append(_normalize_trace(
            session_id=sid, date=date, timestamp=ts, model=model,
            inp=_to_int(ti), out=_to_int(to), cached=_to_int(cr),
            tokens_source="reported", layer="L2", request_id=str(req or ""),
            seq=len(traces),
        ))
    return traces


def _read_cursor(conn, start_date, end_date, tokenizer=None):
    rows = conn.execute(
        "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'"
    ).fetchall()
    traces = []
    for key, raw in rows:
        try:
            data = json.loads(raw) if isinstance(raw, str) else {}
        except json.JSONDecodeError:
            continue
        tc = data.get("tokenCount") or {}
        date = _date_of(data.get("createdAt"))
        if not date or not (start_date <= date <= end_date):
            continue
        traces.append(_normalize_trace(
            session_id=key.split("::")[0].replace("bubbleId:", ""), date=date,
            timestamp=data.get("createdAt"), model=data.get("model"),
            inp=_to_int(tc.get("inputTokens")), out=_to_int(tc.get("outputTokens")),
            tokens_source="reported_partial", layer="L2", seq=len(traces),
            tokenizer_note="cursor 上游不记 cache，缓存口径缺失（reported_partial）",
        ))
    return traces


def _read_openclaw_gateway(conn, start_date, end_date, tokenizer=None):
    """Gateway transcript_events：token 藏在 event_json 内，无独立列（V2 实测）。"""
    rows = conn.execute(
        "SELECT session_id, seq, event_json FROM transcript_events "
        "ORDER BY session_id, seq"
    ).fetchall()
    traces = []
    for sid, seq, raw in rows:
        try:
            event = json.loads(raw) if isinstance(raw, str) else {}
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        role = event.get("role") or message.get("role")
        if role not in (None, "assistant"):
            continue
        timestamp = event.get("timestamp") or event.get("created_at") or message.get("timestamp")
        date = _date_of(timestamp)
        if not date or not (start_date <= date <= end_date):
            continue
        usage = event.get("usage") or message.get("usage") or {}
        text = _text_of(message.get("content") or event.get("content"))
        inp, out, cached, source, layer, estimated, missing = _token_fields(
            usage, text, tokenizer
        )
        traces.append(_normalize_trace(
            session_id=str(sid), date=date, timestamp=timestamp,
            model=event.get("model") or message.get("model"),
            inp=inp, out=out, cached=cached, tokens_source=source, layer=layer,
            estimated=estimated, missing=missing, text=text,
            request_id=str(event.get("request_id") or event.get("id") or ""),
            seq=seq if isinstance(seq, int) else len(traces),
        ))
    return traces


_PROFILE_READERS = {
    "opencode_current": _read_opencode_current,
    "opencode_legacy": _read_opencode_legacy,
    "devin": _read_devin,
    "cursor": _read_cursor,
    "openclaw_gateway": _read_openclaw_gateway,
}


def _collect_sqlite(paths, start_date, end_date, tokenizer=None):
    traces, profiles, errors = [], {}, []
    for path in paths:
        try:
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
        except sqlite3.Error as exc:
            errors.append(f"{path.name}: 只读打开失败 {exc}")
            continue
        try:
            profile = detect_sqlite_profile(conn)
            if profile is None:
                continue
            reader = _PROFILE_READERS[profile]
            got = reader(conn, start_date, end_date, tokenizer)
            profiles[profile] = profiles.get(profile, 0) + len(got)
            traces.extend(got)
        except sqlite3.OperationalError as exc:
            # 锁冲突必须可观测：不静默返回空当「无数据」（V1 实测 SQLITE_BUSY）。
            errors.append(f"{path.name}: 只读查询失败（锁冲突/格式异常）{exc}")
        finally:
            conn.close()
    for message in errors:
        print(f"[WARN] generic SQLite reader: {message}；已跳过该库，未按无数据处理",
              file=sys.stderr)
    return traces, profiles, len(errors)


# ── 会话派生与公共入口 ──────────────────────────────────────────────────────

def _derive_sessions(traces):
    by_sid = defaultdict(list)
    for trace in traces:
        by_sid[trace["session_id"]].append(trace)
    sessions = []
    for sid, group in by_sid.items():
        group.sort(key=lambda t: (t["date"], t["started_at"]))
        dialogue = "\n".join(t.get("_dialogue_text", "") for t in group if t.get("_dialogue_text"))
        sessions.append({
            "id": sid,
            "cwd": "",  # 不把绝对路径写进报告（R3：只写「通用数据源」）
            "title": "通用数据源",
            "custom_title": "",
            "created_date": group[0]["date"],
            "mode": "generic",
            "model": group[0].get("raw_model") or "",
            "_dialogue_text": dialogue,
        })
    return sessions


def collect_generic(start_date, end_date, home=None, env=None, tokenizer=None):
    """返回 (traces, db_data)，与 docs/ADAPTERS.md §7.1 契约一致。"""
    environ = os.environ if env is None else env
    root = home or resolve_generic_home(environ)
    projects = resolve_generic_projects_root(root, environ)
    jsonl_paths = sorted(_walk_files(projects, JSONL_SUFFIXES))
    sqlite_paths = sorted(_walk_files(root, SQLITE_SUFFIXES))

    jsonl_traces, skipped_bad = _collect_jsonl(jsonl_paths, start_date, end_date, tokenizer)
    sqlite_traces, profiles, sqlite_errors = _collect_sqlite(
        sqlite_paths, start_date, end_date, tokenizer
    )
    traces = jsonl_traces + sqlite_traces

    layer_summary = defaultdict(lambda: {"traces": 0, "tokens": 0})
    for trace in traces:
        layer = trace.get("tokens_source_layer", "missing")
        layer_summary[layer]["traces"] += 1
        layer_summary[layer]["tokens"] += trace.get("total_tokens", 0)

    relative_paths = []
    for path in jsonl_paths + sqlite_paths:
        try:
            relative_paths.append(str(path.relative_to(root)))
        except ValueError:
            relative_paths.append(path.name)

    meta = {
        "source": CHANNEL,
        "supports_cost": SUPPORTS_COST,
        "tokens_source": "estimated",
        "tokens_source_layer_summary": dict(layer_summary),
        "tokens_notice": (
            "通用数据源 token 分层展示：L2 上游回传 / L3 tokenizer / L4 字符启发式互不合并；"
            "中文 L4（字符÷4）相对 tokenizer 平均绝对偏差 68.3%，仅供量级参考，不可当账单引用。"
        ),
        "collected_paths": relative_paths,
        "skipped_bad_lines": skipped_bad,
        "sqlite_profiles": profiles,
        "sqlite_errors": sqlite_errors,
        "otel_pin": OTEL_PIN_COMMIT,
        "openclaw_layout": detect_openclaw_layout(env=environ),
    }
    db_data = {
        "sessions": _derive_sessions(traces),
        "automation_runs": [],
        "session_credits": [],
        "skill_usage": {"skills": {}, "active_days": sorted({t["date"] for t in traces})},
        "outputs": [],
        "memory_logs": {},
        "_meta": meta,
    }
    print(
        f"[INFO] 通用数据源：{len(traces)} 条 trace / {len(db_data['sessions'])} 个会话"
        f"（JSONL {len(jsonl_traces)} / SQLite {len(sqlite_traces)}；"
        f"坏行跳过 {skipped_bad}；金额恒为 0）",
        file=sys.stderr,
    )
    return traces, db_data
