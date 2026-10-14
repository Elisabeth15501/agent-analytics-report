# -*- coding: utf-8 -*-
"""TP4 合规 · --task-classifier llm 出站确认门禁测试

覆盖 build_task_classifier_with_consent 的三种路径：
  - 默认 heuristic：完全离线，不打印警告、不构建分类器；
  - llm 但未确认（无 --ack 且无 AAR_TASK_LLM_ACK）：打印警告、回退启发式、不传输；
  - llm 且确认（--ack 或 AAR_TASK_LLM_ACK=1）：打印已确认警告、构建 LLM 分类器。

⚠️ 不发起任何真实网络请求（LLM 构建用 mock）。
"""

import os
import sys
import types
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for _p in (str(SKILL_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import collect_usage_data as cud  # noqa: E402
import task_classifier_llm as tcl  # noqa: E402


def _make_args(task_classifier="heuristic", endpoint=None, model=None, ack=False):
    a = types.SimpleNamespace()
    a.task_classifier = task_classifier
    a.task_llm_endpoint = endpoint
    a.task_llm_model = model
    a.task_llm_api_key = None
    a.task_classifier_llm_ack = ack
    return a


def _must_not_call(*_a, **_k):
    raise RuntimeError("build_llm_classifier 不应被调用（未确认时禁止出站）")


def test_task_type_classifier_default_heuristic_unaffected(capsys):
    os.environ.pop("AAR_TASK_LLM_ACK", None)
    cls = cud.build_task_classifier_with_consent(_make_args("heuristic"))
    assert cls is None
    err = capsys.readouterr().err
    # 默认路径必须完全静默、无任何出站提示
    assert "出站" not in err


def test_task_type_classifier_llm_skipped_without_ack(capsys, monkeypatch):
    os.environ.pop("AAR_TASK_LLM_ACK", None)
    monkeypatch.setattr(tcl, "build_llm_classifier", _must_not_call)
    cls = cud.build_task_classifier_with_consent(
        _make_args("llm", endpoint="http://localhost:11434/v1", model="qwen2.5:7b", ack=False)
    )
    assert cls is None
    err = capsys.readouterr().err
    assert "出站传输未确认" in err
    assert "回退" in err


def test_task_type_classifier_llm_used_with_ack_flag(capsys, monkeypatch):
    os.environ.pop("AAR_TASK_LLM_ACK", None)
    sentinel = object()
    monkeypatch.setattr(tcl, "build_llm_classifier", lambda *a, **k: sentinel)
    cls = cud.build_task_classifier_with_consent(
        _make_args("llm", endpoint="http://localhost:11434/v1", model="qwen2.5:7b", ack=True)
    )
    assert cls is sentinel
    err = capsys.readouterr().err
    assert "已确认出站" in err


def test_task_type_classifier_llm_used_with_env_ack(capsys, monkeypatch):
    monkeypatch.setenv("AAR_TASK_LLM_ACK", "1")
    sentinel = object()
    monkeypatch.setattr(tcl, "build_llm_classifier", lambda *a, **k: sentinel)
    cls = cud.build_task_classifier_with_consent(
        _make_args("llm", endpoint="http://localhost:11434/v1", model="qwen2.5:7b", ack=False)
    )
    assert cls is sentinel
    err = capsys.readouterr().err
    assert "已确认出站" in err
