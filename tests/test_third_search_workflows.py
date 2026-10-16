"""第三方搜索工作流：OpenAI / Anthropic / Gemini 三条分支。

用 FakeSession 按顺序喂回包，并替换 ``_bocha_from_tool_input``，
覆盖：无工具调用、单轮、多轮、参数/结构错误、轮次耗尽强制回答、上游非 200。
"""

from __future__ import annotations

import pytest

import easy_ai as ea
from conftest import FakeResponse, run


@pytest.fixture
def bocha(monkeypatch):
    """替换博查调用，记录参数并固定返回 2 条结果。"""
    calls = []

    async def fake(args):
        calls.append(args)
        return "SEARCH RESULT", 2

    monkeypatch.setattr(ea, "_bocha_from_tool_input", fake)
    return calls


def _openai_initial(tool_calls):
    return {"choices": [{"message": {"tool_calls": tool_calls}}]}


def _openai_text(text):
    return {"choices": [{"message": {"content": text}}]}


def _call(call_id="c1", name="web_search", arguments='{"query": "q"}'):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


def _anthropic_initial(blocks):
    return {"content": blocks}


def _anthropic_text(text):
    return {"content": [{"type": "text", "text": text}]}


def _gemini_initial(parts):
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


def _gemini_text(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


# ==========================================================================
# OpenAI
# ==========================================================================
def test_openai_no_tool_calls(fake_session, bocha):
    session = fake_session([])
    reply = run(ea._run_openai_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER", _openai_text("直接回答")))
    assert reply.text == "直接回答"
    assert reply.search.performed is False
    assert reply.search.count == 0
    assert session.requests == []
    assert bocha == []


def test_openai_tool_calls_none_is_treated_as_no_call(fake_session, bocha):
    session = fake_session([])
    reply = run(ea._run_openai_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER",
        {"choices": [{"message": {"content": "x", "tool_calls": None}}]}))
    assert reply.text == "x"
    assert session.requests == []


def test_openai_single_round(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 2)
    session = fake_session([_openai_text("最终答案")])
    reply = run(ea._run_openai_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER", _openai_initial([_call()])))
    assert reply.text == "最终答案"
    assert reply.search.performed is True
    assert reply.search.count == 2

    payload = session.requests[0]["json"]
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert payload["messages"][2]["tool_calls"][0]["id"] == "c1"
    assert payload["messages"][3]["content"] == "SEARCH RESULT"
    assert bocha == [{"query": "q"}]


def test_openai_preserves_content_and_reasoning(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    initial = {"choices": [{"message": {
        "content": "思考中", "reasoning_content": "推理", "tool_calls": [_call()]}}]}
    session = fake_session([_openai_text("done")])
    run(ea._run_openai_third_search_workflow(session, "u", {}, "mid", "SYS", "USER", initial))
    assistant = session.requests[0]["json"]["messages"][2]
    assert assistant["content"] == "思考中"
    assert assistant["reasoning_content"] == "推理"


def test_openai_multi_batch_count(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 3)
    session = fake_session([
        _openai_initial([_call("c2")]),   # round 0 -> 再搜一次
        _openai_text("done"),             # round 1 -> 结束
    ])
    reply = run(ea._run_openai_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER", _openai_initial([_call("c1")])))
    assert reply.search.count == 4  # 两批各 2 条
    # 倒数第二轮仍带工具；最后调用强制文本
    assert len(session.requests) == 2


def test_openai_last_round_removes_tools_and_appends_suffix(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    session = fake_session([_openai_text("done")])
    run(ea._run_openai_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER", _openai_initial([_call()])))
    payload = session.requests[0]["json"]
    assert "tools" not in payload
    assert payload["messages"][0]["content"] == "SYS" + ea.SEARCH_EXHAUSTED_SUFFIX


def test_openai_last_round_still_tool_call(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    session = fake_session([_openai_initial([_call("c9")])])
    reply = run(ea._run_openai_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER", _openai_initial([_call()])))
    assert reply.text == ""
    assert reply.search.performed is True


def test_openai_unsupported_tool_name(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    session = fake_session([_openai_text("done")])
    run(ea._run_openai_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER", _openai_initial([_call(name="other")])))
    assert "不支持的工具" in session.requests[0]["json"]["messages"][3]["content"]
    assert bocha == []


def test_openai_invalid_arguments_json(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    session = fake_session([_openai_text("done")])
    run(ea._run_openai_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER",
        _openai_initial([_call(arguments="{not json")])))
    assert "参数格式错误" in session.requests[0]["json"]["messages"][3]["content"]


def test_openai_arguments_not_object(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    session = fake_session([_openai_text("done")])
    run(ea._run_openai_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER", _openai_initial([_call(arguments="[1,2]")])))
    assert "参数格式错误" in session.requests[0]["json"]["messages"][3]["content"]


@pytest.mark.parametrize(
    "tool_call",
    [
        "not-a-dict",
        {"type": "function", "function": {"name": "web_search", "arguments": "{}"}},  # 缺 id
        {"id": "", "type": "function", "function": {"name": "web_search", "arguments": "{}"}},
        {"id": "c", "type": "other", "function": {"name": "web_search", "arguments": "{}"}},
        {"id": "c", "type": "function"},                                     # 缺 function
        {"id": "c", "type": "function", "function": {"arguments": "{}"}},    # 缺函数名
        {"id": "c", "type": "function", "function": {"name": "web_search", "arguments": {}}},
    ],
)
def test_openai_malformed_tool_call_raises(monkeypatch, fake_session, bocha, tool_call):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    session = fake_session([_openai_text("done")])
    with pytest.raises(ValueError):
        run(ea._run_openai_third_search_workflow(
            session, "u", {}, "mid", "SYS", "USER", _openai_initial([tool_call])))
    assert bocha == []
    assert session.requests == []


def test_openai_mixed_batch_does_not_search(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    session = fake_session([_openai_text("done")])
    batch = [_call("ok"), {"type": "function", "function": {"name": "web_search", "arguments": "{}"}}]
    with pytest.raises(ValueError):
        run(ea._run_openai_third_search_workflow(
            session, "u", {}, "mid", "SYS", "USER", _openai_initial(batch)))
    assert bocha == []


def test_openai_followup_non_200_raises(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    session = fake_session([FakeResponse(500, text="upstream boom")])
    with pytest.raises(Exception, match="upstream boom"):
        run(ea._run_openai_third_search_workflow(
            session, "u", {}, "mid", "SYS", "USER", _openai_initial([_call()])))


# ==========================================================================
# Anthropic
# ==========================================================================
def test_anthropic_no_tool_use(fake_session, bocha):
    session = fake_session([])
    reply = run(ea._run_anthropic_third_search_workflow(
        session, "u", {}, "mid", "SYS", [{"type": "text", "text": "USER"}],
        _anthropic_text("直接回答")))
    assert reply.text == "直接回答"
    assert reply.search.performed is False
    assert session.requests == []


def test_anthropic_single_round_preserves_blocks(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 2)
    blocks = [
        {"type": "thinking", "thinking": "t", "signature": "sig"},
        {"type": "tool_use", "id": "tu1", "name": "web_search", "input": {"query": "q"}},
    ]
    session = fake_session([_anthropic_text("最终")])
    reply = run(ea._run_anthropic_third_search_workflow(
        session, "u", {}, "mid", "SYS", [{"type": "text", "text": "USER"}],
        _anthropic_initial(blocks)))
    assert reply.text == "最终"
    assert reply.search.count == 2
    payload = session.requests[0]["json"]
    assert payload["system"] == "SYS"
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant", "user"]
    assert payload["messages"][1]["content"] == blocks  # 原样回填
    tool_result = payload["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "tu1"
    assert tool_result["content"] == "SEARCH RESULT"


def test_anthropic_unsupported_tool(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    blocks = [{"type": "tool_use", "id": "tu1", "name": "other", "input": {}}]
    session = fake_session([_anthropic_text("done")])
    run(ea._run_anthropic_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER", _anthropic_initial(blocks)))
    assert "不支持的工具" in session.requests[0]["json"]["messages"][2]["content"][0]["content"]
    assert bocha == []


def test_anthropic_input_not_dict(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    blocks = [{"type": "tool_use", "id": "tu1", "name": "web_search", "input": "bad"}]
    session = fake_session([_anthropic_text("done")])
    run(ea._run_anthropic_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER", _anthropic_initial(blocks)))
    assert "参数格式错误" in session.requests[0]["json"]["messages"][2]["content"][0]["content"]


def test_anthropic_last_round_suffix_and_no_tools(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    blocks = [{"type": "tool_use", "id": "t", "name": "web_search", "input": {"query": "q"}}]
    session = fake_session([_anthropic_text("done")])
    run(ea._run_anthropic_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER", _anthropic_initial(blocks)))
    payload = session.requests[0]["json"]
    assert payload["system"] == "SYS" + ea.SEARCH_EXHAUSTED_SUFFIX
    assert "tools" not in payload


def test_anthropic_multi_round_count(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 3)
    session = fake_session([
        _anthropic_initial([{"type": "tool_use", "id": "t2", "name": "web_search", "input": {"query": "q2"}}]),
        _anthropic_text("done"),
    ])
    reply = run(ea._run_anthropic_third_search_workflow(
        session, "u", {}, "mid", "SYS", "USER",
        _anthropic_initial([{"type": "tool_use", "id": "t1", "name": "web_search", "input": {"query": "q1"}}])))
    assert reply.search.count == 4


def test_anthropic_followup_non_200_raises(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    session = fake_session([FakeResponse(500, text="anthro boom")])
    blocks = [{"type": "tool_use", "id": "t", "name": "web_search", "input": {"query": "q"}}]
    with pytest.raises(Exception, match="anthro boom"):
        run(ea._run_anthropic_third_search_workflow(
            session, "u", {}, "mid", "SYS", "USER", _anthropic_initial(blocks)))


# ==========================================================================
# Gemini
# ==========================================================================
def test_gemini_no_function_call(fake_session, bocha):
    session = fake_session([])
    reply = run(ea._run_gemini_third_search_workflow(
        session, "u", {}, "SYS", [{"text": "USER"}], _gemini_text("直接回答")))
    assert reply.text == "直接回答"
    assert reply.search.performed is False
    assert session.requests == []


def test_gemini_single_round_preserves_parts_and_id(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 2)
    parts = [
        {"thought": True, "text": "hmm", "thoughtSignature": "SIG"},
        {"functionCall": {"name": "web_search", "args": {"query": "q"}, "id": "call_1"}},
    ]
    session = fake_session([_gemini_text("最终")])
    reply = run(ea._run_gemini_third_search_workflow(
        session, "u", {}, "SYS", [{"text": "USER"}], _gemini_initial(parts)))
    assert reply.text == "最终"
    assert reply.search.count == 2
    contents = session.requests[0]["json"]["contents"]
    assert contents[0]["role"] == "user"
    assert contents[1]["parts"] == parts  # 原样回填，保留 thoughtSignature
    fn = contents[2]["parts"][0]["functionResponse"]
    assert fn["name"] == "web_search"
    assert fn["id"] == "call_1"
    assert fn["response"] == {"result": "SEARCH RESULT"}


def test_gemini_unsupported_tool(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    parts = [{"functionCall": {"name": "other", "args": {}}}]
    session = fake_session([_gemini_text("done")])
    run(ea._run_gemini_third_search_workflow(
        session, "u", {}, "SYS", "USER", _gemini_initial(parts)))
    fn = session.requests[0]["json"]["contents"][2]["parts"][0]["functionResponse"]
    assert "不支持的工具" in fn["response"]["error"]
    assert bocha == []


def test_gemini_args_not_dict(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    parts = [{"functionCall": {"name": "web_search", "args": "bad"}}]
    session = fake_session([_gemini_text("done")])
    run(ea._run_gemini_third_search_workflow(
        session, "u", {}, "SYS", "USER", _gemini_initial(parts)))
    fn = session.requests[0]["json"]["contents"][2]["parts"][0]["functionResponse"]
    assert "参数格式错误" in fn["response"]["error"]


def test_gemini_missing_call_id_omits_id(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    parts = [{"functionCall": {"name": "web_search", "args": {"query": "q"}}}]
    session = fake_session([_gemini_text("done")])
    run(ea._run_gemini_third_search_workflow(
        session, "u", {}, "SYS", "USER", _gemini_initial(parts)))
    fn = session.requests[0]["json"]["contents"][2]["parts"][0]["functionResponse"]
    assert "id" not in fn


def test_gemini_last_round_suffix_and_no_tools(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    parts = [{"functionCall": {"name": "web_search", "args": {"query": "q"}}}]
    session = fake_session([_gemini_text("done")])
    run(ea._run_gemini_third_search_workflow(
        session, "u", {}, "SYS", "USER", _gemini_initial(parts)))
    payload = session.requests[0]["json"]
    assert payload["systemInstruction"]["parts"][0]["text"] == "SYS" + ea.SEARCH_EXHAUSTED_SUFFIX
    assert "tools" not in payload


def test_gemini_multi_round_count(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 3)
    session = fake_session([
        _gemini_initial([{"functionCall": {"name": "web_search", "args": {"query": "q2"}, "id": "c2"}}]),
        _gemini_text("done"),
    ])
    reply = run(ea._run_gemini_third_search_workflow(
        session, "u", {}, "SYS", "USER",
        _gemini_initial([{"functionCall": {"name": "web_search", "args": {"query": "q1"}, "id": "c1"}}])))
    assert reply.search.count == 4
    assert len(session.requests) == 2


def test_gemini_followup_non_200_raises(monkeypatch, fake_session, bocha):
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", 1)
    session = fake_session([FakeResponse(500, text="gemini boom")])
    parts = [{"functionCall": {"name": "web_search", "args": {"query": "q"}}}]
    with pytest.raises(Exception, match="gemini boom"):
        run(ea._run_gemini_third_search_workflow(
            session, "u", {}, "SYS", "USER", _gemini_initial(parts)))


def test_has_gemini_function_call_helper():
    assert ea._has_gemini_function_call({"parts": [{"functionCall": {}}]}) is True
    assert ea._has_gemini_function_call({"parts": [{"text": "x"}]}) is False
    assert ea._has_gemini_function_call({"parts": "bad"}) is False
    assert ea._has_gemini_function_call("bad") is False


# ==========================================================================
# 辅助函数的直接分支（工作流之外的早退）
# ==========================================================================
def test_execute_web_search_non_dict_assistant_raises():
    with pytest.raises(ValueError):
        run(ea._execute_web_search([], "not-a-dict"))


def test_execute_web_search_tool_calls_not_list_raises():
    with pytest.raises(ValueError):
        run(ea._execute_web_search([], {"tool_calls": "x"}))


def test_execute_anthropic_web_search_no_tool_use(bocha):
    messages = []
    assert run(ea._execute_anthropic_web_search(messages, [{"type": "text", "text": "x"}])) == 0
    assert messages == []


def test_execute_gemini_web_search_no_function_call(bocha):
    contents = []
    assert run(ea._execute_gemini_web_search(contents, {"parts": [{"text": "x"}]})) == 0
    assert contents == []


def test_execute_gemini_web_search_bad_parts(bocha):
    assert run(ea._execute_gemini_web_search([], {"parts": "bad"})) == 0
    assert run(ea._execute_gemini_web_search([], "bad")) == 0
