"""事件入口：handle_ai_chat / record_chat_history / sync_history_on_startup。"""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest

import easy_ai as ea
from conftest import FakeBot, FakeEvent, MessageSegment, run
from nonebot.adapters.onebot.v11 import Event

GROUP = 10001
OTHER_GROUP = 20002


async def _chat_rows(group=GROUP):
    async with aiosqlite.connect(ea.DB_PATH) as db:
        async with db.execute(f'SELECT content FROM "group_{group}"') as cursor:
            return await cursor.fetchall()


def _at_bot_message(text="你好", bot_id="999"):
    return MessageSegment.at(bot_id) + MessageSegment.text(text)


def _patch_complete(monkeypatch, result=None, error=None, recorder=None):
    async def fake_complete(bot, event, selection, user_input, visual_attachments,
                            qq_nickname, group_nickname):
        if recorder is not None:
            recorder["user_input"] = user_input
            recorder["selection"] = selection
            recorder["visual_attachments"] = visual_attachments
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(ea.chat_service, "complete", fake_complete)


def _chat_completion(text="答案", history=3, images=0, search=None):
    return ea.ChatCompletion(
        reply=ea.ModelReply(text=text, search=search or ea.SearchResult()),
        history_count=history,
        image_count=images,
    )


# ==========================================================================
# handle_ai_chat
# ==========================================================================
def test_non_group_message(monkeypatch):
    with pytest.raises(ea.FinishedException):
        run(ea.handle_ai_chat(FakeBot(), Event()))


def test_non_whitelisted_group_returns(temp_db):
    event = FakeEvent(group_id=99999, message=_at_bot_message())
    # 不应抛 FinishedException，也不应发送／写入
    run(ea.handle_ai_chat(FakeBot(), event))
    assert ea.chat_handler.sent == []


def test_empty_input_sends_heyiwei(temp_db):
    event = FakeEvent(group_id=GROUP, message=[MessageSegment.at("999")])
    with pytest.raises(ea.FinishedException):
        run(ea.handle_ai_chat(FakeBot(), event))
    assert "何意味" in str(ea.chat_handler.sent[-1])


def test_image_without_vision_is_rejected(temp_db):
    # /A 模型 vision=False
    event = FakeEvent(group_id=GROUP, message=[
        MessageSegment.at("999"),
        MessageSegment.text("/A"),
        MessageSegment.image("f.jpg", "猫"),
    ])
    with pytest.raises(ea.FinishedException):
        run(ea.handle_ai_chat(FakeBot(), event))
    assert "不具备图片识别能力" in str(ea.chat_handler.sent[-1])


def test_quick_ack_then_reply_and_save(temp_db, monkeypatch):
    _patch_complete(monkeypatch, result=_chat_completion("正式答案"), recorder=None)
    event = FakeEvent(group_id=GROUP, user_id=555, message=_at_bot_message("你好"))
    with pytest.raises(ea.FinishedException):
        run(ea.handle_ai_chat(FakeBot(), event))

    sent = [str(m) for m in ea.chat_handler.sent]
    assert any("Waiting……" in m for m in sent)
    assert any("模型：" in m and "正式答案" in m for m in sent)

    rows = run(_chat_rows())
    assert any("你好" in r[0] for r in rows)          # 用户触发消息
    assert any("正式答案" in r[0] for r in rows)       # 机器人正式回复


def test_quick_ack_disabled(monkeypatch, temp_db):
    monkeypatch.setattr(ea, "ENABLE_QUICK_ACK", False)
    _patch_complete(monkeypatch, result=_chat_completion())
    event = FakeEvent(group_id=GROUP, message=_at_bot_message())
    with pytest.raises(ea.FinishedException):
        run(ea.handle_ai_chat(FakeBot(), event))
    assert not any("Waiting……" in str(m) for m in ea.chat_handler.sent)


def test_prefix_is_stripped_before_complete(temp_db, monkeypatch):
    recorder = {}
    _patch_complete(monkeypatch, result=_chat_completion(), recorder=recorder)
    event = FakeEvent(group_id=GROUP, message=_at_bot_message("/A 解释一下"))
    with pytest.raises(ea.FinishedException):
        run(ea.handle_ai_chat(FakeBot(), event))
    assert recorder["user_input"] == "解释一下"
    assert recorder["selection"].config is ea.MODELS_CONFIG["A"]
    assert recorder["selection"].mode == "serious"


def test_search_prefix_in_reply(temp_db, monkeypatch):
    _patch_complete(monkeypatch, result=_chat_completion("有搜索结果", search=ea.SearchResult(True, 4)))
    event = FakeEvent(group_id=GROUP, message=_at_bot_message())
    with pytest.raises(ea.FinishedException):
        run(ea.handle_ai_chat(FakeBot(), event))
    assert any("搜索：4" in str(m) for m in ea.chat_handler.sent)


