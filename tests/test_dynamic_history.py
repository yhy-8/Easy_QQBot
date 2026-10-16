"""动态历史条数：固定算法、AI 决策三种协议、异常回退。"""

from __future__ import annotations

import datetime

import aiosqlite
import pytest

import easy_ai as ea
from conftest import FakeResponse, run

GROUP = 10001
NOW = int(datetime.datetime.now().timestamp())


def seed(rows):
    """rows: [(message_id, timestamp)]，直接写入聊天表。"""

    async def _seed():
        async with aiosqlite.connect(ea.DB_PATH) as db:
            await db.executemany(
                f'INSERT OR IGNORE INTO "group_{GROUP}" '
                '(message_id, timestamp, user_id, content) VALUES (?, ?, ?, ?)',
                [(mid, ts, "u", "c") for mid, ts in rows],
            )
            await db.commit()

    run(_seed())


def _models(monkeypatch, api_type):
    config = {
        "api_key": "K", "api_url": "https://host", "name": "n",
        "api_type": api_type, "model_id": "mid", "vision": False, "search": False,
    }
    monkeypatch.setattr(ea, "MODELS_CONFIG", {"default": config, "dyn": config})
    monkeypatch.setattr(ea, "DYNAMIC_HISTORY_MODEL", "dyn")
    return config


# --------------------------------------------------------------------------
# 空窗口与数据库异常
# --------------------------------------------------------------------------
def test_no_messages_returns_default(temp_db):
    assert run(ea.get_dynamic_history_length(GROUP)) == 80


def test_db_query_failure_returns_default(monkeypatch, temp_db):
    real_connect = aiosqlite.connect

    def broken_connect(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(aiosqlite, "connect", broken_connect)
    assert run(ea.get_dynamic_history_length(GROUP)) == 80
    monkeypatch.setattr(aiosqlite, "connect", real_connect)


# --------------------------------------------------------------------------
# 固定算法分支
# --------------------------------------------------------------------------
def test_fixed_algorithm_low_activity_hits_min(monkeypatch, temp_db):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", False)
    seed([(f"m{i}", NOW - 60) for i in range(10)])
    assert run(ea.get_dynamic_history_length(GROUP)) == 50


def test_fixed_algorithm_high_activity_hits_max(monkeypatch, temp_db):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", False)
    seed([(f"m{i}", NOW - 60) for i in range(400)])
    assert run(ea.get_dynamic_history_length(GROUP)) == 300


def test_fixed_algorithm_mixes_one_to_two_hours(monkeypatch, temp_db):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", False)
    seed([(f"a{i}", NOW - 60) for i in range(100)])
    seed([(f"b{i}", NOW - 5000) for i in range(200)])
    # 100 + int(200 * 0.5..1.0) ∈ [200, 300]
    result = run(ea.get_dynamic_history_length(GROUP))
    assert 200 <= result <= 300


def test_fixed_algorithm_ignores_messages_older_than_2h(monkeypatch, temp_db):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", False)
    seed([(f"old{i}", NOW - 8000) for i in range(300)])
    assert run(ea.get_dynamic_history_length(GROUP)) == 80  # 窗口内无消息


# --------------------------------------------------------------------------
# AI 决策分支
# --------------------------------------------------------------------------
@pytest.mark.parametrize("api_type", ["openai", "anthropic", "gemini"])
def test_ai_decision_clamps_and_parses(monkeypatch, fake_session, temp_db, api_type):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", True)
    _models(monkeypatch, api_type)
    seed([(f"m{i}", NOW - 60) for i in range(5)])

    if api_type == "openai":
        payload = {"choices": [{"message": {"content": "  123  "}}]}
    elif api_type == "anthropic":
        payload = {"content": [{"type": "text", "text": "123"}]}
    else:
        payload = {"candidates": [{"content": {"parts": [{"text": "123"}]}}]}

    fake_session([FakeResponse(200, payload)])
    assert run(ea.get_dynamic_history_length(GROUP, current_message="hi")) == 123


def test_ai_decision_clamps_above_max(monkeypatch, fake_session, temp_db):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", True)
    _models(monkeypatch, "openai")
    seed([("m1", NOW - 60)])
    fake_session([FakeResponse(200, {"choices": [{"message": {"content": "9999"}}]})])
    assert run(ea.get_dynamic_history_length(GROUP)) == 300


def test_ai_decision_clamps_below_min(monkeypatch, fake_session, temp_db):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", True)
    _models(monkeypatch, "openai")
    seed([("m1", NOW - 60)])
    fake_session([FakeResponse(200, {"choices": [{"message": {"content": "3"}}]})])
    assert run(ea.get_dynamic_history_length(GROUP)) == 50


def test_ai_decision_no_number_returns_default(monkeypatch, fake_session, temp_db):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", True)
    _models(monkeypatch, "openai")
    seed([("m1", NOW - 60)])
    fake_session([FakeResponse(200, {"choices": [{"message": {"content": "很多"}}]})])
    assert run(ea.get_dynamic_history_length(GROUP)) == 80


def test_ai_decision_non_200_returns_default(monkeypatch, fake_session, temp_db):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", True)
    _models(monkeypatch, "openai")
    seed([("m1", NOW - 60)])
    fake_session([FakeResponse(500, text="err")])
    assert run(ea.get_dynamic_history_length(GROUP)) == 80


def test_ai_decision_exception_returns_default(monkeypatch, fake_session, temp_db, logger):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", True)
    _models(monkeypatch, "openai")
    seed([("m1", NOW - 60)])
    fake_session([RuntimeError("network")])
    assert run(ea.get_dynamic_history_length(GROUP)) == 80
    assert logger.contains("exception")


def test_ai_decision_unknown_protocol_returns_default(monkeypatch, temp_db, logger):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", True)
    _models(monkeypatch, "mystery")
    seed([("m1", NOW - 60)])
    assert run(ea.get_dynamic_history_length(GROUP)) == 80
    assert logger.contains("error")


# --------------------------------------------------------------------------
# 各协议请求体裁
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "api_type,expected_url,checks",
    [
        ("openai", "https://host", lambda p: "messages" in p),
        ("anthropic", "https://host/v1/messages",
         lambda p: p["max_tokens"] == 256 and p["thinking"] == {"type": "disabled"}),
        ("gemini", "https://host/mid:generateContent",
         lambda p: "contents" in p),
    ],
)
def test_ai_decision_request_shape(monkeypatch, fake_session, temp_db,
                                   api_type, expected_url, checks):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", True)
    _models(monkeypatch, api_type)
    seed([("m1", NOW - 60)])
    session = fake_session([FakeResponse(200, {"choices": [{"message": {"content": "80"}}]})])

    run(ea.get_dynamic_history_length(GROUP, current_message="hi"))
    request = session.requests[0]
    assert request["url"] == expected_url
    assert checks(request["json"])


