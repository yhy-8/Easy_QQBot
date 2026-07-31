import aiohttp
import datetime
import aiosqlite
import re
import asyncio
import base64
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from nonebot import on_message, get_driver, logger
from nonebot.rule import to_me
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, GroupMessageEvent
from nonebot.exception import FinishedException

# ================= 配置区域 =================
# ===== 基础配置 =====
ALLOWED_GROUPS = [12345678] #白名单群
DB_PATH = "/qqbot/chat_history.db"  # SQLite 数据库文件路径
ENABLE_QUICK_ACK = True             # 是否开启收到提问后立刻回复“Waiting……”的提示 (True/False)

ENABLE_AI_HISTORY_DECISION = True  # 是否开启 AI 动态决定历史记录条数 (True/False)
DYNAMIC_HISTORY_MODEL = "default"   # 决定上下文条数的模型标识 (对应 MODELS_CONFIG 中的键名，如 "default", "A")

DYNAMIC_HISTORY_TIMEOUT = 30  # 动态决定历史记录条数(前置AI)的超时时间（秒）
AI_CHAT_TIMEOUT = 120         # 正式聊天(正式AI)的超时时间（秒）

# 图片本地缓存目录配置
# 1. 如果代码和 NapCat 在同一台电脑/同一个 Docker 容器内，请保持留空 ""，程序会自动读取绝对路径。
# 2. 如果是跨 Docker 容器部署，导致路径不通，请在此填入挂载到当前容器的绝对路径（例如 "/napcat/xxx/images"）
IMAGE_BASE_DIR = ""

DEFAULT_MODE = "casual"  # 无前缀时默认模式: "serious"(严肃) 或 "casual"(随性)

# ===== 第三方搜索配置 =====
THIRD_SEARCH_API_KEY = ""                                    # 博查AI API Key
THIRD_SEARCH_API_URL = "https://api.bocha.cn/v1/web-search"  # 博查AI搜索端点
THIRD_SEARCH_COUNT = 25                                      # 单次搜索返回条数（运行时限制在 1-50）
THIRD_SEARCH_TIMEOUT = 30                                    # 第三方搜索请求超时时间（秒）
MAX_SEARCH_ROUNDS = 3                                        # 最大搜索轮数；小于等于0时不提供第三方搜索工具
ENABLE_THIRD_SEARCH = False                                 # 第三方搜索总开关 (True/False)，仅当模型无原生搜索时生效

# ===== 模型配置 =====
MODELS_CONFIG = {
    "default": {
        "api_key": "",
        "api_url": "https://api.deepseek.com/chat/completions",
        "name": "ds-v4-flash",
        "api_type": "openai",
        "model_id": "deepseek-v4-flash",  # DeepSeek 需要在 body 传入这个
        "vision": False,
        "search": False
    },
    "A": {
        "api_key": "",
        "api_url": "https://api.deepseek.com/chat/completions",
        "name": "ds-v4-pro",
        "api_type": "openai",
        "model_id": "deepseek-v4-pro",
        "vision": False,
        "search": False
    },
    "B": {
        "api_key": "",
        "api_url": "https://xxxxxx/v1beta/models",
        "name": "gemini-3-flash",
        "api_type": "gemini",
        "model_id": "gemini-3-flash-preview",
        "vision": True,
        "search": True
    },
    "C": {
        "api_key": "",
        "api_url": "https://xxxxxx/v1beta/models",
        "name": "gemini-3.1-pro",
        "api_type": "gemini",
        "model_id": "gemini-3.1-pro-preview",
        "vision": True,
        "search": True
    }
}

# ================= 提示词模板 =================
# 注意：{bot_identity} 会在运行时被替换，格式为 "群昵称（QQ昵称：aaa）" 或仅昵称（两者相同时）
# ---- 严肃模式：客观AI助手 ----
SERIOUS_SYSTEM_PROMPT = """你（{bot_identity}）是本群的一位客观的AI助手，严格遵守以下【输出规范】进行回复：
1. 必须严格使用纯文本输出，绝对禁止使用任何 Markdown 语法（如加粗 **、列表 *、代码块 ``` 等）。
2. 绝对禁止在回答中重复提问者的用户ID或昵称。
3. 结合提供的群聊历史记录，作出答复。
4. 对于你不确切了解的客观事实、时效性信息或专有名词，严禁任何形式的猜测、推理或胡编乱造。
5. 如果你具备联网搜索能力，遇到未知信息必须优先调用搜索工具；如果你不具备该能力，或搜索后未找到确凿结果，必须直接如实回答"我不清楚相关信息"。
6. 历史记录中你的正式回复头（如“模型：…，记录：…”）和“Waiting……”快速回复均由系统固定生成；你只需输出回答正文，绝对禁止仿造、重复或自行输出这些内容。
"""

# ---- 随性模式：语气轻松的AI助手 ----
CASUAL_SYSTEM_PROMPT = """你（{bot_identity}）是本群的一个语气轻松的AI助手。请遵守以下规范：
1. 必须使用纯文本输出，禁止使用 Markdown 语法（如加粗 **、列表 *、代码块 ``` 等）。
2. 绝对禁止在回答中重复提问者的用户ID或昵称。
3. 你的本质是工具，不是角色——语气可以自由随意一些，但不应塑造独立人设或主动扮演角色。
4. 以当前消息为第一优先级，自动感知话题切换（前文写小说、当前问正经问题→自然回应新话题，不延续旧语境）。
5. 群聊历史用于理解指代关系，同时揣测各群友的当前意图（写小说、找乐子、玩梗等），但以当前消息为最终判断依据，自动感知话题切换。
6. 群友明显玩梗或找乐子时，可适度配合但不过度演绎，保持简短轻松。
7. 历史记录中你的正式回复头（如“模型：…，记录：…”）和“Waiting……”快速回复均由系统固定生成；你只需输出回答正文，绝对禁止仿造、重复或自行输出这些内容。
"""

# 模式 → 提示词映射
MODE_PROMPTS = {
    "serious": SERIOUS_SYSTEM_PROMPT,
    "casual": CASUAL_SYSTEM_PROMPT,
}

# 机器人 QQ 昵称（启动时从框架获取）
_bot_nickname = "AI助手"


# ========== 单文件内的结构化领域对象 ==========
@dataclass(frozen=True)
class VisualAttachment:
    """OneBot 图片段中的 summary 标签及可用文件来源。"""
    placeholder: str
    file_id: str = ""
    local_path: str = ""


@dataclass
class ParsedMessage:
    """统一的 OneBot 消息解析结果。"""
    text: str
    visual_attachments: list[VisualAttachment] = field(default_factory=list)


@dataclass(frozen=True)
class ResolvedVisual:
    """完成格式判断后的视觉附件及其最终提示词占位。"""
    placeholder: str
    mime_type: str | None = None
    base64_data: str | None = None

    @property
    def sendable(self) -> bool:
        return bool(self.mime_type and self.base64_data)


def _visual_placeholder_token(index: int) -> str:
    """保留视觉附件在富文本中的位置，不会进入最终提示词。"""
    return f"\0easy_ai_visual:{index}\0"


@dataclass(frozen=True)
class SearchResult:
    """模型回复所携带的搜索状态，避免用 int/bool 混合表达不同语义。"""
    performed: bool | None = False
    count: int | None = None

    def prefix_value(self) -> str | None:
        if type(self.count) is int and self.count > 0:
            return str(self.count)
        if self.performed is True and type(self.count) is int and self.count == 0:
            return "False"
        return None


@dataclass
class ModelReply:
    """模型正文与附属状态。"""
    text: str
    search: SearchResult = field(default_factory=SearchResult)


@dataclass(frozen=True)
class ModelSelection:
    """一次消息选中的模型和对话模式。"""
    mode: str
    prefix_to_remove: str
    config: dict

    @property
    def api_type(self) -> str:
        return self.config.get("api_type", "openai")

    @property
    def api_url(self) -> str:
        if self.api_type == "gemini":
            return (
                f"{self.config['api_url']}/{self.config['model_id']}"
                f":generateContent"
            )
        return self.config["api_url"]

    @property
    def vision_enabled(self) -> bool:
        return self.config.get("vision", False)

    @property
    def search_enabled(self) -> bool:
        return self.config.get("search", False)

    @property
    def use_third_search(self) -> bool:
        return bool(
            not self.search_enabled
            and ENABLE_THIRD_SEARCH
            and THIRD_SEARCH_API_KEY
            and MAX_SEARCH_ROUNDS > 0
        )

    @property
    def information(self) -> str:
        mode_label = "SER" if self.mode == "serious" else "CAS"
        return f"{self.config['name']}，{mode_label}"


@dataclass
class PreparedModelRequest:
    """已经完成协议组装、可直接发往上游的请求。"""
    api_type: str
    api_url: str
    headers: dict
    payload: dict
    system_prompt: str
    user_message_content: object = None
    native_search_adapter: str = ""
    use_third_search: bool = False


