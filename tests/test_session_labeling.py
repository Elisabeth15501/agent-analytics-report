# -*- coding: utf-8 -*-
"""会话标题标注回归测试：孤儿 trace 与真·无标题会话必须区分（v1.3.0 修复）。

核心不变量：
  - trace 引用的 session_id 在本地会话库查不到（孤儿）→ 合并为单一「未关联会话」行
  - 会话存在于本地库但无标题 → 标「未命名会话」
  - 「未命名会话」绝不应包含孤儿 trace，孤儿也不应被误告警为「未命名高成本会话」
"""
import pytest


def _trace(sid, **kw):
    t = {
        "session_id": sid,
        "total_tokens": 1000, "input_tokens": 600, "output_tokens": 400,
        "cached_tokens": 0, "total_cost": 0.0, "effective_tokens": 1000,
        "effective_cost": 0.01, "call_count": 1, "exec_model": "glm-5.2",
        "date": "2026-09-01",
    }
    t.update(kw)
    return t


def test_orphan_traces_merge_to_single_row(collector_module):
    sessions = [{"id": "s1", "title": "真实会话", "custom_title": "", "task_type": "写代码"}]
    traces = [
        _trace("orphanA"), _trace("orphanB"), _trace("orphanC"),
        _trace("s1", total_tokens=500, effective_tokens=500, effective_cost=0.05),
    ]
    rows = collector_module.aggregate_by_session(traces, sessions)["rows"]
    orphan_rows = [r for r in rows if r["title"] == collector_module.ORPHAN_LABEL]
    unnamed_rows = [r for r in rows if r["title"] == collector_module.UNNAMED_LABEL]
    assert len(orphan_rows) == 1, "3 个孤儿 trace 应合并为 1 行「未关联会话」"
    assert len(unnamed_rows) == 0, "本期无真·无标题会话，不应出现「未命名会话」"
    o = orphan_rows[0]
    assert o["session_id"] == collector_module.ORPHAN_LABEL
    assert o["effective_tokens"] == 3000, "孤儿行应汇总 3 个孤儿 trace 的 token"


def test_truly_untitled_session_labeled_unnamed(collector_module):
    sessions = [{"id": "s2", "title": "", "custom_title": "", "task_type": "其他"}]
    traces = [_trace("s2")]
    rows = collector_module.aggregate_by_session(traces, sessions)["rows"]
    assert len(rows) == 1
    assert rows[0]["title"] == collector_module.UNNAMED_LABEL
    assert rows[0]["session_id"] == "s2"


def test_real_session_with_title_not_relabeled(collector_module):
    sessions = [{"id": "s3", "title": "命名会话", "custom_title": "", "task_type": "其他"}]
    traces = [_trace("s3")]
    rows = collector_module.aggregate_by_session(traces, sessions)["rows"]
    assert len(rows) == 1
    assert rows[0]["title"] == "命名会话"


def test_top_tasks_orphan_merge(collector_module):
    sessions = [{"id": "s1", "title": "真实会话", "custom_title": "", "task_type": "写代码"}]
    traces = [_trace("orphanA"), _trace("orphanB"), _trace("s1")]
    rows = collector_module.aggregate_top_tasks(traces, sessions, top_n=10)
    orphan_rows = [r for r in rows if r["title"] == collector_module.ORPHAN_LABEL]
    assert len(orphan_rows) == 1, "Top-N 中孤儿也应合并为单一「未关联会话」行"


def test_cache_untitled_excludes_orphan(report_module):
    """孤儿行（标题=未关联会话）不得进入「未命名高成本会话」告警列表。"""
    data = {
        "summary": {
            "total_cached_tokens": 0, "total_input_tokens": 100,
            "total_effective_cost": 999,
        },
        "traces": [{"session_id": "orphanX", "cached_tokens": 0, "input_tokens": 100,
                    "effective_tokens": 100, "effective_cost": 50.0}],
        "sessions": [],
        "session_stats": {"rows": [{
            "session_id": report_module.ORPHAN_LABEL,
            "title": report_module.ORPHAN_LABEL,
            "task_type": "其他", "effective_cost": 100.0, "calls": 1,
            "first_date": "2026-09-01",
        }]},
    }
    payload = report_module._compute_cache_and_untitled(data)
    untitled_titles = [u["title"] for u in payload["untitled"]]
    assert report_module.ORPHAN_LABEL not in untitled_titles
    # 缓存健康度里孤儿也不得显示为「未命名会话」
    cache_titles = [c["title"] for c in payload["cache_health"]["items"]]
    assert report_module.UNNAMED_LABEL not in cache_titles
