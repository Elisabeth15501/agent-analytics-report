#!/usr/bin/env python3
"""
collect_usage_data.py — WorkBuddy Agent 使用数据采集器

从多个数据源采集 Agent 使用情况：
  1. traces/       — token 消耗、模型信息、会话时长
  2. workbuddy.db  — 会话元数据、自动化运行记录、信用消耗
  3. usage-log.json — 技能使用记录、活跃天数
  4. WorkBuddy/    — 会话产出文件、记忆日志
  5. automation API — 自动化任务配置

用法:
  python collect_usage_data.py [--days N] [--output data.json]
  默认采集最近 7 天数据，输出到 stdout

功能增强：
  - 添加成本货币化计算（基于 token 消耗）
  - 添加模型使用状况统计
  - 支持实时数据采集（通过 --realtime 参数）
"""

import argparse
import calendar
import json
import os
import urllib.parse
import urllib.request
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from collections import defaultdict

# ── 成本货币化配置 ───────────────────────────────────────
# 模型单价（元 / 1M tokens），输入 / 输出分别计价。
# 键名须与本机 trace 中 modelInfo.models[0] 完全一致（已扫描本机确认真实模型名）。
#   - 免费模型（:free 后缀或官方免费档）：记为 0；
#   - 付费模型：填 {"input": 元/1M, "output": 元/1M}；
#   - 路由别名（见 ROUTER_ALIASES）：填 None，由代码自动取「所有计费模型均价」做代表性估算；
#   - 其他未配置 / 未知：填 None（新「模型成本对比」章节显示「未配置」，不瞎算）。
# 价格来源（联网查证，2026-07-29）：
#   · hy3           —— 腾讯混元官方 RMB：输入 1 元 / 输出 4 元（缓存命中 0.25）
#   · GLM 系列      —— 智谱国内 bigmodel.cn 官方 RMB 价（用户接口走 bigmodel.cn，非 Z.ai 美元价）：
#                      glm-5.2  1M 上下文 8/28；glm-5     [32k+) 6/22；glm-4.6v [32k,128k) 2/6；
#                      glm-4.5-air [32k,128k) 1.2/8；glm-4.7-flash 免费；glm-5.2-x 暂按 glm-5.2
#                      （均取 agent 长上下文档代表值；短上下文档更低，详见 open.bigmodel.cn/pricing）
#   · deepseek-v4-pro —— DeepSeek 官方永久价 RMB：输入 3 元 / 输出 6 元（缓存命中 0.025）
#   注：以上为公开标价估算，请以 WorkBuddy 实际账单为准。

# 限时免费模型：键=归一化模型名（小写），值=免费截止日期（含当天，'YYYY-MM-DD'）。
# 在截止日（含）之前的调用计为免费（单价 0、花费 ¥0.00），并在报告中标注「限时免费」。
# 来源：腾讯混元 Hy3 限免活动延长至 2026-08-31（已联网核实：腾讯张军微博 / 腾讯云开发者社区）。
#   - 限免对象：WorkBuddy / CodeBuddy 用户；其他渠道（TokenHub API / 私有化）计费规则不同。
#   - 截止日需随官方活动更新（8/31 后若再延期，改此处即可）。
TIMED_FREE = {
    "hy3": "2026-08-31",
}

MODEL_PRICING = {
    # —— WorkBuddy 官方内置模型（与 scripts/pricing.json 保持一致；仅作兜底，运行时以 pricing.json 为准）——
    # 路由别名：无单一单价，由代码估算（见 ROUTER_ALIASES）
    "auto": None,            # WorkBuddy 智能路由：执行时自动调配最适合模型
    # —— 腾讯混元（官方 RMB）——
    "hy3": {"input": 1.0, "output": 4.0},           # 腾讯混元官方：输入1 / 输出4（限时免费至 2026-08-31）
    # —— 智谱 GLM 系列（bigmodel.cn 国内官方 RMB，非 Z.ai 美元折算）——
    "glm-5.2": {"input": 8.0, "output": 28.0},      # 智谱官方 1M 上下文：输入8 / 输出28
    "glm-5.1": {"input": 8.0, "output": 28.0},      # 智谱官方：输入8 / 输出28
    "glm-5v-turbo": {"input": 5.0, "output": 22.0}, # 智谱官方多模态：输入5 / 输出22
    # —— MiniMax（官方 RMB）——
    "minimax-m3": {"input": 1.0, "output": 4.0},    # 与 hy3 同网关，同价位
    "minimax-m2.7": {"input": 2.1, "output": 8.4},  # MiniMax 官方：输入2.1 / 输出8.4
    # —— Kimi（官方 RMB）——
    "kimi-k3": {"input": 20.0, "output": 100.0},    # Kimi K3 官方：输入20 / 输出100
    "kimi-k2.7-code": {"input": 6.5, "output": 27.0}, # Kimi 官方：输入6.5 / 输出27
    "kimi-k2.6": {"input": 6.5, "output": 27.0},    # Kimi 官方：输入6.5 / 输出27
    # —— DeepSeek（官方 RMB 永久价）——
    "deepseek-v4-flash": {"input": 1.0, "output": 2.0}, # 官方：输入1 / 输出2
    "deepseek-v4-pro": {"input": 3.0, "output": 6.0},  # 官方永久价：输入3 / 输出6
}

# ── 默认模型（兜底映射）───────────────────────────────────
# trace 中若 modelInfo 缺失，collect 会兜底记为字面量 "default"（见 collect_traces L341）。
# 这是「早期 7 月孤儿 trace 同时缺 sessionId 与 modelInfo」造成的占位符，无法从数据自动解析，
# 故需手动指定你 WorkBuddy 的「默认模型」——即选 default 时实际跑的付费模型。
# ⚠️ TODO（请按你 WorkBuddy 设置里的默认模型修改此值）：常见候选 glm-5.2 / custom-local:GLM-4.5-air /
#   custom-local:glm-4.6v 等；改这一个常量即可，下游计价与警告卡自动跟随。
DEFAULT_MODEL = "glm-5.2"

def resolve_model(name):
    """把 trace 兜底字面量 'default' 映射到用户默认付费模型；其余名原样返回。

    仅 'default' 被替换——'auto'(router)、真实模型名、'custom-local:*' 均不动。
    """
    if normalize_model(name) == "default":
        return DEFAULT_MODEL
    return name

# 路由别名集合：这些不是具体模型，而是「自动调配」入口（如 auto）。
# 其单价无法固定，代码在 aggregate_by_model 中自动取「所有计费模型（单价>0）的均价」做代表性估算，
# 并在结果中标记 is_router=True，供报告注明「估算值」。
ROUTER_ALIASES = {"auto", "default"}

# 已知的「智能路由 / 聚合网关」外部供应商（vendor 或 url host 命中即视为路由器）。
# 这些服务一次调用可能落到不同底层模型 / 上游 host（如 OpenRouter 按价格+容量挑 provider、
# Groq 在自有开源模型间 fallback）。报告里据此打 🔀 标记并提示「单价/花费为粗略参考」。
# 注意：SiliconFlow（硅基流动）不自托管推理平台，每个模型固定一个 endpoint、
# 单价公开明确，不算智能路由——不加 🔀，按普通外部 API（🔧）处理。
# SiliconFlow 模型在 trace 中常以 "Vendor/Model" 形式出现（如 zai-org/GLM-5.2、
# moonshotai/kimi-k2.7-code），但 sessions.model 可能剥掉 custom-local: 前缀裸存为
# "glm-5.2"，导致被误判为 WorkBuddy 官方入口。以下 vendor 前缀命中时强制判为
# custom-local 通道，确保不计入官方 gateway 统计。
SILICONFLOW_VENDOR_PREFIXES = (
    "zai-org/",
    "z.ai/",
    "moonshotai/",
    "meituan-longcat/",
    "qwen/",
    "thudm/",
    "deepseek-ai/",
)
ROUTER_VENDORS = {
    "openrouter", "request", "requesty", "haimaker", "lockllm", "groq", "together",
    "fireworks", "cloudflare", "portkey", "truefoundry", "nanogpt", "tokenmix",
    "eden", "edenai", "helicone", "bifrost", "oneapi", "litellm", "ai21",
    # 国内聚合 / 路由网关
    "302", "volcengine", "openllmapi",
}
ROUTER_HOSTS = (
    "openrouter.com", "requesty.ai", "haimaker.ai", "lockllm", "groq.com",
    "api.groq.com", "together.xyz", "fireworks.ai", "cloudflare.com",
    "workers.ai", "portkey.ai", "truefoundry.ai", "tokenmix.ai", "eden.ai",
    # 国内聚合 / 路由网关 host
    "302.ai", "volces.com", "ark.cn-beijing.volces.com", "openllmapi.com",
)

# 未配置单价的模型回退使用的历史 blended 估算单价（元/1M），
# 仅供既有「实际成本」表格保持现有显示，不用于新的「模型成本对比」章节。
DEFAULT_BLENDED_PER_MILLION = 1.0

# 提示词前缀缓存命中 token 的计费折扣（行业惯例约 1/10 价）。
# cached_tokens 是「已消耗但打折」的 token：模型确实读取并处理了它们，
# 只是命中前缀缓存后按约 input 的 10% 计费，而非全新输入的全价。
# 因此「实际消耗（计费等效）= 原始 Token − 缓存命中 ×(1−折扣)」，
# 既承认它消耗了，又不被重复读上下文夸大。
CACHE_DISCOUNT = 0.1


def effective_tokens_of(total_tokens, cached_tokens):
    """计费等效 token：原始 token 减去缓存命中享受的折扣量（cached 是 input 子集）。

    返回整数——token 是离散单位，浮点减法会产生 4,437,750.399999999 这类噪声，
    取整后再聚合可避免报告里出现无意义的浮点尾巴。
    """
    return max(int(round(total_tokens - (cached_tokens or 0) * (1 - CACHE_DISCOUNT))), 0)

# ── 路径常量 ──────────────────────────────────────────────
HOME = Path.home()
WB_DIR = HOME / ".workbuddy"
TRACES_DIR = WB_DIR / "traces"
SESSIONS_DIR = WB_DIR / "sessions"
PROJECTS_DIR = WB_DIR / "projects"
DB_PATH = WB_DIR / "workbuddy.db"
USAGE_LOG_PATH = WB_DIR / "usage-log.json"
WORKBUDDY_SESSIONS = HOME / "WorkBuddy"

# 时区：GMT+8
TZ = timezone(timedelta(hours=8))


def ts_to_date(ts, tz=TZ):
    """Unix 毫秒时间戳 → 'YYYY-MM-DD' 字符串"""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts / 1000, tz).strftime("%Y-%m-%d")

def normalize_model(name):
    """模型名归一化：去空白、转小写；保留 :free 等后缀（其决定免费）。"""
    if not name:
        return "default"
    return name.strip().lower()


# ── GLM-5.2 夜猫子计划（夜间折扣）──────────────────────────────
# 官方「夜猫子错峰优惠计划」：GLM-5.2 专属，自 2026-07-16 起上线（已联网核实）。
# 消耗系数（Credits 抵扣系数，已用账单 Credits 反推核实，并经用户澄清修正）：
#   · glm-5.2（官方，裸名）：恒 0.79x（基础/白天消耗速度，无额外折扣）
#   · glm-5.2-x / glm-5.2x：恒 0.5x —— 它本身就是「WorkBuddy 晚间调用 glm-5.2」在 trace
#     中的记录名，即夜间折扣已编码进模型名，无需按时间戳现算。
# 该系数仅作用于 GLM-5.2 家族裸名（glm-5.2 / glm-5.2-x / glm-5.2x），不影响其他模型；
# 乘数作用于价目表 8/28（视为 1.0x 基准价）折算真实消耗。若 8/28 本就已是白天 0.79x 价，
# 把 GLM52_RATE["glm-5.2"] 改为 1.0 即可（-x 夜间则改为 0.5/0.79≈0.633）。
GLM52_FAMILY = {"glm-5.2", "glm-5.2-x", "glm-5.2x"}
GLM52_RATE = {"glm-5.2": 0.79, "glm-5.2-x": 0.50, "glm-5.2x": 0.50}


