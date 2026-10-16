"""API 回包解析：正文提取、原生搜索统计、Anthropic/Gemini 回复解析。"""

from __future__ import annotations

import pytest

import easy_ai as ea


# --------------------------------------------------------------------------
# _get_openai_message / _get_gemini_content
# --------------------------------------------------------------------------
@pytest.mark.parametrize("data", [None, [], "x", 3, {}, {"choices": []}, {"choices": [None]}])
def test_get_openai_message_malformed(data):
    assert ea._get_openai_message(data) == {}


def test_get_openai_message_non_dict_message():
    assert ea._get_openai_message({"choices": [{"message": "bad"}]}) == {}


def test_get_openai_message_ok():
    assert ea._get_openai_message({"choices": [{"message": {"content": "hi"}}]}) == {"content": "hi"}


@pytest.mark.parametrize("data", [None, [], "x", {}, {"candidates": []}, {"candidates": [None]},
                                  {"candidates": [{"content": "bad"}]}])
def test_get_gemini_content_malformed(data):
    assert ea._get_gemini_content(data) == {}


def test_get_gemini_content_ok():
    payload = {"candidates": [{"content": {"parts": [{"text": "x"}]}}]}
    assert ea._get_gemini_content(payload) == {"parts": [{"text": "x"}]}


# --------------------------------------------------------------------------
# _extract_api_reply_text
# --------------------------------------------------------------------------
def test_extract_openai_ok():
    assert ea._extract_api_reply_text({"choices": [{"message": {"content": "  hello  "}}]}, "openai") == "hello"


@pytest.mark.parametrize("content", [None, 123, "", "   "])
def test_extract_openai_non_string(content):
    assert ea._extract_api_reply_text({"choices": [{"message": {"content": content}}]}, "openai") == ""


def test_extract_openai_malformed():
    assert ea._extract_api_reply_text("not-a-dict", "openai") == ""


def test_extract_anthropic_joins_text_blocks_and_skips_thinking():
    data = {
        "content": [
            {"type": "thinking", "thinking": "secret"},
            {"type": "text", "text": "answer "},
            {"type": "server_tool_use", "name": "web_search"},
            {"type": "text", "text": "done"},
            {"type": "tool_use", "name": "web_search"},
        ]
    }
    assert ea._extract_api_reply_text(data, "anthropic") == "answer done"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"content": "not-a-list"},
        {"content": [None, {"type": "text", "text": 5}]},
    ],
)
def test_extract_anthropic_malformed(data):
    assert ea._extract_api_reply_text(data, "anthropic") == ""


def test_extract_gemini_skips_thought_and_takes_last_text():
    data = {"candidates": [{"content": {"parts": [
        {"text": "first"},
        {"thought": True, "text": "chain of thought"},
        {"text": ""},
        {"text": "final"},
    ]}}]}
    assert ea._extract_api_reply_text(data, "gemini") == "final"


def test_extract_gemini_skips_blank_parts():
    # 逆序扫描时，strip 后为空的段应继续向前找，而不是提前返回空串
    data = {"candidates": [{"content": {"parts": [
        {"text": "正文"},
        {"text": "   "},
    ]}}]}
    assert ea._extract_api_reply_text(data, "gemini") == "正文"


@pytest.mark.parametrize("data", [{}, {"candidates": []}, {"candidates": [{"content": {"parts": "x"}}]},
                                  {"candidates": [{"content": {"parts": [{"text": 5}]}}]}])
def test_extract_gemini_malformed(data):
    assert ea._extract_api_reply_text(data, "gemini") == ""


def test_extract_unknown_api_type_returns_empty():
    assert ea._extract_api_reply_text({"whatever": 1}, "unknown") == ""


# --------------------------------------------------------------------------
# _parse_openai_native_search_response
# --------------------------------------------------------------------------
def test_native_search_without_adapter_hides_search():
    reply = ea._parse_openai_native_search_response({"choices": []}, "text", "")
    assert reply.text == "text"
    assert reply.search.prefix_value() is None


def test_native_search_tool_calls_count():
    data = {"choices": [{"message": {"content": "x", "tool_calls": [{}, {}]}}]}
    reply = ea._parse_openai_native_search_response(data, "x", "generic_search")
    assert reply.search.performed is True
    assert reply.search.count == 2


def test_native_search_server_side_tool_usage():
    data = {
        "usage": {
            "server_side_tool_usage": {
                "WEB_SEARCH": 2,
                "FILE_SEARCH": 1,
                "other": 9,
                "bad": "3",
            }
        }
    }
    reply = ea._parse_openai_native_search_response(data, "x", "generic_search")
    assert reply.search.count == 3  # 只统计键名含 SEARCH 的整数


