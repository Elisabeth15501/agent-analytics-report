"""ca_cli.py — CLI 边界：参数、日期、分类器构造与出站确认门禁（main() 拆分）。

批次 2 收纳（自门面原样搬入，语义不变）：
  - build_task_classifier_with_consent : 按 --task-classifier + ACK 门禁构造分类器

批次 6 收纳（阶段 II，main 切 20 行流水线；逐字自门面抽取，语义/文案不变）：
  - build_parser                       : ① 参数定义 + parse_args
  - validate_args                      : ② auto 探测闸门 + import-official 兼容 + tokens-only 锁定
  - resolve_period_window              : ③⑥ 日期窗口 + 显式参数提示 + meta_days

依赖说明：
  - 内部 `from task_classifier_llm import build_llm_classifier` 与 `from ca_detect import
    detect_source` 均为**函数内延迟导入**，保留原样（避免 import 时序副作用）。
  - 门面 collect_usage_data.py re-export 全部名字，测试按 cud.<name> 访问不受影响。
"""

import argparse
import os
import sys
from datetime import datetime

from ca_core import PERIOD_DAYS, resolve_date_range
from ca_types import Window


# ── 批次 6：CLI 边界三函数 ─────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(description="WorkBuddy Agent 使用数据采集器")
    parser.add_argument(
        "--period",
        choices=["day", "week", "month", "year"],
        default="week",
        help="时间窗口预设：day=今天 / week=最近7天 / month=最近30天 / year=最近365天（默认 week）",
    )
    parser.add_argument(
        "--days", type=int, help="自定义滚动天数，覆盖 --period（如 --days 14）"
    )
    parser.add_argument(
        "--start", type=str, help="绝对起始日期 YYYY-MM-DD（与 --end 搭配）"
    )
    parser.add_argument(
        "--end", type=str, help="绝对结束日期 YYYY-MM-DD（与 --start 搭配）"
    )
    parser.add_argument(
        "--output", "-o", type=str, help="输出 JSON 文件路径（默认 stdout）"
    )
    parser.add_argument(
        "--realtime", action="store_true", help="实时采集模式（立即采集最新数据）"
    )
    parser.add_argument(
        "--lookup-pricing",
        choices=["offline", "online"],
        default="offline",
        help="缺失单价模型的处理：offline=仅提示如何补写（默认，纯本地）；"
        "online=尝试联网检索（需 --pricing-api 指向你自己的定价镜像，"
        "否则仅生成可点击的搜索链接）。联网结果一律标注「网络估算价，仅供参考」",
    )
    # 安全边界：--pricing-api 只接受用户显式传入的 URL，不内置任何默认端点；
    # 不传则完全离线。URL 仅来自命令行参数，绝不接受从 trace / 会话内容派生的 URL（防 SSRF）。
    parser.add_argument(
        "--pricing-api",
        type=str,
        default=None,
        help='online 模式可选：指向一个返回 {"models": {模型名: {input,output}}} 的 JSON 端点，'
        "用于补全缺失模型单价（取自你自己的定价镜像，避免抓第三方页面）",
    )
    parser.add_argument(
        "--source",
        choices=[
            "workbuddy", "claude-code", "codex", "qwenwork", "dumate", "generic", "auto"
        ],
        default="auto",
        help="数据源：workbuddy=默认（WorkBuddy traces/workbuddy.db/usage-log），"
        "claude-code=读取 ~/.claude/projects/ 下的 Claude Code 会话 JSONL（P2-1 适配器 MVP）。"
        "claude-code 模式无需 WorkBuddy 环境，成本按 pricing.json 中 Claude 模型估算价计算。"
        "codex=读取 ~/.codex/sessions/ 下的 OpenAI Codex CLI rollout JSONL，"
        "无需 WorkBuddy 环境，成本按 pricing.json 中 OpenAI 模型估算价计算。"
        "qwenwork=读取千问办公（~/.qwenworkcn/projects 转录 + logs/runs 调用日志 + "
        "agents.db 会话元信息），无需 WorkBuddy 环境；"
        "千问办公上游不回传 token，故 token 为字符估算、成本未计价；"
        "dumate=百度搭子（DuMate）数据源：读 qianfan-desktop-app 的 opencode.db"
        "（OpenCode schema + SQLite，非 JSONL，与其他 Agent 不同），"
        "DUMATE_DB_PATH 可显式覆盖；"
        "generic=通用数据源（JSONL/SQLite 双 reader，零金额、tokens-only，"
        "只读 GENERIC_HOME/GENERIC_PROJECTS_DIR 指定目录）；"
        "auto=默认，按 workbuddy>claude-code>codex>qwenwork>dumate>generic 探测，"
        "只判各源标志存在性（dumate 判 opencode.db 文件），全不命中走 generic；"
        "显式指定 --source 时完全绕过探测",
    )
    parser.add_argument(
        "--cost-mode",
        choices=["auto", "tokens-only", "priced"],
        default="auto",
        help="计费维度：auto=默认，本期**一个单价都没命中**时自动切 tokens_only；"
        "tokens-only=强制整份报告隐藏金额（只讲 Token / 调用 / 耗时 / 任务），"
        "适合按积分订阅计费、没有公开单 token 刊例价的数据源（如千问办公）；"
        "priced=无论如何都按现状输出金额（与历史行为一致）。"
        "注意：限免 / 本地模型那种「单价已配置且合法为 0」的情况不会被 auto 误判",
    )
    parser.add_argument(
        "--task-classifier",
        choices=["heuristic", "llm"],
        default="heuristic",
        help="任务类型分类器：heuristic=默认加权启发式（离线、零依赖）；"
        "llm=可选增强，须同时提供 --task-llm-endpoint（本地 Ollama 或自有 OpenAI 兼容端点），"
        "调用失败自动回退启发式",
    )
    # 安全边界：--task-llm-endpoint 只接受用户显式传入的 URL，不内置任何默认端点；
    # 不传则完全离线。URL 仅来自命令行参数，绝不接受从 trace / 会话内容派生的 URL（防 SSRF）。
    parser.add_argument(
        "--task-llm-endpoint",
        type=str,
        default=None,
        help="LLM 分类器的 OpenAI 兼容 /v1 根地址（如本地 Ollama http://localhost:11434/v1）",
    )
    parser.add_argument(
        "--task-llm-model",
        type=str,
        default=None,
        help="LLM 分类器使用的模型名（如本地 Ollama 的 qwen2.5:7b）",
    )
    parser.add_argument(
        "--task-llm-api-key",
        type=str,
        default=None,
        help="LLM 端点的 API Key（本地端点可省略）",
    )
    parser.add_argument(
        "--task-classifier-llm-ack",
        action="store_true",
        default=False,
        help="确认启用 --task-classifier llm 的出站传输：会话派生文本将被发送至你提供的"
        "自有端点（OpenAI 兼容）。未设置时，即便传了 --task-classifier llm 也只走本地启发式、"
        "不做任何出站传输。也可设置环境变量 AAR_TASK_LLM_ACK=1 等效确认。",
    )
    parser.add_argument(
        "--import-official",
        type=str,
        default=None,
        metavar="XLSX",
        help="导入官方用量导出（官网下载的 request-usage-*.xlsx），作为**成本真值（L1）**对账源。"
        "纯本地只读解析，不联网、不上传。不传时成本为静态价表估算（L2）。"
        "仅支持 --source workbuddy",
    )
    parser.add_argument(
        "--official-sheet",
        type=str,
        default=None,
        help="官方导出工作表名（默认取第一个工作表；官方固定为 'Usage Details'）",
    )
    return parser.parse_args()


