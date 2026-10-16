"""Pytest 引导与测试基础设施。

``easy_ai.py`` 是单文件 NoneBot2 插件：模块级就会 ``from nonebot import ...``
并注册 Handler。真实 nonebot 未随仓库安装，因此这里在导入前把最小可用的
nonebot API（logger / get_driver / on_message / MessageSegment / FinishedException）
注入 ``sys.modules``，让测试可以直接调用插件内部的函数、解析器与业务对象。

测试统一用 ``run(coro)`` 在同步用例里运行协程，不依赖 pytest-asyncio。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def run(coro):
    """在同步测试里运行协程。"""
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# nonebot 桩
# --------------------------------------------------------------------------
class _StubLogger:
    """记录日志调用，便于断言异常边界是否命中。"""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _log(self, level: str, message, *args, **kwargs) -> None:
        self.records.append((level, str(message)))

    def info(self, message, *args, **kwargs):
        self._log("info", message)

    def warning(self, message, *args, **kwargs):
        self._log("warning", message)

    def error(self, message, *args, **kwargs):
        self._log("error", message)

    def exception(self, message, *args, **kwargs):
        self._log("exception", message)

    def debug(self, message, *args, **kwargs):
        self._log("debug", message)

    def critical(self, message, *args, **kwargs):
        self._log("critical", message)

    def contains(self, level: str) -> bool:
        return any(record_level == level for record_level, _ in self.records)


class _StubDriver:
    def __init__(self) -> None:
        self.startup: list = []
        self.shutdown: list = []
        self.connect: list = []

    def on_startup(self, func):
        self.startup.append(func)
        return func

    def on_shutdown(self, func):
        self.shutdown.append(func)
        return func

    def on_bot_connect(self, func):
        self.connect.append(func)
        return func


class MessageSegment:
    """最小可用的 OneBot 消息段，支持 ``at``/``text``/``image`` 与加法拼接。"""

    def __init__(self, type: str = "", data: dict | None = None) -> None:
        self.type = type
        self.data = data if data is not None else {}

    @staticmethod
    def at(qq) -> "MessageSegment":
        return MessageSegment("at", {"qq": str(qq)})

    @staticmethod
    def text(text) -> "MessageSegment":
        return MessageSegment("text", {"text": str(text)})

    @staticmethod
    def image(file: str = "", summary: str = "", **extra) -> "MessageSegment":
        data = {"file": file, "summary": summary}
        data.update(extra)
        return MessageSegment("image", data)

    def __add__(self, other):
        return Message([self]).__add__(other)

    def __radd__(self, other):
        return Message(other).__add__(self)

    def __str__(self) -> str:
        if self.type == "text":
            return str(self.data.get("text", ""))
        if self.type == "at":
            return f"[at:{self.data.get('qq')}]"
        return f"[{self.type}]"

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"MessageSegment({self.type!r}, {self.data!r})"


class Message(list):
    """最小可用的 OneBot 消息列表。"""

    def __init__(self, iterable=()) -> None:
        super().__init__()
        for item in self._coerce(iterable):
            self.append(item)

    @staticmethod
    def _coerce(value) -> list:
        if value is None:
            return []
        if isinstance(value, MessageSegment):
            return [value]
        if isinstance(value, Message):
            return list(value)
        if isinstance(value, str):
            return [MessageSegment("text", {"text": value})]
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    def __add__(self, other):
        return Message(list(self) + self._coerce(other))

    def __radd__(self, other):
        return Message(self._coerce(other) + list(self))

    def __str__(self) -> str:
        return "".join(str(segment) for segment in self)

    def extract_plain_text(self) -> str:
        return "".join(
            str(seg.data.get("text", ""))
            for seg in self
            if getattr(seg, "type", "") == "text"
        )


class FinishedException(Exception):
    """对应 nonebot 的 FinishedException。"""


class _StubMatcher:
    """对应 ``on_message(...)`` 返回的 Matcher。"""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.sent: list = []
        self.send_result: dict | None = {"message_id": "sent-1"}
        self.fail_sends: int = 0
        self.finish_calls: int = 0

    def handle(self):
        def decorator(func):
            return func

        return decorator

    async def send(self, msg):
        if self.fail_sends > 0:
            self.fail_sends -= 1
            raise RuntimeError("simulated send failure")
        self.sent.append(msg)
        return self.send_result

    async def finish(self, msg=None):
        self.finish_calls += 1
        if msg is not None:
            self.sent.append(msg)
        raise FinishedException()


def _install_nonebot_stubs() -> None:
    if getattr(sys.modules.get("nonebot"), "_easy_ai_stub", False):
        return

    nonebot = types.ModuleType("nonebot")
    nonebot._easy_ai_stub = True
    logger = _StubLogger()
    driver = _StubDriver()
    nonebot.logger = logger
    nonebot.get_driver = lambda: driver
    nonebot.on_message = lambda **kwargs: _StubMatcher(**kwargs)
    nonebot._driver = driver
    nonebot._logger = logger
    sys.modules["nonebot"] = nonebot

    adapters = types.ModuleType("nonebot.adapters")
    sys.modules["nonebot.adapters"] = adapters
    onebot = types.ModuleType("nonebot.adapters.onebot")
    sys.modules["nonebot.adapters.onebot"] = onebot
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")

    class Bot:
        pass

    class Event:
        pass

    class GroupMessageEvent(Event):
        pass

    v11.Bot = Bot
    v11.Event = Event
    v11.GroupMessageEvent = GroupMessageEvent
    v11.MessageSegment = MessageSegment
    v11.Message = Message
    sys.modules["nonebot.adapters.onebot.v11"] = v11

    exception = types.ModuleType("nonebot.exception")
    exception.FinishedException = FinishedException
    sys.modules["nonebot.exception"] = exception


_install_nonebot_stubs()

import easy_ai  # noqa: E402  (必须在安装桩之后导入)

from nonebot.adapters.onebot.v11 import GroupMessageEvent  # noqa: E402


# --------------------------------------------------------------------------
# 测试替身
# --------------------------------------------------------------------------
class FakeSender:
    def __init__(self, nickname: str = "nick", card: str = "") -> None:
        self.nickname = nickname
        self.card = card


class FakeEvent(GroupMessageEvent):
    """可定制的群消息事件。"""

    def __init__(
            self,
            group_id: int = 10001,
            user_id: int = 555,
            message=None,
            message_id: str = "mid-1",
            timestamp: int | None = None,
            to_me: bool = False,
            nickname: str = "nick",
            card: str = "",
    ) -> None:
        self.group_id = group_id
        self.user_id = user_id
        self.original_message = (
            message if isinstance(message, Message) else Message(message or [])
        )
        self.message_id = message_id
        self.time = timestamp if timestamp is not None else 1_700_000_000
        self.to_me = to_me
        self.sender = FakeSender(nickname=nickname, card=card)

    def get_plaintext(self) -> str:
        return self.original_message.extract_plain_text()


class FakeBot:
    """可编程的 Bot 替身，未配置的调用按“合理默认值”返回。"""

    def __init__(self, self_id: str = "999", nickname: str = "BotNick") -> None:
        self.self_id = self_id
        self.nickname = nickname
        self.member_info: dict = {}
        self.msg_by_id: dict = {}
        self.image_info: dict = {}
        self.group_history: dict = {}
        self.calls: list[tuple[str, dict]] = []

    async def get_login_info(self):
        self.calls.append(("get_login_info", {}))
        return {"nickname": self.nickname, "user_id": int(self.self_id)}

    async def get_group_member_info(self, group_id, user_id, no_cache=False):
        self.calls.append(("get_group_member_info", {"group_id": group_id, "user_id": user_id}))
        value = self.member_info.get((group_id, int(user_id)))
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        return {"nickname": f"u{user_id}", "card": ""}

    async def get_msg(self, message_id):
        self.calls.append(("get_msg", {"message_id": message_id}))
        value = self.msg_by_id.get(str(message_id))
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise RuntimeError(f"message {message_id} not found")
        return value

    async def get_image(self, file):
        self.calls.append(("get_image", {"file": file}))
        value = self.image_info.get(str(file))
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise RuntimeError(f"image {file} not found")
        return value

    async def get_group_msg_history(self, group_id):
        self.calls.append(("get_group_msg_history", {"group_id": group_id}))
        value = self.group_history.get(group_id, {"messages": []})
        if isinstance(value, Exception):
            raise value
        return value


class FakeResponse:
    def __init__(self, status: int = 200, payload=None, text: str | None = None,
                 raise_json: Exception | None = None) -> None:
        self.status = status
        self._payload = payload
        self._text = text
        self._raise_json = raise_json

    async def json(self):
        if self._raise_json is not None:
            raise self._raise_json
        return self._payload

    async def text(self):
        if self._text is not None:
            return self._text
        return str(self._payload)


class _PostContext:
    def __init__(self, response) -> None:
        self._response = response

    async def __aenter__(self):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    """按顺序吐出预设响应的 aiohttp 会话替身。"""

    def __init__(self, responses=None) -> None:
        self.responses = list(responses or [])
        self.requests: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.requests.append({
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        })
        if not self.responses:
            raise AssertionError("FakeSession 没有更多预设响应")
        response = self.responses.pop(0)
        if not isinstance(response, (FakeResponse, Exception)):
            response = FakeResponse(status=200, payload=response)
        return _PostContext(response)


def ok_json(payload) -> FakeResponse:
    return FakeResponse(status=200, payload=payload)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_plugin_state():
    """每个用例前后恢复插件全局可变状态，避免用例互相污染。"""
    import nonebot

    easy_ai._group_session.clear()
    easy_ai._http_session = None
    easy_ai._bot_nickname = "AI助手"
    nonebot._logger.records.clear()

    for handler in (easy_ai.record_handler, easy_ai.chat_handler):
        handler.sent.clear()
        handler.send_result = {"message_id": "sent-1"}
        handler.fail_sends = 0
        handler.finish_calls = 0
    yield
    easy_ai._group_session.clear()
    easy_ai._http_session = None


@pytest.fixture
def logger():
    import nonebot

    return nonebot._logger


TEST_GROUP = 10001


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """初始化一个临时 SQLite 库，并把白名单限定为该测试群。"""
    db_path = tmp_path / "chat_history.db"
    monkeypatch.setattr(easy_ai, "DB_PATH", str(db_path))
    monkeypatch.setattr(easy_ai, "ALLOWED_GROUPS", [TEST_GROUP])
    run(easy_ai.init_db())
    return db_path


@pytest.fixture
def fake_session(monkeypatch):
    """把 get_http_session 替换为 FakeSession 的工厂。"""

    def factory(responses=None):
        session = FakeSession(responses)

        async def _get():
            return session

        monkeypatch.setattr(easy_ai, "get_http_session", _get)
        return session

    return factory
