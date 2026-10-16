"""数据库：初始化、写入不变量、历史查询与格式化。"""

from __future__ import annotations

import datetime

import aiosqlite
import pytest

import easy_ai as ea
from conftest import run

GROUP = 10001


async def _fetchall(sql, params=()):
    async with aiosqlite.connect(ea.DB_PATH) as db:
        async with db.execute(sql, params) as cursor:
            return await cursor.fetchall()


def _rowid(message_id):
    rows = run(_fetchall(f'SELECT rowid FROM "group_{GROUP}" WHERE message_id = ?', (str(message_id),)))
    return rows[0][0] if rows else None


# --------------------------------------------------------------------------
# init_db
# --------------------------------------------------------------------------
def test_init_db_creates_tables_and_index(temp_db):
    tables = {r[0] for r in run(_fetchall("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert {"user_info", "user_group_info", f"group_{GROUP}"} <= tables
    indexes = {r[0] for r in run(_fetchall("SELECT name FROM sqlite_master WHERE type='index'"))}
    assert f"idx_group_{GROUP}_timestamp" in indexes


def test_init_db_is_idempotent(monkeypatch, temp_db):
    run(ea.insert_message_to_db("m1", GROUP, 100, "u1", "qq", "grp", "hello"))
    run(ea.init_db())
    assert len(run(_fetchall(f'SELECT * FROM "group_{GROUP}"'))) == 1


# --------------------------------------------------------------------------
# insert_message_to_db
# --------------------------------------------------------------------------
def test_insert_writes_chat_and_user_tables(temp_db):
    run(ea.insert_message_to_db("m1", GROUP, 100, "u1", "qqnick", "groupcard", "hello"))
    chat = run(_fetchall(f'SELECT message_id, timestamp, user_id, content FROM "group_{GROUP}"'))
    assert chat == [("m1", 100, "u1", "hello")]
    user = run(_fetchall('SELECT user_id, qq_nickname, last_global_speak_time FROM "user_info"'))
    assert user == [("u1", "qqnick", 100)]
    group_user = run(_fetchall(
        'SELECT user_id, group_id, group_nickname, last_group_speak_time FROM "user_group_info"'))
    assert group_user == [("u1", GROUP, "groupcard", 100)]


def test_insert_dedupes_by_message_id_but_updates_user(temp_db):
    run(ea.insert_message_to_db("m1", GROUP, 100, "u1", "old", "oldcard", "first"))
    run(ea.insert_message_to_db("m1", GROUP, 200, "u1", "new", "newcard", "second"))
    chat = run(_fetchall(f'SELECT content FROM "group_{GROUP}"'))
    assert chat == [("first",)]
    user = run(_fetchall('SELECT qq_nickname, last_global_speak_time FROM "user_info"'))
    assert user == [("new", 200)]


@pytest.mark.parametrize("content", ["", None])
def test_insert_skips_empty_content(temp_db, content):
    run(ea.insert_message_to_db("m1", GROUP, 100, "u1", "qq", "grp", content))
    assert run(_fetchall(f'SELECT * FROM "group_{GROUP}"')) == []
    assert run(_fetchall('SELECT * FROM "user_info"')) == []


def test_insert_skips_non_whitelisted_group(temp_db):
    run(ea.insert_message_to_db("m1", 99999, 100, "u1", "qq", "grp", "hello"))
    assert run(_fetchall(f'SELECT * FROM "group_{GROUP}"')) == []


def test_insert_swallows_db_errors(monkeypatch, temp_db, logger):
    real_connect = aiosqlite.connect

    def broken_connect(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(aiosqlite, "connect", broken_connect)
    run(ea.insert_message_to_db("m1", GROUP, 100, "u1", "qq", "grp", "hello"))
    assert logger.contains("exception")
    monkeypatch.setattr(aiosqlite, "connect", real_connect)


# --------------------------------------------------------------------------
# _load_history_rows
# --------------------------------------------------------------------------
def _seed_chain(temp_db, count=5):
    for i in range(1, count + 1):
        run(ea.insert_message_to_db(
            f"m{i}", GROUP, 100 + i, "u1", "qqnick", "groupcard", f"c{i}"))


def test_load_history_excludes_current_and_bounds_by_rowid(temp_db):
    _seed_chain(temp_db)
    rows = run(ea._load_history_rows(GROUP, "m4", 10))
    assert [row[3] for row in rows] == ["c1", "c2", "c3"]
    # 升序：rowid 递增
    assert [row[4] for row in rows] == sorted(row[4] for row in rows)


def test_load_history_respects_limit(temp_db):
    _seed_chain(temp_db)
    rows = run(ea._load_history_rows(GROUP, "m5", 2))
    assert [row[3] for row in rows] == ["c3", "c4"]


def test_load_history_unknown_message_has_no_upper_bound(temp_db):
    _seed_chain(temp_db)
    rows = run(ea._load_history_rows(GROUP, "not-in-db", 10))
    assert [row[3] for row in rows] == ["c1", "c2", "c3", "c4", "c5"]


def test_load_history_joins_display_names(temp_db):
    _seed_chain(temp_db)
    rows = run(ea._load_history_rows(GROUP, "m3", 10))
    assert rows[0][1] == "qqnick"
    assert rows[0][2] == "groupcard"


def test_load_history_db_error_returns_empty(monkeypatch, temp_db, logger):
    real_connect = aiosqlite.connect
    monkeypatch.setattr(aiosqlite, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert run(ea._load_history_rows(GROUP, "m1", 5)) == []
    assert logger.contains("exception")
    monkeypatch.setattr(aiosqlite, "connect", real_connect)


# --------------------------------------------------------------------------
# _load_rows_after
# --------------------------------------------------------------------------
def test_load_rows_after_incremental_and_bounded(temp_db):
    _seed_chain(temp_db)
    watermark = _rowid("m2")
    rows = run(ea._load_rows_after(GROUP, "m5", watermark))
    assert [row[3] for row in rows] == ["c3", "c4"]  # m5 自身排除


def test_load_rows_after_empty_when_watermark_is_latest(temp_db):
    _seed_chain(temp_db)
    rows = run(ea._load_rows_after(GROUP, "m5", _rowid("m4")))
    assert rows == []


def test_load_rows_after_db_error_returns_empty(monkeypatch, temp_db, logger):
    real_connect = aiosqlite.connect
    monkeypatch.setattr(aiosqlite, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert run(ea._load_rows_after(GROUP, "m1", 0)) == []
    assert logger.contains("exception")
    monkeypatch.setattr(aiosqlite, "connect", real_connect)


# --------------------------------------------------------------------------
# _load_user_display_map
# --------------------------------------------------------------------------
def test_user_display_map_formats_card_and_nickname(temp_db):
    run(ea.insert_message_to_db("m1", GROUP, 100, "u1", "qqnick", "groupcard", "x"))
    run(ea.insert_message_to_db("m2", GROUP, 100, "u2", "samenick", "", "y"))
    mapping = run(ea._load_user_display_map(GROUP))
    assert mapping["u1"] == "groupcard（QQ昵称：qqnick）"
    assert mapping["u2"] == "samenick"


def test_user_display_map_db_error_returns_empty(monkeypatch, temp_db):
    real_connect = aiosqlite.connect
    monkeypatch.setattr(aiosqlite, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert run(ea._load_user_display_map(GROUP)) == {}
    monkeypatch.setattr(aiosqlite, "connect", real_connect)


# --------------------------------------------------------------------------
# _format_history_text
# --------------------------------------------------------------------------
def _row(ts, qq, group, content, rowid):
    return (ts, qq, group, content, rowid)


def test_format_history_text_basic():
    rows = [_row(1_700_000_000, "qqnick", "groupcard", "hello", 1)]
    text = ea._format_history_text(rows, {})
    expected_time = datetime.datetime.fromtimestamp(1_700_000_000).strftime("%m-%d %H:%M")
    assert text == f"[{expected_time}] groupcard（QQ昵称：qqnick）: hello"


def test_format_history_text_same_name():
    rows = [_row(1_700_000_000, "same", "same", "hi", 1)]
    assert ea._format_history_text(rows, {}).endswith("same: hi")


def test_format_history_text_converts_reply_and_at():
    rows = [_row(1_700_000_000, "a", "a", "[引用回复(时间：1700000000，发言人：42)] [@42]", 1)]
    mapping = {"42": "张三"}
    text = ea._format_history_text(rows, mapping)
    assert "[引用回复(时间：" in text
    assert "发言人：张三" in text
    assert "[@张三]" in text


# --------------------------------------------------------------------------
# _format_reply_prefix
# --------------------------------------------------------------------------
def test_reply_prefix_plain():
    assert ea._format_reply_prefix("m", 3, 0, ea.SearchResult()) == "模型：m，记录：3\n"


def test_reply_prefix_with_image():
    assert ea._format_reply_prefix("m", 3, 2, ea.SearchResult()) == "模型：m，记录：3，图片：2\n"


def test_reply_prefix_with_search_count():
    prefix = ea._format_reply_prefix("m", 3, 0, ea.SearchResult(True, 5))
    assert prefix == "模型：m，记录：3，搜索：5\n"


def test_reply_prefix_with_search_false():
    prefix = ea._format_reply_prefix("m", 3, 0, ea.SearchResult(True, 0))
    assert prefix == "模型：m，记录：3，搜索：False\n"


def test_reply_prefix_hides_search_when_unknown():
    prefix = ea._format_reply_prefix("m", 3, 0, ea.SearchResult(None, None))
    assert "搜索" not in prefix


def test_reply_prefix_all_fields():
    prefix = ea._format_reply_prefix("m", 10, 1, ea.SearchResult(True, 4))
    assert prefix == "模型：m，记录：10，图片：1，搜索：4\n"
