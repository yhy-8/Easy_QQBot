"""发送与落库：无限重发、超时收口、备用提示、Waiting 不入库。"""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest

import easy_ai as ea
from conftest import FakeBot, FakeEvent, MessageSegment, run

GROUP = 10001


async def _chat_rows():
    async with aiosqlite.connect(ea.DB_PATH) as db:
        async with db.execute(f'SELECT content FROM "group_{GROUP}"') as cursor:
            return await cursor.fetchall()


def _formal_msg(text="正式正文"):
    return MessageSegment.at(555) + "\n" + MessageSegment.text(text)


# --------------------------------------------------------------------------
# _send_loop
# --------------------------------------------------------------------------
def test_send_loop_retries_until_success(logger):
    matcher = ea.chat_handler
    matcher.fail_sends = 3
    result = run(ea._send_loop(matcher, "msg", "测试"))
    assert result == {"message_id": "sent-1"}
    assert len(matcher.sent) == 1
    warnings = [m for level, m in logger.records if level == "warning"]
    assert len(warnings) == 3


# --------------------------------------------------------------------------
# 快速回复（Waiting……）
# --------------------------------------------------------------------------
def test_quick_ack_not_saved_and_not_finished(temp_db):
    matcher = ea.chat_handler
    event = FakeEvent(group_id=GROUP)
    run(ea.send_and_save(FakeBot(), event, matcher, _formal_msg("Waiting……"), is_finish=False))
    assert run(_chat_rows()) == []
    assert matcher.finish_calls == 0
    assert len(matcher.sent) == 1


# --------------------------------------------------------------------------
# 正式回复
# --------------------------------------------------------------------------
def test_formal_reply_saved_and_finishes(temp_db):
    matcher = ea.chat_handler
    event = FakeEvent(group_id=GROUP, user_id=555)
    with pytest.raises(ea.FinishedException):
        run(ea.send_and_save(FakeBot(), event, matcher, _formal_msg("正式正文"), is_finish=True))
    rows = run(_chat_rows())
    assert len(rows) == 1
    assert "正式正文" in rows[0][0]
    assert matcher.finish_calls == 1


def test_send_result_without_message_id_is_not_saved(temp_db):
    matcher = ea.chat_handler
    matcher.send_result = {"status": "ok"}
    event = FakeEvent(group_id=GROUP)
    with pytest.raises(ea.FinishedException):
        run(ea.send_and_save(FakeBot(), event, matcher, _formal_msg(), is_finish=True))
    assert run(_chat_rows()) == []


def test_send_result_none_is_not_saved(temp_db):
    matcher = ea.chat_handler
    matcher.send_result = None
    event = FakeEvent(group_id=GROUP)
    with pytest.raises(ea.FinishedException):
        run(ea.send_and_save(FakeBot(), event, matcher, _formal_msg(), is_finish=True))
    assert run(_chat_rows()) == []


def test_save_failure_is_swallowed(temp_db, monkeypatch, logger):
    matcher = ea.chat_handler
    event = FakeEvent(group_id=GROUP)

    async def boom(*args, **kwargs):
        raise RuntimeError("save failed")

    monkeypatch.setattr(ea, "parse_message_content", boom)
    with pytest.raises(ea.FinishedException):
        run(ea.send_and_save(FakeBot(), event, matcher, _formal_msg(), is_finish=True))
    assert logger.contains("exception")


# --------------------------------------------------------------------------
# 超时与备用提示
# --------------------------------------------------------------------------
def test_timeout_uses_fallback_and_saves_fallback(temp_db, monkeypatch):
    monkeypatch.setattr(ea, "SEND_RETRY_TIMEOUT", 0.05)
    matcher = ea.chat_handler
    event = FakeEvent(group_id=GROUP)
    formal = _formal_msg("会被拦截")
    fallback = _formal_msg("消息被拦截，发送失败")

    async def send(msg):
        await asyncio.sleep(0)  # 让出事件循环，使 wait_for 超时能生效
        if msg is formal:
            raise RuntimeError("blocked")
        matcher.sent.append(msg)
        return {"message_id": "fallback-1"}

    monkeypatch.setattr(matcher, "send", send)
    with pytest.raises(ea.FinishedException):
        run(ea.send_and_save(FakeBot(), event, matcher, formal,
                             is_finish=True, fallback_msg=fallback))
    rows = run(_chat_rows())
    assert len(rows) == 1
    assert "消息被拦截，发送失败" in rows[0][0]


def test_timeout_without_fallback_does_not_finish(temp_db, monkeypatch):
    monkeypatch.setattr(ea, "SEND_RETRY_TIMEOUT", 0.05)
    matcher = ea.chat_handler
    event = FakeEvent(group_id=GROUP)

    async def send(msg):
        await asyncio.sleep(0)
        raise RuntimeError("always blocked")

    monkeypatch.setattr(matcher, "send", send)
    run(ea.send_and_save(FakeBot(), event, matcher, _formal_msg(), is_finish=False))
    assert matcher.finish_calls == 0
    assert run(_chat_rows()) == []


def test_fallback_also_times_out_is_swallowed(temp_db, monkeypatch, logger):
    monkeypatch.setattr(ea, "SEND_RETRY_TIMEOUT", 0.05)
    matcher = ea.chat_handler
    event = FakeEvent(group_id=GROUP)

    async def send(msg):
        await asyncio.sleep(0)
        raise RuntimeError("always blocked")

    monkeypatch.setattr(matcher, "send", send)
    with pytest.raises(ea.FinishedException):
        run(ea.send_and_save(FakeBot(), event, matcher, _formal_msg(),
                             is_finish=True, fallback_msg=_formal_msg("备用")))
    assert run(_chat_rows()) == []
    assert any("备用提示" in m for _, m in logger.records)


def test_save_uses_fallback_identity_when_login_info_fails(temp_db, monkeypatch):
    matcher = ea.chat_handler
    event = FakeEvent(group_id=GROUP)
    bot = FakeBot()

    async def boom():
        raise RuntimeError("no login info")

    monkeypatch.setattr(bot, "get_login_info", boom)
    with pytest.raises(ea.FinishedException):
        run(ea.send_and_save(bot, event, matcher, _formal_msg("正文"), is_finish=True))
    assert len(run(_chat_rows())) == 1
