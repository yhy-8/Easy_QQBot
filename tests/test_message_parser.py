"""OneBot 消息解析：存储分支、AI 富文本分支、引用展开、视觉附件登记。"""

from __future__ import annotations

import datetime

import pytest

import easy_ai as ea
from conftest import FakeBot, MessageSegment, run

GROUP = 10001


def parse(bot, message, *, for_ai=False):
    return run(ea.OneBotMessageParser(bot, GROUP).parse(message, for_ai=for_ai))


# --------------------------------------------------------------------------
# 字符串输入
# --------------------------------------------------------------------------
def test_string_input_storage_replaces_cq_codes():
    parsed = parse(FakeBot(), "hi[CQ:image,file=x.jpg]there")
    assert parsed.text == "hi[媒体/表情]there"
    assert parsed.visual_attachments == []


def test_string_input_ai_returns_empty():
    parsed = parse(FakeBot(), "hi[CQ:image,file=x.jpg]", for_ai=True)
    assert parsed.text == ""


def test_string_input_strips():
    assert parse(FakeBot(), "  hello  ").text == "hello"


# --------------------------------------------------------------------------
# 文本 / at
# --------------------------------------------------------------------------
def test_text_and_empty_segments_are_joined():
    message = [
        {"type": "text", "data": {"text": "a"}},
        {"type": "", "data": {}},
        MessageSegment.text("b"),
    ]
    assert parse(FakeBot(), message).text == "ab"


def test_at_bot_storage_keeps_id_ai_removes():
    bot = FakeBot(self_id="999")
    message = [MessageSegment.at("999"), MessageSegment.text("hi")]
    assert parse(bot, message).text == "[@999]hi"
    assert parse(bot, message, for_ai=True).text == "hi"


def test_at_other_uses_member_card():
    bot = FakeBot()
    bot.member_info[(GROUP, 777)] = {"nickname": "qqname", "card": "群名片"}
    message = [MessageSegment.at("777")]
    assert parse(bot, message).text == "[@777]"
    assert parse(bot, message, for_ai=True).text == "[@群名片（QQ昵称：qqname）]"


def test_at_other_without_card():
    bot = FakeBot()
    bot.member_info[(GROUP, 778)] = {"nickname": "onlynick", "card": ""}
    assert parse(bot, [MessageSegment.at("778")], for_ai=True).text == "[@onlynick]"


def test_at_all():
    message = [MessageSegment("at", {"qq": "all"})]
    assert parse(FakeBot(), message).text == "[@全体成员]"
    assert parse(FakeBot(), message, for_ai=True).text == "[@全体成员]"


def test_at_non_digit_id():
    message = [MessageSegment("at", {"qq": "abc"})]
    assert parse(FakeBot(), message, for_ai=True).text == "[@abc]"


def test_at_lookup_failure_falls_back_to_id():
    bot = FakeBot()
    bot.member_info[(GROUP, 779)] = RuntimeError("api down")
    assert parse(bot, [MessageSegment.at("779")], for_ai=True).text == "[@779]"


# --------------------------------------------------------------------------
# 图片
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "summary,expected",
    [("一只猫", "一只猫"), ("   ", "[图片]"), ("", "[图片]"), (None, "[图片]")],
)
def test_image_storage_placeholder(summary, expected):
    seg = {"type": "image", "data": {"summary": summary, "file": "f.jpg"}}
    assert parse(FakeBot(), [seg]).text == expected


def test_image_ai_registers_attachment_and_keeps_token():
    bot = FakeBot()
    seg = {"type": "image", "data": {"summary": "猫", "file": "f.jpg", "path": "/p/f.jpg"}}
    parsed = parse(bot, [seg], for_ai=True)
    assert parsed.text == ea._visual_placeholder_token(0)
    assert len(parsed.visual_attachments) == 1
    attachment = parsed.visual_attachments[0]
    assert attachment.placeholder == "猫"
    assert attachment.file_id == "f.jpg"
    assert attachment.local_path == "/p/f.jpg"


