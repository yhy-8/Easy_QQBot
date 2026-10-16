"""第三方搜索：freshness 校验、博查调用、工具参数适配。"""

from __future__ import annotations

import asyncio

import pytest

import easy_ai as ea
from conftest import FakeResponse, run


# --------------------------------------------------------------------------
# _is_valid_search_freshness
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    ["noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear",
     "2026-01-01", "2026-12-31", "2026-01-01..2026-12-31", "2028-02-29"],
)
def test_freshness_valid(value):
    assert ea._is_valid_search_freshness(value) is True


@pytest.mark.parametrize(
    "value",
    ["", "yesterday", "2026-13-01", "2026-02-30", "2026-02-29", "2026-1-1",
     "2026-12-31..2026-01-01", 5, None, True, ["noLimit"]],
)
def test_freshness_invalid(value):
    assert ea._is_valid_search_freshness(value) is False


# --------------------------------------------------------------------------
# bocha_search
# --------------------------------------------------------------------------
def _payload(results):
    return {"code": 200, "data": {"webPages": {"value": results}}}


def _bocha(fake_session, responses, **kwargs):
    session = fake_session(responses)
    result = run(ea.bocha_search(kwargs.pop("query", "q"), **kwargs))
    return result, session


def test_bocha_no_key(monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "")
    assert run(ea.bocha_search("q")) == ("", 0, False)


