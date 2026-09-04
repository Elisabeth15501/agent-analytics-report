#!/usr/bin/env python3
"""gen_example_report.py — 生成示例报告（fixture 驱动）

本脚本从合成数据生成 examples/sample-report.md 和 examples/sample-report.html，
确保示例报告与代码逻辑一致，避免手工维护导致漂移。

用法:
  python scripts/gen_example_report.py [--seed N] [--output-dir examples]
"""

import json
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── 固定种子，确保每次生成结果一致 ──────────────────────────────────────────
SEED = 42
for i, a in enumerate(sys.argv):
    if a.startswith("--seed="):
        SEED = int(a.split("=")[1])
        break

rng = random.Random(SEED)

# ── 周期参数（可通过环境变量覆盖）───────────────────────────────────────────
START_DATE = "2026-08-31"
END_DATE = "2026-09-06"
DATE_FMT = "%Y-%m-%d"

def _parse_date(s):
    return datetime.strptime(s, DATE_FMT).date()

start = _parse_date(START_DATE)
end = _parse_date(END_DATE)
period_days = (end - start).days + 1  # 7

# ── 合成 trace 数据 ────────────────────────────────────────────────────────
MODELS = ["glm-5.2", "glm-4.7-flash", "deepseek-v4-pro", "kimi-k2.6", "auto"]
CHANNELS = ["gateway", "router", "openrouter-free"]
SESSION_IDS = [f"sess_{i:04d}" for i in range(1, 30)]  # 29 个会话
DAILY_TOKENS = [
    ("2026-08-31", 1_740_000),   # 1.74M
    ("2026-09-01", 3_540_000),   # 3.54M (高峰日)
    ("2026-09-02", 1_440_000),   # 1.44M
    ("2026-09-03", 1_740_000),   # 1.74M
    ("2026-09-04", 1_580_000),   # 1.58M
    ("2026-09-05", 2_380_000),   # 2.38M
    ("2026-09-06", 2_490_000),   # 2.49M
]
TOTAL_EFFECTIVE = sum(d[1] for d in DAILY_TOKENS)  # 14.91M
TOTAL_INPUT = int(TOTAL_EFFECTIVE * 0.4)            # 约 5.96M
TOTAL_OUTPUT = int(TOTAL_EFFECTIVE * 0.6)           # 约 8.95M
TOTAL_CACHED = int(TOTAL_INPUT * 0.875)             # 87.5% cache rate

traces = []
for date_str, daily_total in DAILY_TOKENS:
    n_calls = rng.randint(3, 8)
    for _ in range(n_calls):
        inp = rng.randint(50_000, 200_000)
        out = rng.randint(20_000, 100_000)
        cached = int(rng.uniform(0.3, 0.9) * inp)
        total_tok = inp + out
        effective_tok = max(inp - cached * 0.9, 0) + out
        # 模拟真实成本：输入 ¥0.5/M, 输出 ¥2.0/M（简化估算）
        input_cost = round(inp * 0.5e-6, 4)
        output_cost = round(out * 2.0e-6, 4)
        cached_saving = round(cached * 0.45e-6, 4)  # 缓存按 1/10 价，省 90%
        traces.append({
            "session_id": rng.choice(SESSION_IDS),
            "model_key": rng.choice(MODELS),
            "model_name": rng.choice(MODELS),
            "exec_model": rng.choice(MODELS),
            "channel": rng.choice(CHANNELS),
            "total_tokens": total_tok,
            "input_tokens": inp,
            "output_tokens": out,
            "cached_tokens": cached,
            "effective_tokens": effective_tok,
            "input_cost": input_cost,
            "output_cost": output_cost,
            "total_cost": round(input_cost + output_cost, 4),
            "effective_cost": round(input_cost + output_cost - cached_saving, 4),
            "date": date_str,
            "call_count": 1,
        })

# ── 合成会话数据 ───────────────────────────────────────────────────────────
TASK_TYPES = ["代码开发", "内容生成", "技能开发", "Bug修复", "研究学习", "自动化配置"]
sessions = []
for sid in SESSION_IDS:
    sessions.append({
        "id": sid,
        "title": rng.choice(["实现新功能", "分析数据", "写周报", "调试 bug", "生成报告", "其他任务"]),
        "task_type": rng.choice(TASK_TYPES),
        "created_at": f"{START_DATE}T{rng.randint(8,22):02d}:{rng.randint(0,59):02d}:00+08:00",
        "cwd": rng.choice(["~/WorkBuddy/project-a", "~/WorkBuddy/project-b"]),
    })