@dataclass
class ChatCompletion:
    """交给事件层发送的完整聊天结果。"""
    reply: ModelReply
    history_count: int
    image_count: int


class UnsupportedAPITypeError(ValueError):
    """模型 api_type 不是已支持的 OpenAI/Gemini 格式。"""


class ModelHTTPError(RuntimeError):
    """正式模型首轮请求返回非 200。"""


# ========== 辅助函数：安全解析 API 回复 ==========
def _get_openai_message(data: dict) -> dict:
    """获取 OpenAI 兼容回包中的第一条 message，缺失时返回空字典"""
    if not isinstance(data, dict):
        return {}
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return {}
    message = choices[0].get("message") or {}
    return message if isinstance(message, dict) else {}


def _extract_api_reply_text(data: dict, api_type: str) -> str:
    """从 OpenAI / Gemini 回包中提取正文；缺少正文时返回空字符串"""
    if not isinstance(data, dict):
        return ""

    if api_type == "openai":
        content = _get_openai_message(data).get("content")
        return content.strip() if isinstance(content, str) else ""

    if api_type == "gemini":
        candidates = data.get("candidates") or []
        if not candidates or not isinstance(candidates[0], dict):
            return ""
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or [] if isinstance(content, dict) else []
        if not isinstance(parts, list):
            return ""
        for part in reversed(parts):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text = part["text"].strip()
                if text:
                    return text

    return ""


def _enable_openai_native_search(payload: dict, model_config: dict) -> str:
    """
    按模型提供商为 OpenAI 兼容请求启用原生联网。

    返回对应的搜索适配器标识，供统一回包解析函数使用。
    """
    model_id_lower = str(model_config.get("model_id", "")).lower()

    # 智谱清言 (GLM-4) 的原生联网参数
    if "glm" in model_id_lower:
        payload["tools"] = [{"type": "web_search", "web_search": {"enable": True}}]
        return "glm_web_search"

    # Moonshot (Kimi) 的原生联网参数
    if "moonshot" in model_id_lower:
        payload["tools"] = [{"type": "builtin_function", "function": {"name": "$web_search"}}]
        return "moonshot_web_search"

    # 其他常见厂商或第三方中转的兼容参数
    payload["web_search"] = True
    payload["network"] = True
    return "generic_search"


def _parse_openai_native_search_response(
        data: dict,
        reply_text: str,
        search_adapter: str
) -> ModelReply:
    """
    统一解析 OpenAI 兼容模型的原生搜索回包。

    新中转的正文轨迹格式或统计字段应统一在此函数内扩展，
    不要在主聊天流程中增加模型类型判断。
    """
    if not search_adapter:
        return ModelReply(text=reply_text)

    reply_text = reply_text if isinstance(reply_text, str) else ""

    if not isinstance(data, dict):
        return ModelReply(
            text=reply_text,
            search=SearchResult(
                performed=None,
                count=None
            )
        )

    message = _get_openai_message(data)
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        return ModelReply(
            text=reply_text,
            search=SearchResult(performed=True, count=len(tool_calls))
        )

    for container in (data, data.get("usage"), message):
        if not isinstance(container, dict):
            continue

        server_usage = container.get("server_side_tool_usage")
        if isinstance(server_usage, dict):
            count = sum(
                value for key, value in server_usage.items()
                if type(value) is int and "SEARCH" in str(key).upper()
            )
            if count > 0:
                return ModelReply(
                    text=reply_text,
                    search=SearchResult(performed=True, count=count)
                )

        for key in ("num_server_side_tools_used", "num_sources_used"):
            value = container.get(key)
            if type(value) is int and value > 0:
                return ModelReply(
                    text=reply_text,
                    search=SearchResult(performed=True, count=value)
                )

        for key in ("citations", "sources"):
            value = container.get(key)
            if isinstance(value, list) and value:
                return ModelReply(
                    text=reply_text,
                    search=SearchResult(performed=True, count=len(value))
                )

    return ModelReply(
        text=reply_text,
        search=SearchResult(
            performed=None,
            count=None
        )
    )


# ========== 数据库初始化 ==========
driver = get_driver()
_http_session: aiohttp.ClientSession | None = None


@driver.on_startup
async def init_http_session():
    """在插件生命周期内复用一个 aiohttp 会话。"""
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()


@driver.on_shutdown
async def close_http_session():
    global _http_session
    if _http_session is not None and not _http_session.closed:
        await _http_session.close()
    _http_session = None


async def get_http_session() -> aiohttp.ClientSession:
    """获取共享会话；保留惰性初始化以兼容特殊的插件加载顺序。"""
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


@driver.on_startup
async def validate_configuration():
    """只报告配置问题，不修改配置或阻止原有启动流程。"""
    if "default" not in MODELS_CONFIG:
        logger.error("[AI Chat] MODELS_CONFIG 缺少 default 模型")
    if DEFAULT_MODE not in MODE_PROMPTS:
        logger.error(
            f"[AI Chat] DEFAULT_MODE={DEFAULT_MODE!r} 不在 MODE_PROMPTS 中"
        )
    if DYNAMIC_HISTORY_MODEL not in MODELS_CONFIG:
        logger.warning(
            f"[AI Chat] DYNAMIC_HISTORY_MODEL={DYNAMIC_HISTORY_MODEL!r} 不存在，"
            "将沿用原逻辑回退到 default"
        )

    required_fields = {
        "api_key", "api_url", "name", "api_type", "model_id", "vision", "search"
    }
    for model_key, model_config in MODELS_CONFIG.items():
        missing_fields = sorted(required_fields - set(model_config))
        if missing_fields:
            logger.warning(
                f"[AI Chat] 模型 {model_key!r} 缺少配置字段: "
                f"{', '.join(missing_fields)}"
            )
        api_type = model_config.get("api_type", "openai")
        if api_type not in {"openai", "gemini"}:
            logger.warning(
                f"[AI Chat] 模型 {model_key!r} 使用未知 api_type={api_type!r}"
            )


@driver.on_startup
async def init_db():
    async with aiosqlite.connect(DB_PATH, timeout=15.0) as db:
        # 开启 WAL 模式
        await db.execute('PRAGMA journal_mode=WAL;')

        # 全局用户信息表（QQ昵称 + 全局最后发言时间）
        await db.execute('''
            CREATE TABLE IF NOT EXISTS "user_info" (
                user_id TEXT PRIMARY KEY,
                qq_nickname TEXT,
                last_global_speak_time INTEGER
            )
        ''')

        # 每群每用户信息表（群昵称 + 该群最后发言时间）
        await db.execute('''
            CREATE TABLE IF NOT EXISTS "user_group_info" (
                user_id TEXT,
                group_id INTEGER,
                group_nickname TEXT,
                last_group_speak_time INTEGER,
                PRIMARY KEY (user_id, group_id)
            )
        ''')

        for group_id in ALLOWED_GROUPS:
            table_name = f"group_{group_id}"
            await db.execute(f'''
                CREATE TABLE IF NOT EXISTS "{table_name}" (
                    message_id TEXT UNIQUE,
                    timestamp INTEGER,
                    user_id TEXT,
                    content TEXT
                )
            ''')

        await db.commit()
    logger.info("[AI Chat] 数据库初始化完成")