def test_image_multiple_tokens_are_sequenced():
    bot = FakeBot()
    message = [
        {"type": "image", "data": {"file": "a.jpg"}},
        MessageSegment.text("and"),
        {"type": "image", "data": {"file": "b.jpg"}},
    ]
    parsed = parse(bot, message, for_ai=True)
    assert parsed.text == (
        ea._visual_placeholder_token(0) + "and" + ea._visual_placeholder_token(1)
    )
    assert [a.file_id for a in parsed.visual_attachments] == ["a.jpg", "b.jpg"]


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"file": "f.jpg"}, "f.jpg"),
        ({"file_id": "fid"}, "fid"),
        ({"file": "marketface", "file_id": "fid2"}, "fid2"),
        ({"file": "MARKETFACE", "file_id": "fid3"}, "fid3"),
        ({"file": "marketface"}, "marketface"),
        ({}, ""),
    ],
)
def test_image_file_id_resolution(data, expected):
    assert ea.OneBotMessageParser._image_file_id(data) == expected


# --------------------------------------------------------------------------
# 表情 / 多媒体 / 卡片
# --------------------------------------------------------------------------
@pytest.mark.parametrize("seg_type", ["face", "mface", "bface"])
def test_face_summary(seg_type):
    seg = {"type": seg_type, "data": {"summary": "微笑"}}
    assert parse(FakeBot(), [seg]).text == "微笑"
    seg = {"type": seg_type, "data": {}}
    assert parse(FakeBot(), [seg]).text == "[表情包]"


@pytest.mark.parametrize(
    "seg_type,expected",
    [("record", "[语音]"), ("video", "[视频]"), ("forward", "[聊天记录]"),
     ("node", "[合并转发节点]"), ("json", "[分享了卡片/链接]"), ("xml", "[分享了卡片/链接]")],
)
def test_multimedia_placeholders(seg_type, expected):
    message = [{"type": seg_type, "data": {}}]
    assert parse(FakeBot(), message).text == expected
    assert parse(FakeBot(), message, for_ai=True).text == expected


def test_file_name_resolution():
    assert parse(FakeBot(), [{"type": "file", "data": {"name": "a.zip"}}]).text == "[文件: a.zip]"
    assert parse(FakeBot(), [{"type": "file", "data": {"file": "b.zip"}}]).text == "[文件: b.zip]"
    assert parse(FakeBot(), [{"type": "file", "data": {"id": "c"}}]).text == "[文件: c]"
    assert parse(FakeBot(), [{"type": "file", "data": {}}]).text == "[文件: 未知文件]"
    # 顶层 AI 分支用半角冒号，引用内分支用全角冒号（现有实现的不一致，测试固化现状）
    assert parse(FakeBot(), [{"type": "file", "data": {"name": "a.zip"}}], for_ai=True).text == "[文件: a.zip]"


def test_unknown_segment_type_is_preserved():
    message = [{"type": "custom", "data": {}}]
    assert parse(FakeBot(), message).text == "[custom]"
    assert parse(FakeBot(), message, for_ai=True).text == "[custom]"


def test_dict_and_object_segments_are_both_supported():
    class Seg:
        type = "text"
        data = {"text": "obj"}

    message = [Seg()]
    assert parse(FakeBot(), message).text == "obj"


# --------------------------------------------------------------------------
# 引用回复
# --------------------------------------------------------------------------
def _reply_msg(time=1_700_000_000, user_id=42, nickname="张三", card="", message=None):
    return {
        "time": time,
        "sender": {"user_id": user_id, "nickname": nickname, "card": card},
        "message": message if message is not None else [{"type": "text", "data": {"text": "原内容"}}],
    }


def test_reply_storage_uses_metadata_only():
    bot = FakeBot()
    bot.msg_by_id["m1"] = _reply_msg()
    message = [{"type": "reply", "data": {"id": "m1"}}]
    assert parse(bot, message).text == "[引用回复(时间：1700000000，发言人：42)]"


def test_reply_storage_failure_placeholder():
    bot = FakeBot()
    bot.msg_by_id["m1"] = RuntimeError("gone")
    assert parse(bot, [{"type": "reply", "data": {"id": "m1"}}]).text == "[引用回复(获取信息失败)]"