def test_native_search_server_side_zero_falls_back_to_total():
    # server_side_tool_usage 存在但无 SEARCH 键（count==0）时不应提前返回，
    # 而应继续走 num_sources_used 兜底
    data = {"usage": {
        "server_side_tool_usage": {"OTHER": 5},
        "num_sources_used": 4,
    }}
    reply = ea._parse_openai_native_search_response(data, "x", "generic_search")
    assert reply.search.performed is True
    assert reply.search.count == 4


def test_native_search_num_sources_used_fallback():
    data = {"usage": {"num_server_side_tools_used": 4}}
    reply = ea._parse_openai_native_search_response(data, "x", "generic_search")
    assert reply.search.count == 4

    data = {"num_sources_used": 1}
    reply = ea._parse_openai_native_search_response(data, "x", "generic_search")
    assert reply.search.count == 1


def test_native_search_citations_and_sources():
    data = {"citations": ["a", "b"]}
    reply = ea._parse_openai_native_search_response(data, "x", "generic_search")
    assert reply.search.count == 2

    data = {"sources": ["a"]}
    reply = ea._parse_openai_native_search_response(data, "x", "generic_search")
    assert reply.search.count == 1


def test_native_search_no_evidence_is_hidden():
    reply = ea._parse_openai_native_search_response({"usage": {}}, "x", "generic_search")
    assert reply.search.performed is None
    assert reply.search.prefix_value() is None


def test_native_search_non_dict_data():
    reply = ea._parse_openai_native_search_response([], "x", "generic_search")
    assert reply.search.performed is None


# --------------------------------------------------------------------------
# parse_anthropic_reply
# --------------------------------------------------------------------------
def test_parse_anthropic_reply_with_search():
    data = {
        "content": [{"type": "text", "text": "answer"}],
        "usage": {"server_tool_use": {"web_search_requests": 5}},
    }
    reply = ea.ChatService.parse_anthropic_reply(data, True)
    assert reply.text == "answer"
    assert reply.search.count == 5
    assert reply.search.prefix_value() == "5"


def test_parse_anthropic_reply_search_not_used_is_hidden():
    data = {"content": [{"type": "text", "text": "answer"}], "usage": {}}
    reply = ea.ChatService.parse_anthropic_reply(data, True)
    assert reply.search.prefix_value() is None


def test_parse_anthropic_reply_zero_requests_is_hidden():
    data = {"content": [{"type": "text", "text": "a"}],
            "usage": {"server_tool_use": {"web_search_requests": 0}}}
    assert ea.ChatService.parse_anthropic_reply(data, True).search.prefix_value() is None


def test_parse_anthropic_reply_search_disabled():
    data = {"content": [{"type": "text", "text": "a"}],
            "usage": {"server_tool_use": {"web_search_requests": 9}}}
    assert ea.ChatService.parse_anthropic_reply(data, False).search.prefix_value() is None


# --------------------------------------------------------------------------
# parse_gemini_reply
# --------------------------------------------------------------------------
def test_parse_gemini_reply_web_search_queries():
    data = {"candidates": [{"content": {"parts": [{"text": "a"}]},
                            "groundingMetadata": {"webSearchQueries": ["q1", "q2"]}}]}
    reply = ea.ChatService.parse_gemini_reply(data, True)
    assert reply.text == "a"
    assert reply.search.count == 2


def test_parse_gemini_reply_grounding_chunks_fallback():
    data = {"candidates": [{"content": {"parts": [{"text": "a"}]},
                            "groundingMetadata": {"groundingChunks": [
                                {"web": {}}, {"web": {}}, {"other": 1},
                            ]}}]}
    reply = ea.ChatService.parse_gemini_reply(data, True)
    assert reply.search.count == 2
    assert reply.search.prefix_value() == "2"


def test_parse_gemini_reply_empty_grounding_chunks_hides_search():
    data = {"candidates": [{"content": {"parts": [{"text": "a"}]},
                            "groundingMetadata": {"groundingChunks": [{"other": 1}]}}]}
    assert ea.ChatService.parse_gemini_reply(data, True).search.prefix_value() is None


def test_parse_gemini_reply_no_grounding_hides_search():
    data = {"candidates": [{"content": {"parts": [{"text": "a"}]}}]}
    assert ea.ChatService.parse_gemini_reply(data, True).search.prefix_value() is None


def test_parse_gemini_reply_search_disabled():
    data = {"candidates": [{"content": {"parts": [{"text": "a"}]},
                            "groundingMetadata": {"webSearchQueries": ["q"]}}]}
    assert ea.ChatService.parse_gemini_reply(data, False).search.prefix_value() is None


def test_native_search_non_dict_container_is_skipped():
    reply = ea._parse_openai_native_search_response({"usage": "bad"}, "x", "generic_search")
    assert reply.search.performed is None