# ========== 辅助函数：动态获取聊天记录数 ==========
async def get_dynamic_history_length(group_id: int) -> int:
    """统计近期消息密度决定要读取的历史消息数量"""

    # --- 提取条数限制配置区 ---
    MIN_LIMIT = 50  # 允许提取的最小历史条数
    MAX_LIMIT = 500  # 允许提取的最大历史条数
    DEFAULT_LIMIT = 80  # API失败或兜底时使用的默认值
    # -----------------------

    table_name = f"group_{group_id}"
    now_ts = int(datetime.datetime.now().timestamp())
    rows = []
    try:
        async with aiosqlite.connect(DB_PATH, timeout=15.0) as db:
            async with db.execute(
                    f'SELECT timestamp FROM "{table_name}" WHERE timestamp > ? ORDER BY timestamp DESC, rowid DESC',
                    (now_ts - 7200,)) as cursor:
                rows = await cursor.fetchall()
    except Exception as e:
        logger.exception(f"[AI Chat] 数据库查询异常 {e}")
        rows = []

    # 如果两小时内没有任何消息，直接返回兜底值，不浪费资源
    if not rows:
        return DEFAULT_LIMIT

    # ================= 分支 1: 固定算法决策逻辑 =================
    if not ENABLE_AI_HISTORY_DECISION:
        # 统计两个时间段的消息条数
        count_0_to_1h = sum(1 for (ts,) in rows if now_ts - ts <= 3600)
        count_1_to_2h = sum(1 for (ts,) in rows if 3600 < now_ts - ts <= 7200)

        # 算法: 1小时内的全部消息 + 1到2小时之间的随机 50% ~ 100%
        random_ratio = random.uniform(0.5, 1.0)
        calculated_num = count_0_to_1h + int(count_1_to_2h * random_ratio)

        # 最终值受到上下限约束
        final_num = max(MIN_LIMIT, min(MAX_LIMIT, calculated_num))
        # 调试输出
        # print(f"[AI Chat] 算法决定提取条数: {final_num} (1h内:{count_0_to_1h}, 1-2h:{count_1_to_2h}, 采纳比例:{random_ratio:.2f})")
        return final_num

    # ================= 分支 2: AI 动态决策逻辑 =================

    # 统计各个时间段的消息量
    stats = {
        "最近10分钟": 0,
        "10-30分钟前": 0,
        "30-60分钟前": 0,
        "1-2小时前": 0
    }

    for (ts,) in rows:
        diff = now_ts - ts
        if diff <= 600:
            stats["最近10分钟"] += 1
        elif diff <= 1800:
            stats["10-30分钟前"] += 1
        elif diff <= 3600:
            stats["30-60分钟前"] += 1
        else:
            stats["1-2小时前"] += 1

    stats_text = ", ".join([f"{k}: {v}条" for k, v in stats.items()])

    prompt = (
        f"你是一个用于评估对话上下文长度的计算模块。请根据以下最近2小时的群聊活跃度数据，决定需要提取的历史记录条数。\n\n"
        f"【活跃度数据】\n"
        f"{stats_text}\n\n"
        f"【评估规则】\n"
        f"1. 活跃度高（消息密集）：适当增加条数，确保上下文逻辑不断层。\n"
        f"2. 活跃度低（消息稀疏）：适当减少条数，避免引入无关噪音和浪费计算资源。\n"
        f"3. 提取条数必须是正整数，且最大绝对不能超过：{MAX_LIMIT}。\n\n"
        f"【输出指令】\n"
        f"仅输出一个纯数字。禁止包含任何标点符号、换行符、前缀或解释性文本！"
    )

    # 动态获取配置，兼容 OpenAI 和 Gemini 格式
    model_config = MODELS_CONFIG.get(DYNAMIC_HISTORY_MODEL, MODELS_CONFIG["default"])
    api_type = model_config.get("api_type", "openai")

    if api_type == "openai":
        url = model_config["api_url"]
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {model_config['api_key']}"
        }
        payload = {
            "model": model_config.get("model_id", "deepseek-chat"),
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0.1
        }
    else:  # Gemini 格式兼容
        url = f"{model_config['api_url']}/{model_config['model_id']}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": model_config["api_key"]
        }
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}]
    }

    try:
        session = await get_http_session()
        async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=DYNAMIC_HISTORY_TIMEOUT
        ) as resp:
            if resp.status == 200:
                data = await resp.json()

                # 按照对应格式解析回包
                reply = _extract_api_reply_text(data, api_type)

                match = re.search(r'\d+', reply)
                if match:
                    num = int(match.group())
                    # AI 回复的数字同样受到上下限约束
                    return max(MIN_LIMIT, min(MAX_LIMIT, num))
            else:
                logger.warning(
                    f"[AI Chat] 获取动态上下文API响应失败，状态码: {resp.status}"
                )
    except Exception as e:
        logger.exception(f"[AI Chat] 获取动态上下文长度执行失败: {e}")

    # 如果请求失败或没有匹配到数字，返回默认值
    return DEFAULT_LIMIT


# ========== 辅助对象：统一 OneBot 消息解析 ==========
class OneBotMessageParser:
    """
    同一个入口解析数据库存储文本和 AI 富文本。

    两种用途的格式差异保留在内部渲染分支，调用方不再各自遍历消息段。
    """

    def __init__(self, bot: Bot, group_id: int):
        self.bot = bot
        self.group_id = group_id

    @staticmethod
    def _segment_type_and_data(segment) -> tuple[str, dict]:
        if isinstance(segment, dict):
            seg_type = segment.get("type", "")
            seg_data = segment.get("data", {})
        else:
            seg_type = getattr(segment, "type", "")
            seg_data = getattr(segment, "data", {})
        return seg_type, seg_data if isinstance(seg_data, dict) else {}

    @staticmethod
    def _image_placeholder(seg_data: dict) -> str:
        summary = str(seg_data.get("summary") or "").strip()
        return summary or "[图片]"

    @staticmethod
    def _image_file_id(seg_data: dict) -> str:
        file_value = str(seg_data.get("file") or "").strip()
        file_id = str(seg_data.get("file_id") or "").strip()
        if file_value.lower() == "marketface" and file_id:
            return file_id
        return file_value or file_id

    @classmethod
    def _render_ai_image(
            cls,
            seg_data: dict,
            visual_attachments: list[VisualAttachment]
    ) -> str:
        placeholder = cls._image_placeholder(seg_data)
        file_id = cls._image_file_id(seg_data)
        token = _visual_placeholder_token(len(visual_attachments))
        visual_attachments.append(
            VisualAttachment(
                placeholder=placeholder,
                file_id=file_id,
                local_path=str(seg_data.get("path") or "").strip()
            )
        )
        return token

    async def _format_member_at(self, qq_id: str) -> str:
        if qq_id == "all":
            return "[@全体成员]"
        if not qq_id.isdigit():
            return f"[@{qq_id}]"

        try:
            member_info = await self.bot.get_group_member_info(
                group_id=self.group_id,
                user_id=int(qq_id),
                no_cache=False
            )
            qq_name = member_info.get("nickname") or qq_id
            card = (member_info.get("card") or "").strip()
            if card and card != qq_name:
                return f"[@{card}（QQ昵称：{qq_name}）]"
            return f"[@{qq_name}]"
        except Exception:
            return f"[@{qq_id}]"

    async def _render_storage_segment(self, seg_type: str, seg_data: dict) -> str:
        if seg_type == "text":
            return seg_data.get("text", "")
        if seg_type == "reply":
            try:
                reply_msg = await self.bot.get_msg(message_id=seg_data.get("id"))
                reply_time = reply_msg.get("time")
                reply_user_id = str(reply_msg.get("sender", {}).get("user_id", "未知"))
                return f"[引用回复(时间：{reply_time}，发言人：{reply_user_id})]"
            except Exception:
                return "[引用回复(获取信息失败)]"
        if seg_type == "image":
            return self._image_placeholder(seg_data)
        if seg_type in ["face", "mface", "bface"]:
            summary = seg_data.get("summary", "").strip()
            return summary if summary else "[表情包]"
        if seg_type == "record":
            return "[语音]"
        if seg_type == "video":
            return "[视频]"
        if seg_type == "file":
            file_name = (
                seg_data.get("name")
                or seg_data.get("file")
                or seg_data.get("id")
                or "未知文件"
            )
            return f"[文件: {file_name}]"
        if seg_type == "forward":
            return "[聊天记录]"
        if seg_type == "node":
            return "[合并转发节点]"
        if seg_type in ["json", "xml"]:
            return "[分享了卡片/链接]"
        if seg_type == "at":
            qq_id = str(seg_data.get("qq", ""))
            return "[@全体成员]" if qq_id == "all" else f"[@{qq_id}]"
        else:
            return f"[{seg_type}]"

    async def _render_quoted_ai_segment(
            self,
            seg_type: str,
            seg_data: dict,
            visual_attachments: list[VisualAttachment]
    ) -> str:
        if seg_type == "text":
            return seg_data.get("text", "")
        if seg_type == "image":
            return self._render_ai_image(seg_data, visual_attachments)
        if seg_type in ["face", "mface", "bface"]:
            summary = seg_data.get("summary", "").strip()
            return summary if summary else "[表情包]"
        if seg_type == "file":
            file_name = (
                seg_data.get("name")
                or seg_data.get("file")
                or seg_data.get("id")
                or "未知文件"
            )
            return f"[文件：{file_name}]"
        if seg_type == "record":
            return "[语音]"
        if seg_type == "video":
            return "[视频]"
        if seg_type == "forward":
            return "[聊天记录]"
        if seg_type == "node":
            return "[合并转发节点]"
        if seg_type == "reply":
            return "[引用回复（未继续展开）]"
        if seg_type == "at":
            return await self._format_member_at(str(seg_data.get("qq", "")))
        if seg_type in ["json", "xml"]:
            return "[分享了卡片/链接]"
        else:
            return f"[{seg_type}]"

    async def _render_ai_reply(
            self,
            seg_data: dict,
            visual_attachments: list[VisualAttachment]
    ) -> str:
        """只展开当前引用一层，引用内容中的 reply 不再请求和递归解析。"""
        try:
            reply_msg = await self.bot.get_msg(message_id=seg_data.get("id"))
            reply_time = datetime.datetime.fromtimestamp(
                reply_msg.get("time", 0)
            ).strftime("%m-%d %H:%M:%S")
            reply_sender = reply_msg.get("sender", {})
            reply_qq_name = reply_sender.get("nickname", "未知")
            reply_card = (reply_sender.get("card") or "").strip()
            display_sender = (
                f"{reply_card}（QQ昵称：{reply_qq_name}）"
                if reply_card and reply_card != reply_qq_name
                else reply_qq_name
            )

            quoted_parts = []
            for quoted_segment in reply_msg.get("message", []):
                quoted_type, quoted_data = self._segment_type_and_data(quoted_segment)
                if not quoted_type:
                    continue
                quoted_parts.append(
                    await self._render_quoted_ai_segment(
                        quoted_type,
                        quoted_data,
                        visual_attachments
                    )
                )
            quoted_content = "".join(quoted_parts)
            return (
                f"\n[引用回复（时间：{reply_time}，发言人：{display_sender}，"
                f"内容：{quoted_content}）]\n"
            )
        except Exception:
            return "[引用回复(获取信息失败)]"

    async def _render_ai_segment(
            self,
            seg_type: str,
            seg_data: dict,
            visual_attachments: list[VisualAttachment]
    ) -> str:
        if seg_type == "text":
            return seg_data.get("text", "")
        if seg_type == "at":
            qq_id = str(seg_data.get("qq", ""))
            if qq_id == str(self.bot.self_id):
                return ""
            return await self._format_member_at(qq_id)
        if seg_type == "image":
            return self._render_ai_image(seg_data, visual_attachments)
        if seg_type in ["face", "mface", "bface"]:
            summary = seg_data.get("summary", "").strip()
            return summary if summary else "[表情包]"
        if seg_type == "reply":
            return await self._render_ai_reply(
                seg_data,
                visual_attachments
            )
        if seg_type == "file":
            file_name = seg_data.get("name") or seg_data.get("file") or "未知文件"
            return f"[文件: {file_name}]"
        if seg_type == "record":
            return "[语音]"
        if seg_type == "video":
            return "[视频]"
        if seg_type == "forward":
            return "[聊天记录]"
        if seg_type == "node":
            return "[合并转发节点]"
        if seg_type in ["json", "xml"]:
            return "[分享了卡片/链接]"
        else:
            return f"[{seg_type}]"

    async def parse(self, raw_message, *, for_ai: bool = False) -> ParsedMessage:
        if isinstance(raw_message, str):
            if for_ai:
                return ParsedMessage(text="")
            clean_text = re.sub(r"\[CQ:[^\]]+\]", "[媒体/表情]", raw_message)
            return ParsedMessage(text=clean_text.strip())

        text_parts = []
        visual_attachments = []
        if hasattr(raw_message, "__iter__"):
            for segment in raw_message:
                seg_type, seg_data = self._segment_type_and_data(segment)
                if not seg_type:
                    continue
                if for_ai:
                    text = await self._render_ai_segment(
                        seg_type,
                        seg_data,
                        visual_attachments
                    )
                else:
                    text = await self._render_storage_segment(seg_type, seg_data)
                text_parts.append(text)

        return ParsedMessage(
            text="".join(text_parts).strip(),
            visual_attachments=visual_attachments
        )