# ── 合成自动化运行 ─────────────────────────────────────────────────────────
AUTO_TASKS = ["AI 日报看板", "token 用量巡检", "Skill 健康检查", "AI 周报生成"]
automation_runs = []
for task in AUTO_TASKS:
    n_runs = rng.randint(1, 5)
    for i in range(n_runs):
        automation_runs.append({
            "task_name": task,
            "result_success": rng.random() > 0.2,  # 80% 成功
            "date": rng.choice([d[0] for d in DAILY_TOKENS]),
            "cwd": "~/WorkBuddy/project-a",
        })

# ── 合成技能使用 ───────────────────────────────────────────────────────────
SKILLS = ["git-push-sandbox", "skill-creator", "github", "ai-weekly", "recommend-experts",
          "agent-analytics-report", "skillhub-publish", "ta[REDACTED_SK_KEY]"]
skill_usage = {"skills": {s: {"usage_count_in_range": rng.randint(1, 10),
                              "last_used": rng.choice([d[0] for d in DAILY_TOKENS])}
                          for s in SKILLS}}

# ── 合成产出文件 ───────────────────────────────────────────────────────────
OUTPUTS = [
    ("analysis.xlsx", "xlsx", rng.choice([START_DATE, END_DATE]), f"{rng.randint(1,10)}.MB"),
    ("report.html", "html", rng.choice([START_DATE, END_DATE]), f"{rng.randint(1,5)}.MB"),
    ("script.py", "py", rng.choice([START_DATE, END_DATE]), f"{rng.randint(100,900)}.KB"),
    ("trace-log.docx", "docx", rng.choice([START_DATE, END_DATE]), f"{rng.randint(1,8)}.MB"),
]

# ── 组装 data dict ────────────────────────────────────────────────────────
data = {
    "meta": {
        "start_date": START_DATE,
        "end_date": END_DATE,
        "period": "week",
        "period_label": f"周报 · 2026-W36（{START_DATE}/{END_DATE}）",
        "days": period_days,
        "timed_free": {"hy3": "2026-09-30"},
    },
    "traces": traces,
    "sessions": sessions,
    "automation_runs": automation_runs,
    "session_credits": [],
    "skill_usage": skill_usage,
    "outputs": [{"name": n, "ext": e, "date": d, "size": s} for n, e, d, s in OUTPUTS],
    "daily_tokens": {d[0]: {"total": d[1]} for d in DAILY_TOKENS},
    "summary": {
        "active_day_count": len(DAILY_TOKENS),
        "active_days": [d[0] for d in DAILY_TOKENS],
        "total_sessions": len(sessions),
        "total_traces": len(traces),
        "total_automation_runs": len(automation_runs),
        "successful_automation_runs": sum(1 for r in automation_runs if r["result_success"]),
        "total_outputs": len(OUTPUTS),
        "skills_used": len(skill_usage["skills"]),
        "total_tokens": TOTAL_EFFECTIVE + TOTAL_CACHED,
        "total_input_tokens": TOTAL_INPUT,
        "total_output_tokens": TOTAL_OUTPUT,
        "total_cached_tokens": TOTAL_CACHED,
    },
    "task_token_stats": [],
    "top_tasks": [],
    "cost_anomalies": {},
    "savings_insights": [],
}

# ── 输出到文件 ─────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SCRIPT_DIR.parent / "examples"
DATA_FILE = EXAMPLES_DIR / "example-data.json"

DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# 调用 generate_report.py 生成报告
GENERATE_SCRIPT = SCRIPT_DIR / "generate_report.py"
t0 = time.time()

for fmt in ["markdown", "html"]:
    suffix = "md" if fmt == "markdown" else fmt
    out_file = EXAMPLES_DIR / f"sample-report.{suffix}"
    result = subprocess.run(
        [sys.executable, str(GENERATE_SCRIPT), str(DATA_FILE),
         "--format", fmt, "--output", str(out_file)],
        cwd=str(SCRIPT_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        print(f"[ERROR] generate_report {fmt} failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] {fmt} → {out_file}")

elapsed = time.time() - t0
print(f"\n完成。耗时 {elapsed:.1f}s")
print(f"  样本数据: {DATA_FILE}")
print(f"  - {len(traces)} traces")
print(f"  - {len(sessions)} sessions")
print(f"  - {len(automation_runs)} automation runs")
print(f"  - 总 token: {TOTAL_EFFECTIVE/1e6:.2f}M")
