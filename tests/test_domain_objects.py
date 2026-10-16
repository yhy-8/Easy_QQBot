"""领域对象：SearchResult / ResolvedVisual / ModelSelection / 异常。"""

from __future__ import annotations

import pytest

import easy_ai as ea


# --------------------------------------------------------------------------
# SearchResult.prefix_value
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "performed,count,expected",
    [
        (True, 3, "3"),
        (True, 1, "1"),
        (True, 0, "False"),
        (False, 0, None),
        (False, 5, "5"),           # 只要 count>0 就显示数字（文档规则 1）
        (None, None, None),
        (None, 2, "2"),
        (True, None, None),
        (True, "3", None),         # 非 int 的数字不显示
        (True, True, None),        # bool 不算合法计数
    ],
)
def test_search_result_prefix_value(performed, count, expected):
    assert ea.SearchResult(performed=performed, count=count).prefix_value() == expected


def test_search_result_default_is_hidden():
    assert ea.SearchResult().prefix_value() is None


# --------------------------------------------------------------------------
# ResolvedVisual.sendable
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mime,data,expected",
    [
        ("image/png", "AAA", True),
        (None, "AAA", False),
        ("image/png", None, False),
        (None, None, False),
        ("", "", False),
    ],
)
def test_resolved_visual_sendable(mime, data, expected):
    assert ea.ResolvedVisual(placeholder="[图片]", mime_type=mime, base64_data=data).sendable is expected


# --------------------------------------------------------------------------
# ModelSelection.api_url / 能力开关
# --------------------------------------------------------------------------
def _selection(api_type="openai", search=False, vision=False, **overrides):
    config = {
        "api_key": "k",
        "api_url": "https://host/base",
        "name": "m",
        "api_type": api_type,
        "model_id": "mid",
        "vision": vision,
        "search": search,
    }
    config.update(overrides)
    return ea.ModelSelection(mode="serious", prefix_to_remove="", config=config)


def test_api_url_per_protocol():
    assert _selection("openai").api_url == "https://host/base"
    assert _selection("anthropic").api_url == "https://host/base/v1/messages"
    assert _selection("gemini").api_url == "https://host/base/mid:generateContent"


def test_api_type_defaults_to_openai():
    sel = _selection()
    sel.config.pop("api_type")
    assert sel.api_type == "openai"


def test_information_label():
    import dataclasses

    assert _selection().information == "m，SER"
    sel = dataclasses.replace(_selection(), mode="casual")
    assert sel.information == "m，CAS"


@pytest.mark.parametrize(
    "search,enabled,key,rounds,expected",
    [
        (False, True, "key", 3, True),
        (True, True, "key", 3, False),     # 原生搜索优先
        (False, False, "key", 3, False),   # 总开关关闭
        (False, True, "", 3, False),       # 无 Key
        (False, True, "key", 0, False),    # 轮数为 0
        (False, True, "key", -1, False),   # 轮数为负
    ],
)
def test_use_third_search_conditions(monkeypatch, search, enabled, key, rounds, expected):
    monkeypatch.setattr(ea, "ENABLE_THIRD_SEARCH", enabled)
    monkeypatch.setattr(ea, "THIRD_SEARCH_API_KEY", key)
    monkeypatch.setattr(ea, "MAX_SEARCH_ROUNDS", rounds)
    assert _selection(search=search).use_third_search is expected


def test_vision_and_search_enabled_read_flags():
    assert _selection(vision=True, search=True).vision_enabled is True
    assert _selection(vision=True, search=True).search_enabled is True
    assert _selection(vision=False, search=False).vision_enabled is False
    assert _selection(vision=False, search=False).search_enabled is False


# --------------------------------------------------------------------------
# 异常类型
# --------------------------------------------------------------------------
def test_exception_types():
    assert issubclass(ea.UnsupportedAPITypeError, ValueError)
    assert issubclass(ea.ModelHTTPError, RuntimeError)