@pytest.mark.parametrize(
    "error,expected",
    [
        (ea.UnsupportedAPITypeError("x"), "API格式设置错误"),
        (ea.ModelHTTPError("upstream"), "请求失败"),
        (asyncio.TimeoutError(), "请求超时"),
        (RuntimeError("boom"), "调用出错"),
    ],
)
def test_error_branches(temp_db, monkeypatch, error, expected):
    _patch_complete(monkeypatch, error=error)
    event = FakeEvent(group_id=GROUP, message=_at_bot_message())
    with pytest.raises(ea.FinishedException):
        run(ea.handle_ai_chat(FakeBot(), event))
    assert any(expected in str(m) for m in ea.chat_handler.sent)


def test_finished_exception_from_complete_is_reraised(temp_db, monkeypatch):
    _patch_complete(monkeypatch, error=ea.FinishedException())
    event = FakeEvent(group_id=GROUP, message=_at_bot_message())
    with pytest.raises(ea.FinishedException):
        run(ea.handle_ai_chat(FakeBot(), event))


def test_quote_without_at_is_saved_but_not_answered(temp_db):
    event = FakeEvent(group_id=GROUP, to_me=True, message=[
        MessageSegment("reply", {"id": "m1"}),
        MessageSegment.text("追问"),
    ])
    with pytest.raises(ea.FinishedException):
        run(ea.handle_ai_chat(FakeBot(), event))
    assert ea.chat_handler.sent == []
    assert len(run(_chat_rows())) == 1


# ==========================================================================
# record_chat_history
# ==========================================================================
def test_record_skips_non_group(temp_db):
    run(ea.record_chat_history(FakeBot(), Event()))
    assert run(_chat_rows()) == []


def test_record_skips_non_whitelist(temp_db):
    event = FakeEvent(group_id=99999, message=[MessageSegment.text("hi")])
    run(ea.record_chat_history(FakeBot(), event))
    assert run(_chat_rows()) == []


def test_record_skips_message_to_bot(temp_db):
    event = FakeEvent(group_id=GROUP, message=_at_bot_message("hi"))
    run(ea.record_chat_history(FakeBot(), event))
    assert run(_chat_rows()) == []


def test_record_normal_message(temp_db):
    event = FakeEvent(group_id=GROUP, user_id=555, message=[MessageSegment.text("群聊内容")],
                      nickname="qqnick", card="群名片")
    run(ea.record_chat_history(FakeBot(), event))
    rows = run(_chat_rows())
    assert rows == [("群聊内容",)]


# ==========================================================================
# sync_history_on_startup
# ==========================================================================
def _history_message(message_id, text, user_id=1, nickname="nick", card="", time=100):
    return {
        "message_id": message_id,
        "time": time,
        "sender": {"user_id": user_id, "nickname": nickname, "card": card},
        "message": [{"type": "text", "data": {"text": text}}],
    }


def test_sync_sets_nickname_and_inserts(temp_db):
    bot = FakeBot(nickname="真实昵称")
    bot.group_history[GROUP] = {"messages": [
        _history_message("h1", "第一条"),
        _history_message("h2", "第二条"),
    ]}
    run(ea.sync_history_on_startup(bot))
    assert ea._bot_nickname == "真实昵称"
    assert len(run(_chat_rows())) == 2


def test_sync_skips_empty_content(temp_db):
    bot = FakeBot()
    bot.group_history[GROUP] = {"messages": [
        _history_message("h1", ""),
        _history_message("h2", "有效"),
    ]}
    run(ea.sync_history_on_startup(bot))
    rows = run(_chat_rows())
    assert rows == [("有效",)]


def test_sync_single_group_failure_does_not_block_others(temp_db, monkeypatch):
    monkeypatch.setattr(ea, "ALLOWED_GROUPS", [GROUP, OTHER_GROUP])
    run(ea.init_db())
    bot = FakeBot()
    bot.group_history[GROUP] = RuntimeError("api down")
    bot.group_history[OTHER_GROUP] = {"messages": [_history_message("h1", "其他群")]}
    run(ea.sync_history_on_startup(bot))
    assert run(_chat_rows(GROUP)) == []
    assert len(run(_chat_rows(OTHER_GROUP))) == 1


def test_sync_malformed_message_is_isolated(temp_db):
    bot = FakeBot()
    bot.group_history[GROUP] = {"messages": [
        "not-a-dict",
        _history_message("h1", "good"),
    ]}
    run(ea.sync_history_on_startup(bot))
    assert run(_chat_rows()) == [("good",)]


def test_sync_login_info_failure_keeps_nickname(temp_db, monkeypatch):
    bot = FakeBot()

    async def boom():
        raise RuntimeError("no login info")

    monkeypatch.setattr(bot, "get_login_info", boom)
    run(ea.sync_history_on_startup(bot))
    assert ea._bot_nickname == "AI助手"