def glm52_discount_multiplier(model_name):
    """返回 GLM-5.2 家族消耗的折扣系数；非家族模型返回 1.0（不改其他模型计费）。

    系数按模型名确定：glm-5.2=0.79x（官方基础消耗速度），glm-5.2-x=0.5x（夜间折扣版，
    WorkBuddy 把「晚间调用 glm-5.2」记录为 -x）。夜间折扣已编码进模型名，无需时段判定。
    """
    return GLM52_RATE.get(normalize_model(model_name), 1.0)


# 用户自建本地接口（custom-local:*）的单价「覆盖」表。
# 键为去掉 "custom-local:" 前缀后的底层模型名（小写）。留空 {} 表示「未单独配置」，
# 此时 price_of 默认对齐默认网关下同名模型的单价（如 custom-local:glm-4.6v 按 glm-4.6v 计）。
# 若你的自建接口走的是别的账单（如自己的 OpenRouter / bigmodel 账号且单价不同），
# 在此按 "glm-4.6v": {"input": x, "output": y} 填写即可覆盖默认对齐。
CUSTOM_LOCAL_PRICING = {}


# ── 定价配置外置（pricing.json + pricing.local.json）───────────
# 上方 MODEL_PRICING / TIMED_FREE / CUSTOM_LOCAL_PRICING / DEFAULT_MODEL 为内置默认值。
# 1. scripts/pricing.json：发布版自带（仅 WorkBuddy 官方内置模型 + auto），开发者维护；不含用户第三方/自建模型。
# 2. scripts/pricing.local.json：用户本地覆盖（自定义模型等），**不在发布包里**，
#    因此 skillhub upgrade 整目录覆盖时不会被触及，用户自定义模型单价得以保留。
# 文件缺失 / 解析失败时自动回退，保证技能仍可独立运行。
def _load_pricing_config():
    """从 pricing.json 加载发布版定价，再合并 pricing.local.json 用户本地覆盖。"""
    here = Path(__file__).resolve().parent
    cfg = {
        "models": dict(MODEL_PRICING),
        "timed_free": dict(TIMED_FREE),
        "custom_local": dict(CUSTOM_LOCAL_PRICING),
        "default_model": DEFAULT_MODEL,
        "delisted_models": set(),
        "user_custom_models": set(),
    }
    cfg_path = here / "pricing.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data.get("models"), dict):
                cfg["models"].update(data["models"])
            if isinstance(data.get("timed_free"), dict):
                cfg["timed_free"].update(data["timed_free"])
            if isinstance(data.get("delisted"), dict):
                # 已下架的官方模型：仍并入 models 以便历史调用计价，并记录到下架集合供报告标注
                cfg["models"].update(data["delisted"])
                cfg["delisted_models"].update(normalize_model(k) for k in data["delisted"])
            if isinstance(data.get("user_custom_models"), (list, tuple, set)):
                # 用户声明覆盖：这些模型名虽可能经 gateway 调用，但实为外部 API 自定义接入
                cfg["user_custom_models"].update(normalize_model(x) for x in data["user_custom_models"])
            if isinstance(data.get("custom_local"), dict):
                cfg["custom_local"].update(data["custom_local"])
            if data.get("default_model"):
                cfg["default_model"] = str(data["default_model"])
        except Exception as e:
            print(f"[WARN] 读取 pricing.json 失败，回退内置定价：{e}", file=sys.stderr)

    local_loaded = False
    local_path = here / "pricing.local.json"
    if local_path.exists():
        try:
            local = json.loads(local_path.read_text(encoding="utf-8"))
            if isinstance(local.get("models"), dict):
                cfg["models"].update(local["models"])
            if isinstance(local.get("delisted"), dict):
                cfg["models"].update(local["delisted"])
                cfg["delisted_models"].update(normalize_model(k) for k in local["delisted"])
            if isinstance(local.get("custom_local"), dict):
                cfg["custom_local"].update(local["custom_local"])
            if isinstance(local.get("user_custom_models"), (list, tuple, set)):
                cfg["user_custom_models"].update(normalize_model(x) for x in local["user_custom_models"])
            if isinstance(local.get("timed_free"), dict):
                cfg["timed_free"].update(local["timed_free"])
            if local.get("default_model"):
                cfg["default_model"] = str(local["default_model"])
            local_loaded = True
        except Exception as e:
            print(f"[WARN] 读取 pricing.local.json 失败，忽略本地覆盖：{e}", file=sys.stderr)
    return cfg, local_loaded

_PRICING, _PRICING_LOCAL_LOADED = _load_pricing_config()
MODEL_PRICING = _PRICING["models"]
TIMED_FREE = _PRICING["timed_free"]
CUSTOM_LOCAL_PRICING = _PRICING["custom_local"]
DEFAULT_MODEL = _PRICING["default_model"]
DELISTED_MODELS = _PRICING.get("delisted_models", set())
USER_CUSTOM_MODELS = _PRICING.get("user_custom_models", set())


def discover_custom_models():
    """从 WorkBuddy 用户模型配置(~/.workbuddy/models.json)自动识别「用户自建模型」，并区分本地/外部。

    返回 (local, external) 两个集合：
      - local：Ollama 本地推理（vendor="ollama" 或 url 含 localhost:11434）。零 API 成本，
        报告里标 🏠、单价强制 0、不计入账单总额。
      - external：外部 API 接口自建（vendor="custom" 或 url 含 openrouter）。可能仍有费用，标 🔧。
    即使模型名也命中官方集（如 GLM-4.7-Flash 同时是官方下架模型），只要用户以自定义端点
    配置即优先判为自定义，避免被误标为官方网关（解决"撞名官方模型走 gateway"的误识别）。
    官方模型被重配（vendor=Kimi/DeepSeek/GLM）不标记。文件缺失/解析失败返回空集，
    回退到 pricing.local.json 的 user_custom_models 人工兜底。
    """
    local = set()
    external = set()
    router = set()
    path = os.path.expanduser("~/.workbuddy/models.json")
    try:
        with open(path, encoding="utf-8") as _f:
            data = json.load(_f)
    except (OSError, json.JSONDecodeError):
        return local, external, router
    entries = data if isinstance(data, list) else data.get("models", [])
    for e in entries:
        if not isinstance(e, dict):
            continue
        mid = normalize_model(e.get("id", ""))
        if not mid:
            continue
        vendor = (e.get("vendor") or "").lower()
        url = (e.get("url") or "").lower()
        # 智能路由 / 聚合网关（外部 API）：一次调用可能落到不同底层模型或上游 host。
        # 通过 vendor 或 url host 识别（权威来源是 models.json，与本地/外部识别同源）。
        is_router = vendor in ROUTER_VENDORS or any(h in url for h in ROUTER_HOSTS)
        if "localhost:11434" in url:
            local.add(mid)
        elif vendor == "ollama":
            local.add(mid)
        elif vendor == "custom" or "openrouter" in url or is_router:
            external.add(mid)
            if is_router:
                router.add(mid)
    return local, external, router


# 运行时自动发现 + 人工兜底清单(user_custom_models，应对 models.json 不可读等极端情况)
DISCOVERED_LOCAL, DISCOVERED_EXTERNAL, DISCOVERED_ROUTER = discover_custom_models()
# 本地模型仅来自自动发现（localhost:11434 必然是本地推理）；外部 + 人工兜底并入自定义集合
ALL_LOCAL_MODELS = DISCOVERED_LOCAL
ALL_CUSTOM_MODELS = USER_CUSTOM_MODELS | DISCOVERED_LOCAL | DISCOVERED_EXTERNAL
# 智能路由 / 聚合网关外部模型（OpenRouter / Groq / Requesty / haimaker / LockLLM /
# SiliconFlow 硅基流动 / 302.AI / 火山方舟 等国内外平台），
# 用于报告里打 🔀 标记并提示「一次调用可能落到不同底层模型」。
ALL_ROUTER_MODELS = DISCOVERED_ROUTER


def parse_channel(raw_model_id):
    """从模型标识符解析「API 接口通道」。

    模型标识符本身编码了接口位置（并非独立字段，trace 还会把前缀剥掉）：
      - "auto"                       → ("router", "auto")             WorkBuddy 默认网关内的智能路由
      - "custom-local:GLM-4.5-air"   → ("custom-local", "glm-4.5-air") 用户自建本地接口
      - "inclusionai/...:free"       → ("openrouter-free", raw)        OpenRouter 免费网关（凭 :free 判定）
      - "custom-local:z.ai/GLM-5.2"  → ("custom-local", "z.ai/GLM-5.2") 用户自建接口（含 / 但非免费）
      - "glm-5.2" / "hy3" 等裸名     → ("gateway", raw)                WorkBuddy 默认网关
    返回 (channel, base_model)。base_model 为去掉通道前缀后的底层模型名。
    """
    raw = (raw_model_id or "default").strip()
    n = raw.lower()
    if n == "auto":
        return ("router", "auto")
    # OpenRouter 免费档：含 :free 后缀即免费（无论是否套了 custom-local 前缀）→ 价 0
    # 注意：仅凭 / 不再判定为 openrouter-free——SiliconFlow 等聚合网关也用 Vendor/Model 命名，
    # 其中不少是付费模型（如 z.ai/GLM-5.2），凭 / 当免费会少算钱。
    if ":free" in n:
        return ("openrouter-free", raw)
    if n.startswith("custom-local:"):
        return ("custom-local", raw[len("custom-local:"):].strip())
    # SiliconFlow 等第三方 API 接入：trace 中保留 Vendor/Model 前缀，但 sessions.model
    # 可能未带 custom-local: 前缀；凭 vendor 前缀强制判为 custom-local，避免混入官方入口。
    if any(n.startswith(p) for p in SILICONFLOW_VENDOR_PREFIXES):
        return ("custom-local", raw)
    return ("gateway", raw)


def is_timed_free(model_name, as_of_date):
    """判断模型在 as_of_date('YYYY-MM-DD') 是否处于限时免费期内（含截止当天）。

    仅看「模型名 + 日期」：与通道无关（hy3 无论走默认网关还是 custom-local 都适用）。
    无截止配置或日期缺失则返回 False。
    """
    if not as_of_date:
        return False
    deadline = TIMED_FREE.get(normalize_model(model_name))
    return bool(deadline) and as_of_date <= deadline


def price_of(model_name, channel=None, as_of_date=None):
    """返回 (input_per_million, output_per_million) 元；未配置 / 未知返回 (None, None)。

    channel 为空时自动从 model_name 解析（支持 custom-local:/:free/auto 等编码）。
    as_of_date('YYYY-MM-DD') 用于限时免费判定：若模型在该日期处于 TIMED_FREE 期内，返回 (0.0, 0.0)。
    """
    model_name = resolve_model(model_name)
    if channel is None:
        channel, model_name = parse_channel(model_name)
    if channel == "openrouter-free":
        return (0.0, 0.0)
    # 本地模型（Ollama / localhost:11434）零 API 成本：强制归零，杜绝借用同名官方价
    # （如 custom-local 通道的 glm-4.5-air 原本会查到官方下架价 1.2/8.0）。不计入账单总额。
    # 注意去前缀：显式传入 channel 时 model_name 可能仍带 custom-local: 前缀。
    if normalize_model(model_name.split("custom-local:", 1)[-1]) in ALL_LOCAL_MODELS:
        return (0.0, 0.0)
    if channel == "router":
        return (None, None)
    # 限时免费：在截止日（含）之前的调用免费（与通道无关，gateway / custom-local 均适用）
    if as_of_date and is_timed_free(model_name, as_of_date):
        return (0.0, 0.0)
    if channel == "custom-local":
        # 优先用用户覆盖表；否则默认对齐默认网关下同名模型的单价
        ov = CUSTOM_LOCAL_PRICING.get(normalize_model(model_name))
        if ov:
            return (ov.get("input"), ov.get("output"))
        p = MODEL_PRICING.get(normalize_model(model_name))
        if p:
            return (p.get("input"), p.get("output"))
        return (None, None)
    # gateway（默认网关）
    p = MODEL_PRICING.get(normalize_model(model_name))
    if not p:
        return (None, None)
    return (p.get("input"), p.get("output"))


def compute_cost(input_tokens, output_tokens, model_name, channel=None):
    """按输入/输出单价精确计算成本（元）。未配置单价返回 None。"""
    ip, op = price_of(model_name, channel)
    if ip is None or op is None:
        return None
    return round((input_tokens / 1_000_000) * ip + (output_tokens / 1_000_000) * op, 4)


