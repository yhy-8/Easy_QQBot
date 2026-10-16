"""高缓存会话：build_prompts 的新对话/延续、前缀稳定、窗口过期、群隔离。"""

from __future__ import annotations

import pytest

import easy_ai as ea
from conftest import FakeBot, FakeEvent, run

GROUP = 10001


def _row(rowid, content, ts=1_700_000_000):
    return (ts, "qq", "grp", content, rowid)


@pytest.fixture
def loaders(monkeypatch):
    """替换数据库读取与前置决策，暴露可控状态。"""
    state = {
        "history": [],
        "increments": [],
        "display": {},
        "dynamic": 80,
        "history_calls": 0,
        "after_calls": 0,
        "dynamic_calls": 0,
        "last_current_message": None,
    }

    async def fake_history(group_id, message_id, limit):
        state["history_calls"] += 1
        return list(state["history"])

    async def fake_after(group_id, message_id, watermark):
        state["after_calls"] += 1
        return [row for row in state["increments"] if row[4] > watermark]

    async def fake_display(group_id):
        return dict(state["display"])

    async def fake_dynamic(group_id, current_message=""):
        state["dynamic_calls"] += 1
        state["last_current_message"] = current_message
        return state["dynamic"]

    monkeypatch.setattr(ea, "_load_history_rows", fake_history)
    monkeypatch.setattr(ea, "_load_rows_after", fake_after)
    monkeypatch.setattr(ea, "_load_user_display_map", fake_display)
    monkeypatch.setattr(ea, "get_dynamic_history_length", fake_dynamic)
    return state


def _selection(vision=False):
    return ea.ModelSelection(
        mode="casual",
        prefix_to_remove="",
        config={
            "api_key": "k", "api_url": "https://host", "name": "m",
            "api_type": "openai", "model_id": "mid", "vision": vision, "search": False,
        },
    )


def _prompts(loaders, message_id="m1", user_input="hi", selection=None, visuals=None, bot=None):
    return run(ea.ChatService.build_prompts(
        bot or FakeBot(), FakeEvent(group_id=GROUP, message_id=message_id),
        selection or _selection(), user_input, "qq", "grp", visuals or []))


# --------------------------------------------------------------------------
# 新对话
# --------------------------------------------------------------------------
def test_new_conversation_uses_dynamic_length(loaders):
    loaders["history"] = [_row(1, "a"), _row(2, "b")]
    loaders["dynamic"] = 77
    _, final, rows = _prompts(loaders, user_input="问题")
    assert loaders["dynamic_calls"] == 1
    assert loaders["last_current_message"] == "问题"
    assert rows == loaders["history"]
    assert loaders["history_calls"] == 1

    session = ea._group_session[GROUP]
    assert session["active"] is True
    assert session["watermark_rowid"] == 2
    assert session["last_bot_ts"] > 0
    assert "--- 真实群聊历史记录 ---" in final
    assert "问题" in final


def test_new_conversation_without_history_has_no_block(loaders):
    _, final, rows = _prompts(loaders)
    assert rows == []
    assert "--- 真实群聊历史记录 ---" not in final
    assert ea._group_session[GROUP]["active"] is True


def test_new_conversation_empty_history_keeps_watermark_zero(loaders):
    _prompts(loaders)
    assert ea._group_session[GROUP]["watermark_rowid"] == 0


# --------------------------------------------------------------------------
# 延续会话：前缀稳定
# --------------------------------------------------------------------------
def test_continuation_appends_and_keeps_prefix(loaders):
    loaders["history"] = [_row(1, "a"), _row(2, "b")]
    _, first, _ = _prompts(loaders, message_id="m1", user_input="q1")

    loaders["increments"] = [_row(3, "c")]
    _, second, rows2 = _prompts(loaders, message_id="m2", user_input="q2")
    assert loaders["dynamic_calls"] == 1  # 延续不再调用前置 AI
    assert loaders["after_calls"] == 1
    assert [r[4] for r in rows2] == [1, 2, 3]
    assert ea._group_session[GROUP]["watermark_rowid"] == 3

    loaders["increments"] = [_row(3, "c"), _row(4, "d")]
    _, third, rows3 = _prompts(loaders, message_id="m3", user_input="q3")
    assert [r[4] for r in rows3] == [1, 2, 3, 4]

    def history_block(text):
        return text.split("--- 真实群聊历史记录 ---\n", 1)[1].split("\n------------------------", 1)[0]

    assert history_block(second).startswith(history_block(first))
    assert history_block(third).startswith(history_block(second))


