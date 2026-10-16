"""ChatService 编排：complete 端到端、视觉占位、send_model_request 分流。"""

from __future__ import annotations

import aiosqlite
import pytest

import easy_ai as ea
from conftest import FakeBot, FakeEvent, FakeResponse, run

GROUP = 10001
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"payload"


def _base_config(**overrides):
    config = {
        "api_key": "K", "api_url": "https://host", "name": "m",
        "api_type": "openai", "model_id": "mid", "vision": False, "search": False,
    }
    config.update(overrides)
    return config


@pytest.fixture(autouse=True)
def _no_ai_decision(monkeypatch):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", False)


def _install_models(monkeypatch, *configs):
    models = {"default": configs[0]}
    for i, cfg in enumerate(configs[1:], start=1):
        models[f"model{i}"] = cfg
    monkeypatch.setattr(ea, "MODELS_CONFIG", models)
    monkeypatch.setattr(ea, "DYNAMIC_HISTORY_MODEL", "default")


def _selection(monkeypatch, config):
    _install_models(monkeypatch, config)
    return ea.ChatService.select_model("")


def _seed_history(temp_db):
    run(ea.insert_message_to_db("h1", GROUP, 100, "u1", "qq", "grp", "历史一"))
    run(ea.insert_message_to_db("h2", GROUP, 200, "u1", "qq", "grp", "历史二"))


async def _rows():
    async with aiosqlite.connect(ea.DB_PATH) as db:
        async with db.execute(f'SELECT content FROM "group_{GROUP}"') as cursor:
            return await cursor.fetchall()


# --------------------------------------------------------------------------
# 视觉占位替换
# --------------------------------------------------------------------------
def test_apply_visual_placeholders():
    visuals = [
        ea.ResolvedVisual(placeholder="猫"),
        ea.ResolvedVisual(placeholder="[图片（未发送：无法识别图片格式）]"),
    ]
    text = (f"前{ea._visual_placeholder_token(0)}中{ea._visual_placeholder_token(1)}后")
    result = ea.ChatService.apply_visual_placeholders(text, visuals)
    assert result == "前猫中[图片（未发送：无法识别图片格式）]后"


def test_apply_visual_placeholders_only_replaces_first():
    visuals = [ea.ResolvedVisual(placeholder="X")]
    text = ea._visual_placeholder_token(0) + ea._visual_placeholder_token(0)
    assert ea.ChatService.apply_visual_placeholders(text, visuals) == (
        "X" + ea._visual_placeholder_token(0))


def test_resolve_visuals_preserves_order(monkeypatch, tmp_path):
    files = []
    for i in range(3):
        path = tmp_path / f"{i}.png"
        path.write_bytes(PNG_BYTES)
        files.append(ea.VisualAttachment(f"[{i}]", local_path=str(path)))
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    resolved = run(ea.ChatService.resolve_visuals(FakeBot(), files))
    assert [v.placeholder for v in resolved] == ["[0]", "[1]", "[2]"]
    assert all(v.sendable for v in resolved)


# --------------------------------------------------------------------------
# complete 端到端
# --------------------------------------------------------------------------
def test_complete_openai(temp_db, fake_session, monkeypatch):
    _seed_history(temp_db)
    selection = _selection(monkeypatch, _base_config())
    session = fake_session([FakeResponse(200, {"choices": [{"message": {"content": "模型回答"}}]})])

    completion = run(ea.chat_service.complete(
        FakeBot(), FakeEvent(group_id=GROUP, message_id="cur"), selection,
        "当前问题", [], "qq", "grp"))

    assert completion.reply.text == "模型回答"
    assert completion.history_count == 2
    assert completion.image_count == 0
    request = session.requests[0]
    assert request["json"]["messages"][0]["role"] == "system"
    assert "当前问题" in request["json"]["messages"][1]["content"]


def test_complete_empty_reply_falls_back(temp_db, fake_session, monkeypatch):
    _seed_history(temp_db)
    selection = _selection(monkeypatch, _base_config())
    fake_session([FakeResponse(200, {"choices": [{"message": {"content": ""}}]})])
    completion = run(ea.chat_service.complete(
        FakeBot(), FakeEvent(group_id=GROUP, message_id="cur"), selection,
        "问题", [], "qq", "grp"))
    assert completion.reply.text == "（模型API拒绝回复）"


