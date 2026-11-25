"""ca_detect.py — ``--source auto`` 的源探测与互斥选择（阶段 2 / P0）。

职责边界：
  * **只判路径存在性**，不打开任何数据库、不读会话内容；
  * 复用各适配器已有 ``resolve_*_home()``，不硬编码新路径；
  * 固定优先级 ``workbuddy > claude-code > codex > qwenwork > dumate > generic``；
  * dumate 自 2026-11-26 纳入 auto：复用 resolve_dumate_db() 只判 opencode.db 存在性，
    不打开库、不读内容；优先级在 qwenwork 之后、generic 兜底之前；
  * OpenClaw 只做双路径探测扩展点，不注册 source、不写解析器；
  * 显式 ``--source`` 完全绕过探测（S2.5）。

独立成模块而非塞进 main()，是为了不增加批次 5 要搬运的 ``collect_from_source``
分发链长度：批次 5 搬分发链时，本模块保持原样即可（见整合梳理 §2.3）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["PRIORITY", "EXCLUDED_SOURCES", "detect_source"]

PRIORITY = ("workbuddy", "claude-code", "codex", "qwenwork", "dumate", "generic")
# 当前无排除源。dumate 于 2026-11-26 纳入 auto（qwenwork 与 generic 之间）。
EXCLUDED_SOURCES = ()


def _safe_dir(path: Path) -> bool:
    """只认真实目录：符号链接一律跳过（不跟随、不采集）。"""
    try:
        return bool(path) and path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def _safe_file(path: Path) -> bool:
    try:
        return bool(path) and path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _workbuddy_root():
    from ca_core import WB_DIR
    return Path(WB_DIR)


def _claude_root():
    from adapters.claude_code import resolve_claude_projects_root
    return Path(resolve_claude_projects_root())


def _codex_root():
    from adapters.codex import resolve_codex_sessions_root
    return Path(resolve_codex_sessions_root())


def _qwenwork_root():
    from adapters.qwenwork import resolve_qwenwork_home
    return Path(resolve_qwenwork_home())


def _dumate_root():
    """百度搭子 opencode.db 路径（复用专用 reader 的发现逻辑，不打开库）。"""
    from adapters.dumate import resolve_dumate_db
    return Path(resolve_dumate_db())


def _openclaw_layout(root=None):
    from adapters.generic import detect_openclaw_layout
    return detect_openclaw_layout(home=root)


def _probe(source, root):
    """返回 (hit, 标志描述)；只读路径，不读内容。"""
    root = Path(root)
    if source == "workbuddy":
        for marker in ("traces", "workbuddy.db", "usage-log.json"):
            candidate = root / marker
            if _safe_dir(candidate) or _safe_file(candidate):
                return True, f"{root}/{marker}"
    elif source == "claude-code":
        if _safe_dir(root):
            return True, str(root)
    elif source == "codex":
        if _safe_dir(root):
            return True, str(root)
    elif source == "qwenwork":
        for marker in ("projects", "logs"):
            candidate = root / marker
            if _safe_dir(candidate):
                return True, f"{root}/{marker}"
    elif source == "dumate":
        # 百度搭子是 SQLite 文件（opencode.db），不是标志目录；只判存在性、
        # 不打开库、不读内容。生产 root 即 db 路径；测试可注入目录。
        candidates = [root] if root.is_file() else [
            root / "data" / "opencode" / "opencode.db",
            root / "opencode.db",
        ]
        for candidate in candidates:
            if _safe_file(candidate):
                return True, str(candidate)
    return False, ""


def detect_source(explicit="auto", *, roots=None, env=None, report=True):
    """决定实际生效的 source。

    Args:
        explicit: argparse 的 ``args.source``。非 ``auto`` 时原样返回（S2.5）。
        roots: 测试注入的根目录映射；生产不传。键同 PRIORITY，另可含 ``openclaw``。
        env: 环境变量映射（默认 os.environ），供测试隔离。
        report: 是否向 stderr 打印命中/跳过信息（S2.4）。

    Returns:
        dict: ``source`` / ``explicit`` / ``hits`` / ``skipped`` / ``openclaw`` / ``reason``
    """
    environ = os.environ if env is None else env
    roots = dict(roots or {})
    if explicit and explicit != "auto":
        decision = {
            "source": explicit,
            "explicit": True,
            "hits": {},
            "skipped": [],
            "openclaw": _openclaw_layout(roots.get("openclaw")),
            "reason": "explicit",
        }
        if report:
            print(f"[INFO] 显式 --source {explicit}：跳过自动探测", file=sys.stderr)
        return decision

    resolvers = {
        "workbuddy": _workbuddy_root,
        "claude-code": _claude_root,
        "codex": _codex_root,
        "qwenwork": _qwenwork_root,
        "dumate": _dumate_root,
    }
    hits, skipped = {}, {}
    for name in PRIORITY:
        if name == "generic":
            continue  # 兜底源不参与标志探测
        root = roots.get(name)
        if root is None:
            try:
                root = resolvers[name]()
            except (ImportError, OSError, ValueError) as exc:
                skipped[name] = f"路径解析失败（{exc}）"
                continue
        hit, marker = _probe(name, root)
        if hit:
            hits[name] = marker
        else:
            skipped[name] = "未发现标志目录"

    openclaw = _openclaw_layout(roots.get("openclaw"))
    chosen = next((name for name in PRIORITY if name in hits), "generic")
    # 多源命中时，低优先级源也要在 stderr 里显式标为「跳过」，
    # 避免用户以为它被一并采集造成双计（B5 / R5）。
    for name in PRIORITY:
        if name in hits and name != chosen:
            skipped[name] = f"命中但优先级低于 {chosen}（互斥，未采用）"
    decision = {
        "source": chosen,
        "explicit": False,
        "hits": hits,
        "skipped": skipped,
        "openclaw": openclaw,
        "reason": "priority" if chosen != "generic" else "fallback",
    }
    if report:
        if chosen == "generic":
            print(
                "[INFO] 源探测：无专用源标志命中 → generic（零金额、tokens-only）",
                file=sys.stderr,
            )
        else:
            print(
                f"[INFO] 源探测：命中 {chosen}（标志 {hits[chosen]}）；"
                f"按优先级选用",
                file=sys.stderr,
            )
        for name, why in skipped.items():
            print(f"[INFO]   跳过 {name}：{why}", file=sys.stderr)
        print(
            "[INFO]   dumate 已纳入自动探测（qwenwork 之后、generic 之前，"
            "只判 opencode.db 存在性）；OpenClaw 仅探测扩展点、未实现解析器",
            file=sys.stderr,
        )
    return decision