# ========== 兼容入口：数据库存储文本 ==========
async def parse_message_content(bot: Bot, group_id: int, raw_message) -> str:
    parsed = await OneBotMessageParser(bot, group_id).parse(raw_message)
    return parsed.text


# ========== 辅助函数：统一发送并存入数据库 ==========
async def send_and_save(bot: Bot, event: GroupMessageEvent, matcher, msg, is_finish: bool = False):
    send_result = None
    try:
        send_result = await matcher.send(msg)
    except Exception as e:
        logger.warning(
            f"[AI Chat] 消息首次发送失败: {type(e).__name__}: {e}"
        )
        if is_finish:
            # 正式回复发送失败时最多再重试 3 次；通道永久失效时只记录日志。
            for retry_num in range(1, 4):
                await asyncio.sleep(1)
                try:
                    send_result = await matcher.send(msg)
                    break
                except Exception as retry_error:
                    logger.warning(
                        f"[AI Chat] 正式回复第 {retry_num}/3 次重试失败: "
                        f"{type(retry_error).__name__}: {retry_error}"
                    )

    if isinstance(send_result, dict) and "message_id" in send_result:
        try:
            content_to_save = await parse_message_content(bot, event.group_id, msg)
            bot_msg_id = send_result["message_id"]
            bot_timestamp = int(datetime.datetime.now().timestamp())

            try:
                bot_info = await bot.get_login_info()
                bot_qq_name = bot_info.get("nickname", "AI助手")
                bot_user_id = str(bot_info.get("user_id", bot.self_id))
                # 获取机器人在该群的群昵称
                bot_member = await bot.get_group_member_info(group_id=event.group_id, user_id=bot_info.get("user_id", bot.self_id), no_cache=False)
                bot_group_name = bot_member.get("card", "").strip() or bot_qq_name
            except Exception:
                bot_qq_name = "AI助手"
                bot_group_name = "AI助手"
                bot_user_id = str(bot.self_id)

            await insert_message_to_db(bot_msg_id, event.group_id, bot_timestamp, bot_user_id, bot_qq_name, bot_group_name, content_to_save)
        except Exception as e:
            logger.exception(f"[AI Chat] 消息存库失败: {e}")

    if is_finish:
        await matcher.finish()


# ========== 辅助函数：异步写入数据库 ==========
async def insert_message_to_db(msg_id, group_id, timestamp, user_id, qq_nickname, group_nickname, content):
    if not content or group_id not in ALLOWED_GROUPS:
        return

    table_name = f"group_{group_id}"
    try:
        async with aiosqlite.connect(DB_PATH, timeout=15.0) as db:
            # 1. 写入聊天记录表
            sql_chat = f'INSERT OR IGNORE INTO "{table_name}" (message_id, timestamp, user_id, content) VALUES (?, ?, ?, ?)'
            await db.execute(sql_chat, (str(msg_id), int(timestamp), str(user_id), content))

            # 2. 更新全局用户信息表
            sql_user = '''
                INSERT INTO "user_info" (user_id, qq_nickname, last_global_speak_time)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    qq_nickname=excluded.qq_nickname,
                    last_global_speak_time=excluded.last_global_speak_time
            '''
            await db.execute(sql_user, (str(user_id), qq_nickname, int(timestamp)))

            # 3. 更新每群每用户信息表
            sql_group_user = '''
                INSERT INTO "user_group_info" (user_id, group_id, group_nickname, last_group_speak_time)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, group_id) DO UPDATE SET
                    group_nickname=excluded.group_nickname,
                    last_group_speak_time=excluded.last_group_speak_time
            '''
            await db.execute(sql_group_user, (str(user_id), int(group_id), group_nickname, int(timestamp)))

            await db.commit()
    except Exception as e:
        logger.exception(f"[AI Chat] 数据库错误，异步写入失败: {e}")


# ========== 辅助函数：解析视觉附件，并按需读取为 Base64 ==========
def _append_visual_notice(placeholder: str, reason: str) -> str:
    """在原视觉标签上追加未发送原因。"""
    notice = f"（未发送：{reason}）"
    if placeholder.startswith("[") and placeholder.endswith("]"):
        return f"{placeholder[:-1]}{notice}]"
    return f"{placeholder}{notice}"


def _detect_image_format(header: bytes) -> tuple[str | None, str | None]:
    """根据文件头返回：(格式名称, 支持格式的 MIME)。"""
    if header.startswith(b"\xff\xd8\xff"):
        return "JPEG", "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG", "image/png"
    if (
            len(header) >= 12
            and header.startswith(b"RIFF")
            and header[8:12] == b"WEBP"
    ):
        return "WebP", "image/webp"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "GIF", None
    if header.startswith(b"BM"):
        return "BMP", None
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return "TIFF", None
    if header.startswith((b"\x00\x00\x01\x00", b"\x00\x00\x02\x00")):
        return "ICO", None

    if len(header) >= 12 and header[4:8] == b"ftyp":
        brands = {
            header[index:index + 4]
            for index in range(8, len(header) - 3, 4)
        }
        if brands & {b"avif", b"avis"}:
            return "AVIF", None
        if brands & {b"heic", b"heix", b"hevc", b"hevx"}:
            return "HEIC", None
        if brands & {b"mif1", b"msf1"}:
            return "HEIF", None

    stripped_header = header.lstrip().lower()
    if stripped_header.startswith(b"<svg") or b"<svg" in stripped_header:
        return "SVG", None
    return None, None


