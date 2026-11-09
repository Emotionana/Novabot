"""文件标记：从 workspace / outbox 安全解析并生成发送消息。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.core.pipeline import Pipeline
from app.file_service import FileService
from app.models import ChatType, InboundMessage


def test_file_marker_is_extracted():
    text, refs = Pipeline._extract_file_tags("给你。<file:workspaces/a.wav>")
    assert text == "给你。"
    assert refs == ["workspaces/a.wav"]


def test_workspace_file_can_be_resolved(db, settings):
    settings.set("security.owner_qq", "10001")
    settings.set("files.send_to", "owner")
    settings.set("files.require_approval", True)
    workspace = Path(db.path).parent.parent / "workspaces" / "acc-a"
    workspace.mkdir(parents=True, exist_ok=True)
    f = workspace / "a.wav"
    f.write_bytes(b"RIFF" + b"0" * 200)

    svc = FileService(db, settings, "acc-a")
    path = svc.resolve_sendable("a.wav", account_id="acc-a")
    assert path == f.resolve()

    p = object.__new__(Pipeline)
    p.files = svc
    result = SimpleNamespace(messages=[])
    msg = InboundMessage(
        account_id="acc-a", chat_id="private:10001", chat_type=ChatType.PRIVATE,
        sender_qq="10001", text="发文件",
    )
    p._attach_files(msg, ["a.wav"], result, is_owner=True)
    assert result.messages
    assert result.messages[0].file == str(f.resolve())


def test_outside_path_is_rejected(db, settings):
    svc = FileService(db, settings, "acc-a")
    try:
        svc.resolve_sendable("C:\\Windows\\win.ini", account_id="acc-a")
    except ValueError:
        return
    raise AssertionError("external path should be rejected")