def trace_cost(input_tokens, output_tokens, model_name, channel=None):
    """用于既有表格的成本：配置了单价用精确值；未配置回退历史 blended 估算，保持现有显示不破。"""
    c = compute_cost(input_tokens, output_tokens, model_name, channel)
    if c is None:
        return round((input_tokens + output_tokens) / 1_000_000 * DEFAULT_BLENDED_PER_MILLION, 4)
    return c

# 删除 classify_model 函数，因为不再需要模型分类功能


def ts_to_dt(ts, tz=TZ):
    """Unix 毫秒时间戳 → datetime"""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts / 1000, tz)


def iso_to_date(iso_str, tz=TZ):
    """ISO 8601 字符串 → 'YYYY-MM-DD' 字符串"""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(tz).strftime("%Y-%m-%d")
    except Exception:
        return None


def parse_date_range(days):
    """返回 (start_date, end_date) 字符串（滚动 N 天，含今天）"""
    end = datetime.now(TZ)
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ── 时间窗口（可调节：天/周/月/年/自定义） ─────────────────────
PERIOD_DAYS = {
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}
PERIOD_LABELS = {
    "day": "日报",
    "week": "周报",
    "month": "月报",
    "year": "年报",
    "custom": "自定义报告",
}
PERIOD_SHORT = {
    "day": "当日",
    "week": "本周",
    "month": "本月",
    "year": "本年",
    "custom": "本期",
}
PERIOD_NEXT = {
    "day": "次日",
    "week": "下周",
    "month": "下月",
    "year": "明年",
    "custom": "下期",
}