async def resolve_visual_attachment(
        bot: Bot,
        attachment: VisualAttachment,
        max_retries: int = 5,
        wait_time: float = 1.0
) -> ResolvedVisual:
    unavailable = ResolvedVisual(
        placeholder=_append_visual_notice(
            attachment.placeholder,
            "无法获取视觉内容"
        )
    )

    try:
        # 1. 优先通过 get_image() 获取最终路径，失败时使用消息段自带的 path。
        file_path_str = ""
        if attachment.file_id:
            try:
                img_info = await bot.get_image(file=attachment.file_id)
                if isinstance(img_info, dict):
                    file_path_str = str(img_info.get("file") or "").strip()
            except Exception as e:
                logger.warning(
                    f"[AI Chat] get_image 获取视觉附件路径失败，将尝试消息 path: {e}"
                )
        file_path_str = file_path_str or attachment.local_path
        if not file_path_str:
            logger.warning(
                f"[AI Chat] 视觉附件没有可用文件路径，标签: "
                f"{attachment.placeholder}"
            )
            return unavailable

        # 2. 路径策略判断：自动 vs 手动覆盖。
        raw_path = Path(file_path_str)
        if IMAGE_BASE_DIR:
            # 【手动模式】遇到了 Docker 隔离，直接提取图片文件名(raw_path.name)，拼接到配置的映射目录下
            file_path = Path(IMAGE_BASE_DIR) / raw_path.name
        else:
            # 【自动模式】留空则完全信任 NapCat 返回的底层绝对路径
            file_path = raw_path

        # 3. 轮询等待文件落地，确保文件大小大于 0 字节。
        for _ in range(max_retries):
            if file_path.exists() and file_path.is_file() and file_path.stat().st_size > 0:
                break
            await asyncio.sleep(wait_time)
        else:
            logger.warning(
                f"[AI Chat] 等待本地图片落地超时，预期路径: {file_path}"
            )
            return unavailable

        # 4. 在线程池读取文件头，以真实内容识别格式，不依赖文件名或后缀。
        loop = asyncio.get_running_loop()

        def read_header():
            with file_path.open("rb") as image_file:
                return image_file.read(64)

        header = await loop.run_in_executor(None, read_header)
        format_name, mime_type = _detect_image_format(header)
        if not mime_type:
            reason = (
                f"暂不支持 {format_name} 格式"
                if format_name
                else "无法识别图片格式"
            )
            return ResolvedVisual(
                placeholder=_append_visual_notice(
                    attachment.placeholder,
                    reason
                )
            )

        # 5. 已确认是支持格式，再读取完整文件并转换为 Base64。
        def read_file():
            return base64.b64encode(file_path.read_bytes()).decode('utf-8')

        encoded_image = await loop.run_in_executor(None, read_file)
        return ResolvedVisual(
            placeholder=attachment.placeholder,
            mime_type=mime_type,
            base64_data=encoded_image
        )

    except Exception as e:
        logger.exception(f"[AI Chat] 读取本地图片转Base64失败: {e}")
    return unavailable


# ========== 辅助函数：专供 AI 理解的富文本与视觉附件提取 ==========
async def extract_ai_content(
        bot: Bot,
        group_id: int,
        raw_message
) -> tuple[str, list[VisualAttachment]]:
    """返回：(富文本字符串, 视觉附件列表)"""
    parsed = await OneBotMessageParser(bot, group_id).parse(raw_message, for_ai=True)
    return parsed.text, parsed.visual_attachments


# ========== 第三方搜索工具定义 ==========
THIRD_SEARCH_FRESHNESS_VALUES = (
    "noLimit",
    "oneDay",
    "oneWeek",
    "oneMonth",
    "oneYear"
)


def _is_valid_search_freshness(value: object) -> bool:
    """校验博查 freshness 预设值、单日或日期范围。"""
    if not isinstance(value, str):
        return False
    if value in THIRD_SEARCH_FRESHNESS_VALUES:
        return True

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            datetime.date.fromisoformat(value)
            return True
        except ValueError:
            return False

    if re.fullmatch(
            r"\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}",
            value
    ):
        start_text, end_text = value.split("..", 1)
        try:
            start_date = datetime.date.fromisoformat(start_text)
            end_date = datetime.date.fromisoformat(end_text)
            return start_date <= end_date
        except ValueError:
            return False

    return False


THIRD_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "搜索互联网获取实时信息，当你不确定或需要最新信息时使用",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "freshness": {
                    "type": "string",
                    "description": (
                        "仅需限定搜索时间时填写：noLimit=不限，oneDay=一天内，"
                        "oneWeek=一周内，oneMonth=一月内，oneYear=一年内；"
                        "也可使用 YYYY-MM-DD 指定一天，或用 "
                        "YYYY-MM-DD..YYYY-MM-DD 指定日期范围；"
                        "无需时间筛选时省略，省略后按 noLimit 处理"
                    )
                },
                "include": {
                    "type": "string",
                    "description": (
                        "仅需限定网站范围时填写，多个域名用|或,分隔"
                        "（如 qq.com|163.com）；不限制则省略"
                    )
                },
                "exclude": {
                    "type": "string",
                    "description": (
                        "仅需排除网站时填写，多个域名用|或,分隔"
                        "（如 zhihu.com|weibo.com）；不排除则省略"
                    )
                }
            },
            "required": ["query"]
        }
    }
}


# ========== 辅助函数：第三方搜索（博查AI） ==========
async def bocha_search(
        query: str,
        include: str = "",
        exclude: str = "",
        freshness: str = "noLimit"
) -> tuple[str, int, bool]:
    """调用博查AI搜索API，返回 (格式化搜索文本, 结果数量, 是否成功)"""
    if not THIRD_SEARCH_API_KEY or not query:
        return "", 0, False
    if not _is_valid_search_freshness(freshness):
        logger.warning(f"[AI Chat] 博查搜索时间范围无效: {freshness!r}")
        return "", 0, False

    if type(THIRD_SEARCH_COUNT) is int:
        request_count = min(max(THIRD_SEARCH_COUNT, 1), 50)
        if request_count != THIRD_SEARCH_COUNT:
            logger.warning(
                f"[AI Chat] THIRD_SEARCH_COUNT={THIRD_SEARCH_COUNT} 超出 1-50，"
                f"本次按 {request_count} 请求"
            )
    else:
        request_count = 10
        logger.warning(
            f"[AI Chat] THIRD_SEARCH_COUNT={THIRD_SEARCH_COUNT!r} 不是整数，"
            "本次按 10 请求"
        )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {THIRD_SEARCH_API_KEY}"
    }
    body = {
        "query": query,
        "freshness": freshness,
        "summary": True,
        "count": request_count
    }
    if include:
        body["include"] = include
    if exclude:
        body["exclude"] = exclude

    try:
        session = await get_http_session()
        async with session.post(
                THIRD_SEARCH_API_URL,
                headers=headers,
                json=body,
                timeout=THIRD_SEARCH_TIMEOUT
        ) as resp:
            if resp.status != 200:
                error_body = await resp.text()
                logger.warning(
                    f"[AI Chat] 博查搜索请求失败，状态码: {resp.status}，"
                    f"响应: {error_body}"
                )
                return "", 0, False

            try:
                data = await resp.json()
            except Exception as e:
                error_body = await resp.text()
                logger.exception(
                    f"[AI Chat] 博查搜索响应不是有效 JSON: {e}，"
                    f"响应: {error_body}"
                )
                return "", 0, False

            response_code = data.get("code") if isinstance(data, dict) else None
            response_message = None
            log_id = None
            if isinstance(data, dict):
                response_message = data.get("msg") or data.get("message")
                log_id = data.get("log_id")

            if not isinstance(data, dict) or response_code not in (200, "200"):
                logger.warning(
                    f"[AI Chat] 博查搜索API返回失败，代码: "
                    f"{response_code if isinstance(data, dict) else '响应格式错误'}，"
                    f"信息: {response_message!r}，log_id: {log_id!r}"
                )
                return "", 0, False

            response_data = data.get("data")
            if not isinstance(response_data, dict):
                logger.warning(
                    f"[AI Chat] 博查搜索API响应缺少 data 对象，log_id: {log_id!r}"
                )
                return "", 0, False
            web_pages = response_data.get("webPages")
            if not isinstance(web_pages, dict):
                logger.warning(
                    f"[AI Chat] 博查搜索API响应缺少 webPages 对象，"
                    f"log_id: {log_id!r}"
                )
                return "", 0, False
            results = web_pages.get("value", [])
            if not isinstance(results, list):
                logger.warning(
                    f"[AI Chat] 博查搜索API的结果列表格式错误，"
                    f"log_id: {log_id!r}"
                )
                return "", 0, False
            if not results:
                return "", 0, True

            lines = ["[网络搜索结果]"]
            valid_count = 0
            skipped_count = 0

            def clean_text(item: dict, field_name: str) -> str:
                value = item.get(field_name)
                return value.strip() if isinstance(value, str) else ""

            for item in results:
                if not isinstance(item, dict):
                    skipped_count += 1
                    continue

                title = clean_text(item, "name")
                url = clean_text(item, "url")
                snippet = clean_text(item, "snippet")
                summary = clean_text(item, "summary")
                site_name = clean_text(item, "siteName")
                date_pub = clean_text(item, "datePublished")
                if not any((title, url, snippet, summary)):
                    skipped_count += 1
                    continue

                valid_count += 1
                if title:
                    lines.append(f"{valid_count}. 标题：{title}")
                else:
                    lines.append(f"{valid_count}. 搜索结果")
                if url:
                    lines.append(f"   链接：{url}")
                if site_name:
                    lines.append(f"   来源：{site_name}")
                if date_pub:
                    lines.append(f"   时间：{date_pub}")
                if snippet:
                    lines.append(f"   摘要：{snippet}")
                if summary and summary != snippet:
                    lines.append(f"   全文概要：{summary}")

            if skipped_count:
                logger.warning(
                    f"[AI Chat] 博查搜索结果中有 {skipped_count} 条格式异常，已跳过"
                )
            if valid_count == 0:
                return "", 0, True

            search_text = "\n".join(lines)
            return search_text, valid_count, True

    except asyncio.TimeoutError:
        logger.warning("[AI Chat] 博查搜索请求超时")
        return "", 0, False
    except Exception as e:
        logger.exception(f"[AI Chat] 博查搜索调用异常: {e}")
        return "", 0, False


