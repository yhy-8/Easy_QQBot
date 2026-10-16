"""配置校验与 HTTP 会话生命周期。"""

from __future__ import annotations

import aiohttp

import easy_ai as ea
from conftest import run


# --------------------------------------------------------------------------
# validate_configuration
# --------------------------------------------------------------------------
def _errors(logger):
    return [m for level, m in logger.records if level == "error"]


def _warnings(logger):
    return [m for level, m in logger.records if level == "warning"]


def test_valid_configuration_is_quiet(logger):
    run(ea.validate_configuration())
    assert _errors(logger) == []
    assert _warnings(logger) == []


def test_missing_default_model(logger, monkeypatch):
    monkeypatch.setattr(ea, "MODELS_CONFIG", {})
    run(ea.validate_configuration())
    assert any("缺少 default 模型" in m for m in _errors(logger))
    assert any("DYNAMIC_HISTORY_MODEL" in m for m in _warnings(logger))


def test_invalid_default_mode(logger, monkeypatch):
    monkeypatch.setattr(ea, "DEFAULT_MODE", "weird")
    run(ea.validate_configuration())
    assert any("DEFAULT_MODE" in m for m in _errors(logger))


def test_dynamic_history_model_missing(logger, monkeypatch):
    monkeypatch.setattr(ea, "DYNAMIC_HISTORY_MODEL", "not-there")
    run(ea.validate_configuration())
    assert any("DYNAMIC_HISTORY_MODEL" in m for m in _warnings(logger))


def test_missing_model_fields(logger, monkeypatch):
    config = {"default": {
        "api_key": "k", "api_url": "u", "name": "n",
        "api_type": "openai", "model_id": "m",
    }}
    monkeypatch.setattr(ea, "MODELS_CONFIG", config)
    run(ea.validate_configuration())
    warnings = _warnings(logger)
    assert any("缺少配置字段" in m and "search" in m and "vision" in m for m in warnings)


def test_unknown_api_type(logger, monkeypatch):
    config = {"default": {
        "api_key": "k", "api_url": "u", "name": "n",
        "api_type": "mystery", "model_id": "m", "vision": False, "search": False,
    }}
    monkeypatch.setattr(ea, "MODELS_CONFIG", config)
    run(ea.validate_configuration())
    assert any("未知 api_type" in m for m in _warnings(logger))


# --------------------------------------------------------------------------
# HTTP 会话
# --------------------------------------------------------------------------
def test_lazy_session_is_reused_and_closed():
    async def scenario():
        session = await ea.get_http_session()
        assert isinstance(session, aiohttp.ClientSession)
        assert await ea.get_http_session() is session
        await ea.close_http_session()
        assert ea._http_session is None

    run(scenario())


def test_init_and_shutdown_lifecycle():
    async def scenario():
        await ea.init_http_session()
        assert ea._http_session is not None
        await ea.init_http_session()  # 已存在时不重建
        assert not ea._http_session.closed
        await ea.close_http_session()
        assert ea._http_session is None
        await ea.close_http_session()  # 幂等

    run(scenario())


def test_get_http_session_recreates_after_close():
    async def scenario():
        first = await ea.get_http_session()
        await ea.close_http_session()
        second = await ea.get_http_session()
        assert second is not first
        assert not second.closed
        await ea.close_http_session()

    run(scenario())
