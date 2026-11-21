"""ca_cli.py — CLI 边界：分类器构造与出站确认门禁（main() 拆分 · 批次 2 建立）。

批次 2 收纳（自门面原样搬入，语义不变）：
  - build_task_classifier_with_consent : 按 --task-classifier + ACK 门禁构造分类器

批次 6 将再加入 build_parser / validate_args / resolve_period_window。

依赖说明：
  - 内部 `from task_classifier_llm import build_llm_classifier` 是**函数内延迟导入**，
    保留原样；测试 monkeypatch tcl.build_llm_classifier 仍命中同一模块对象。
  - 门面 collect_usage_data.py re-export 这个名字，测试按 cud.<name> 访问不受影响。
"""

import os
import sys


def build_task_classifier_with_consent(args, _stderr=None):
    """按 --task-classifier 与出站确认门禁构造任务分类器（TP4 合规）。

    仅当 args.task_classifier == "llm" 且显式确认（--task-classifier-llm-ack
    或环境变量 AAR_TASK_LLM_ACK=1）时，才构建会将会话派生文本外传的 LLM 分类器；
    否则返回 None，调用方据此走内置加权启发式、零出站。

    默认离线路径（heuristic）完全不受影响：直接返回 None，不打印任何警告。
    """
    if args.task_classifier != "llm":
        return None
    endpoint = args.task_llm_endpoint
    ack = bool(getattr(args, "task_classifier_llm_ack", False)) or \
        os.environ.get("AAR_TASK_LLM_ACK") == "1"
    stderr = _stderr if _stderr is not None else sys.stderr
    if not ack:
        print(
            "[WARN] ⚠ 出站传输未确认：--task-classifier llm 本会把会话派生文本"
            "（会话标题 / 对话内容摘要等）\n"
            f"       发送至你提供的端点 {endpoint or '(未提供)'}，该内容会离开本机。\n"
            "       未检测到 --task-classifier-llm-ack 或 AAR_TASK_LLM_ACK=1，"
            "已自动回退本地启发式分类，不做任何出站传输。",
            file=stderr,
        )
        return None
    print(
        "[WARN] ⚠ 已确认出站：--task-classifier llm 将把会话派生文本"
        f"（会话标题 / 对话内容摘要等）发送至端点 {endpoint}（该内容会离开本机）。",
        file=stderr,
    )
    try:
        from task_classifier_llm import build_llm_classifier

        return build_llm_classifier(
            endpoint,
            args.task_llm_model,
            api_key=args.task_llm_api_key,
        )
    except (ValueError, ImportError) as e:
        print(f"[WARN] LLM 分类器不可用（{e}），回退加权启发式", file=stderr)
        return None