def _to_num(v, default=0):
    """安全地把值转成 float；无法转换（脏数据/字符串/None）时回退默认值，避免整段采集崩溃。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def resolve_date_range(period=None, days=None, start=None, end=None):
    """解析时间窗口，返回 (start_date, end_date, period_key, period_label)。

    优先级：绝对日期(start+end) > 自定义天数(days) > 预设周期(period)。
    - 绝对日期：直接使用给定起止（含两端）。
    - 自定义天数：最近 N 天（滚动窗口，含今天）。
    - 预设周期（日历对齐，与生成时刻无关）：
      day=今天 / week=当前日历周(周一~周日) / month=当前自然月(1日~月底) / year=当前自然年(1/1~12/31)。
    """
    if start and end:
        # 若起止恰好对应标准周期长度，识别为对应报告类型（而非「自定义报告」），
        # 使「报告类型」能以日历日期呈现：
        #   单日 → 日报；完整 7 天 → 周报；完整月 → 月报；完整年 → 年报。
        try:
            sd = datetime.strptime(start, "%Y-%m-%d")
            ed = datetime.strptime(end, "%Y-%m-%d")
        except ValueError:
            raise ValueError(
                f"无效的 --start/--end 日期：需要 YYYY-MM-DD 格式，"
                f"收到 start={start!r} end={end!r}"
            )
        span = (ed - sd).days + 1
        today = datetime.now(TZ).date()
        if span == 1:
            return start, end, "day", PERIOD_LABELS["day"]
        # 完整自然年（1/1~12/31）或 年初至今（1/1~今年内且至今日）→ 年报
        if sd.month == 1 and sd.day == 1 and ed.year == sd.year:
            if (ed.month == 12 and ed.day == 31) or ed.date() >= today:
                return start, end, "year", PERIOD_LABELS["year"]
        # 完整自然月 → 月报
        if sd.day == 1 and ed.year == sd.year and ed.month == sd.month:
            last_day = calendar.monthrange(sd.year, sd.month)[1]
            if ed.day == last_day:
                return start, end, "month", PERIOD_LABELS["month"]
        # 完整 7 天 → 周报
        if span == 7:
            return start, end, "week", PERIOD_LABELS["week"]
        return start, end, "custom", PERIOD_LABELS["custom"]
    if days is not None:
        e = datetime.now(TZ)
        s = e - timedelta(days=days)
        return (s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d"),
                "custom", f"自定义（最近 {days} 天）")
    pk = period or "week"
    today = datetime.now(TZ).date()
    if pk == "day":
        d = today.strftime("%Y-%m-%d")
        return d, d, "day", PERIOD_LABELS["day"]
    if pk == "week":
        # 当前日历周（周一 ~ 周日，ISO 周），不论生成日在周中还是周末
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return (monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d"),
                "week", PERIOD_LABELS["week"])
    if pk == "month":
        # 当前自然月（1 日 ~ 月底），不论生成日在月内哪一天
        first = today.replace(day=1)
        last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return (first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d"),
                "month", PERIOD_LABELS["month"])
    if pk == "year":
        # 当前自然年（1/1 ~ 12/31）
        y = today.year
        return f"{y}-01-01", f"{y}-12-31", "year", PERIOD_LABELS["year"]
    # 兜底（choices 已约束，正常不会到这）：保留滚动窗口行为
    n = PERIOD_DAYS.get(pk, 7)
    e = datetime.now(TZ)
    s = e - timedelta(days=n)
    return s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d"), pk, PERIOD_LABELS.get(pk, "周报")


# ── 1. 采集 Trace 数据（token 消耗） ─────────────────────
def collect_traces(start_date, end_date, sid_to_rawmodel=None):
    """扫描 traces 目录，提取 token 消耗数据（含成本）。

    sid_to_rawmodel: {session_id: 带通道前缀的原始模型标识符}，来自 workbuddy.db 的
    sessions.model（如 "custom-local:glm-4.6v"）。用于把 trace 关联到真实 API 接口通道——
    因为 trace 的 modelInfo 会把前缀剥掉，只有 sessions.model 保留通道真相。

    成本口径统一规则（修复「概览 / 每日 / 模型章节总额互不一致」）：
      - 配置了单价的模型：输入 / 输出分别精确计价；
      - 路由别名（auto 等）：使用「本周期实际出现的已计费模型（单价>0）的均价」估算，
        与 aggregate_by_model 对 auto 行的计价完全一致 —— 故概览总额 ≡ 3.1 合计 ≡ 每日合计；
      - 未配置单价的模型：成本记 0（与模型章节「未配置」一致），不再用历史 blended 兜底，
        避免概览凭空多出一笔。
    缓存命中按 CACHE_DISCOUNT 折扣计入「实际消耗（计费等效）」。
    """
    if not TRACES_DIR.exists():
        return []
    sid_map = sid_to_rawmodel or {}

    # —— 第一遍：解析各 trace 元数据与 token，并收集本周期出现的模型以计算路由均价 ——
    parsed = []
    present_models = set()
    for pid_dir in TRACES_DIR.iterdir():
        if not pid_dir.is_dir():
            continue
        for trace_file in pid_dir.glob("trace_*.json"):
            try:
                with open(trace_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                trace = data.get("trace", {})
                trace_date = iso_to_date(trace.get("startedAt", ""))
                if not trace_date or trace_date < start_date or trace_date > end_date:
                    continue

                model_info = trace.get("modelInfo", {})
                models = model_info.get("models", [])
                bare_model = models[0] if models else "default"
                # 通道感知：优先用 sessions.model 的原始（带前缀）标识符，否则退回 trace 裸名。
                # 但若 trace 实际执行模型带 SiliconFlow 等第三方 vendor 前缀（如 zai-org/GLM-5.2），
                # 说明 sessions.model 只是选了同名官方模型做入口，真实调用走的是外部 API；
                # 此时以 trace 实际模型为准，强制判为 custom-local，避免混入官方 gateway。
                # ⚠️ 仅当会话配置「未显式声明」custom-local: 时才触发该覆盖——否则会丢失用户
                # 显式选定的自建接口前缀，导致 custom-local:zai-org/glm-5.2 与裸 zai-org/glm-5.2
                # 在 §3.1 被误并为同一行（即「同一模型的不同接口」无法区分）。
                sid = trace.get("sessionId", "")
                raw_model = sid_map.get(sid, bare_model)
                raw_is_custom = raw_model.lower().startswith("custom-local:")
                if (not raw_is_custom) and \
                   any(bare_model.lower().startswith(p) for p in SILICONFLOW_VENDOR_PREFIXES):
                    raw_model = bare_model
                channel, base_model = parse_channel(raw_model)

                parsed.append({
                    "trace_id": trace.get("traceId", ""),
                    "pid": int(_to_num(pid_dir.name, 0)),
                    "date": trace_date,
                    "started_at": trace.get("startedAt", ""),
                    "ended_at": trace.get("endedAt", ""),
                    "duration_ms": _to_num(trace.get("duration", 0)),
                    "status": trace.get("status", "unknown"),
                    "session_id": sid,
                    "total_tokens": _to_num(trace.get("totalTokens", 0)),
                    "input_tokens": _to_num(model_info.get("totalInputTokens", 0)),
                    "output_tokens": _to_num(model_info.get("totalOutputTokens", 0)),
                    "cached_tokens": _to_num(model_info.get("totalCachedTokens", 0)),
                    "call_count": _to_num(model_info.get("callCount", 0)),
                    "models": models,
                    "model_name": base_model,   # 裸底层模型名（来自 session 配置去前缀）
                    "exec_model": bare_model,   # 裸底层模型名（来自 trace 的 modelInfo.models[0]，即 API 实际执行的真实模型）
                    "model_key": raw_model,     # 带通道前缀的原始标识符（模型维度聚合键）
                    "channel": channel,         # 接口通道
                    "raw_model": raw_model,
                })
                present_models.add(raw_model)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

    # 路由均价：本周期出现的、已配置且单价>0 的模型（去重后等权平均），与 aggregate_by_model 一致
    paid = []
    for rm in present_models:
        ip, op = price_of(rm)
        if ip is not None and op is not None and (ip > 0 or op > 0):
            paid.append((ip, op))
    router_avg_ip = (sum(ip for ip, op in paid) / len(paid)) if paid else None
    router_avg_op = (sum(op for ip, op in paid) / len(paid)) if paid else None

    # —— 第二遍：统一口径计价 ——
    traces = []
    for p in parsed:
        channel = p["channel"]
        raw_model = p["raw_model"]
        input_tokens = p["input_tokens"]
        output_tokens = p["output_tokens"]
        cached_tokens = p["cached_tokens"]

        # 选定本 trace 的单价：路由别名用均价；限时免费模型按 trace 日期判 0；否则用模型真实单价
        pricing_model = resolve_model(p.get("exec_model") or p.get("raw_model"))
        use_router_avg = (channel == "router" and router_avg_ip is not None)
        if use_router_avg:
            ip, op = router_avg_ip, router_avg_op
        elif is_timed_free(pricing_model, p.get("date")):
            ip, op = 0.0, 0.0
        else:
            ip, op = price_of(pricing_model, as_of_date=p.get("date"))

        if ip is not None and op is not None:
            input_cost = (input_tokens / 1_000_000) * ip
            output_cost = (output_tokens / 1_000_000) * op
            cost = input_cost + output_cost
            eff_in = max(input_tokens - cached_tokens * (1 - CACHE_DISCOUNT), 0)
            eff_cost = (eff_in / 1_000_000) * ip + (output_tokens / 1_000_000) * op
            # GLM-5.2 夜猫子计划：按模型名定率（glm-5.2=0.79x / glm-5.2-x=0.5x）
            _mult = glm52_discount_multiplier(pricing_model)
            cost *= _mult
            eff_cost *= _mult
        else:
            input_cost = output_cost = cost = eff_cost = 0.0

        # 计费等效 token：原始 token 减去缓存命中享受的折扣量（与 aggregate 口径一致）
        eff_tokens = effective_tokens_of(p["total_tokens"], cached_tokens)
        p.update({
            "total_cost": round(cost, 4),
            "input_cost": round(input_cost, 4),
            "output_cost": round(output_cost, 4),
            "effective_tokens": eff_tokens,
            "effective_cost": round(eff_cost, 4),
            "is_free": is_timed_free(pricing_model, p.get("date")),
        })
        traces.append(p)

    traces.sort(key=lambda x: x["started_at"])
    return traces


# ── 2. 采集 DB 数据（会话、自动化、信用） ─────────────────
def collect_db_data(start_date, end_date):
    """从 workbuddy.db 采集会话、自动化运行、信用消耗数据"""
    result = {"sessions": [], "automation_runs": [], "session_credits": []}
    if not DB_PATH.exists():
        return result

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=TZ).timestamp() * 1000)
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=TZ).timestamp() * 1000) + 86400000

    # 会话
    try:
        rows = db.execute(
            "SELECT * FROM sessions WHERE created_at >= ? AND created_at < ? AND deleted_at IS NULL ORDER BY created_at",
            (start_ts, end_ts),
        ).fetchall()
        for r in rows:
            result["sessions"].append({
                "id": r["id"],
                "cwd": r["cwd"],
                "title": r["title"] or "",
                "custom_title": r["custom_title"] or "",
                "status": r["status"],
                "created_at": r["created_at"],
                "created_date": ts_to_date(r["created_at"]),
                "updated_at": r["updated_at"],
                "mode": r["mode"],
                "model": r["model"],
                "is_background_automation": bool(r["is_background_automation"]),
            })
    except Exception as e:
        print(f"[WARN] sessions query: {e}", file=sys.stderr)

    # 自动化任务名称映射（用于报告展示，避免只显示任务 ID）
    # 注意：不过滤 deleted_at —— 已软删除的自动化仍可能留有历史运行记录，
    # 映射全部任务名才能保证报告里显示名称而非裸 ID。
    try:
        rows = db.execute("SELECT id, name, status, deleted_at FROM automations").fetchall()
        auto_names = {r["id"]: r["name"] for r in rows}
        # 记录每个自动化定义的状态：ACTIVE=执行中；PAUSED=已暂停；deleted_at 非空=已删除。
        # 用于报告层区分「正在执行的自动化」与「已停止的自动化」。
        auto_status = {}
        for r in rows:
            if r["deleted_at"]:
                auto_status[r["id"]] = "DELETED"
            else:
                auto_status[r["id"]] = (r["status"] or "UNKNOWN")
    except Exception as e:
        print(f"[WARN] automations query: {e}", file=sys.stderr)
        auto_names = {}
        auto_status = {}

    # 自动化运行
    try:
        rows = db.execute(
            "SELECT * FROM automation_runs WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
            (start_ts, end_ts),
        ).fetchall()
        for r in rows:
            result["automation_runs"].append({
                "thread_id": r["thread_id"],
                "automation_id": r["automation_id"],
                "automation_name": auto_names.get(r["automation_id"], ""),
                "status": r["status"],
                "result_success": r["result_success"],
                "auto_status": auto_status.get(r["automation_id"], "UNKNOWN"),
                "created_at": r["created_at"],
                "created_date": ts_to_date(r["created_at"]),
                "updated_at": r["updated_at"],
                "thread_title": (r["thread_title"] or "")[:500],
                "source_cwd": r["source_cwd"],
                "metadata_json": r["metadata_json"],
            })
    except Exception as e:
        print(f"[WARN] automation_runs query: {e}", file=sys.stderr)

    # 会话信用消耗
    try:
        rows = db.execute(
            "SELECT * FROM session_usage WHERE updated_at >= ? AND updated_at < ? ORDER BY updated_at",
            (start_ts, end_ts),
        ).fetchall()
        for r in rows:
            credit = {}
            if r["credit_json"]:
                try:
                    credit = json.loads(r["credit_json"])
                except json.JSONDecodeError:
                    pass
            result["session_credits"].append({
                "session_id": r["session_id"],
                "used": r["used"],
                "size": r["size"],
                "updated_at": r["updated_at"],
                "updated_date": ts_to_date(r["updated_at"]),
                "credits": credit,
            })
    except Exception as e:
        print(f"[WARN] session_usage query: {e}", file=sys.stderr)

    db.close()
    return result


# ── 3. 采集技能使用记录 ───────────────────────────────────
def collect_skill_usage(start_date, end_date):
    """从 usage-log.json 采集技能使用数据"""
    if not USAGE_LOG_PATH.exists():
        return {"skills": {}, "active_days": []}

    with open(USAGE_LOG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    skills = {}
    for sid, sdata in data.get("skills", {}).items():
        recent = [d for d in sdata.get("recentDates", []) if start_date <= d <= end_date]
        if recent or (sdata.get("lastUsedDate") and start_date <= sdata["lastUsedDate"] <= end_date):
            skills[sid] = {
                "last_used": sdata.get("lastUsedDate", ""),
                "first_seen": sdata.get("firstSeenDate", ""),
                "recent_dates_in_range": recent,
                "usage_count_in_range": len(recent),
            }

    active_days = [d for d in data.get("activeDays", []) if start_date <= d <= end_date]
    return {"skills": skills, "active_days": active_days}


# ── 4. 采集会话产出文件和记忆日志 ─────────────────────────
def collect_session_outputs(start_date, end_date):
    """扫描 WorkBuddy 会话目录，提取产出文件和记忆日志"""
    outputs = []
    memory_logs = {}

    if not WORKBUDDY_SESSIONS.exists():
        return outputs, memory_logs

    # 从目录名解析日期
    dir_pattern = re.compile(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})")

    for session_dir in WORKBUDDY_SESSIONS.iterdir():
        if not session_dir.is_dir():
            continue
        m = dir_pattern.match(session_dir.name)
        if not m:
            continue
        dir_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if dir_date < start_date or dir_date > end_date:
            continue

        # 产出文件（非隐藏文件）
        for item in session_dir.iterdir():
            if item.is_file() and not item.name.startswith("."):
                outputs.append({
                    "file_name": item.name,
                    "file_path": str(item),
                    "date": dir_date,
                    "size_bytes": item.stat().st_size,
                    "extension": item.suffix,
                })

        # 记忆日志
        mem_dir = session_dir / ".workbuddy" / "memory"
        if mem_dir.exists():
            for mem_file in mem_dir.glob("*.md"):
                try:
                    content = mem_file.read_text(encoding="utf-8")
                    log_date = mem_file.stem  # YYYY-MM-DD
                    if log_date not in memory_logs:
                        memory_logs[log_date] = []
                    memory_logs[log_date].append({
                        "file": str(mem_file),
                        "content": content,
                        "session_dir": session_dir.name,
                    })
                except Exception:
                    continue

    outputs.sort(key=lambda x: x["date"])
    return outputs, memory_logs


# ── 5. 任务类型分类 ───────────────────────────────────────
# 顺序敏感：通用词靠后的类型须排在更具体的代码/技能类之前或之后需权衡。
# 关键约定：
#   1) 技能开发 / 代码开发 排在 自动化配置 之前——后者的 "automation/自动化"
#      单词极易在代码/agent 会话里 incidental 命中，会抢走真正的开发类任务；
#      自动化配置只保留高置信短语（定时/提醒/领券/每日任务/配置自动化 等）。
#   2) 内容生成 排在 代码开发 之前——除对话内容里的短剧/剧本/分镜/小说/文案等创作信号外，
#      更可靠的依据是「生成物指纹」（见 get_session_artifact_fingerprint）：
#      transcript 中的 ImageGen/VideoGen 工具调用、以及 file-history-snapshot 保留的
#      「已删除」图片/视频文件名。凡出现 imagegen/videogen 或 [artifacts] 媒体文件名，
#      即可判定为内容生成，即便对话 jsonl 已被归档、或产物已删除。
#      例：创建AI短剧生成团队（连续多轮中长文本/分镜/角色/视频生成）归内容生成。
#      注意：agent.*team/多智能体/创建.*团队 在内容生成排除后仍用于代码开发；
#      仅含"团队"而无内容创作/生成物信号时（如搭建开发团队协作规范）仍归代码开发。
#   3) 研究学习 / Bug修复 通用词（学习/研究/调试）排在代码类之后会被 python 抢先，
#      故 研究学习 仍置于靠后、Bug修复 置于较前（修复类信号较强）。
TASK_TYPE_RULES = [
    # 1. 技能安装 —— 明确安装技能的表述
    ("技能安装", [r"skillhub\s+install", r"安装.*skill", r"install.*skill", r"技能安装",
                   r"添加.*技能", r"下载.*skill|skill.*下载", r"技能\s*添加"]),
    # 2. 技能开发 —— 创作/打包新的技能
    ("技能开发", [r"SKILL\.md", r"skill.*creat", r"skill.*develop", r"技能.*创建", r"技能.*开发",
                   r"打包.*skill", r"开发.*skill", r"skill\s*开发", r"从零.*skill",
                   r"封装.*skill", r"技能.*封装", r"skill\s*打包"]),
    # 3. 报告生成 —— 生成周报、月报等报告类任务
    ("报告生成", [r"周报", r"weekly.*report", r"report.*generat", r"AI.*report",
                   r"生成.*报告", r"报告.*生成", r"月报", r"年报", r"日报",
                   r"生成.*摘要", r"统计.*报告", r"分析.*报告"]),
    # 4. Bug修复 —— 调试、错误处理
    ("Bug修复", [r"bug", r"修复", r"fix", r"误报", r"debug", r"调试", r"错误", r"速率限制",
                r"报错", r"错误.*修复", r"bug.*fix", r"异常.*处理"]),
    # 5. 内容生成 —— 创作类任务（优先级高于代码开发，避免“创建团队”误判）
    ("内容生成", [r"短剧", r"剧本", r"分镜", r"小说", r"文案", r"故事", r"剧情",
                  r"台词", r"旁白", r"文生图", r"文生视频", r"图生视频", r"AI生成.*图",
                  r"图像生成", r"长文本", r"中长文本", r"连续.*生成", r"content.*generat",
                  r"创作", r"角色设定.*(短剧|剧本|小要|分镜)",
                  r"脚本.*编写", r"剧本.*创作", r"视频脚本", r"配音稿",
                  r"营销文案", r"广告语", r"产品描述", r"产品文案",
                  # 生成物指纹（含已删除）：图片/视频生成工具调用 + 媒体文件名。
                  r"imagegen", r"videogen",
                  r"\[artifacts\][^\]]*\.(png|jpe?g|gif|webp|mp4|webm|mov)",
                  r"生成.*图片", r"生成.*视频"]),
    # 6. 代码开发 —— 编程类任务
    ("代码开发", [r"编程", r"python", r"练习", r"编写.*代码", r"实现.*功能", r"新增.*功能",
                  r"编码", r"写代码", r"代码实现", r"实现.*算法", r"调试.*脚本",
                  r"github", r"上传.*代码", r"添加.*支持", r"多格式", r"代码.*编写",
                  r"函数.*实现", r"模块.*开发", r"package.*json", r"requirements.*txt"]),
    # 7. 自动化配置 —— 自动化任务配置（高置信短语，避免 incidental 命中）
    ("自动化配置", [r"自动化.*任务", r"配置.*自动化", r"设置.*自动化", r"定时.*任务",
                   r"schedul", r"领券", r"提醒", r"每日.*任务", r"每日.*提醒",
                   r"cron.*表达式", r"周期.*任务", r"任务.*调度", r"自动化.*脚本"]),
    # 8. 环境搭建 —— 开发环境配置
    ("环境搭建", [r"SkillHub", r"环境搭建", r"配置.*python", r"install.*cli", r"环境变量",
                  r"python.*环境", r"虚拟环境.*构建", r"conda", r"poetry",
                  r"依赖.*安装", r"package.*install", r"node.*环境"]),
    # 9. 研究学习 —— 学习、调研、参考资料
    ("研究学习", [r"第一性原理", r"first\s*principle", r"学习", r"研究", r"教程",
                  r"调研", r"趋势", r"对比", r"了解", r"用途", r"主线",
                  r"学习.*笔记", r"知识.*总结", r"原理.*分析", r"文档.*阅读",
                  r"官方.*文档", r"指南.*学习"]),
    # 10. 代码分析 —— 分析、审查、诊断
    ("代码分析", [r"hermes", r"agent.*prompt", r"解构", r"分析.*项目", r"代码.*分析", r"代码结构",
                  r"健康检查", r"审查", r"解读", r"诊断", r"代码.*审查",
                  r"性能.*分析", r"代码.*诊断", r"项目.*分析"]),
    # 11. 文档编写 —— 文档、规范、流程
    ("文档编写", [r"指南", r"guide", r"文档", r"workflow", r"规范", r"流程", r"交付",
                   r"编写.*文档", r"文档.*编写", r"操作手册", r"使用说明",
                   r"安装指南", r"配置指南", r"API.*文档"]),
]


def _transform_cwd(cwd):
    """cwd 路径 → projects 目录名（与 WorkBuddy 对话存档命名一致）"""
    return "c-" + cwd.replace(":", "").lower().replace("\\", "-").replace("/", "-")


def _find_session_jsonl(session_id, cwd):
    """根据 session id 与 cwd 定位对话 transcript JSONL 文件，失败返回 None"""
    # 1. 直接路径：projects/<cwd哈希>/<id>.jsonl（近期 session id 即 UUID）
    cand = PROJECTS_DIR / _transform_cwd(cwd) / f"{session_id}.jsonl"
    if cand.exists():
        return cand
    # 2. 旧体系：numeric id → SESSIONS_DIR/<id>.json 取 UUID 再定位
    sj = SESSIONS_DIR / f"{session_id}.json"
    if sj.exists():
        try:
            uuid = json.loads(sj.read_text(encoding="utf-8")).get("sessionId")
            if uuid:
                cand = PROJECTS_DIR / _transform_cwd(cwd) / f"{uuid}.jsonl"
                if cand.exists():
                    return cand
        except Exception:
            pass
    # 3. 兜底：全局搜索
    hits = list(PROJECTS_DIR.glob(f"**/{session_id}.jsonl"))
    if hits:
        return hits[0]
    return None


# 系统注入块（<system-reminder> 内含 <user_info>/<identity_context> 等，
# 含通用的"环境"/PATH/配置 词，会污染任务分类），抽取时逐段剥离。
SYSTEM_REMINDER_RE = re.compile(r"<system-reminder.*?</system-reminder>", re.DOTALL)


def _extract_text_from_parts(raw):
    """从 content / rawContent 列表里抽取文本片段。

    兼容多种结构：
      - message.content: [{type: input_text/output_text, text: ...}]
      - reasoning.rawContent: [{type: reasoning_text, text: ...}]
      - 亦兼容裸字符串或 {content: ...} 形式
    """
    out = []
    if isinstance(raw, str):
        out.append(raw)
    elif isinstance(raw, list):
        for p in raw:
            if isinstance(p, dict):
                txt = p.get("text") or p.get("content") or ""
                if txt:
                    out.append(txt)
            elif isinstance(p, str):
                out.append(p)
    return out


# 生成物（含已删除的）指纹涉及的文件扩展名
MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
              ".mp4", ".webm", ".mov", ".avi", ".mkv", ".mp3", ".wav"}


def get_session_artifact_fingerprint(session_id, cwd):
    """抽取会话的「生成物指纹」，含**已删除**的生成物。

    生成物来源（即使产物文件已物理删除，以下记录在 transcript 里仍保留）：
      1. function_call 记录里的 ImageGen / VideoGen 工具调用 —— 确定性内容生成证据；
      2. file-history-snapshot.trackedFileBackups 的键名 —— 编辑器曾跟踪过的文件名，
         文件删除后键名仍保留，是「已删除生成物」的最佳来源（含 .png/.mp4/.html 等）。

    返回形如 "[artifacts] imagegen videogen file.png file.mp4 ..." 的归一词串，
    供 classify_task 作为对话内容的补充信号；无任何生成物时为 ""。
    """
    jl = _find_session_jsonl(session_id, cwd)
    if not jl:
        return ""
    gen_tools, media_files = [], []
    try:
        for ln in jl.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                m = json.loads(ln)
            except Exception:
                continue
            if m.get("type") == "function_call":
                blob = json.dumps(m, ensure_ascii=False)
                if "ImageGen" in blob:
                    gen_tools.append("imagegen")
                if "VideoGen" in blob:
                    gen_tools.append("videogen")
            elif m.get("type") == "file-history-snapshot":
                tb = (m.get("snapshot") or {}).get("trackedFileBackups") or {}
                for fn in tb.keys():
                    if Path(fn).suffix.lower() in MEDIA_EXTS:
                        media_files.append(Path(fn).name.lower())
    except Exception:
        pass
    if not gen_tools and not media_files:
        return ""
    parts = ["[artifacts]"]
    parts += gen_tools
    for fn in media_files[:30]:           # 限制长度，避免超大会话拖慢分类
        parts.append(fn)
    return " ".join(parts)


def get_session_content(session_id, cwd, max_chars=3000):
    """抽取会话对话文本（user/assistant 消息 + reasoning），截断以提速。

    对话内容存储于 ~/.workbuddy/projects/<cwd哈希>/<sessionId>.jsonl，
    每行一条记录：
      - type=message：role=user/assistant，content 为文本片段列表
      - type=reasoning：助手思考，真实文本在 rawContent（content 常为 []）

    关键：先把每段文本里的 <system-reminder> 注入块剥离，再计入长度并判断是否
    截断——否则首条 user 消息携带的巨型 system-reminder 会让 total 瞬间超额、
    循环在抽到任何有效内容前就 break，导致只剩裸 user_query。
    """
    jl = _find_session_jsonl(session_id, cwd)
    if not jl:
        return ""
    parts, total = [], 0
    try:
        for ln in jl.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                m = json.loads(ln)
            except Exception:
                continue
            t = m.get("type")
            if t == "message":
                raw = m.get("content")
            elif t == "reasoning":
                # 真实思考文本在 rawContent，其次 content/text/reasoning
                raw = m.get("rawContent") or m.get("content") or m.get("text") or m.get("reasoning")
            else:
                continue
            for txt in _extract_text_from_parts(raw):
                # 先剥离系统注入块，再计入长度，避免污染分类与误触发截断
                txt = SYSTEM_REMINDER_RE.sub(" ", txt)
                if not txt.strip():
                    continue
                parts.append(txt)
                total += len(txt)
                if total >= max_chars:
                    break
            if total >= max_chars:
                break
    except Exception:
        pass
    return " ".join(parts)[:max_chars]


def classify_task(session_text):
    """根据会话文本推断任务类型。

    传入的 session_text 通常已由 collect_task_types 拼接为
    「对话内容 + 生成物指纹 + 会话标题」三部分，故此处只负责关键词匹配。
    """
    text = (session_text or "").lower()
    for task_type, patterns in TASK_TYPE_RULES:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return task_type
    return "其他"


def collect_task_types(sessions):
    """为每个会话分配任务类型

    分类依据（按优先级合并，全面覆盖「对话内容 + 生成物（含已删除）」）：
      1. 后台自动化会话（is_background_automation）直接归为「自动化配置」；
      2. 其余会话的候选文本 = 对话内容 + 生成物指纹 + 会话标题 + 自定义标题：
         a) 对话内容：user/assistant 消息 + reasoning（剥离 system-reminder）；
         b) 生成物指纹：transcript 中的 ImageGen/VideoGen 调用、以及
            file-history-snapshot 保留的「已删除」文件键名（见 get_session_artifact_fingerprint）；
         c) 对话内容/生成物指纹均缺失时，回退到会话标题，保证分类稳定可复现。
    """
    task_map = {}
    for s in sessions:
        if s.get("is_background_automation"):
            task_type = "自动化配置"
        else:
            content = get_session_content(s["id"], s.get("cwd", ""))
            artifacts = get_session_artifact_fingerprint(s["id"], s.get("cwd", ""))
            title = (s.get("title", "") + " " + s.get("custom_title", "")).strip()
            combined = " ".join(p for p in (content, artifacts, title) if p)
            text = combined if combined.strip() else (s.get("title", "") + " " + s.get("custom_title", ""))
            task_type = classify_task(text)
        s["task_type"] = task_type
        task_map[s["id"]] = task_type
    return task_map


def aggregate_task_token_stats(traces, sessions):
    """按任务类型聚合 token 消耗（join traces.session_id → session.task_type）"""
    sid_to_type = {s["id"]: s.get("task_type", "其他") for s in sessions}
    tt_agg = defaultdict(lambda: {"sessions": set(), "total": 0, "input": 0,
                                  "output": 0, "cached": 0, "cost": 0.0, "calls": 0,
                                  "effective": 0, "effective_cost": 0.0})
    for t in traces:
        tt = sid_to_type.get(t.get("session_id"), "其他")
        a = tt_agg[tt]
        a["sessions"].add(t.get("session_id"))
        a["total"] += t["total_tokens"]
        a["input"] += t["input_tokens"]
        a["output"] += t["output_tokens"]
        a["cached"] += t["cached_tokens"]
        a["cost"] += t.get("total_cost", 0.0)
        a["calls"] += t.get("call_count", 0)
        a["effective"] += t.get("effective_tokens", 0)
        a["effective_cost"] += t.get("effective_cost", 0.0)
    stats = []
    for tt, a in tt_agg.items():
        stats.append({
            "task_type": tt,
            "session_count": len(a["sessions"]),
            "total_tokens": a["total"],
            "input_tokens": a["input"],
            "output_tokens": a["output"],
            "cached_tokens": a["cached"],
            "effective_tokens": a["effective"],
            "cost": round(a["cost"], 2),
            "effective_cost": round(a["effective_cost"], 2),
            "calls": a["calls"],
        })
    stats.sort(key=lambda x: x["effective_tokens"], reverse=True)
    return stats


def aggregate_top_tasks(traces, sessions, top_n=10):
    """按会话聚合 token 消耗，返回最吃 token 的 Top N 个任务对话框。

    单个会话可能对应多天的 traces，需先按 session_id 汇总；
    会话标题/任务类型取自 sessions（collect_task_types 已写入 task_type）。
    返回字段：session_id、title、task_type、total_tokens、input_tokens、
    output_tokens、cached_tokens、cost、calls。
    """
    sid_to_type = {s["id"]: s.get("task_type", "其他") for s in sessions}
    sid_to_title = {s["id"]: (s.get("custom_title") or s.get("title") or "未命名会话") for s in sessions}
    agg = defaultdict(lambda: {"total": 0, "input": 0, "output": 0,
                               "cached": 0, "cost": 0.0, "calls": 0,
                               "effective": 0, "effective_cost": 0.0})
    for t in traces:
        sid = t.get("session_id")
        if not sid:
            continue
        a = agg[sid]
        a["total"] += t["total_tokens"]
        a["input"] += t["input_tokens"]
        a["output"] += t["output_tokens"]
        a["cached"] += t["cached_tokens"]
        a["cost"] += t.get("total_cost", 0.0)
        a["calls"] += t.get("call_count", 0)
        a["effective"] += t.get("effective_tokens", 0)
        a["effective_cost"] += t.get("effective_cost", 0.0)
    rows = []
    for sid, a in agg.items():
        rows.append({
            "session_id": sid,
            "title": sid_to_title.get(sid, "未命名会话"),
            "task_type": sid_to_type.get(sid, "其他"),
            "total_tokens": a["total"],
            "input_tokens": a["input"],
            "output_tokens": a["output"],
            "cached_tokens": a["cached"],
            "effective_tokens": a["effective"],
            "cost": round(a["cost"], 2),
            "effective_cost": round(a["effective_cost"], 2),
            "calls": a["calls"],
        })
    rows.sort(key=lambda x: x["effective_tokens"], reverse=True)
    return rows[:top_n]


def aggregate_traces_by(traces, key_field, resolve_key_fn=None):
    """按 key_field 聚合模型维度。

    key_field 取值：
      - "model_key"  → 接口/通道维度（trace 关联的 session 配置接口标识，如
                       "custom-local:glm-4.6v" 与裸 "glm-4.6v" 分两行，用于准确计费）
      - "model_name" → 实际执行模型维度（API 实际执行的裸模型名，如 "glm-5.2"，
                       反映你真实使用了哪些模型、各多少次）
      - None         → 使用 resolve_key_fn 自定义键解析函数

    resolve_key_fn: 可选回调，接收 trace 字典，返回聚合键。用于处理特殊修正逻辑
    （如 hy3/hy3-x 误标修正）。

    返回列表（已配置单价的模型按花费降序在前）。每条：
      model / channel / calls / total_tokens / input_tokens / output_tokens / cached_tokens /
      effective_tokens / input_cost / output_cost / total_cost / effective_cost /
      configured(bool) / unit_price_input / unit_price_output
    单价取自 price_of（通道感知：openrouter-free=0；custom-local 默认对齐同名网关价/可覆盖；
    gateway 取 MODEL_PRICING）；未配置单价的模型 configured=False，其 unit_price_* 为 None、
    cost 字段为 0（不瞎算，交由报告显示「未配置」）。
    """
    # 计费口径统一：exec_model 维度（§3.1 账单口径）直接汇总 trace 级已算好的
    # effective_cost / total_cost 等字段 —— 与概览头条、每日表、§4 同源（均来自 collect_traces
    # 的逐 trace 计价，含 auto 的 router_avg、default 的 resolve_model 映射），因此 §3.1 合计
    # ≡ 概览「实际成本」必然对账一致，消除「报告自己矛盾」。
    # model_key 维度（§3.2 入口/使用视图）仍按价重算（入口免费就记 0，反映「你请求了哪个免费入口」）。
    sum_trace_cost = (key_field == "exec_model")
    agg = {}
    for t in traces:
        if resolve_key_fn is not None:
            m = resolve_key_fn(t)
        else:
            m = t.get(key_field)
        if not m or m == "default":
            # 接口维度允许回退到裸名；实际执行维度严格只用 model_name
            if key_field == "model_key":
                m = t.get("model_name") or "default"
            else:
                continue
        if m == "default":
            continue
        # GLM-5.2 变体不再合并：glm-5.2-x / glm-5.2x 与 glm-5.2 各自独立成行
        ip, op = price_of(m)
        configured = (ip is not None and op is not None)
        ch = parse_channel(m)[0]
        a = agg.setdefault(m, {
            "model": m, "channel": ch, "calls": 0,
            "total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
            "effective_tokens": 0,
            "input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0, "effective_cost": 0.0,
            "configured": configured,
            "unit_price_input": ip if configured else None,
            "unit_price_output": op if configured else None,
            "free_calls": 0,
        })
        a["calls"] += 1
        a["total_tokens"] += t.get("total_tokens", 0)
        a["input_tokens"] += t.get("input_tokens", 0)
        a["output_tokens"] += t.get("output_tokens", 0)
        a["cached_tokens"] += t.get("cached_tokens", 0)
        a["effective_tokens"] += t.get("effective_tokens", 0)
        # 限免调用计数（与计价分支无关）：用于两种口径都能正确标注「限时免费」，
        # 避免账单口径下限免模型的 ¥0 被渲染器误判为「未配置单价」。
        if is_timed_free(m, t.get("date")):
            a["timed_free_calls"] = a.get("timed_free_calls", 0) + 1
        if sum_trace_cost:
            # 账单口径：直接累加 trace 已算成本（含 auto router_avg / 限免 ¥0），必然与头条一致
            a["input_cost"] += t.get("input_cost", 0.0)
            a["output_cost"] += t.get("output_cost", 0.0)
            a["total_cost"] += t.get("total_cost", 0.0)
            a["effective_cost"] += t.get("effective_cost", 0.0)
        else:
            # 入口/使用视图：按本模型在本 trace 日期的真实单价重算（免费入口记 0）
            tip, top = price_of(m, as_of_date=t.get("date"))
            if tip is not None and top is not None:
                inp = t.get("input_tokens", 0)
                out = t.get("output_tokens", 0)
                # GLM-5.2 夜猫子计划：按模型名定率（与 §3.1 同源）
                _mult = glm52_discount_multiplier(m)
                a["input_cost"] += (inp / 1_000_000) * tip * _mult
                a["output_cost"] += (out / 1_000_000) * top * _mult
                a["total_cost"] += ((inp / 1_000_000) * tip + (out / 1_000_000) * top) * _mult
                eff_in = max(inp - t.get("cached_tokens", 0) * (1 - CACHE_DISCOUNT), 0)
                a["effective_cost"] += ((eff_in / 1_000_000) * tip + (out / 1_000_000) * top) * _mult
                # 免费判定基于「本模型在本 trace 日期的实际单价是否为 0」（而非 trace 级 is_free，
                # 因 is_free 基于 raw_model，无法覆盖「走 auto 路由实际执行 hy3」这类 trace）
                if tip == 0 and top == 0:
                    a["free_calls"] += 1
    rows = list(agg.values())
    if not sum_trace_cost:
        # 限时免费：仅入口视图需要（按入口免费判定）；账单口径已按 trace 实际成本汇总，无需再标
        for r in rows:
            if r["calls"] > 0 and r["free_calls"] == r["calls"] and normalize_model(r["model"]) in TIMED_FREE:
                r["unit_price_input"] = 0.0
                r["unit_price_output"] = 0.0
                r["configured"] = True
                r["timed_free"] = True
            else:
                r["timed_free"] = False
        # 路由别名（如 auto）：无单一单价，用「所有计费模型（单价>0）的均价」做代表性估算
        paid = [r for r in rows if r["configured"] and (r["unit_price_input"] or 0) > 0]
        avg_ip = sum(r["unit_price_input"] for r in paid) / len(paid) if paid else None
        avg_op = sum(r["unit_price_output"] for r in paid) / len(paid) if paid else None
        for r in rows:
            if r["model"] in ROUTER_ALIASES and not r["configured"] and avg_ip is not None:
                r["unit_price_input"] = round(avg_ip, 4)
                r["unit_price_output"] = round(avg_op, 4)
                r["configured"] = True
                r["is_router"] = True
                inp = r["input_tokens"]
                out = r["output_tokens"]
                r["input_cost"] = (inp / 1_000_000) * avg_ip
                r["output_cost"] = (out / 1_000_000) * avg_op
                r["total_cost"] = r["input_cost"] + r["output_cost"]
                eff_in = max(inp - r["cached_tokens"] * (1 - CACHE_DISCOUNT), 0)
                r["effective_cost"] = (eff_in / 1_000_000) * avg_ip + (out / 1_000_000) * avg_op
            else:
                r.setdefault("is_router", False)
    else:
        # 账单口径：auto 等路由别名已按 trace 实际成本汇总，仅打标 is_router 便于显示「原生路由」。
        # timed_free 仍需如实标注：本期全部调用都落在限免期内的模型，其 ¥0 是「限时免费」而非「未配置」。
        # 此处保留刊例单价（不清零），让报告能同时呈现「原价 X / 实付 ¥0」的对比。
        for r in rows:
            r["timed_free"] = bool(
                r["calls"] > 0 and r.get("timed_free_calls", 0) == r["calls"]
            )
            r.setdefault("is_router", r["model"] in ROUTER_ALIASES)
    for r in rows:
        # ── delisted vs custom 的边界（依据用户定义，二者必须分开）──
        #  - delisted（官方已下架）：曾由 WorkBuddy 官方提供、现已下架的模型。
        #    仅当底层模型名命中官方下架名单、且走的是官方通道（非用户自建）时才成立。
        #  - custom（用户自定义）：用户后来通过外部 API 接口自建 / 接入的模型，
        #    表现为 model_key 带 `custom-local:` 前缀或走 openrouter-free 免费档。
        #  判定顺序：① 前缀信号 ② custom-local 通道 ③ openrouter-free 通道
        #  ④ 名称命中自定义集（ALL_CUSTOM_MODELS = 运行时从 models.json 自动发现 ∪ user_custom_models 人工兜底），
        #     专治「撞名官方模型但走官方 gateway」的误识别（如 GLM-4.7-Flash）。
        base = normalize_model(r["model"].split("custom-local:", 1)[-1])
        is_custom = (
            r["model"].lower().startswith("custom-local:")
            or r["channel"] == "custom-local"
            or r["channel"] == "openrouter-free"
            or base in ALL_CUSTOM_MODELS
        )
        # 本地模型（Ollama / localhost:11434）：零 API 成本，标 🏠、单价归零、不计入账单总额。
        # 它同时属于 is_custom（用户自建），但优先按「本地」识别，且绝不误标为官方下架。
        is_local = base in ALL_LOCAL_MODELS
        r["is_custom"] = is_custom
        r["is_local"] = is_local
        # 智能路由 / 聚合网关（外部 API）：标 🔀，提示单价 / 花费为粗略参考。
        # 判定：① 名称命中自动发现的路由模型集；② 走 openrouter-free 免费网关（本身即路由）。
        r["is_router_api"] = (base in ALL_ROUTER_MODELS) or (r["channel"] == "openrouter-free")
        if is_local:
            r["unit_price_input"] = 0.0
            r["unit_price_output"] = 0.0
            r["configured"] = True
            r["input_cost"] = 0.0
            r["output_cost"] = 0.0
            r["total_cost"] = 0.0
            r["effective_cost"] = 0.0
            r["free_calls"] = r["calls"]
        r["is_delisted"] = (not is_custom) and (base in DELISTED_MODELS)
        r["input_cost"] = round(r["input_cost"], 4)
        r["output_cost"] = round(r["output_cost"], 4)
        r["total_cost"] = round(r["total_cost"], 2)
        # 不在此处对 effective_cost 四舍五入：保留全精度，使各模型 effective_cost 之和
        # ≡ 概览「实际成本」总额（summary.total_effective_cost，二者同源、仅分组方式不同），
        # 消除逐行四舍五入导致的 1 分钱漂移（P0 对账目标）。显示端仍按 :.2f 取整。
        r["effective_cost"] = r["effective_cost"]
    # 已配置按估算花费降序在前；未配置排后，仍按调用次数降序
    rows.sort(key=lambda x: (x["configured"], x["effective_cost"], x["calls"]), reverse=True)
    return rows


def aggregate_by_model(traces):
    """按「接口/通道」维度聚合（配置维度，用于准确计费）。

    与 aggregate_by_exec_model 的区别：此处按 trace 的 model_key（用户配置的入口标识）聚合，
    反映你实际请求了哪些入口、各多少次。

    注意：WorkBuddy 在 2026-08-21 之前存在 trace 标签误标问题 —— hy3 调用被错误标记为
    model_key=hy3-x，但 exec_model=hy3。为消除此误标，当 model_key=hy3-x 而 exec_model=hy3 时，
    强制将其归入 hy3 行（与账单口径一致）。
    """
    def _resolve_key(t):
        mk = t.get("model_key", "")
        em = t.get("exec_model", "")
        # hy3-x 误标修正：model_key=hy3-x 但 exec_model=hy3 的 trace 实际是 hy3 调用
        if mk.lower() == "hy3-x" and em.lower() == "hy3":
            return "hy3"
        return mk
    return aggregate_traces_by(traces, None, resolve_key_fn=_resolve_key)


def aggregate_by_exec_model(traces):
    """按「实际执行模型」维度聚合（API 实际执行的裸模型名，反映真实使用分布）。

    与 aggregate_by_model 的区别：此处按 trace 的 exec_model（来自 modelInfo.models[0]，
    即 API 真实执行的底层模型）聚合，因此走 auto 路由实际执行 glm-5.2 的调用、以及
    custom-local 通道的 GLM 调用，都会归到对应裸模型行，而非被 auto / custom-local:* 吸收。
    用于回答「我到底实际用了哪些模型、各多少次」。
    花费按裸名单价估算；自建接口（custom-local）实际单价未知，仅供粗略参考。
    """
    return aggregate_traces_by(traces, "exec_model")


def aggregate_by_session(traces, sessions):
    """按会话聚合成本（计费等效 effective_cost）、实际消耗 Token、调用次数、底层模型集合。

    用于回答「哪些会话/任务最烧钱」——这是行业调研中 Agent 使用者最关心的维度（每任务/每会话成本）。
    返回 dict：rows（按 effective_cost 降序的会话明细）、buckets（成本分桶分布）。
    """
    sid_to_title = {s["id"]: (s.get("custom_title") or s.get("title") or "未命名会话") for s in sessions}
    sid_to_type = {s["id"]: s.get("task_type", "其他") for s in sessions}
    agg = {}
    for t in traces:
        sid = t.get("session_id")
        if not sid:
            continue
        a = agg.setdefault(sid, {
            "session_id": sid,
            "title": sid_to_title.get(sid, "未命名会话"),
            "task_type": sid_to_type.get(sid, "其他"),
            "total_tokens": 0, "effective_tokens": 0, "effective_cost": 0.0,
            "calls": 0, "models": set(), "dates": set(),
        })
        a["total_tokens"] += t.get("total_tokens", 0)
        a["effective_tokens"] += t.get("effective_tokens", 0)
        a["effective_cost"] += t.get("effective_cost", 0.0)
        a["calls"] += t.get("call_count", 0)
        em = t.get("exec_model")
        if em and em != "default":
            # GLM-5.2 变体不再合并，原始裸名入集
            a["models"].add(em)
        d = t.get("date")
        if d:
            a["dates"].add(d)
    rows = []
    for sid, a in agg.items():
        date_list = sorted(a["dates"])
        rows.append({
            "session_id": sid,
            "title": a["title"],
            "task_type": a["task_type"],
            "total_tokens": a["total_tokens"],
            "effective_tokens": a["effective_tokens"],
            "effective_cost": round(a["effective_cost"], 2),
            "calls": a["calls"],
            "models": sorted(a["models"]),
            "first_date": date_list[0] if date_list else "",
            "last_date": date_list[-1] if date_list else "",
        })
    rows.sort(key=lambda x: x["effective_cost"], reverse=True)
    # 成本分桶分布（按 effective_cost，元）
    buckets = [
        (0, 1, "¥0–1"), (1, 5, "¥1–5"), (5, 20, "¥5–20"),
        (20, 50, "¥20–50"), (50, float("inf"), "¥50+"),
    ]
    dist = []
    for lo, hi, label in buckets:
        cnt = sum(1 for r in rows if lo <= r["effective_cost"] < hi)
        cost = sum(r["effective_cost"] for r in rows if lo <= r["effective_cost"] < hi)
        dist.append({"label": label, "count": cnt, "cost": round(cost, 2)})
    return {"rows": rows, "buckets": dist}


def _percentile(sorted_vals, p):
    """线性插值百分位（sorted_vals 已升序）。"""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    idx = (p / 100.0) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return float(sorted_vals[lo]) * (1 - frac) + float(sorted_vals[hi]) * frac


def _fmt_anom_val(v, is_cost):
    """异常数值格式化：成本用 ¥，Token 用千分位 + token。"""
    return f"¥{v:.2f}" if is_cost else f"{v:,.0f} token"


def _detect_daily_anomalies(series, is_cost):
    """通用日级阈值 / 环比异常检测。series: list of (date, value)。

    返回 (daily_anom_list, thresholds{p50,p95})；daily_anom 每项 {date, value, reasons}。
    - 超阈值：value > p95；
    - 环比突增：value >= 2× 前一日 且 value > p50。
    """
    vals = [v for _, v in series]
    if not vals:
        return [], {"p50": 0, "p95": 0}
    p50 = _percentile(sorted(vals), 50)
    p95 = _percentile(sorted(vals), 95)
    daily = []
    prev = None
    for d, v in series:
        reasons = []
        if p95 > 0 and v > p95:
            reasons.append(f"超过 p95（{_fmt_anom_val(p95, is_cost)}）")
        if prev is not None and prev > 0 and v >= 2 * prev and v > p50:
            reasons.append(f"环比突增 {(v / prev * 100 - 100):.0f}%（前一日 {_fmt_anom_val(prev, is_cost)}）")
        if reasons:
            daily.append({"date": d, "value": v, "reasons": reasons})
        prev = v
    return daily, {"p50": p50, "p95": p95}


def _detect_session_anomalies(session_stats, metric):
    """会话级异常。metric='cost' 用 effective_cost；metric='tokens' 用 calls（调用次数）。

    返回 (session_anom_list[:10], session_p95)。session_anom 每项含 session_id/title/value/
    models，tokens 口径另含 calls。
    """
    srows = session_stats.get("rows", []) if isinstance(session_stats, dict) else session_stats
    if metric == "cost":
        vals = sorted(r["effective_cost"] for r in srows) if srows else []
        sp95 = _percentile(vals, 95)
        anom = [{"session_id": r["session_id"], "title": r["title"], "value": r["effective_cost"],
                 "models": r["models"]} for r in srows if sp95 > 0 and r["effective_cost"] > sp95]
    else:
        vals = sorted(r.get("calls", 0) for r in srows) if srows else []
        sp95 = _percentile(vals, 95)
        anom = [{"session_id": r["session_id"], "title": r["title"], "value": r.get("effective_tokens", 0),
                 "calls": r.get("calls", 0), "models": r["models"]}
                for r in srows if sp95 > 0 and r.get("calls", 0) > sp95]
    anom.sort(key=lambda x: x["value"], reverse=True)
    return anom[:10], round(sp95, 2 if metric == "cost" else 1)


def detect_cost_anomalies(daily_tokens, session_stats):
    """双口径异常检测：同时以「成本」与「Token 消耗」两个**独立**口径检测异常日 / 会话。

    动机：免费 / 限时免费模型会拉低「成本」口径，可能漏报 Token 峰值（如 0726-0801 中
    7/30 的 48.81M token 峰值——当日成本极低却消耗巨大）。Token 口径始终独立检测，
    确保真实峰值被标记；成本口径仅在存在真实成本数据时启用。

    返回 dict：
      - cost_all_zero (bool)：本期实际成本是否全为 ¥0.00（幽灵 / 全免费 / 限时免费）。
      - cost_note (str)：cost_all_zero 时解释成本口径为何不适用。
      - cost (dict|None)：成本口径异常块（cost_all_zero 时为 None）。
      - token (dict)：Token 口径异常块（始终存在）。
      每个块含 daily / session 列表与 thresholds{p50, p95, session_p95}。
    """
    days = sorted(daily_tokens.keys())
    cost_series = [(d, daily_tokens[d].get("effective_cost", 0.0)) for d in days]
    token_series = [(d, daily_tokens[d].get("effective", 0)) for d in days]

    # Token 口径：始终检测（与成本独立），避免免费拉低成本口径而漏报 Token 峰值
    tok_daily, tok_thr = _detect_daily_anomalies(token_series, is_cost=False)
    tok_session, tok_sp95 = _detect_session_anomalies(session_stats, metric="tokens")
    token_block = {
        "daily": tok_daily, "session": tok_session,
        "thresholds": {**tok_thr, "session_p95": tok_sp95},
    }

    costs = [c for _, c in cost_series]
    total_cost = sum(costs)
    if total_cost <= 0 or not any(c > 0 for c in costs):
        # 成本未解析（幽灵 / 全免费 / 限时免费）→ 仅 Token 口径有效
        return {
            "cost_all_zero": True,
            "cost_note": ("本期实际成本为 ¥0.00（多为幽灵 / 空 trace，或全免费 / 限时免费模型，"
                          "见 §1 / §4 顶部提示），成本口径异常检测不适用；"
                          "以下 Token 口径异常基于实际消耗 token，峰值仍值得关注。"),
            "cost": None,
            "token": token_block,
        }

    # 成本口径：在正常成本数据上检测
    cost_daily, cost_thr = _detect_daily_anomalies(cost_series, is_cost=True)
    cost_session, cost_sp95 = _detect_session_anomalies(session_stats, metric="cost")
    cost_block = {
        "daily": cost_daily, "session": cost_session,
        "thresholds": {**cost_thr, "session_p95": cost_sp95},
    }
    return {
        "cost_all_zero": False,
        "cost_note": "",
        "cost": cost_block,
        "token": token_block,
    }


# 已知模型家族中的「更便宜替代」（用于省钱杠杆洞察）
# ⚠️ 目标只能是发布版内置的官方模型（见 scripts/pricing.json），不指向任何用户自定义/第三方模型。
_CHEAPER_ALT = {
    "glm-5.2": ("minimax-m3", "简单/模板类任务可迁移至 MiniMax-M3（约 ¥1/百万 tokens）"),
    "glm-5.1": ("minimax-m3", "简单任务可迁移至 MiniMax-M3（约 ¥1/百万 tokens）"),
    "glm-5": ("minimax-m3", "简单问答/抽取类任务可迁移至 MiniMax-M3（约 ¥1/百万 tokens）"),
    "glm-4.7": ("minimax-m3", "简单任务可迁移至 MiniMax-M3（约 ¥1/百万 tokens）"),
    "deepseek-v4-pro": ("deepseek-v4-flash", "简单检索/改写类任务可迁移至 DeepSeek-V4-Flash（约 ¥1/百万 tokens）"),
    "hy3": ("minimax-m3", "如非限时免费期，简单问答/抽取类任务可迁移至 MiniMax-M3"),
    "gpt-4o": ("minimax-m3", "非复杂推理任务可迁移至 MiniMax-M3"),
    "claude-3.5-sonnet": ("minimax-m3", "非复杂推理任务可迁移至 MiniMax-M3"),
}


def _normalize_model_key(name):
    """归一化模型名用于替代映射匹配：去掉通道前缀、变体后缀、:free 标签。

    例：openrouter/glm-5.2-x → glm-5.2；gateway:hy3 → hy3；cohere/north-mini-code:free → north-mini-code
    """
    if not name:
        return ""
    n = name.strip().lower()
    n = n.split("/")[-1]          # 去掉通道前缀（openrouter/、gateway: 等）
    n = n.split(":")[0]           # 去掉 :free / :xxx 标签
    n = re.sub(r"-(x|flash|air|mini|pro|plus|turbo|preview|lite|ultra)$", "", n)  # 去掉变体后缀
    return n


def build_savings_insights(exec_stats):
    """基于实际执行维度（model_exec_stats），找出高占比付费模型，给出更便宜替代与预计月省（估算）。
    
    估算口径（保守、透明）：
      - 取该模型 effective_cost 的 30% 作为「可迁移到更便宜模型的简单任务」比例；
      - 用输出单价比 price_alt/price_model 作为替代性价比；
      - 预计月省 = 该模型 effective_cost × 30% × (1 - 价格比)。
    仅当存在已知更便宜替代且单价可解析时给出建议。
    """
    paid = [m for m in exec_stats
            if m.get("configured") and m.get("effective_cost", 0) > 0 and not m.get("is_router")]
    total_paid = sum(m["effective_cost"] for m in paid) or 1
    items = []
    total_save = 0.0
    for m in sorted(paid, key=lambda x: x["effective_cost"], reverse=True):
        alt = _CHEAPER_ALT.get(_normalize_model_key(m["model"])) or _CHEAPER_ALT.get(m["model"])
        if not alt:
            continue
        alt_model, note = alt
        ip_a, op_a = price_of(alt_model)
        ip_m, op_m = price_of(m["model"])
        if ip_a is None or op_a is None or ip_m is None or op_m is None:
            continue
        if op_m <= 0:
            continue
        ratio = op_a / op_m
        offload_ratio = 0.30  # 假设 30% 的使用场景可迁移到更便宜模型
        save = m["effective_cost"] * offload_ratio * (1 - ratio)
        if save <= 0:
            continue
        total_save += save
        items.append({
            "model": m["model"],
            "cost": round(m["effective_cost"], 2),
            "cost_share": round(m["effective_cost"] / total_paid * 100, 1),
            "alternative": alt_model,
            "note": note,
            "estimated_monthly_save": round(save, 2),
        })
    items.sort(key=lambda x: x["estimated_monthly_save"], reverse=True)
    return {"items": items, "total_estimated_monthly_save": round(total_save, 2)}


# ── 主流程 ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="WorkBuddy Agent 使用数据采集器")
    parser.add_argument("--period", choices=["day", "week", "month", "year"], default="week",
                        help="时间窗口预设：day=今天 / week=最近7天 / month=最近30天 / year=最近365天（默认 week）")
    parser.add_argument("--days", type=int, help="自定义滚动天数，覆盖 --period（如 --days 14）")
    parser.add_argument("--start", type=str, help="绝对起始日期 YYYY-MM-DD（与 --end 搭配）")
    parser.add_argument("--end", type=str, help="绝对结束日期 YYYY-MM-DD（与 --start 搭配）")
    parser.add_argument("--output", "-o", type=str, help="输出 JSON 文件路径（默认 stdout）")
    parser.add_argument("--realtime", action="store_true", help="实时采集模式（立即采集最新数据）")
    parser.add_argument("--lookup-pricing", choices=["offline", "online"], default="offline",
                        help="缺失单价模型的处理：offline=仅提示如何补写（默认，纯本地）；"
                             "online=尝试联网检索（需 --pricing-api 指向你自己的定价镜像，"
                             "否则仅生成可点击的搜索链接）。联网结果一律标注「网络估算价，仅供参考」")
    parser.add_argument("--pricing-api", type=str, default=None,
                        help="online 模式可选：指向一个返回 {\"models\": {模型名: {input,output}}} 的 JSON 端点，"
                             "用于补全缺失模型单价（取自你自己的定价镜像，避免抓第三方页面）")
    args = parser.parse_args()

    if args.realtime:
        # 实时模式：立即采集最新数据（今天）
        start_date, end_date, period_key, period_label = resolve_date_range(period="day")
        print(f"[INFO] 实时模式：采集范围 {start_date} ~ {end_date}（当日）", file=sys.stderr)
    else:
        # 记录显式指定的参数，用于提示用户实际生效的参数
        explicit_params = []
        if args.start and args.end:
            explicit_params.append("--start/--end")
        if args.days is not None:
            explicit_params.append(f"--days {args.days}")
        if args.period != "week" or explicit_params:  # 只有显式指定了非默认值，或其他参数覆盖时才提示
            if args.period != "week" and not (args.days or args.start):
                explicit_params.append(f"--period {args.period}")
        
        try:
            start_date, end_date, period_key, period_label = resolve_date_range(
                period=args.period, days=args.days, start=args.start, end=args.end)
        except ValueError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(2)

        # 详细提示：显式指定参数 vs 默认值
        if explicit_params:
            print(f"[INFO] 采集范围[{period_label}]：{start_date} ~ {end_date}（生效参数：{', '.join(explicit_params)}）", file=sys.stderr)
        else:
            print(f"[INFO] 采集范围[{period_label}]：{start_date} ~ {end_date}（默认一周，可用 --period/--days/--start/--end 自定义）", file=sys.stderr)

    # 采集各数据源：先取会话（sessions.model 含带通道前缀的原始模型标识符，
    # 是「接口通道」的真相源），据其构建 session_id→raw_model 映射，再采集 trace 并关联通道。
    db_data = collect_db_data(start_date, end_date)
    sid_to_rawmodel = {s["id"]: (s.get("model") or "default") for s in db_data["sessions"]}
    traces = collect_traces(start_date, end_date, sid_to_rawmodel)

    # 补全会话：窗口内有 trace 但创建于窗口外的会话，确保 token 统计能关联到任务类型
    try:
        trace_sids = {t.get("session_id") for t in traces if t.get("session_id")}
        existing_ids = {s["id"] for s in db_data["sessions"]}
        missing = trace_sids - existing_ids
        if missing:
            cdb = sqlite3.connect(str(DB_PATH))
            cdb.row_factory = sqlite3.Row
            ph = ",".join("?" * len(missing))
            for r in cdb.execute(
                f"SELECT * FROM sessions WHERE id IN ({ph}) AND deleted_at IS NULL", list(missing)
            ).fetchall():
                db_data["sessions"].append({
                    "id": r["id"], "cwd": r["cwd"], "title": r["title"] or "",
                    "custom_title": r["custom_title"] or "", "status": r["status"],
                    "created_at": r["created_at"], "created_date": ts_to_date(r["created_at"]),
                    "updated_at": r["updated_at"], "mode": r["mode"], "model": r["model"],
                    "is_background_automation": bool(r["is_background_automation"]),
                })
            cdb.close()
    except Exception as e:
        print(f"[WARN] supplementary sessions query: {e}", file=sys.stderr)

    # 修复：补全会话后，把跨窗口长会话并入 sid_to_rawmodel 并重采 trace——
    # 否则这些会话的 sid 在映射里缺失，raw_model 会退化成 trace 执行模型名，
    # 导致通道误判（如 hy3 网关会话里的 deepseek-v4-pro 被错归、或反之）。
    for s in db_data["sessions"]:
        sid_to_rawmodel.setdefault(s["id"], s.get("model") or "default")
    traces = collect_traces(start_date, end_date, sid_to_rawmodel)

    skill_usage = collect_skill_usage(start_date, end_date)
    outputs, memory_logs = collect_session_outputs(start_date, end_date)

    # 任务类型分类（基于对话内容）
    task_types = collect_task_types(db_data["sessions"])

    # 汇总
    if args.days is not None:
        meta_days = args.days
    elif args.start and args.end:
        try:
            meta_days = (datetime.strptime(args.end, "%Y-%m-%d")
                         - datetime.strptime(args.start, "%Y-%m-%d")).days + 1
        except Exception:
            meta_days = PERIOD_DAYS.get(period_key, 7)
    else:
        meta_days = PERIOD_DAYS.get(period_key, 7)

    result = {
        "meta": {
            "start_date": start_date,
            "end_date": end_date,
            "period": period_key,
            "period_label": period_label,
            "days": meta_days,
            "generated_at": datetime.now(TZ).isoformat(),
            "is_realtime": args.realtime,
        },
        "traces": traces,
        "sessions": db_data["sessions"],
        "automation_runs": db_data["automation_runs"],
        "session_credits": db_data["session_credits"],
        "skill_usage": skill_usage,
        "outputs": outputs,
        "memory_logs": {k: [{"file": v["file"], "session_dir": v["session_dir"]} for v in vals]
                        for k, vals in memory_logs.items()},
        "task_types": task_types,
    }

    # 统计摘要
    total_tokens = sum(t["total_tokens"] for t in traces)
    total_input = sum(t["input_tokens"] for t in traces)
    total_output = sum(t["output_tokens"] for t in traces)
    total_cached = sum(t["cached_tokens"] for t in traces)
    total_effective = sum(t.get("effective_tokens", 0) for t in traces)
    total_credits = sum(c["used"] for c in db_data["session_credits"])
    # 活跃天数 = 窗口内产生 token 活动的日期（仅统计 trace 日期）。
    # 不计入会话创建日：窗口外创建、但窗口内有 trace 的会话已被纳入 token 聚合，
    # 若把其创建日也算进 active_days 会虚高"活跃天数"（如 --days 7 却显示 12 天）。
    active_days = set(t["date"] for t in traces)

    # 计算总成本
    total_cost = sum(t["total_cost"] for t in traces)
    total_input_cost = sum(t["input_cost"] for t in traces)
    total_output_cost = sum(t["output_cost"] for t in traces)
    total_effective_cost = round(sum(t.get("effective_cost", 0.0) for t in traces), 2)
    # 缓存占比：缓存命中 token 占输入 token 的比例（越高说明越多重复上下文被廉价复用）
    cache_rate = (total_cached / total_input * 100) if total_input else 0

    result["summary"] = {
        "total_traces": len(traces),
        "total_sessions": len(db_data["sessions"]),
        "total_tokens": total_tokens,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cached_tokens": total_cached,
        "total_effective_tokens": total_effective,
        "cache_rate": round(cache_rate, 1),
        "total_credits_used": total_credits,
        "active_days": sorted(active_days),
        "active_day_count": len(active_days),
        "total_automation_runs": len(db_data["automation_runs"]),
        "successful_automation_runs": sum(1 for r in db_data["automation_runs"] if r["result_success"]),
        "total_outputs": len(outputs),
        "skills_used": len(skill_usage["skills"]),
        "task_type_distribution": {},
        "total_cost": total_cost,
        "total_input_cost": total_input_cost,
        "total_output_cost": total_output_cost,
        "total_effective_cost": round(total_effective_cost, 2),
    }

    # 任务类型分布（D5：仅统计本期有 trace 的会话，避免历史空会话虚高 §5）
    _traced_ids = {t.get("session_id") for t in traces if t.get("session_id")}
    for s in db_data["sessions"]:
        if s.get("id") not in _traced_ids:
            continue
        tt = s.get("task_type", "其他")
        result["summary"]["task_type_distribution"][tt] = result["summary"]["task_type_distribution"].get(tt, 0) + 1

    # 任务 token 消耗统计（按任务类型聚合 traces）
    result["task_token_stats"] = aggregate_task_token_stats(traces, db_data["sessions"])

    # Top N 最吃 token 的任务对话框（按会话聚合）
    result["top_tasks"] = aggregate_top_tasks(traces, db_data["sessions"], top_n=10)

    # 按实际执行模型聚合（计费维度 / 账单口径：hy3 路由到的付费模型在此体现，与概览总额一致）→ §3.1
    result["model_stats"] = aggregate_by_exec_model(traces)
    # 按入口 / 配置模型聚合（使用分布维度：auto / hy3 / custom-local 等入口）→ §3.2
    result["model_exec_stats"] = aggregate_by_model(traces)

    # 收集本期「未配置单价」的模型名（排除路由别名 auto，其本就无单一单价），供报告给出可补写片段
    unconfigured = set()
    for stats in (result["model_stats"], result["model_exec_stats"]):
        for m in stats:
            # 已下架官方模型不算「缺失单价」——它们本就无需用户补写，仅标注即可
            if not m.get("configured") and m.get("model") not in ROUTER_ALIASES and not m.get("is_delisted"):
                unconfigured.add(m["model"])
    result["meta"]["unconfigured_models"] = sorted(unconfigured)
    # 限时免费截止日（来自 pricing.json 的 timed_free），供报告渲染「限时免费至 X」标签，
    # 避免在渲染器里硬编码日期——用户改了 pricing.json 后标签会自动跟随。
    result["meta"]["timed_free"] = dict(TIMED_FREE)
    # 是否加载了本地定价覆盖（pricing.local.json），供报告透明提示。
    result["meta"]["pricing_local_loaded"] = bool(_PRICING_LOCAL_LOADED)

    # 可选联网检索（--lookup-pricing online）：仅生成搜索链接，或拉取用户自有定价镜像。
    # ⚠️ 联网拿到的单价一律视为「网络估算价，仅供参考」，绝不用于权威成本总额。
    pricing_lookup = {
        "mode": args.lookup_pricing,
        "api": args.pricing_api,
        "network_estimates": {},
        "search_links": {},
    }
    if args.lookup_pricing == "online" and unconfigured:
        for model in sorted(unconfigured):
            pricing_lookup["search_links"][model] = (
                "https://duckduckgo.com/html/?q=" + urllib.parse.quote(f"{model} API pricing")
            )
            if args.pricing_api:
                try:
                    with urllib.request.urlopen(args.pricing_api, timeout=10) as resp:
                        remote = json.loads(resp.read().decode("utf-8"))
                    rm = (remote.get("models", {}).get(normalize_model(model))
                          or remote.get("models", {}).get(model))
                    if rm and "input" in rm and "output" in rm:
                        pricing_lookup["network_estimates"][model] = {
                            "input": float(rm["input"]), "output": float(rm["output"]),
                        }
                except Exception as e:
                    print(f"[WARN] 联网检索 {model} 失败：{e}", file=sys.stderr)
    result["meta"]["pricing_lookup"] = pricing_lookup

    # P1 成本深度分析：每会话成本 / 省钱杠杆（cost_anomalies 依赖 daily_tokens，在下方构建后计算）
    result["session_stats"] = aggregate_by_session(traces, db_data["sessions"])
    # 省钱洞察基于计费维度（exec_model）找真实付费贵模型，给出更便宜替代与预计月省
    result["savings_insights"] = build_savings_insights(result["model_stats"])

    # 每日 token 统计
    daily_tokens = {}
    for t in traces:
        d = t["date"]
        if d not in daily_tokens:
            daily_tokens[d] = {
                "total": 0, "input": 0, "output": 0, "cached": 0, "calls": 0,
                "total_cost": 0, "input_cost": 0, "output_cost": 0,
                "effective": 0, "effective_cost": 0,
            }
        daily_tokens[d]["total"] += t["total_tokens"]
        daily_tokens[d]["input"] += t["input_tokens"]
        daily_tokens[d]["output"] += t["output_tokens"]
        daily_tokens[d]["cached"] += t["cached_tokens"]
        daily_tokens[d]["calls"] += t["call_count"]
        daily_tokens[d]["total_cost"] += t["total_cost"]
        daily_tokens[d]["input_cost"] += t["input_cost"]
        daily_tokens[d]["output_cost"] += t["output_cost"]
        daily_tokens[d]["effective"] += t.get("effective_tokens", 0)
        daily_tokens[d]["effective_cost"] += t.get("effective_cost", 0.0)
    result["daily_tokens"] = daily_tokens

    # P1 成本异常检测（依赖 daily_tokens 与 session_stats，故置于每日统计之后）
    result["cost_anomalies"] = detect_cost_anomalies(result["daily_tokens"], result["session_stats"])

    output_json = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"[OK] 数据已保存到 {args.output}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