def test_complete_with_vision_image(temp_db, fake_session, monkeypatch, tmp_path):
    _seed_history(temp_db)
    selection = _selection(monkeypatch, _base_config(vision=True))
    png = tmp_path / "a.png"
    png.write_bytes(PNG_BYTES)
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    attachment = ea.VisualAttachment("[图片]", local_path=str(png))
    session = fake_session([FakeResponse(200, {"choices": [{"message": {"content": "看图"}}]})])

    completion = run(ea.chat_service.complete(
        FakeBot(), FakeEvent(group_id=GROUP, message_id="cur"), selection,
        ea._visual_placeholder_token(0), [attachment], "qq", "grp"))

    assert completion.image_count == 1
    content = session.requests[0]["json"]["messages"][1]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    # 占位符已替换为可读标签，内部标记不进入提示词
    assert "easy_ai_visual" not in content[0]["text"]
    assert "[图片]" in content[0]["text"]


def test_complete_unsupported_api_type(temp_db, monkeypatch):
    _seed_history(temp_db)
    selection = _selection(monkeypatch, _base_config(api_type="mystery"))
    with pytest.raises(ea.UnsupportedAPITypeError):
        run(ea.chat_service.complete(
            FakeBot(), FakeEvent(group_id=GROUP, message_id="cur"), selection,
            "问题", [], "qq", "grp"))


def test_complete_anthropic_native_search(temp_db, fake_session, monkeypatch):
    _seed_history(temp_db)
    selection = _selection(monkeypatch, _base_config(
        api_type="anthropic", api_url="https://host/anthropic", search=True))
    payload = {
        "content": [{"type": "text", "text": "anthropic 回答"}],
        "usage": {"server_tool_use": {"web_search_requests": 3}},
    }
    session = fake_session([FakeResponse(200, payload)])

    completion = run(ea.chat_service.complete(
        FakeBot(), FakeEvent(group_id=GROUP, message_id="cur"), selection,
        "问题", [], "qq", "grp"))

    assert completion.reply.text == "anthropic 回答"
    assert completion.reply.search.count == 3
    request = session.requests[0]
    assert request["url"] == "https://host/anthropic/v1/messages"
    assert request["json"]["system"]
    assert request["json"]["tools"] == [ea.ANTHROPIC_WEB_SEARCH_TOOL]


def test_complete_gemini_native_search(temp_db, fake_session, monkeypatch):
    _seed_history(temp_db)
    selection = _selection(monkeypatch, _base_config(
        api_type="gemini", api_url="https://host/v1beta/models", search=True))
    payload = {"candidates": [{"content": {"parts": [{"text": "gemini 回答"}]},
                               "groundingMetadata": {"webSearchQueries": ["q1", "q2"]}}]}
    session = fake_session([FakeResponse(200, payload)])

    completion = run(ea.chat_service.complete(
        FakeBot(), FakeEvent(group_id=GROUP, message_id="cur"), selection,
        "问题", [], "qq", "grp"))

    assert completion.reply.text == "gemini 回答"
    assert completion.reply.search.count == 2
    assert session.requests[0]["json"]["tools"] == [{"googleSearch": {}}]