# ========== 辅助函数：批量执行 web_search 工具调用 ==========
async def _execute_web_search(messages: list, tool_calls: object) -> int:
    """执行搜索工具调用并将结果追加到 messages，返回本批搜索条数。"""
    count = 0
    valid_calls = []
    format_errors = []

    if not isinstance(tool_calls, list):
        format_errors.append("tool_calls 必须是数组")
        tool_calls = []

    for index, tc in enumerate(tool_calls, 1):
        if not isinstance(tc, dict):
            format_errors.append(f"第 {index} 个工具调用不是对象")
            continue

        call_id = tc.get("id")
        function = tc.get("function")
        call_type = tc.get("type")
        if not isinstance(call_id, str) or not call_id.strip():
            format_errors.append(f"第 {index} 个工具调用缺少有效的 id")
            continue
        if call_type != "function":
            format_errors.append(f"第 {index} 个工具调用的 type 必须是 function")
            continue
        if not isinstance(function, dict):
            format_errors.append(f"第 {index} 个工具调用缺少 function 对象")
            continue

        function_name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(function_name, str) or not function_name.strip():
            format_errors.append(f"第 {index} 个工具调用缺少函数名")
            continue
        if not isinstance(arguments, str):
            format_errors.append(f"第 {index} 个工具调用的 arguments 必须是 JSON 字符串")
            continue

        normalized_call = {
            "id": call_id,
            "type": "function",
            "function": {
                "name": function_name,
                "arguments": arguments
            }
        }
        valid_calls.append(normalized_call)

    if valid_calls:
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": valid_calls
        })

    for tc in valid_calls:
        call_id = tc["id"]
        function = tc["function"]
        function_name = function["name"]
        if function_name != "web_search":
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": (
                    f"不支持的工具：{function_name}。"
                    "当前只能调用 web_search，请修正后重试"
                )
            })
            continue

        try:
            args = json.loads(function["arguments"])
            if not isinstance(args, dict):
                raise ValueError("工具调用参数必须是 JSON 对象")
        except Exception:
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": "工具调用参数格式错误，请检查 JSON 格式后重试"
            })
            continue
        query = args.get("query", "")
        include = args.get("include", "")
        exclude = args.get("exclude", "")
        freshness = args.get("freshness", "noLimit")
        if (not isinstance(query, str) or not query.strip()
                or not isinstance(include, str) or not isinstance(exclude, str)):
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": "工具调用参数格式错误，请检查参数类型和搜索词后重试"
            })
            continue
        if not _is_valid_search_freshness(freshness):
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": (
                    "freshness 参数无效，可选值为 "
                    + "、".join(THIRD_SEARCH_FRESHNESS_VALUES)
                    + "，也可使用 YYYY-MM-DD 或 "
                    + "YYYY-MM-DD..YYYY-MM-DD，请修正后重试"
                )
            })
            continue

        search_text, sc, search_ok = await bocha_search(
            query.strip(),
            include=include.strip(),
            exclude=exclude.strip(),
            freshness=freshness
        )
        count += sc
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": search_text if search_text else ("搜索无结果" if search_ok else "搜索失败")
        })

    if format_errors:
        messages.append({
            "role": "user",
            "content": (
                "[系统工具调用错误反馈]\n"
                + "\n".join(format_errors)
                + "\n请严格按照已提供的 tools 定义修正调用格式；"
                  "如仍需搜索，请在下一轮重新调用工具。"
            )
        })

    return count