def test_continuation_without_new_messages(loaders):
    loaders["history"] = [_row(1, "a")]
    _prompts(loaders, message_id="m1")
    _, _, rows = _prompts(loaders, message_id="m2")
    assert [r[4] for r in rows] == [1]
    assert ea._group_session[GROUP]["watermark_rowid"] == 1


def test_continuation_from_empty_base_loads_all(loaders):
    _prompts(loaders, message_id="m1")  # 新对话时库为空
    loaders["increments"] = [_row(1, "a"), _row(2, "b")]
    _, _, rows = _prompts(loaders, message_id="m2")
    assert [r[4] for r in rows] == [1, 2]


# --------------------------------------------------------------------------
# 窗口过期
# --------------------------------------------------------------------------
def test_window_expiry_starts_new_conversation(loaders):
    loaders["history"] = [_row(1, "a")]
    _prompts(loaders, message_id="m1")
    ea._group_session[GROUP]["last_bot_ts"] -= (ea.CONTEXT_CACHE_WINDOW + 5)

    loaders["history"] = [_row(9, "fresh")]
    _, _, rows = _prompts(loaders, message_id="m2")
    assert loaders["dynamic_calls"] == 2
    assert rows == loaders["history"]
    assert ea._group_session[GROUP]["watermark_rowid"] == 9


def test_should_extend_conversation_requires_active_and_window():
    loaders_session = ea._get_group_session(GROUP)
    assert ea._should_extend_conversation(GROUP) is False
    loaders_session["active"] = True
    loaders_session["last_bot_ts"] = int(__import__("time").time())
    assert ea._should_extend_conversation(GROUP) is True
    loaders_session["last_bot_ts"] -= (ea.CONTEXT_CACHE_WINDOW + 1)
    assert ea._should_extend_conversation(GROUP) is False


# --------------------------------------------------------------------------
# 群隔离
# --------------------------------------------------------------------------
def test_sessions_are_isolated_per_group(loaders):
    loaders["history"] = [_row(1, "a")]
    _prompts(loaders, message_id="m1")
    loaders["history"] = [_row(9, "other")]
    run(ea.ChatService.build_prompts(
        FakeBot(), FakeEvent(group_id=20002, message_id="x1"),
        _selection(), "hi", "qq", "grp", []))
    assert ea._group_session[GROUP]["base_rows"][-1][4] == 1
    assert ea._group_session[20002]["base_rows"][-1][4] == 9


# --------------------------------------------------------------------------
# 提示词内容
# --------------------------------------------------------------------------
def test_visual_hint_added_when_supported(loaders):
    visual = ea.ResolvedVisual(placeholder="[图片]", mime_type="image/png", base64_data="A")
    system, _, _ = _prompts(loaders, selection=_selection(vision=True), visuals=[visual])
    assert "用户本次提问附带了视觉内容" in system


def test_visual_hint_skipped_when_model_lacks_vision(loaders):
    visual = ea.ResolvedVisual(placeholder="[图片]", mime_type="image/png", base64_data="A")
    system, _, _ = _prompts(loaders, selection=_selection(vision=False), visuals=[visual])
    assert "用户本次提问附带了视觉内容" not in system


def test_bot_identity_uses_group_card(loaders):
    bot = FakeBot(self_id="999")
    bot.member_info[(GROUP, 999)] = {"nickname": "BotNick", "card": "BotCard"}
    system, _, _ = _prompts(loaders, bot=bot)
    # QQ 昵称取自全局 _bot_nickname（登录时写入），群名片取自成员信息
    assert "BotCard（QQ昵称：AI助手）" in system


def test_bot_identity_falls_back_to_nickname(loaders):
    system, _, _ = _prompts(loaders)
    assert "AI助手" in system


def test_user_name_in_prompt(loaders):
    _, final, _ = _prompts(loaders, user_input="hello")
    assert "用户 grp（QQ昵称：qq） 正在向你提问" in final
    assert "hello" in final


def test_history_display_map_applied(loaders):
    loaders["history"] = [_row(1, "[@42]")]
    loaders["display"] = {"42": "张三"}
    _, final, _ = _prompts(loaders)
    assert "[@张三]" in final


def test_same_nickname_uses_single_name(loaders):
    _, final, _ = run(ea.ChatService.build_prompts(
        FakeBot(), FakeEvent(group_id=GROUP, message_id="m1"),
        _selection(), "hi", "same", "same", []))
    assert "用户 same 正在向你提问" in final
    assert "QQ昵称" not in final.split("正在向你提问")[0].split("用户")[-1]


def test_bot_member_lookup_failure_falls_back(loaders):
    bot = FakeBot(self_id="999")
    bot.member_info[(GROUP, 999)] = RuntimeError("member api down")
    system, _, _ = _prompts(loaders, bot=bot)
    assert "AI助手" in system