def test_complete_third_search_flow(temp_db, fake_session, monkeypatch):
    _seed_history(temp_db)
    monkeypatch.setattr(ea, "ENABLE_THIRD_SEARCH", True)
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "bocha")
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 2)
    selection = _selection(monkeypatch, _base_config(search=False))

    async def fake_bocha(args):
        return "SEARCH RESULT", 2

    monkeypatch.setattr(ea, "_bocha_from_tool_input", fake_bocha)
    fake_session([
        FakeResponse(200, {"choices": [{"message": {"tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "web_search", "arguments": '{"query": "q"}'}}]}}]}),
        FakeResponse(200, {"choices": [{"message": {"content": "整合答案"}}]}),
    ])

    completion = run(ea.chat_service.complete(
        FakeBot(), FakeEvent(group_id=GROUP, message_id="cur"), selection,
        "问题", [], "qq", "grp"))

    assert completion.reply.text == "整合答案"
    assert completion.reply.search.performed is True
    assert completion.reply.search.count == 2


# --------------------------------------------------------------------------
# send_model_request 首轮错误
# --------------------------------------------------------------------------
def test_send_model_request_non_200_raises(temp_db, fake_session, monkeypatch):
    selection = _selection(monkeypatch, _base_config())
    request = ea.ChatService.build_model_request(selection, "SYS", "USER", [])
    session = fake_session([FakeResponse(500, text="upstream down")])
    with pytest.raises(ea.ModelHTTPError, match="upstream down"):
        run(ea.ChatService.send_model_request(session, selection, request))


def test_send_model_request_unknown_type_raises(temp_db, fake_session, monkeypatch):
    selection = _selection(monkeypatch, _base_config())
    request = ea.PreparedModelRequest(
        api_type="mystery", api_url="u", headers={}, payload={},
        system_prompt="s")
    session = fake_session([FakeResponse(200, {"ok": True})])
    with pytest.raises(ea.UnsupportedAPITypeError):
        run(ea.ChatService.send_model_request(session, selection, request))


# --------------------------------------------------------------------------
# send_model_request 其余分派：OpenAI 原生搜索、Anthropic/Gemini 第三方搜索
# --------------------------------------------------------------------------
def test_complete_openai_native_search(temp_db, fake_session, monkeypatch):
    _seed_history(temp_db)
    selection = _selection(monkeypatch, _base_config(search=True, model_id="glm-4"))
    payload = {
        "choices": [{"message": {"content": "原生搜索回答"}}],
        "usage": {"server_side_tool_usage": {"WEB_SEARCH": 2}},
    }
    fake_session([FakeResponse(200, payload)])
    completion = run(ea.chat_service.complete(
        FakeBot(), FakeEvent(group_id=GROUP, message_id="cur"), selection,
        "问题", [], "qq", "grp"))
    assert completion.reply.text == "原生搜索回答"
    assert completion.reply.search.count == 2


def test_complete_anthropic_third_search(temp_db, fake_session, monkeypatch):
    _seed_history(temp_db)
    monkeypatch.setattr(ea, "ENABLE_THIRD_SEARCH", True)
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "bocha")
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 2)
    selection = _selection(monkeypatch, _base_config(api_type="anthropic", search=False))

    async def fake_bocha(args):
        return "SEARCH RESULT", 2

    monkeypatch.setattr(ea, "_bocha_from_tool_input", fake_bocha)
    fake_session([
        FakeResponse(200, {"content": [
            {"type": "tool_use", "id": "t1", "name": "web_search", "input": {"query": "q"}}]}),
        FakeResponse(200, {"content": [{"type": "text", "text": "整合答案"}]}),
    ])
    completion = run(ea.chat_service.complete(
        FakeBot(), FakeEvent(group_id=GROUP, message_id="cur"), selection,
        "问题", [], "qq", "grp"))
    assert completion.reply.text == "整合答案"
    assert completion.reply.search.count == 2


def test_complete_gemini_third_search(temp_db, fake_session, monkeypatch):
    _seed_history(temp_db)
    monkeypatch.setattr(ea, "ENABLE_THIRD_SEARCH", True)
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "bocha")
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 2)
    selection = _selection(monkeypatch, _base_config(api_type="gemini", search=False))

    async def fake_bocha(args):
        return "SEARCH RESULT", 2

    monkeypatch.setattr(ea, "_bocha_from_tool_input", fake_bocha)
    fake_session([
        FakeResponse(200, {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "web_search", "args": {"query": "q"}, "id": "c1"}}]}}]}),
        FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "整合答案"}]}}]}),
    ])
    completion = run(ea.chat_service.complete(
        FakeBot(), FakeEvent(group_id=GROUP, message_id="cur"), selection,
        "问题", [], "qq", "grp"))
    assert completion.reply.text == "整合答案"
    assert completion.reply.search.count == 2