def test_ai_decision_anthropic_headers(monkeypatch, fake_session, temp_db):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", True)
    _models(monkeypatch, "anthropic")
    seed([("m1", NOW - 60)])
    session = fake_session([FakeResponse(200, {"content": [{"type": "text", "text": "80"}]})])
    run(ea.get_dynamic_history_length(GROUP))
    headers = session.requests[0]["headers"]
    assert headers["x-api-key"] == "K"
    assert headers["anthropic-version"] == ea.ANTHROPIC_VERSION


def test_ai_decision_prompt_contains_current_message(monkeypatch, fake_session, temp_db):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", True)
    _models(monkeypatch, "openai")
    seed([("m1", NOW - 60)])
    session = fake_session([FakeResponse(200, {"choices": [{"message": {"content": "80"}}]})])
    run(ea.get_dynamic_history_length(GROUP, current_message="独特的问题"))
    prompt = session.requests[0]["json"]["messages"][0]["content"]
    assert "独特的问题" in prompt
    assert "最近10分钟" in prompt


def test_ai_decision_time_buckets(monkeypatch, fake_session, temp_db):
    monkeypatch.setattr(ea, "ENABLE_AI_HISTORY_DECISION", True)
    _models(monkeypatch, "openai")
    timestamps = [
        ("b0", NOW - 300),      # 最近10分钟
        ("b1", NOW - 1200),     # 10-30分钟前
        ("b2", NOW - 2400),     # 30-60分钟前
        ("b3", NOW - 5000),     # 1-2小时前
        ("b4", NOW - 10000),    # 2-4小时前
        ("b5", NOW - 18000),    # 4-6小时前
    ]
    seed(timestamps)
    session = fake_session([FakeResponse(200, {"choices": [{"message": {"content": "80"}}]})])
    run(ea.get_dynamic_history_length(GROUP, current_message="hi"))
    prompt = session.requests[0]["json"]["messages"][0]["content"]
    assert "最近10分钟: 1条" in prompt
    assert "10-30分钟前: 1条" in prompt
    assert "30-60分钟前: 1条" in prompt
    assert "1-2小时前: 1条" in prompt
    assert "2-4小时前: 1条" in prompt
    assert "4-6小时前: 1条" in prompt
