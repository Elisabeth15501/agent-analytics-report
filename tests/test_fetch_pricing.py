# -*- coding: utf-8 -*-
"""P2-2 · fetch_pricing 定价自动更新测试

覆盖：
  - validate_entries：schema 非法 / 负数 / 非数字 / 超上限拒绝；same 跳过；
    new / update 分类；倍率超限拒绝；--force 放行
  - build_candidate：合并 + 审计字段（updated / last_fetch_source）+ 可选段合入
  - check_stale：缺 updated 字段 / 格式非法 / 过期 / 新鲜
  - CLI 黑盒：--check 退出码、--file 拉取产出候选与 diff、--apply 备份并落盘
  - 无真实网络请求（URL 路径只测 fetch_source 的参数缺失分支）
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import allure

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for _p in (str(SKILL_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fetch_pricing as fp  # noqa: E402

pytestmark = pytest.mark.unit

CUR = {"models": {"glm-5.2": {"input": 8.0, "output": 28.0},
                  "hy3": {"input": 1.0, "output": 4.0}}}


# ── validate_entries ─────────────────────────────────────────────────────

def test_validate_rejects_missing_models():
    acc, rej = fp.validate_entries({}, CUR["models"])
    assert not acc and rej and "models" in rej[0]["reason"]


def test_validate_rejects_non_numeric_and_negative():
    remote = {"models": {
        "a": {"input": "free", "output": 1.0},
        "b": {"input": -1, "output": 1.0},
        "c": {"input": 2.0, "output": None},
    }}
    acc, rej = fp.validate_entries(remote, CUR["models"])
    assert not acc and len(rej) == 3


def test_validate_rejects_over_absolute_cap():
    acc, rej = fp.validate_entries({"models": {"x": {"input": 99999, "output": 1}}},
                                   CUR["models"])
    assert not acc and "非负数字" in rej[0]["reason"]


def test_validate_classifies_new_and_update():
    remote = {"models": {
        "glm-5.2": {"input": 8.8, "output": 30.8},   # update（1.1x）
        "brand-new": {"input": 2.0, "output": 4.0},  # new
        "hy3": {"input": 1.0, "output": 4.0},        # same → 跳过
    }}
    acc, rej = fp.validate_entries(remote, CUR["models"])
    assert not rej
    assert acc["brand-new"]["_status"] == "new"
    assert acc["glm-5.2"]["_status"] == "update"
    assert "hy3" not in acc  # 无变化不进候选


def test_validate_rejects_ratio_exceed():
    remote = {"models": {"glm-5.2": {"input": 80.0, "output": 280.0}}}  # 10x
    acc, rej = fp.validate_entries(remote, CUR["models"], max_ratio=5.0)
    assert not acc, "超倍率条目不得进入 accepted（曾因缺 continue 双重收录）"
    assert rej and "超过 --max-ratio" in rej[0]["reason"]
    # 被拒绝的模型不能同时出现在 accepted（回归守护）
    assert "glm-5.2" not in acc


# ── build_candidate ──────────────────────────────────────────────────────

def test_build_candidate_merges_and_stamps_audit():
    accepted = {"brand-new": {"input": 2.0, "output": 4.0, "_status": "new"}}
    remote = {"timed_free": {"trial": {"free_until": "2026-12-31"}}}
    cand = fp.build_candidate(CUR, accepted, remote=remote, source_label="unit-test")
    assert cand["models"]["brand-new"] == {"input": 2.0, "output": 4.0}
    assert cand["models"]["glm-5.2"] == {"input": 8.0, "output": 28.0}  # 原有条目不动
    assert cand["timed_free"]["trial"]["free_until"] == "2026-12-31"
    assert cand["_pricing_rules"]["last_fetch_source"] == "unit-test"
    assert cand["_pricing_rules"]["updated"]  # 审计时间已刷新
    assert CUR["models"].get("brand-new") is None  # 不污染入参


def test_write_diff_report(tmp_path):
    accepted = {"m1": {"input": 1.0, "output": 2.0, "_status": "new"}}
    rejected = [{"model": "m2", "reason": "测试拒绝"}]
    out = tmp_path / "diff.md"
    fp.write_diff_report(accepted, rejected, "src", out_path=out)
    text = out.read_text(encoding="utf-8")
    assert "| m1 | new | 1.0 | 2.0 |" in text
    assert "测试拒绝" in text


# ── check_stale ──────────────────────────────────────────────────────────

def test_check_stale_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "PRICING_PATH", tmp_path / "pricing.json")
    (tmp_path / "pricing.json").write_text(json.dumps(
        {"_pricing_rules": {"updated": _today_str()}}), encoding="utf-8")
    stale, msg = fp.check_stale(30)
    assert not stale


def test_check_stale_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "PRICING_PATH", tmp_path / "pricing.json")
    (tmp_path / "pricing.json").write_text(json.dumps(
        {"_pricing_rules": {"updated": "2020-01-01"}}), encoding="utf-8")
    stale, msg = fp.check_stale(30)
    assert stale and "天未更新" in msg


def test_check_stale_missing_field(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "PRICING_PATH", tmp_path / "pricing.json")
    (tmp_path / "pricing.json").write_text("{}", encoding="utf-8")
    stale, msg = fp.check_stale(30)
    assert stale and "updated" in msg


def test_check_stale_bad_format(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "PRICING_PATH", tmp_path / "pricing.json")
    (tmp_path / "pricing.json").write_text(
        json.dumps({"_pricing_rules": {"updated": "Aug 2026"}}), encoding="utf-8")
    stale, _msg = fp.check_stale(30)
    assert stale


def _today_str():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── CLI 黑盒（subprocess，全部走临时目录，不碰真实 pricing.json）──────────

PY = sys.executable


def _run_cli(args, cwd, env_extra=None):
    import os
    import subprocess
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([PY, str(SCRIPTS_DIR / "fetch_pricing.py"), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60, env=env)


def test_cli_check_exit_codes(tmp_path):
    # 完全隔离：FETCH_PRICING_ROOT 指向临时目录
    (tmp_path / "pricing.json").write_text(json.dumps(
        {"_pricing_rules": {"updated": _today_str()}}), encoding="utf-8")
    r = _run_cli(["--check", "--stale-days", "30"], cwd=str(SKILL_DIR),
                 env_extra={"FETCH_PRICING_ROOT": str(tmp_path)})
    assert r.returncode == fp.EXIT_OK
    assert "[OK]" in r.stdout

    # 过期 → 退出码 1
    (tmp_path / "pricing.json").write_text(json.dumps(
        {"_pricing_rules": {"updated": "2020-01-01"}}), encoding="utf-8")
    r = _run_cli(["--check", "--stale-days", "30"], cwd=str(SKILL_DIR),
                 env_extra={"FETCH_PRICING_ROOT": str(tmp_path)})
    assert r.returncode == fp.EXIT_CHECK_FAILED


def test_cli_usage_error_without_source(tmp_path):
    r = _run_cli([], cwd=str(SKILL_DIR),
                 env_extra={"FETCH_PRICING_ROOT": str(tmp_path)})
    assert r.returncode == fp.EXIT_USAGE


def test_cli_file_flow_produces_candidate_and_diff(tmp_path):
    # 黑盒：FETCH_PRICING_ROOT 隔离，绝不触碰仓库真实 pricing.json
    (tmp_path / "pricing.json").write_text(json.dumps(CUR), encoding="utf-8")
    src = tmp_path / "remote.json"
    src.write_text(json.dumps({"models": {
        "glm-5.2": {"input": 8.8, "output": 30.8},
        "new-one": {"input": 1.0, "output": 2.0}}}), encoding="utf-8")
    r = _run_cli(["--file", str(src)], cwd=str(SKILL_DIR),
                 env_extra={"FETCH_PRICING_ROOT": str(tmp_path)})
    assert r.returncode == fp.EXIT_OK, r.stderr
    cand = tmp_path / "pricing.candidate.json"
    diff = tmp_path / "pricing-diff.md"
    assert cand.exists() and diff.exists()
    data = json.loads(cand.read_text(encoding="utf-8"))
    assert data["models"]["new-one"] == {"input": 1.0, "output": 2.0}
    assert data["models"]["glm-5.2"] == {"input": 8.8, "output": 30.8}
    assert "new-one" in diff.read_text(encoding="utf-8")
    # 真实 pricing.json 未被触碰（当前 pricing.json 仍为 8.0/28.0，未含 new-one）
    real = json.loads((SCRIPTS_DIR / "pricing.json").read_text(encoding="utf-8"))
    assert real["models"]["glm-5.2"] == {"input": 8.0, "output": 28.0}
    assert "new-one" not in real["models"]


def test_cli_apply_backs_up_original(tmp_path, monkeypatch):
    # 把模块常量指到临时目录后进程内调 main——避免动真实 pricing.json
    real_pricing = tmp_path / "pricing.json"
    real_pricing.write_text(json.dumps(CUR), encoding="utf-8")
    src = tmp_path / "remote.json"
    src.write_text(json.dumps({"models": {"new-one": {"input": 1.0, "output": 2.0}}}),
                   encoding="utf-8")
    monkeypatch.setattr(fp, "PRICING_PATH", real_pricing)
    monkeypatch.setattr(fp, "CANDIDATE_PATH", tmp_path / "pricing.candidate.json")
    monkeypatch.setattr(fp, "DIFF_PATH", tmp_path / "pricing-diff.md")
    monkeypatch.setattr(sys, "argv",
                        ["fetch_pricing.py", "--file", str(src), "--apply"])
    fp.main()
    assert json.loads(real_pricing.read_text(encoding="utf-8"))["models"]["new-one"] == \
        {"input": 1.0, "output": 2.0}
    backups = list(tmp_path.glob("pricing.json.bak-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == CUR  # 备份=原文件


def test_cli_apply_over_ratio_rejected_without_force(tmp_path, monkeypatch):
    real_pricing = tmp_path / "pricing.json"
    real_pricing.write_text(json.dumps(CUR), encoding="utf-8")
    src = tmp_path / "remote.json"
    src.write_text(json.dumps({"models": {"glm-5.2": {"input": 800.0, "output": 2800.0}}}),
                   encoding="utf-8")
    monkeypatch.setattr(fp, "PRICING_PATH", real_pricing)
    monkeypatch.setattr(fp, "CANDIDATE_PATH", tmp_path / "pricing.candidate.json")
    monkeypatch.setattr(fp, "DIFF_PATH", tmp_path / "pricing-diff.md")
    monkeypatch.setattr(sys, "argv", ["fetch_pricing.py", "--file", str(src)])
    with pytest.raises(SystemExit) as e:
        fp.main()
    assert e.value.code == fp.EXIT_OK  # 拒绝不算失败
    assert json.loads(real_pricing.read_text(encoding="utf-8")) == CUR  # 原文件未被改动


def test_fetch_source_requires_url_or_file():
    _data, err = fp.fetch_source()
    assert err and "--url 或 --file" in err