def test_bocha_empty_query(monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    assert run(ea.bocha_search("")) == ("", 0, False)


def test_bocha_invalid_freshness(monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    assert run(ea.bocha_search("q", freshness="bogus")) == ("", 0, False)


def test_bocha_formats_results(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    results = [
        {"name": "标题A", "url": "https://a", "snippet": "摘要A", "summary": "概要A",
         "siteName": "站点A", "datePublished": "2026-01-01"},
        {"name": "标题B", "url": "https://b", "snippet": "摘要B", "summary": "摘要B"},
    ]
    (text, count, ok), session = _bocha(fake_session, [FakeResponse(200, _payload(results))])
    assert ok is True
    assert count == 2
    assert text.startswith("[网络搜索结果]")
    assert "1. 标题：标题A" in text
    assert "   链接：https://a" in text
    assert "   来源：站点A" in text
    assert "   时间：2026-01-01" in text
    assert "   摘要：摘要A" in text
    assert "   全文概要：概要A" in text
    # summary == snippet 时不重复输出全文概要
    assert text.count("摘要B") == 1


def test_bocha_no_results_is_success(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    (text, count, ok), _ = _bocha(fake_session, [FakeResponse(200, _payload([]))])
    assert (text, count, ok) == ("", 0, True)


def test_bocha_non_200(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    (text, count, ok), _ = _bocha(fake_session, [FakeResponse(500, text="boom")])
    assert (text, count, ok) == ("", 0, False)


def test_bocha_bad_json(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    resp = FakeResponse(200, raise_json=ValueError("bad json"), text="not json")
    (text, count, ok), _ = _bocha(fake_session, [resp])
    assert (text, count, ok) == ("", 0, False)


@pytest.mark.parametrize("code", [400, "400", 500, None])
def test_bocha_api_failure_code(fake_session, monkeypatch, code):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    resp = FakeResponse(200, {"code": code, "msg": "bad", "log_id": "x"})
    (text, count, ok), _ = _bocha(fake_session, [resp])
    assert (text, count, ok) == ("", 0, False)


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 200},                                   # 缺 data
        {"code": 200, "data": "x"},                      # data 非对象
        {"code": 200, "data": {}},                       # 缺 webPages
        {"code": 200, "data": {"webPages": {"value": "x"}}},  # value 非列表
    ],
)
def test_bocha_malformed_payload(fake_session, monkeypatch, payload):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    (text, count, ok), _ = _bocha(fake_session, [FakeResponse(200, payload)])
    assert (text, count, ok) == ("", 0, False)


def test_bocha_missing_value_is_no_result(fake_session, monkeypatch):
    # webPages 存在但缺 value 时，按“无结果”处理（成功且 0 条）
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    (text, count, ok), _ = _bocha(fake_session, [FakeResponse(200, {"code": 200, "data": {"webPages": {}}})])
    assert (text, count, ok) == ("", 0, True)


def test_bocha_skips_invalid_items(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    results = [
        "not-a-dict",
        {"name": "", "url": "", "snippet": "", "summary": ""},  # 全空
        {"name": "有效", "url": "https://ok"},
    ]
    (text, count, ok), _ = _bocha(fake_session, [FakeResponse(200, _payload(results))])
    assert ok is True
    assert count == 1
    assert "有效" in text


def test_bocha_all_items_invalid_is_success_with_zero(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    (text, count, ok), _ = _bocha(fake_session, [FakeResponse(200, _payload(["bad"]))])
    assert (text, count, ok) == ("", 0, True)


def test_bocha_item_without_title_or_url_only_snippet(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    results = [{"snippet": "只有摘要"}]
    (text, count, ok), _ = _bocha(fake_session, [FakeResponse(200, _payload(results))])
    assert count == 1
    assert "1. 搜索结果" in text
    assert "   摘要：只有摘要" in text


def test_bocha_count_clamped(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    _, session = _bocha(fake_session, [FakeResponse(200, _payload([]))], count=999)
    assert session.requests[0]["json"]["count"] == 50

    _, session = _bocha(fake_session, [FakeResponse(200, _payload([]))], count=0)
    assert session.requests[0]["json"]["count"] == 1

    _, session = _bocha(fake_session, [FakeResponse(200, _payload([]))], count="25")
    assert session.requests[0]["json"]["count"] == 25


def test_bocha_summary_non_bool_defaults_true(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    _, session = _bocha(fake_session, [FakeResponse(200, _payload([]))], summary="yes")
    assert session.requests[0]["json"]["summary"] is True


def test_bocha_include_exclude_only_when_set(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    _, session = _bocha(fake_session, [FakeResponse(200, _payload([]))])
    body = session.requests[0]["json"]
    assert "include" not in body and "exclude" not in body

    _, session = _bocha(fake_session, [FakeResponse(200, _payload([]))],
                        include="qq.com", exclude="weibo.com")
    body = session.requests[0]["json"]
    assert body["include"] == "qq.com"
    assert body["exclude"] == "weibo.com"


def test_bocha_timeout(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    (text, count, ok), _ = _bocha(fake_session, [asyncio.TimeoutError()])
    assert (text, count, ok) == ("", 0, False)


def test_bocha_generic_exception(fake_session, monkeypatch):
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", "k")
    (text, count, ok), _ = _bocha(fake_session, [RuntimeError("network")])
    assert (text, count, ok) == ("", 0, False)


# --------------------------------------------------------------------------
# _bocha_from_tool_input
# --------------------------------------------------------------------------
def test_tool_input_valid_passthrough(monkeypatch):
    async def fake_bocha(*args, **kwargs):
        assert kwargs["count"] == 10
        return ("RESULT", 3, True)

    monkeypatch.setattr(ea, "bocha_search", fake_bocha)
    text, count = run(ea._bocha_from_tool_input({"query": " q ", "count": 10}))
    assert (text, count) == ("RESULT", 3)


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": 5},
        {"query": "q", "include": 5},
        {"query": "q", "exclude": 5},
    ],
)
def test_tool_input_bad_types(args):
    text, count = run(ea._bocha_from_tool_input(args))
    assert count == 0
    assert "参数格式错误" in text


def test_tool_input_bad_freshness():
    text, count = run(ea._bocha_from_tool_input({"query": "q", "freshness": "nope"}))
    assert count == 0
    assert "freshness 参数无效" in text


def test_tool_input_search_no_result(monkeypatch):
    async def fake_bocha(*args, **kwargs):
        return ("", 0, True)

    monkeypatch.setattr(ea, "bocha_search", fake_bocha)
    assert run(ea._bocha_from_tool_input({"query": "q"})) == ("搜索无结果", 0)


def test_tool_input_search_failed(monkeypatch):
    async def fake_bocha(*args, **kwargs):
        return ("", 0, False)

    monkeypatch.setattr(ea, "bocha_search", fake_bocha)
    assert run(ea._bocha_from_tool_input({"query": "q"})) == ("搜索失败", 0)


@pytest.mark.parametrize(
    "value",
    ["2026-02-30..2026-03-01", "2026-01-01..2026-02-30", "9999-99-99..9999-99-99"],
)
def test_freshness_range_with_invalid_date(value):
    assert ea._is_valid_search_freshness(value) is False
