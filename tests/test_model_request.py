"""请求组装：build_model_request 与 _enable_openai_native_search。"""

from __future__ import annotations

import pytest

import easy_ai as ea


def _selection(api_type="openai", search=False, vision=False, **overrides):
    config = {
        "api_key": "KEY",
        "api_url": "https://host/base",
        "name": "model-name",
        "api_type": api_type,
        "model_id": "mid",
        "vision": vision,
        "search": search,
    }
    config.update(overrides)
    return ea.ModelSelection(mode="casual", prefix_to_remove="", config=config)


def _visual(mime="image/png", data="QUJD"):
    return ea.ResolvedVisual(placeholder="[图片]", mime_type=mime, base64_data=data)


def _enable_third(monkeypatch):
    monkeypatch.setattr(ea, "ENABLE_THIRD_SEARCH", True)
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "bocha-key")
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 3)


# --------------------------------------------------------------------------
# _enable_openai_native_search
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "model_id,adapter,key",
    [
        ("glm-4-plus", "glm_web_search", "tools"),
        ("GLM-4", "glm_web_search", "tools"),
        ("moonshot-v1", "moonshot_web_search", "tools"),
        ("kimi-k2", "generic_search", "web_search"),
    ],
)
def test_enable_openai_native_search(model_id, adapter, key):
    payload = {}
    result = ea._enable_openai_native_search(payload, {"model_id": model_id})
    assert result == adapter
    assert key in payload


def test_enable_openai_native_search_generic_sets_both_flags():
    payload = {}
    ea._enable_openai_native_search(payload, {"model_id": "unknown"})
    assert payload["web_search"] is True
    assert payload["network"] is True


# --------------------------------------------------------------------------
# OpenAI
# --------------------------------------------------------------------------
def test_openai_plain_text():
    request = ea.ChatService.build_model_request(_selection(), "SYS", "USER", [])
    assert request.api_url == "https://host/base"
    assert request.headers["Authorization"] == "Bearer KEY"
    assert request.payload["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert "tools" not in request.payload
    assert request.native_search_adapter == ""
    assert request.use_third_search is False


def test_openai_vision_builds_image_url_blocks():
    request = ea.ChatService.build_model_request(
        _selection(vision=True), "SYS", "USER", [_visual()])
    content = request.payload["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "USER"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_openai_vision_ignored_when_model_lacks_support():
    request = ea.ChatService.build_model_request(
        _selection(vision=False), "SYS", "USER", [_visual()])
    assert request.payload["messages"][1]["content"] == "USER"


def test_openai_native_search_registers_adapter():
    request = ea.ChatService.build_model_request(
        _selection(search=True, model_id="glm-4"), "SYS", "USER", [])
    assert request.native_search_adapter == "glm_web_search"
    assert "tools" in request.payload


def test_openai_third_search_tool(monkeypatch):
    _enable_third(monkeypatch)
    request = ea.ChatService.build_model_request(
        _selection(search=False), "SYS", "USER", [])
    assert request.use_third_search is True
    assert request.payload["tools"] == [ea.THIRD_SEARCH_TOOL]


def test_openai_native_takes_priority_over_third(monkeypatch):
    _enable_third(monkeypatch)
    request = ea.ChatService.build_model_request(
        _selection(search=True), "SYS", "USER", [])
    assert request.use_third_search is False
    assert request.native_search_adapter == "generic_search"


def test_openai_no_key_disables_third_search(monkeypatch):
    monkeypatch.setattr(ea, "ENABLE_THIRD_SEARCH", True)
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "")
    request = ea.ChatService.build_model_request(
        _selection(search=False), "SYS", "USER", [])
    assert request.use_third_search is False
    assert "tools" not in request.payload


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------
def test_anthropic_plain_request():
    request = ea.ChatService.build_model_request(
        _selection("anthropic"), "SYS", "USER", [])
    assert request.api_url == "https://host/base/v1/messages"
    assert request.headers["x-api-key"] == "KEY"
    assert request.headers["anthropic-version"] == ea.ANTHROPIC_VERSION
    assert request.payload["system"] == "SYS"
    assert request.payload["max_tokens"] == ea.ANTHROPIC_MAX_TOKENS
    assert request.payload["messages"][0]["content"] == [{"type": "text", "text": "USER"}]
    assert "thinking" not in request.payload
    assert "tools" not in request.payload


def test_anthropic_native_search_tool():
    request = ea.ChatService.build_model_request(
        _selection("anthropic", search=True), "SYS", "USER", [])
    assert request.payload["tools"] == [ea.ANTHROPIC_WEB_SEARCH_TOOL]


def test_anthropic_third_search_tool(monkeypatch):
    _enable_third(monkeypatch)
    request = ea.ChatService.build_model_request(
        _selection("anthropic", search=False), "SYS", "USER", [])
    assert request.use_third_search is True
    assert request.payload["tools"] == [ea.THIRD_SEARCH_TOOL_ANTHROPIC]


def test_anthropic_vision_blocks():
    request = ea.ChatService.build_model_request(
        _selection("anthropic", vision=True), "SYS", "USER", [_visual("image/webp", "WFla")])
    blocks = request.payload["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "USER"}
    assert blocks[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/webp", "data": "WFla"},
    }


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------
def test_gemini_plain_request():
    request = ea.ChatService.build_model_request(
        _selection("gemini"), "SYS", "USER", [])
    assert request.api_url == "https://host/base/mid:generateContent"
    assert request.headers["x-goog-api-key"] == "KEY"
    assert request.payload["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert request.payload["contents"][0]["parts"] == [{"text": "USER"}]
    assert "tools" not in request.payload


def test_gemini_native_search_tool():
    request = ea.ChatService.build_model_request(
        _selection("gemini", search=True), "SYS", "USER", [])
    assert request.payload["tools"] == [{"googleSearch": {}}]


def test_gemini_third_search_tool(monkeypatch):
    _enable_third(monkeypatch)
    request = ea.ChatService.build_model_request(
        _selection("gemini", search=False), "SYS", "USER", [])
    assert request.use_third_search is True
    assert request.payload["tools"] == [{"functionDeclarations": [ea.THIRD_SEARCH_TOOL_GEMINI]}]


def test_gemini_vision_inline_data():
    request = ea.ChatService.build_model_request(
        _selection("gemini", vision=True), "SYS", "USER", [_visual()])
    parts = request.payload["contents"][0]["parts"]
    assert parts[0] == {"text": "USER"}
    assert parts[1] == {"inlineData": {"mimeType": "image/png", "data": "QUJD"}}


# --------------------------------------------------------------------------
# 未知协议
# --------------------------------------------------------------------------
def test_unknown_api_type_raises():
    with pytest.raises(ea.UnsupportedAPITypeError):
        ea.ChatService.build_model_request(_selection("mystery"), "SYS", "USER", [])
