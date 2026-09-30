# -*- coding: utf-8 -*-
"""L0 · 千问办公专有维度（技能调用 / 交付产出 / 自动化运行 / 后台运行）测试。

千问办公对这三件事的侦测机制与 WorkBuddy 完全不同：

  | 维度       | WorkBuddy                        | 千问办公                                        |
  |------------|----------------------------------|-------------------------------------------------|
  | 技能调用   | usage-log.json（含日期窗口）      | 转录 tool_use name="Skill" → input.skill        |
  | 自动化运行 | workbuddy.db automation + runs   | agents.db 的 scheduled_tasks + task_run_logs    |
  | 产出文件   | 扫 ~/WorkBuddy/ 会话目录          | present_files 调用 ∪ <cwd>/outputs 磁盘扫描      |

`skill-usage.json` 只有累计 usageCount（本机实测 2 条 vs 转录 17 次调用，低估约 8.5 倍），
故本适配器一律以转录为权威源，该文件不进统计。

⚠️ 全部用例走 QWENWORK_HOME / QWENWORK_DB + tmp_path，绝不读取真实的 ~/.qwenworkcn/。
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import allure

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for _p in (str(SKILL_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.qwenwork import (  # noqa: E402
    _deliverable,
    aggregate_skill_usage,
    collect_deliverables,
    collect_qwenwork,
    load_automation_runs,
    load_background_runs,
)

pytestmark = [pytest.mark.unit, pytest.mark.integration]

PY = sys.executable
START, END = "2026-02-01", "2026-02-28"
TS = "2026-02-16T10:00:00.000Z"


# ── fixture 构造 ────────────────────────────────────────────────────────────

def _line(obj):
    return json.dumps(obj, ensure_ascii=False)


def _user(sid, ts, text, cwd):
    return _line({"type": "user", "sessionId": sid, "timestamp": ts, "cwd": cwd,
                  "humanInput": {"text": text, "mode": "prompt"},
                  "message": {"role": "user",
                              "content": [{"type": "text", "text": text}]}})


def _assistant(sid, ts, rid, blocks, cwd, model="flash"):
    out = []
    for blk in blocks:
        out.append(_line({"type": "assistant", "sessionId": sid, "timestamp": ts,
                          "cwd": cwd, "version": "1.1.59", "isSidechain": False,
                          "requestTokenAnchor": {"request": "rq", "response": "rs",
                                                 "requestId": rid},
                          "message": {"role": "assistant", "model": model,
                                      "content": [blk]}}))
    return out


def _tool(name, **inp):
    return {"type": "tool_use", "id": "call_1", "name": name, "input": inp}


def _write_session(home, slug, sid, lines):
    d = Path(home) / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / (sid + ".jsonl")).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_db(path, with_automation=True):
    """造一个只含本项目用到的表的最小 agents.db。"""
    con = sqlite3.connect(str(path))
    con.executescript(
        "CREATE TABLE scheduled_tasks (id TEXT PRIMARY KEY, name TEXT, enabled INT);"
        "CREATE TABLE task_run_logs (id INTEGER PRIMARY KEY, task_id TEXT, run_at INT,"
        " created_at INT, status TEXT, duration_ms INT);"
        "CREATE TABLE nudge_logs (id INTEGER PRIMARY KEY, type TEXT, duration_ms INT,"
        " is_error INT, created_at INT);")
    if with_automation:
        con.executemany("INSERT INTO scheduled_tasks VALUES (?,?,?)",
                        [("t1", "每日晨报", 1), ("t2", "已停用的任务", 0)])
        # run_at / created_at 是秒级 epoch（2026-02-16 02:00Z / 02-20、含一条窗口外）
        con.executemany("INSERT INTO task_run_logs VALUES (NULL,?,?,?,?,?)",
                        [("t1", 1771207200, 1771207200, "success", 1200),
                         ("t1", 1771545600, 1771545600, "failed", 900),
                         ("t2", 1771207200, 1771207200, "success", 10),
                         ("t1", 1774656000, 1774656000, "success", 10)])   # 窗口外
    con.executemany("INSERT INTO nudge_logs VALUES (NULL,?,?,?,?)",
                    [("memory_saved", 5000, 0, 1771207200),
                     ("reflection_memory_error", 8000, 1, 1771300000),
                     ("reflection_memory_error", 3000, 1, 1771300100),
                     ("memory_saved", 100, 0, 1774656000)])               # 窗口外
    con.commit()
    con.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "qwhome"
    (home / "projects").mkdir(parents=True)
    (home / "logs" / "runs").mkdir(parents=True)
    db = tmp_path / "agents.db"
    _make_db(db)
    monkeypatch.setenv("QWENWORK_HOME", str(home))
    monkeypatch.setenv("QWENWORK_DB", str(db))
    return {"home": home, "db": db, "tmp": tmp_path}


# ── 一、技能调用 ────────────────────────────────────────────────────────────

@allure.feature("千问办公维度")
@allure.story("技能调用")
@allure.title("tool_use name=Skill → skill_usage 契约四字段齐全")
def test_skill_calls_from_transcript(env):
    home = env["home"]
    cwd = str(env["tmp"] / "ws" / "chat1")
    rid_a = "aaaaaaaa-1111-1111-1111-111111111111"
    rid_b = "bbbbbbbb-2222-2222-2222-222222222222"
    lines = [_user("s1", TS, "帮我发布这个页面", cwd)]
    lines += _assistant("s1", TS, rid_a,
                        [{"type": "text", "text": "先调 qw-pages"},
                         _tool("Skill", skill="qw-pages")], cwd=cwd)
    lines += _assistant("s1", TS, rid_b,
                        [_tool("Skill", skill="qw-pages"),
                         _tool("Skill", skill="github")], cwd=cwd)
    _write_session(home, "C--ws-chat1", "s1", lines)

    _, db_data = collect_qwenwork(START, END)
    skills = db_data["skill_usage"]["skills"]
    assert set(skills) == {"qw-pages", "github"}
    # 同一技能两次调用 → 计 2 次、日期去重成 1 天
    assert skills["qw-pages"]["usage_count_in_range"] == 2
    assert skills["qw-pages"]["recent_dates_in_range"] == ["2026-02-16"]
    assert skills["qw-pages"]["first_seen"] == skills["qw-pages"]["last_used"] == "2026-02-16"
    for e in skills.values():                       # 与 ca_sources.collect_skill_usage 同形
        assert {"last_used", "first_seen", "recent_dates_in_range",
                "usage_count_in_range"} <= set(e)


@allure.feature("千问办公维度")
@allure.story("技能调用")
@allure.title("窗口外的技能调用不计入本期")
def test_skill_calls_window_filtered(env):
    home = env["home"]
    cwd = str(env["tmp"] / "ws" / "chat2")
    rid = "cccccccc-3333-3333-3333-333333333333"
    lines = [_user("s2", "2026-01-05T10:00:00.000Z", "去年的调用", cwd)]
    lines += _assistant("s2", "2026-01-05T10:00:05.000Z", rid,
                        [_tool("Skill", skill="github")], cwd=cwd)
    _write_session(home, "C--ws-chat2", "s2", lines)
    _, db_data = collect_qwenwork(START, END)
    assert db_data["skill_usage"]["skills"] == {}


@allure.feature("千问办公维度")
@allure.story("技能调用")
@allure.title("聚合函数按调用次数计、按日期去重")
def test_aggregate_skill_usage_counts_calls_not_dates():
    sessions = [{"_skill_calls": [("github", "2026-02-16"), ("github", "2026-02-16"),
                                  ("github", "2026-02-17")]}]
    got = aggregate_skill_usage(sessions, {"2026-02-16", "2026-02-17"})
    g = got["skills"]["github"]
    assert g["usage_count_in_range"] == 3
    assert g["recent_dates_in_range"] == ["2026-02-16", "2026-02-17"]
    assert g["first_seen"] == "2026-02-16" and g["last_used"] == "2026-02-17"
    assert got["active_days"] == ["2026-02-16", "2026-02-17"]


# ── 二、交付产出 ────────────────────────────────────────────────────────────

@allure.feature("千问办公维度")
@allure.story("交付产出")
@allure.title("present_files 调用 → §九 契约（file_name/extension/size_bytes/date）")
def test_deliverables_from_present_files(env):
    home = env["home"]
    out_dir = env["tmp"] / "ws" / "chat3" / "outputs"
    out_dir.mkdir(parents=True)
    f = out_dir / "报告.html"
    f.write_text("<html>x</html>", encoding="utf-8")
    cwd = str(out_dir.parent)
    rid = "dddddddd-4444-4444-4444-444444444444"
    lines = [_user("s3", TS, "出个报告", cwd)]
    lines += _assistant("s3", TS, rid,
                        [_tool("mcp__qw-builtin__qwenwork_file_present_files",
                               files=[{"file_path": str(f)}])], cwd=cwd)
    _write_session(home, "C--ws-chat3", "s3", lines)

    _, db_data = collect_qwenwork(START, END)
    outs = db_data["outputs"]
    assert [o["file_name"] for o in outs] == ["报告.html"]
    o = outs[0]
    assert o["extension"] == "html"
    assert o["size_bytes"] == f.stat().st_size > 0
    assert o["date"] == "2026-02-16"          # 调用时刻，不是 mtime
    assert o["source"] == "present_files"


@allure.feature("千问办公维度")
@allure.story("交付产出")
@allure.title("磁盘兜底扫描：跳过备份/临时/隐藏文件，且不与 present_files 双计")
def test_disk_scan_skips_backups_and_dedupes(env):
    out_dir = env["tmp"] / "ws" / "chat4" / "outputs"
    out_dir.mkdir(parents=True)
    (out_dir / "keep.md").write_text("a", encoding="utf-8")
    (out_dir / "data.json.bak.20260923").write_text("x", encoding="utf-8")
    (out_dir / "tmp.tmp").write_text("x", encoding="utf-8")
    (out_dir / "~$doc.docx").write_text("x", encoding="utf-8")
    (out_dir / ".hidden").write_text("x", encoding="utf-8")
    (out_dir / "sub").mkdir()                  # 目录本身不进清单
    # mtime 必须落在被测窗口内（Feb 2026），否则会被日期过滤掉——用固定时间而非 now
    import calendar
    feb16 = calendar.timegm((2026, 2, 16, 10, 0, 0, 0, 0, 0))
    os.utime(out_dir / "keep.md", (feb16, feb16))

    got = collect_deliverables([{"cwd": str(out_dir.parent)}], START, END)
    assert [g["file_name"] for g in got] == ["keep.md"]
    g0 = got[0]
    assert g0["source"] == "disk_scan"
    assert g0["date"] == "2026-02-16"          # mtime 落在窗口内

    # 同一路径 present_files 已记 → 磁盘扫描不双计，且保留调用日期
    dup = collect_deliverables([{"cwd": str(out_dir.parent),
                                 "_presents": [(str(out_dir / "keep.md"), "2026-02-16")]}],
                               START, END)
    assert len(dup) == 1 and dup[0]["source"] == "present_files"
    assert dup[0]["date"] == "2026-02-16"


@allure.feature("千问办公维度")
@allure.story("交付产出")
@allure.title("文件已删除 → size 0 且 missing 标记，不抛异常")
def test_deliverable_missing_file_is_graceful(tmp_path):
    d = _deliverable(tmp_path / "gone.md", "2026-02-16")
    assert d["size_bytes"] == 0 and d["missing"] is True
    assert d["file_name"] == "gone.md" and d["date"] == "2026-02-16"


# ── 三、自动化与后台运行 ────────────────────────────────────────────────────

@allure.feature("千问办公维度")
@allure.story("自动化运行")
@allure.title("scheduled_tasks ⟕ task_run_logs → 五字段契约（含 result_success）")
def test_automation_runs_contract(env):
    runs = load_automation_runs(env["db"], START, END)
    assert len(runs) == 3, "窗口外那条应被过滤"
    for r in runs:
        # collect_usage_data 汇总时是 r["result_success"] 硬索引，缺 key 会 KeyError
        assert {"automation_id", "automation_name", "auto_status",
                "result_success", "created_date"} <= set(r)
    daily = [r for r in runs if r["automation_name"] == "每日晨报"]
    assert len(daily) == 2 and {r["result_success"] for r in daily} == {True, False}
    assert all(r["auto_status"] == "ACTIVE" for r in daily)      # enabled=1
    stopped = [r for r in runs if r["automation_name"] == "已停用的任务"]
    assert len(stopped) == 1 and stopped[0]["auto_status"] == "PAUSED"  # 不算「执行中自动化」


@allure.feature("千问办公维度")
@allure.story("自动化运行")
@allure.title("无定时任务的机器上返回 []（真 0，不是采不到）")
def test_automation_runs_empty_when_no_rows(env):
    empty = env["tmp"] / "empty.db"
    con = sqlite3.connect(str(empty))
    con.executescript("CREATE TABLE scheduled_tasks (id TEXT, name TEXT, enabled INT);"
                      "CREATE TABLE task_run_logs (id INTEGER, task_id TEXT, run_at INT,"
                      " created_at INT, status TEXT, duration_ms INT);")
    con.commit()
    con.close()
    assert load_automation_runs(empty, START, END) == []
    # 库缺失 / 非 sqlite 时同样优雅降级，不抛
    assert load_automation_runs(env["tmp"] / "nope.db", START, END) == []


@allure.feature("千问办公维度")
@allure.story("后台运行")
@allure.title("nudge_logs 只出全局计数，不混进 automation_runs")
def test_background_runs_summary_separate(env):
    br = load_background_runs(env["db"], START, END)
    assert br["count"] == 3, "窗口外那条 nudge 应被过滤"
    assert br["error_count"] == 2
    assert br["types"]["reflection_memory_error"] == 2
    assert br["total_duration_ms"] == 8000 + 3000 + 5000
    # 关键边界：后台运行绝不进 automation_runs（会话归因率太低，会造出一堆 unknown 组）
    assert all("memory" not in (r["automation_name"] or "").lower()
               for r in load_automation_runs(env["db"], START, END))


# ── 四、端到端：三个维度真的流到采集与报告 ──────────────────────────────────

@allure.feature("千问办公维度")
@allure.story("端到端")
@allure.title("同一会话的转录散落多个文件时不双计调用与 token")
def test_cross_transcript_duplicate_not_double_counted(env):
    """会话被恢复 / 续写时，同一 sessionId 与同一 requestId 会在两份文件里各存一份。

    本机月报实测过这个坑：1022 条 trace 只有 834 个唯一 requestId（虚高 22%）。
    """
    home = env["home"]
    cwd = str(env["tmp"] / "ws" / "chatdup")
    rid = "55555555-5555-5555-5555-555555555555"
    shared = [_user("sdup", TS, "同一会话的提问内容在这里", cwd)]
    shared += _assistant("sdup", TS, rid,
                         [{"type": "text", "text": "一份有长度的回复，重复出现时不该再算一次"}],
                         cwd=cwd)
    _write_session(home, "C--ws-chatdup", "sdup", shared)
    # 另一份转录里出现了同一个 sessionId 的同一段内容（模拟会话被恢复后回写）
    _write_session(home, "C--ws-chatdup-alias", "sdup-part2",
                   [_line({"type": "runtime-config", "sessionId": "sdup",
                           "model": "flash", "timestamp": TS})] + shared)

    traces, db_data = collect_qwenwork(START, END)
    assert len(traces) == 1, "同一 requestId 只能算一次调用"
    assert len(db_data["sessions"]) == 1, "同一 sessionId 不该产出两条会话"
    s = db_data["sessions"][0]
    assert s["_human_turns"] == 1, "重复转录的提问轮数取大值，不求和"
    assert sum(t["total_tokens"] for t in traces) == traces[0]["total_tokens"]


@allure.feature("千问办公维度")
@allure.story("端到端")
@allure.title("「有 token 活动」单一口径：0-token 会话不进 §五 任务计数")
def test_token_active_predicate_shared_by_all_sections(env):
    """回归护栏：§一 会话总数补充 / §4.2 分布 / §五·§十 任务计数必须同源。

    曾经各节自己定义「有活动」（一处看有 trace、一处看有 token），千问办公这种
    字符估算口径下偶发 0-token 会话，就会出现 §5=8 而 §1/§4.2=7 的全文打架。
    """
    from ca_core import token_active_session_ids
    home = env["home"]
    cwd = str(env["tmp"] / "ws" / "chat9")
    # 会话 A：正常有 token；会话 B：只有一条空响应（估算 token = 0）
    lines_a = [_user("s9a", TS, "有内容的会话", cwd)]
    lines_a += _assistant("s9a", TS, "99999999-1111-1111-1111-111111111111",
                          [{"type": "text", "text": "这是一段有长度的回复内容"}], cwd=cwd)
    _write_session(home, "C--ws-chat9", "s9a", lines_a)
    # 会话 B：没有用户输入（input_ctx=0）+ 空响应（out=0）→ 该 trace 总 token = 0
    lines_b = _assistant("s9b", TS, "88888888-2222-2222-2222-222222222222",
                         [{"type": "text", "text": ""}], cwd=cwd)
    _write_session(home, "C--ws-chat9", "s9b", lines_b)

    traces, db_data = collect_qwenwork(START, END)
    active = token_active_session_ids(traces)
    assert "s9a" in active
    assert "s9b" not in active, "0-token 会话不该算「有 token 活动」"
    counted = {s["id"] for s in db_data["sessions"] if s["id"] in active}
    assert len(counted) == 1


@allure.feature("千问办公维度")
@allure.story("端到端")
@allure.title("collect_usage_data → summary 与 meta 带上新维度")
def test_cli_carries_new_dimensions(env):
    home = env["home"]
    out_dir = env["tmp"] / "ws" / "chat5" / "outputs"
    out_dir.mkdir(parents=True)
    (out_dir / "交付.md").write_text("x" * 32, encoding="utf-8")
    cwd = str(out_dir.parent)
    rid = "eeeeeeee-5555-5555-5555-555555555555"
    lines = [_user("s5", TS, "调技能并交付", cwd)]
    lines += _assistant("s5", TS, rid,
                        [_tool("Skill", skill="skill-inventory"),
                         _tool("mcp__qw-builtin__qwenwork_file_present_files",
                               files=[{"file_path": str(out_dir / "交付.md")}])], cwd=cwd)
    _write_session(home, "C--ws-chat5", "s5", lines)

    out_json = env["tmp"] / "dims.json"
    r = subprocess.run(
        [PY, str(SCRIPTS_DIR / "collect_usage_data.py"), "--source", "qwenwork",
         "--start", START, "--end", END, "-o", str(out_json)],
        capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ), cwd=str(SKILL_DIR))
    assert r.returncode == 0, r.stderr[-800:]
    d = json.loads(out_json.read_text(encoding="utf-8"))
    assert d["summary"]["skills_used"] == 1
    assert d["summary"]["total_outputs"] >= 1
    # 定时任务确实存在 → automation_runs 非空且能进 §八（summary 不 KeyError）
    assert d["summary"]["total_automation_runs"] == 3
    assert d["summary"]["successful_automation_runs"] == 2
    assert d["meta"]["single_channel"] is True
    assert d["meta"]["background_runs"]["count"] == 3
    assert d["meta"]["skill_usage_source"] == "transcript:tool_use.Skill"

    out_md = env["tmp"] / "dims.md"
    r2 = subprocess.run(
        [PY, str(SCRIPTS_DIR / "generate_report.py"), str(out_json),
         "--output", str(out_md), "--format", "markdown"],
        capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ), cwd=str(SKILL_DIR))
    assert r2.returncode == 0, r2.stderr[-800:]
    md = out_md.read_text(encoding="utf-8")
    assert "skill-inventory" in md                 # §七 有内容了
    assert "交付.md" in md                         # §九 有内容了
    assert "每日晨报" in md                        # §八 用上了 automation_runs
    assert "后台自动运行" in md                    # §一 那一行
    assert "用户提问轮数（真实计数）" in md
    assert "估算请求数" not in md                  # 有真值就不再摆估算行