def test_reply_ai_expands_one_level():
    bot = FakeBot()
    bot.msg_by_id["m1"] = _reply_msg(card="群名片", message=[
        {"type": "text", "data": {"text": "原文"}},
        {"type": "image", "data": {"summary": "图", "file": "q.jpg"}},
        {"type": "reply", "data": {"id": "m0"}},
    ])
    parsed = parse(bot, [{"type": "reply", "data": {"id": "m1"}}], for_ai=True)
    expected_time = datetime.datetime.fromtimestamp(1_700_000_000).strftime("%m-%d %H:%M:%S")
    assert f"[引用回复（时间：{expected_time}，发言人：群名片（QQ昵称：张三），内容：" in parsed.text
    assert "原文" in parsed.text
    assert "[引用回复（未继续展开）]" in parsed.text
    # 引用内的图片也要登记为视觉附件
    assert len(parsed.visual_attachments) == 1
    assert parsed.visual_attachments[0].file_id == "q.jpg"
    # get_msg 只调用一次
    assert sum(1 for name, _ in bot.calls if name == "get_msg") == 1


def test_reply_ai_failure_placeholder():
    bot = FakeBot()
    bot.msg_by_id["m1"] = RuntimeError("gone")
    assert parse(bot, [{"type": "reply", "data": {"id": "m1"}}], for_ai=True).text == "[引用回复(获取信息失败)]"


def test_reply_ai_quoted_multimedia_and_unknown():
    bot = FakeBot()
    bot.msg_by_id["m1"] = _reply_msg(message=[
        {"type": "face", "data": {"summary": "笑"}},
        {"type": "video", "data": {}},
        {"type": "json", "data": {}},
        {"type": "weird", "data": {}},
        {"type": "at", "data": {"qq": "42"}},
    ])
    parsed = parse(bot, [{"type": "reply", "data": {"id": "m1"}}], for_ai=True)
    for fragment in ("笑", "[视频]", "[分享了卡片/链接]", "[weird]"):
        assert fragment in parsed.text


def test_quoted_file_uses_fullwidth_colon():
    bot = FakeBot()
    bot.msg_by_id["m1"] = _reply_msg(message=[{"type": "file", "data": {"name": "a.zip"}}])
    parsed = parse(bot, [{"type": "reply", "data": {"id": "m1"}}], for_ai=True)
    assert "[文件：a.zip]" in parsed.text


# --------------------------------------------------------------------------
# 兼容入口
# --------------------------------------------------------------------------
def test_parse_message_content_wrapper():
    bot = FakeBot()
    assert run(ea.parse_message_content(bot, GROUP, [MessageSegment.text("x")])) == "x"


def test_extract_ai_content_wrapper_returns_attachments():
    bot = FakeBot()
    message = [{"type": "image", "data": {"summary": "猫", "file": "f.jpg"}}]
    text, attachments = run(ea.extract_ai_content(bot, GROUP, message))
    assert text == ea._visual_placeholder_token(0)
    assert len(attachments) == 1


# --------------------------------------------------------------------------
# 补齐分支：引用内的 record/forward/node、空段、顶层 AI face
# --------------------------------------------------------------------------
def test_reply_ai_quoted_record_forward_node():
    bot = FakeBot()
    bot.msg_by_id["m1"] = _reply_msg(message=[
        {"type": "record", "data": {}},
        {"type": "forward", "data": {}},
        {"type": "node", "data": {}},
    ])
    parsed = parse(bot, [{"type": "reply", "data": {"id": "m1"}}], for_ai=True)
    assert "[语音]" in parsed.text
    assert "[聊天记录]" in parsed.text
    assert "[合并转发节点]" in parsed.text


def test_reply_ai_skips_empty_type_segments():
    bot = FakeBot()
    bot.msg_by_id["m1"] = _reply_msg(message=[
        {"type": "", "data": {}},
        {"type": "text", "data": {"text": "有效"}},
    ])
    parsed = parse(bot, [{"type": "reply", "data": {"id": "m1"}}], for_ai=True)
    assert "有效" in parsed.text


def test_top_level_ai_face_summary():
    assert parse(FakeBot(), [{"type": "face", "data": {"summary": "微笑"}}], for_ai=True).text == "微笑"
    assert parse(FakeBot(), [{"type": "face", "data": {}}], for_ai=True).text == "[表情包]"