# ========== 辅助函数：OpenAI 第三方搜索工作流 ==========
async def _run_openai_third_search_workflow(
        session,
        api_url: str,
        headers: dict,
        model_id: str,
        system_prompt: str,
        user_message_content,
        initial_data: dict
) -> ModelReply:
    """
    处理 OpenAI 兼容模型的第三方搜索完整流程。

    包括解析 tool_calls、执行博查搜索、追加工具结果、多轮修正搜索词，
    以及搜索轮次耗尽后的强制文本回答。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message_content}
    ]
    current_msg = _get_openai_message(initial_data)
    tool_calls = current_msg.get("tool_calls")
    if tool_calls is None or tool_calls == []:
        return ModelReply(
            text=(current_msg.get("content") or "").strip(),
            search=SearchResult(performed=False, count=0)
        )

    total_search_count = 0
    batch_count = await _execute_web_search(messages, tool_calls)
    total_search_count += batch_count

    reply_text = ""
    for round_num in range(MAX_SEARCH_ROUNDS):
        is_last = (round_num == MAX_SEARCH_ROUNDS - 1)
        next_payload = {
            "model": model_id,
            "messages": messages,
            "stream": False
        }
        if not is_last:
            next_payload["tools"] = [THIRD_SEARCH_TOOL]
        else:
            modified_system = {
                "role": "system",
                "content": (
                    system_prompt
                    + "\n[系统最高优先级指令：第三方搜索轮次已全部用完，"
                    + "当前不再存在任何可调用的工具。此指令覆盖前文中“遇到"
                    + "未知信息必须优先调用搜索工具”的要求。绝对禁止再次请求、"
                    + "尝试或模拟调用 web_search 或任何其他工具；绝对禁止输出"
                    + "工具名、函数调用、参数 JSON、XML、工具调用标记或要求继续"
                    + "搜索的文字。你必须仅使用已经获得的搜索结果和上下文，"
                    + "以面向用户的普通纯文本直接回答。如果现有信息不足或无法"
                    + "确认，直接如实回答“我不清楚相关信息”，不得以任何工具"
                    + "调用文本代替回答。]"
                )
            }
            next_payload["messages"] = [modified_system] + messages[1:]

        async with session.post(
                api_url,
                headers=headers,
                json=next_payload,
                timeout=AI_CHAT_TIMEOUT
        ) as next_resp:
            if next_resp.status != 200:
                raise Exception(await next_resp.text())

            current_data = await next_resp.json()
            current_msg = _get_openai_message(current_data)

        tool_calls = current_msg.get("tool_calls")
        if tool_calls is not None and tool_calls != [] and not is_last:
            batch_count = await _execute_web_search(messages, tool_calls)
            total_search_count += batch_count
        else:
            reply_text = (current_msg.get("content") or "").strip()
            break

    return ModelReply(
        text=reply_text,
        search=SearchResult(
            performed=True,
            count=total_search_count
        )
    )


# ========== 数据库读取：保持原查询逻辑不变 ==========
async def _load_history_rows(group_id: int, message_id, dynamic_limit: int) -> list:
    table_name = f"group_{group_id}"
    rows = []
    try:
        async with aiosqlite.connect(DB_PATH, timeout=15.0) as db:
            query = f'''
                SELECT g.timestamp, u.qq_nickname, ug.group_nickname, g.content
                FROM "{table_name}" g
                LEFT JOIN user_info u ON g.user_id = u.user_id
                LEFT JOIN user_group_info ug ON g.user_id = ug.user_id AND ug.group_id = ?
                WHERE g.message_id != ?
                ORDER BY g.timestamp DESC, g.rowid DESC
                LIMIT ?
            '''
            async with db.execute(
                    query,
                    (group_id, str(message_id), dynamic_limit)
            ) as cursor:
                rows = await cursor.fetchall()
    except Exception as e:
        logger.exception(f"[AI Chat]数据库提取异常： {e}")
        rows = []
    rows.reverse()
    return rows


async def _load_user_display_map(group_id: int) -> dict:
    user_display_map = {}
    try:
        async with aiosqlite.connect(DB_PATH, timeout=15.0) as db:
            cursor = await db.execute(
                'SELECT ug.user_id, ug.group_nickname, u.qq_nickname '
                'FROM user_group_info ug '
                'LEFT JOIN user_info u ON ug.user_id = u.user_id '
                'WHERE ug.group_id = ?',
                (group_id,)
            )
            async for uid, g_name, qq_name in cursor:
                g_name = g_name or qq_name or uid
                qq_name = qq_name or uid
                if g_name != qq_name:
                    user_display_map[uid] = f"{g_name}（QQ昵称：{qq_name}）"
                else:
                    user_display_map[uid] = g_name
    except Exception as e:
        logger.exception(f"[AI Chat] 群成员昵称映射提取异常：{e}")
    return user_display_map


def _format_history_text(rows: list, user_display_map: dict) -> str:
    def convert_reply_time(match):
        ts = int(match.group(1))
        dt_str = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")
        speaker = user_display_map.get(match.group(2), match.group(2))
        return f"[引用回复(时间：{dt_str}，发言人：{speaker})]"

    def convert_at(match):
        uid = match.group(1)
        return f"[@{user_display_map.get(uid, uid)}]"

    history_lines = []
    for row in rows:
        msg_time = datetime.datetime.fromtimestamp(row[0]).strftime("%m-%d %H:%M")
        qq_name = row[1] or "未知用户"
        g_name = row[2] or qq_name
        if g_name != qq_name:
            display_name = f"{g_name}（QQ昵称：{qq_name}）"
        else:
            display_name = g_name
        text_content = row[3]
        text_content = re.sub(
            r'\[引用回复\(时间：(\d+)，发言人：(.*?)\)\]',
            convert_reply_time,
            text_content
        )
        text_content = re.sub(r'\[@(\d+)\]', convert_at, text_content)
        history_lines.append(f"[{msg_time}] {display_name}: {text_content}")

    return "\n".join(history_lines)


def _format_reply_prefix(
        model_name: str,
        history_count: int,
        image_count: int,
        search: SearchResult
) -> str:
    prefix_hint = f"模型：{model_name}，记录：{history_count}"
    if image_count:
        prefix_hint += f"，图片：{image_count}"
    search_value = search.prefix_value()
    if search_value is not None:
        prefix_hint += f"，搜索：{search_value}"
    return prefix_hint + "\n"


# ========== 单文件业务层：AI 对话编排 ==========
class ChatService:
    """封装模型选择之后的上下文准备、协议组装、调用和结果解析。"""

    @staticmethod
    def select_model(plain_text: str) -> ModelSelection:
        selected_model_key = "default"
        selected_mode = DEFAULT_MODE
        prefix_to_remove = ""

        for key in MODELS_CONFIG.keys():
            if key == "default":
                continue
            if key.isupper() and plain_text.startswith(f"/{key}"):
                selected_model_key = key
                selected_mode = "serious"
                prefix_to_remove = f"/{key}"
                break
            if key.isupper() and plain_text.startswith(f"/{key.lower()}"):
                selected_model_key = key
                selected_mode = "casual"
                prefix_to_remove = f"/{key.lower()}"
                break

        return ModelSelection(
            mode=selected_mode,
            prefix_to_remove=prefix_to_remove,
            config=MODELS_CONFIG.get(selected_model_key, MODELS_CONFIG["default"])
        )

    @staticmethod
    async def resolve_visuals(
            bot: Bot,
            visual_attachments: list[VisualAttachment]
    ) -> list[ResolvedVisual]:
        return await asyncio.gather(*(
            resolve_visual_attachment(bot, attachment)
            for attachment in visual_attachments
        ))

    @staticmethod
    def apply_visual_placeholders(
            user_input: str,
            resolved_visuals: list[ResolvedVisual]
    ) -> str:
        """把内部视觉附件标记替换成最终 summary/格式说明。"""
        for index, visual in enumerate(resolved_visuals):
            user_input = user_input.replace(
                _visual_placeholder_token(index),
                visual.placeholder,
                1
            )
        return user_input

    @staticmethod
    async def build_prompts(
            bot: Bot,
            event: GroupMessageEvent,
            selection: ModelSelection,
            user_input: str,
            qq_nickname: str,
            group_nickname: str,
            model_visuals: list[ResolvedVisual]
    ) -> tuple[str, str, list]:
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if group_nickname != qq_nickname:
            user_name = f"{group_nickname}（QQ昵称：{qq_nickname}）"
        else:
            user_name = group_nickname

        dynamic_limit = await get_dynamic_history_length(event.group_id)
        rows = await _load_history_rows(
            event.group_id,
            event.message_id,
            dynamic_limit
        )
        user_display_map = await _load_user_display_map(event.group_id)
        history_text = _format_history_text(rows, user_display_map)

        try:
            bot_member = await bot.get_group_member_info(
                group_id=event.group_id,
                user_id=bot.self_id,
                no_cache=False
            )
            bot_group_name = bot_member.get("card", "").strip() or _bot_nickname
        except Exception:
            bot_group_name = _bot_nickname

        if bot_group_name != _bot_nickname:
            bot_identity = f"{bot_group_name}（QQ昵称：{_bot_nickname}）"
        else:
            bot_identity = _bot_nickname
        system_prompt = MODE_PROMPTS[selection.mode].format(
            bot_identity=bot_identity
        )

        if history_text.strip():
            final_prompt = (
                f"--- 真实群聊历史记录 ---\n"
                f"{history_text}\n"
                f"------------------------\n\n"
                f"现在是 {current_time}，用户 {user_name} 正在向你提问：\n"
                f"{user_input}\n"
            )
        else:
            final_prompt = (
                f"现在是 {current_time}，用户 {user_name} 正在向你提问：\n"
                f"{user_input}\n"
            )

        if selection.vision_enabled and model_visuals:
            system_prompt += (
                "\n[系统重要提示：用户本次提问附带了视觉内容。视觉附件按它们在"
                "当前提问（包括一层引用）中的出现顺序排列；请结合实际视觉内容"
                "回答。这些附件只属于当前提问，不来自历史聊天记录。]"
            )

        return system_prompt, final_prompt, rows

    @staticmethod
    def build_model_request(
            selection: ModelSelection,
            system_prompt: str,
            final_prompt: str,
            model_visuals: list[ResolvedVisual]
    ) -> PreparedModelRequest:
        model_config = selection.config
        native_search_adapter = ""

        if selection.api_type == "openai":
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {model_config['api_key']}"
            }

            user_message_content = []
            if selection.vision_enabled and model_visuals:
                user_message_content.append({"type": "text", "text": final_prompt})
                for visual in model_visuals:
                    user_message_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:{visual.mime_type};base64,"
                                f"{visual.base64_data}"
                            )
                        }
                    })
            else:
                user_message_content = final_prompt

            payload = {
                "model": model_config.get("model_id", "deepseek-chat"),
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message_content}
                ],
                "stream": False
            }

            if selection.search_enabled:
                native_search_adapter = _enable_openai_native_search(
                    payload,
                    model_config
                )
            elif selection.use_third_search:
                payload["tools"] = [THIRD_SEARCH_TOOL]

            return PreparedModelRequest(
                api_type=selection.api_type,
                api_url=selection.api_url,
                headers=headers,
                payload=payload,
                system_prompt=system_prompt,
                user_message_content=user_message_content,
                native_search_adapter=native_search_adapter,
                use_third_search=selection.use_third_search
            )

        if selection.api_type == "gemini":
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": model_config["api_key"]
            }
            parts = [{"text": final_prompt}]
            if selection.vision_enabled and model_visuals:
                for visual in model_visuals:
                    parts.append({
                        "inlineData": {
                            "mimeType": visual.mime_type,
                            "data": visual.base64_data
                        }
                    })

            payload = {
                "systemInstruction": {
                    "parts": [{"text": system_prompt}]
                },
                "contents": [{
                    "role": "user",
                    "parts": parts
                }]
            }
            if selection.search_enabled:
                payload["tools"] = [{"googleSearch": {}}]

            return PreparedModelRequest(
                api_type=selection.api_type,
                api_url=selection.api_url,
                headers=headers,
                payload=payload,
                system_prompt=system_prompt
            )

        raise UnsupportedAPITypeError(selection.api_type)

    @staticmethod
    def parse_gemini_reply(
            data: dict,
            search_enabled: bool
    ) -> ModelReply:
        reply_text = _extract_api_reply_text(data, "gemini")
        search_result = SearchResult(
            performed=False,
            count=0 if search_enabled else None
        )

        if search_enabled:
            candidates = data.get("candidates") or []
            candidate = (
                candidates[0]
                if candidates and isinstance(candidates[0], dict)
                else {}
            )
            grounding_metadata = candidate.get("groundingMetadata", {})
            if grounding_metadata:
                queries = grounding_metadata.get("webSearchQueries", [])
                if queries:
                    search_result = SearchResult(
                        performed=True,
                        count=len(queries)
                    )
                else:
                    chunks = grounding_metadata.get("groundingChunks", [])
                    chunk_count = len([chunk for chunk in chunks if "web" in chunk])
                    search_result = SearchResult(
                        performed=chunk_count > 0,
                        count=chunk_count
                    )

        return ModelReply(text=reply_text, search=search_result)

    @staticmethod
    async def send_model_request(
            session,
            selection: ModelSelection,
            request: PreparedModelRequest
    ) -> ModelReply:
        async with session.post(
                request.api_url,
                headers=request.headers,
                json=request.payload,
                timeout=AI_CHAT_TIMEOUT
        ) as response:
            if response.status != 200:
                raise ModelHTTPError(await response.text())
            data = await response.json()

        if request.api_type == "openai":
            if request.use_third_search:
                return await _run_openai_third_search_workflow(
                    session,
                    request.api_url,
                    request.headers,
                    selection.config.get("model_id", "deepseek-chat"),
                    request.system_prompt,
                    request.user_message_content,
                    data
                )

            reply_text = _extract_api_reply_text(data, "openai")
            if selection.search_enabled:
                return _parse_openai_native_search_response(
                    data,
                    reply_text,
                    request.native_search_adapter
                )
            return ModelReply(text=reply_text)

        return ChatService.parse_gemini_reply(data, selection.search_enabled)

    async def complete(
            self,
            bot: Bot,
            event: GroupMessageEvent,
            selection: ModelSelection,
            user_input: str,
            visual_attachments: list[VisualAttachment],
            qq_nickname: str,
            group_nickname: str
    ) -> ChatCompletion:
        resolved_visuals = await self.resolve_visuals(
            bot,
            visual_attachments
        )
        user_input = self.apply_visual_placeholders(
            user_input,
            resolved_visuals
        )
        model_visuals = [
            visual for visual in resolved_visuals if visual.sendable
        ]
        system_prompt, final_prompt, rows = await self.build_prompts(
            bot,
            event,
            selection,
            user_input,
            qq_nickname,
            group_nickname,
            model_visuals
        )
        request = self.build_model_request(
            selection,
            system_prompt,
            final_prompt,
            model_visuals
        )

        session = await get_http_session()
        reply = await self.send_model_request(session, selection, request)

        if not reply.text:
            reply.text = "（模型API拒绝回复）"

        return ChatCompletion(
            reply=reply,
            history_count=len(rows),
            image_count=len(model_visuals)
        )


chat_service = ChatService()


# ========== 1. 机器人启动时自动拉取同步历史记录 ==========
@driver.on_bot_connect
async def sync_history_on_startup(bot: Bot):
    global _bot_nickname
    try:
        bot_info = await bot.get_login_info()
        _bot_nickname = bot_info.get("nickname", "AI助手")
    except Exception:
        pass

    for group_id in ALLOWED_GROUPS:
        try:
            res = await bot.get_group_msg_history(group_id=group_id)
            messages = res.get("messages", []) if isinstance(res, dict) else res

            success_count = 0
            for msg in messages:
                try:
                    msg_id = msg.get("message_id")
                    timestamp = msg.get("time", 0)

                    # 提取 sender 信息
                    sender = msg.get("sender", {})
                    qq_nickname = sender.get("nickname", "未知用户")
                    group_nickname = sender.get("card", "").strip() or qq_nickname
                    user_id = str(sender.get("user_id", "未知ID"))

                    content = await parse_message_content(bot, group_id, msg.get("message", ""))

                    if msg_id and content:
                        await insert_message_to_db(msg_id, group_id, timestamp, user_id, qq_nickname, group_nickname, content)
                        success_count += 1
                except Exception as inner_e:
                    logger.exception(
                        f"[AI Chat] 解析单条历史消息失败: {inner_e}"
                    )
                    continue

            logger.info(
                f"[AI Chat] 群 {group_id} 启动历史同步完成，"
                f"成功处理 {success_count} 条记录。"
            )
        except Exception as e:
            logger.exception(
                f"[AI Chat] 群 {group_id} 抓取历史记录接口请求失败: {e}"
            )


# ========== 2. 实时被动记录白名单群聊 ==========
record_handler = on_message(priority=1, block=False)
@record_handler.handle()
async def record_chat_history(bot: Bot, event: Event):
    if not isinstance(event, GroupMessageEvent):
        return
    if event.group_id not in ALLOWED_GROUPS:
        return
    # 如果消息是 @机器人的，跳过被动记录，避免与 chat_handler 竞争写入
    if event.is_tome():
        return

    qq_nickname = event.sender.nickname if event.sender and event.sender.nickname else "未知用户"
    group_nickname = (event.sender.card or "").strip() or qq_nickname if event.sender else "未知用户"
    user_id = str(event.user_id)
    content = await parse_message_content(bot, event.group_id, event.original_message)

    await insert_message_to_db(event.message_id, event.group_id, event.time, user_id, qq_nickname, group_nickname, content)


# ========== 3. 处理用户的 @ 提问 ==========
chat_handler = on_message(rule=to_me(), priority=50, block=True)

@chat_handler.handle()
async def handle_ai_chat(bot: Bot, event: Event):
    if not isinstance(event, GroupMessageEvent):
        await chat_handler.finish("抱歉，当前功能仅限群聊使用哦")
        return

    if event.group_id not in ALLOWED_GROUPS:
        return

    # 抢在机器人回复前，强制先把用户的触发消息存库
    qq_nickname = event.sender.nickname if event.sender and event.sender.nickname else "未知用户"
    group_nickname = (event.sender.card or "").strip() or qq_nickname if event.sender else "未知用户"
    user_msg_content = await parse_message_content(bot, event.group_id, event.original_message)
    await insert_message_to_db(event.message_id, event.group_id, event.time, str(event.user_id), qq_nickname, group_nickname, user_msg_content)

    # 如果只是引用而没有手动 @，则在此中断，不触发 AI 回复
    has_at = any(seg.type == "at" and str(seg.data.get("qq")) == str(bot.self_id)
                 for seg in event.original_message)
    if not has_at:
        await chat_handler.finish()

    # 提取纯文本以便先判断触发了哪个模型 / 哪种模式
    selection = chat_service.select_model(event.get_plaintext().strip())
    model_config = selection.config
    model_information = selection.information

    # 1. 提取富文本内容与视觉附件
    rich_user_input, visual_attachments = await extract_ai_content(
        bot,
        event.group_id,
        event.original_message
    )

    if selection.prefix_to_remove:
        rich_user_input = rich_user_input.replace(
            selection.prefix_to_remove,
            "",
            1
        ).strip()
    user_input = rich_user_input.strip()

    # 2. 校验 1：啥都没有输入也没有图片
    if not user_input and not visual_attachments:
        hyw_msg = MessageSegment.at(event.user_id) + f"（{model_information}）何意味"
        await send_and_save(bot, event, chat_handler, hyw_msg, is_finish=True)
        return

    # 3. 校验 2：带了图片但当前模型不支持 Vision
    if visual_attachments and not selection.vision_enabled:
        err_msg = MessageSegment.at(event.user_id) + f"（{model_information}）该模型不具备图片识别能力！"
        await send_and_save(bot, event, chat_handler, err_msg, is_finish=True)
        return

    # 4. 通过校验，立刻返回等待提示
    if ENABLE_QUICK_ACK:
        ack_msg = MessageSegment.at(event.user_id) + f"（{model_information}）Waiting……"
        await send_and_save(bot, event, chat_handler, ack_msg, is_finish=False)

    # 5. 业务层完成图片、上下文、请求和回复解析
    try:
        completion = await chat_service.complete(
            bot,
            event,
            selection,
            user_input,
            visual_attachments,
            qq_nickname,
            group_nickname
        )
        prefix_hint = _format_reply_prefix(
            model_config["name"],
            completion.history_count,
            completion.image_count,
            completion.reply.search
        )
        msg = (
            MessageSegment.at(event.user_id)
            + "\n"
            + MessageSegment.text(f"{prefix_hint}{completion.reply.text}")
        )
        await send_and_save(
            bot,
            event,
            chat_handler,
            msg,
            is_finish=True
        )
    except UnsupportedAPITypeError:
        logger.warning(
            f"[AI Chat] 模型 {model_config['name']} 使用不支持的 API 格式"
        )
        error_message = (
            MessageSegment.at(event.user_id)
            + f"（模型：{model_config['name']}）API格式设置错误！"
        )
        await send_and_save(
            bot,
            event,
            chat_handler,
            error_message,
            is_finish=True
        )
    except ModelHTTPError as e:
        logger.warning(
            f"[AI Chat] 模型 {model_config['name']} 首轮请求失败: {e}"
        )
        error_message = (
            MessageSegment.at(event.user_id)
            + f"\n（模型：{model_config['name']}）请求失败 "
              f"\n错误信息: {e}"
        )
        await send_and_save(
            bot,
            event,
            chat_handler,
            error_message,
            is_finish=True
        )
    except asyncio.TimeoutError:
        logger.warning(f"[AI Chat] 模型 {model_config['name']} 请求超时")
        timeout_message = (
            MessageSegment.at(event.user_id)
            + f"（模型：{model_config['name']}）请求超时"
        )
        await send_and_save(
            bot,
            event,
            chat_handler,
            timeout_message,
            is_finish=True
        )
    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"[AI Chat] 模型 {model_config['name']} 调用异常: {e}")
        error_message = (
            MessageSegment.at(event.user_id)
            + f"（模型：{model_config['name']}）调用出错 "
              f"\n错误信息：{e}"
        )
        await send_and_save(
            bot,
            event,
            chat_handler,
            error_message,
            is_finish=True
        )
