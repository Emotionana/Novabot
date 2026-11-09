"""文件沙箱：上传、权限、审批、敏感文件拒绝。"""

from __future__ import annotations

import asyncio

from app.file_service import FileService
from app.models import ChatType, InboundMessage, MediaItem


def _svc(db, settings):
    settings.set("security.owner_qq", "10001")
    settings.set("files.enabled", True)
    settings.set("files.receive_from", "owner")
    settings.set("files.send_to", "owner")
    settings.set("files.require_approval", True)
    return FileService(db, settings)


def test_upload_request_and_approve(db, settings):
    svc = _svc(db, settings)
    r = svc.upload("note.txt", b"hello")
    assert r["ok"] is True
    assert svc.list("outbox")[0]["name"] == "note.txt"

    req = svc.request("note.txt", chat_id="private:10001", account_id="a1", sender="10001")
    assert req["ok"] is True
    assert req["requires_approval"] is True
    assert len(svc.pending()) == 1
    got = svc.approve(req["id"])
    assert got["ok"] is True
    assert got["name"] == "note.txt"
    assert svc.pending() == []


def test_cannot_escape_sandbox(db, settings):
    svc = _svc(db, settings)
    assert svc.request("../secret.txt", chat_id="private:10001", account_id="a1")["ok"] is False
    assert svc.request("C:/Windows/win.ini", chat_id="private:10001", account_id="a1")["ok"] is False


def test_sensitive_filename_and_content_rejected(db, settings):
    svc = _svc(db, settings)
    assert svc.upload(".env", b"TOKEN=1234567890")["ok"] is False
    secret = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    assert svc.upload("note.txt", secret.encode())["ok"] is False


def test_image_does_not_get_file_permission_note(db, settings):
    svc = _svc(db, settings)
    msg = InboundMessage(
        account_id="a1", chat_id="group:1", chat_type=ChatType.GROUP,
        sender_qq="20002", text="[图片]",
        media=[MediaItem(kind="image", url="https://example.com/a.jpg")],
    )
    assert asyncio.run(svc.ingest(msg)) == ""
