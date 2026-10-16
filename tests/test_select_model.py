"""模型选择与 @ 判定。"""

from __future__ import annotations

import pytest

import easy_ai as ea
from conftest import FakeBot, FakeEvent, MessageSegment, run
from nonebot.adapters.onebot.v11 import Event

GROUP = 10001


# --------------------------------------------------------------------------
# select_model
# --------------------------------------------------------------------------
def test_default_selection():
    sel = ea.ChatService.select_model("你好")
    assert sel.config is ea.MODELS_CONFIG["default"]
    assert sel.mode == ea.DEFAULT_MODE
    assert sel.prefix_to_remove == ""


@pytest.mark.parametrize(
    "text,key,mode,prefix",
    [
        ("/A 你好", "A", "serious", "/A"),
        ("/a 你好", "A", "casual", "/a"),
        ("/B 你好", "B", "serious", "/B"),
        ("/b 你好", "B", "casual", "/b"),
        ("/C 你好", "C", "serious", "/C"),
        ("/c 你好", "C", "casual", "/c"),
    ],
)
def test_prefix_selection(text, key, mode, prefix):
    sel = ea.ChatService.select_model(text)
    assert sel.config is ea.MODELS_CONFIG[key]
    assert sel.mode == mode
    assert sel.prefix_to_remove == prefix


def test_unknown_prefix_falls_back():
    sel = ea.ChatService.select_model("/Z 你好")
    assert sel.config is ea.MODELS_CONFIG["default"]
    assert sel.prefix_to_remove == ""


def test_prefix_must_be_at_start():
    sel = ea.ChatService.select_model("前面 /A 你好")
    assert sel.prefix_to_remove == ""
    assert sel.config is ea.MODELS_CONFIG["default"]


def test_lowercase_only_keys_do_not_participate(monkeypatch):
    configs = {"default": dict(ea.MODELS_CONFIG["default"])}
    configs["lower"] = dict(ea.MODELS_CONFIG["default"], name="lower")
    monkeypatch.setattr(ea, "MODELS_CONFIG", configs)
    sel = ea.ChatService.select_model("/lower 你好")
    assert sel.config is configs["default"]


def test_prefix_order_is_insertion_order(monkeypatch):
    base = dict(ea.MODELS_CONFIG["default"])
    configs = {
        "default": dict(base),
        "A": dict(base, name="A"),
        "AB": dict(base, name="AB"),
    }
    monkeypatch.setattr(ea, "MODELS_CONFIG", configs)
    # 短前缀先命中（现有实现按字典插入顺序 break）
    sel = ea.ChatService.select_model("/AB 你好")
    assert sel.config["name"] == "A"


# --------------------------------------------------------------------------
# is_message_to_bot
# --------------------------------------------------------------------------
def test_non_group_event_is_false():
    assert run(ea.is_message_to_bot(FakeBot(), Event())) is False


def test_to_me_true():
    event = FakeEvent(to_me=True)
    assert run(ea.is_message_to_bot(FakeBot(), event)) is True


def test_at_bot_segment():
    bot = FakeBot(self_id="999")
    event = FakeEvent(message=[MessageSegment.at("999"), MessageSegment.text("hi")])
    assert run(ea.is_message_to_bot(bot, event)) is True


def test_at_other_is_false():
    bot = FakeBot(self_id="999")
    event = FakeEvent(message=[MessageSegment.at("777")])
    assert run(ea.is_message_to_bot(bot, event)) is False


def test_nickname_match(monkeypatch):
    monkeypatch.setattr(ea, "_bot_nickname", "小助手")
    event = FakeEvent(message=[MessageSegment.text("小助手你好")])
    assert run(ea.is_message_to_bot(FakeBot(), event)) is True


def test_empty_nickname_does_not_match(monkeypatch):
    monkeypatch.setattr(ea, "_bot_nickname", "")
    event = FakeEvent(message=[MessageSegment.text("无关键词")])
    assert run(ea.is_message_to_bot(FakeBot(), event)) is False


def test_exception_in_segment_scan_returns_false():
    class Exploding:
        def __iter__(self):
            raise RuntimeError("boom")

    event = FakeEvent()
    event.original_message = Exploding()
    assert run(ea.is_message_to_bot(FakeBot(), event)) is False