def validate_args(args):
    # ── 阶段 2（P0）：auto 探测与互斥选择 ──
    # 兼容闸门：--import-official 历来只支持 workbuddy，旧脚本常省略 --source
    # （靠历史默认值 workbuddy）。默认改为 auto 后若让探测把它判成 generic，
    # 老命令会直接 exit 2 —— 属于用户功能退化，故先把这类调用锚回 workbuddy。
    # 用户显式传了非 workbuddy 时仍由下方检查报错，不改变原语义。
    if args.import_official and args.source == "auto":
        args.source = "workbuddy"
    # 探测结果就地写回 args.source：后续所有分支与统计只看具体源名，
    # 批次 5 搬 collect_from_source 时可原样搬走分发链，不必搬探测逻辑。
    # 显式 --source 在 detect_source 内直接返回，完全绕过探测（S2.5）。
    from ca_detect import detect_source

    args.source = detect_source(args.source)["source"]
    if args.source in ("generic", "dumate"):
        # S1.8 / dumate：两源都显式强制 tokens-only。_decide_cost_mode 的 auto 只认
        # has_priced_model，若 generic 模型名恰好命中价表会被切回 priced；
        # 这里在入口处锁死口径，不靠「恰好没单价」隐式推断。
        args.cost_mode = "tokens-only"

    # 官方导出只对 WorkBuddy 源有意义：claude-code / codex 没有官方积分账单可对
    if args.import_official and args.source != "workbuddy":
        print(
            f"[ERROR] --import-official 仅支持 --source workbuddy（当前 --source {args.source}）",
            file=sys.stderr,
        )
        sys.exit(2)


def resolve_period_window(args):
    """③⑥：日期窗口 + 显式参数提示 + meta_days，返回 Window。"""
    if args.realtime:
        # 实时模式：立即采集最新数据（今天）
        start_date, end_date, period_key, period_label = resolve_date_range(
            period="day"
        )
        print(
            f"[INFO] 实时模式：采集范围 {start_date} ~ {end_date}（当日）",
            file=sys.stderr,
        )
    else:
        # 记录显式指定的参数，用于提示用户实际生效的参数
        explicit_params = []
        if args.start and args.end:
            explicit_params.append("--start/--end")
        if args.days is not None:
            explicit_params.append(f"--days {args.days}")
        if (
            args.period != "week" or explicit_params
        ):  # 只有显式指定了非默认值，或其他参数覆盖时才提示
            if args.period != "week" and not (args.days or args.start):
                explicit_params.append(f"--period {args.period}")

        try:
            start_date, end_date, period_key, period_label = resolve_date_range(
                period=args.period, days=args.days, start=args.start, end=args.end
            )
        except ValueError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(2)

        # 详细提示：显式指定参数 vs 默认值
        if explicit_params:
            print(
                f"[INFO] 采集范围[{period_label}]：{start_date} ~ {end_date}（生效参数：{', '.join(explicit_params)}）",
                file=sys.stderr,
            )
        else:
            print(
                f"[INFO] 采集范围[{period_label}]：{start_date} ~ {end_date}（默认一周，可用 --period/--days/--start/--end 自定义）",
                file=sys.stderr,
            )

    if args.days is not None:
        meta_days = args.days
    elif args.start and args.end:
        try:
            meta_days = (
                datetime.strptime(args.end, "%Y-%m-%d")
                - datetime.strptime(args.start, "%Y-%m-%d")
            ).days + 1
        except (ValueError, TypeError):
            meta_days = PERIOD_DAYS.get(period_key, 7)
    else:
        meta_days = PERIOD_DAYS.get(period_key, 7)
    return Window(
        start=start_date, end=end_date, key=period_key,
        label=period_label, days=meta_days,
    )


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
