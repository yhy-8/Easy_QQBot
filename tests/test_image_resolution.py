"""视觉附件：文件头识别、占位说明、路径策略与 Base64 读取。"""

from __future__ import annotations

import base64

import pytest

import easy_ai as ea
from conftest import FakeBot, run


# --------------------------------------------------------------------------
# _visual_placeholder_token / _append_visual_notice
# --------------------------------------------------------------------------
def test_visual_placeholder_token_unique():
    assert ea._visual_placeholder_token(0) != ea._visual_placeholder_token(1)


def test_append_visual_notice_with_brackets():
    assert ea._append_visual_notice("[图片]", "原因") == "[图片（未发送：原因）]"


def test_append_visual_notice_without_brackets():
    assert ea._append_visual_notice("图片", "原因") == "图片（未发送：原因）"


# --------------------------------------------------------------------------
# _detect_image_format
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "header,expected",
    [
        (b"\xff\xd8\xff\xe0rest", ("JPEG", "image/jpeg")),
        (b"\x89PNG\r\n\x1a\nrest", ("PNG", "image/png")),
        (b"RIFF\x00\x00\x00\x00WEBPrest", ("WebP", "image/webp")),
        (b"GIF87a", ("GIF", None)),
        (b"GIF89a", ("GIF", None)),
        (b"BM\x00\x00", ("BMP", None)),
        (b"II*\x00", ("TIFF", None)),
        (b"MM\x00*", ("TIFF", None)),
        (b"\x00\x00\x01\x00", ("ICO", None)),
        (b"\x00\x00\x02\x00", ("ICO", None)),
        (b"\x00\x00\x00\x18ftypavif", ("AVIF", None)),
        (b"\x00\x00\x00\x18ftypheic", ("HEIC", None)),
        (b"\x00\x00\x00\x18ftypmif1", ("HEIF", None)),
        (b"  <svg xmlns='http://www.w3.org/2000/svg'>", ("SVG", None)),
        (b"junkjunk", (None, None)),
        (b"", (None, None)),
    ],
)
def test_detect_image_format(header, expected):
    assert ea._detect_image_format(header) == expected


def test_detect_webp_requires_full_riff_header():
    # 长度不足 12 不应误判为 WebP
    assert ea._detect_image_format(b"RIFF") == (None, None)


# --------------------------------------------------------------------------
# resolve_visual_attachment
# --------------------------------------------------------------------------
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"payload"


def _write(tmp_path, name, data=PNG_BYTES):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_resolve_uses_local_path_and_returns_base64(monkeypatch, tmp_path):
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    png = _write(tmp_path, "a.png")
    attachment = ea.VisualAttachment("[图片]", file_id="", local_path=str(png))
    resolved = run(ea.resolve_visual_attachment(FakeBot(), attachment, max_retries=1, wait_time=0))
    assert resolved.sendable
    assert resolved.mime_type == "image/png"
    assert base64.b64decode(resolved.base64_data) == PNG_BYTES
    assert resolved.placeholder == "[图片]"


def test_resolve_prefers_get_image_result(monkeypatch, tmp_path):
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    png = _write(tmp_path, "a.png")
    bot = FakeBot()
    bot.image_info["fid"] = {"file": str(png)}
    attachment = ea.VisualAttachment("[图片]", file_id="fid", local_path="/nonexistent.png")
    resolved = run(ea.resolve_visual_attachment(bot, attachment, max_retries=1, wait_time=0))
    assert resolved.sendable


def test_resolve_falls_back_to_segment_path_when_get_image_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    png = _write(tmp_path, "a.png")
    bot = FakeBot()
    bot.image_info["fid"] = RuntimeError("napcat boom")
    attachment = ea.VisualAttachment("[图片]", file_id="fid", local_path=str(png))
    resolved = run(ea.resolve_visual_attachment(bot, attachment, max_retries=1, wait_time=0))
    assert resolved.sendable


def test_resolve_without_any_path_is_unavailable(monkeypatch):
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    attachment = ea.VisualAttachment("[图片]", file_id="", local_path="")
    resolved = run(ea.resolve_visual_attachment(FakeBot(), attachment, max_retries=1, wait_time=0))
    assert not resolved.sendable
    assert resolved.placeholder == "[图片（未发送：无法获取视觉内容）]"


def test_resolve_uses_image_base_dir_basename(monkeypatch, tmp_path):
    mapping_dir = tmp_path / "mapped"
    mapping_dir.mkdir()
    png = mapping_dir / "real.png"
    png.write_bytes(PNG_BYTES)
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", str(mapping_dir))
    attachment = ea.VisualAttachment("[图片]", file_id="", local_path="/napcat/other/real.png")
    resolved = run(ea.resolve_visual_attachment(FakeBot(), attachment, max_retries=1, wait_time=0))
    assert resolved.sendable


def test_resolve_missing_file_times_out(monkeypatch, tmp_path):
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    attachment = ea.VisualAttachment("[图片]", file_id="", local_path=str(tmp_path / "missing.png"))
    resolved = run(ea.resolve_visual_attachment(FakeBot(), attachment, max_retries=1, wait_time=0))
    assert not resolved.sendable
    assert "无法获取视觉内容" in resolved.placeholder


def test_resolve_unsupported_format(monkeypatch, tmp_path):
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    gif = _write(tmp_path, "a.gif", b"GIF89a-data")
    attachment = ea.VisualAttachment("[动画表情]", file_id="", local_path=str(gif))
    resolved = run(ea.resolve_visual_attachment(FakeBot(), attachment, max_retries=1, wait_time=0))
    assert not resolved.sendable
    assert resolved.placeholder == "[动画表情（未发送：暂不支持 GIF 格式）]"


def test_resolve_unknown_format(monkeypatch, tmp_path):
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    weird = _write(tmp_path, "a.bin", b"not-an-image")
    attachment = ea.VisualAttachment("[图片]", file_id="", local_path=str(weird))
    resolved = run(ea.resolve_visual_attachment(FakeBot(), attachment, max_retries=1, wait_time=0))
    assert not resolved.sendable
    assert resolved.placeholder == "[图片（未发送：无法识别图片格式）]"


def test_resolve_empty_file_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    empty = _write(tmp_path, "empty.png", b"")
    attachment = ea.VisualAttachment("[图片]", file_id="", local_path=str(empty))
    resolved = run(ea.resolve_visual_attachment(FakeBot(), attachment, max_retries=1, wait_time=0))
    assert not resolved.sendable
    assert "无法获取视觉内容" in resolved.placeholder


def test_resolve_read_error_is_caught(monkeypatch, tmp_path):
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    target = tmp_path / "dir.png"
    target.mkdir()  # 是目录而非文件
    attachment = ea.VisualAttachment("[图片]", file_id="", local_path=str(target))
    resolved = run(ea.resolve_visual_attachment(FakeBot(), attachment, max_retries=1, wait_time=0))
    assert not resolved.sendable


def test_resolve_format_detection_exception_is_caught(monkeypatch, tmp_path, logger):
    monkeypatch.setattr(ea, "IMAGE_BASE_DIR", "")
    png = _write(tmp_path, "a.png")

    def boom(header):
        raise RuntimeError("detector crashed")

    monkeypatch.setattr(ea, "_detect_image_format", boom)
    attachment = ea.VisualAttachment("[图片]", file_id="", local_path=str(png))
    resolved = run(ea.resolve_visual_attachment(FakeBot(), attachment, max_retries=1, wait_time=0))
    assert not resolved.sendable
    assert "无法获取视觉内容" in resolved.placeholder
    assert logger.contains("exception")
