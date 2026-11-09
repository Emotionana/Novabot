"""表情批量重新理解：会遍历全部有文件的表情，而不是只补未理解。"""

from __future__ import annotations

import struct

import pytest

from app.settings import Settings
from app.stickers import StickerService


def _png() -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
            struct.pack(">II", 100, 100) + b"\x08\x06\x00\x00\x00" + b"\x00" * 20)


@pytest.mark.asyncio
async def test_analyze_all_includes_already_analyzed(db):
    st = Settings(db, account_id="acc-a")
    st.set("stickers.enabled", True)
    svc = StickerService(db, st, "acc-a")
    a = svc.add_bytes(_png(), name="a", tags=["a"])["item"]
    b = svc.add_bytes(_png() + b"different", name="b", tags=["b"])["item"]
    svc._set_analysis(a["md5"], {"analyzed": True, "meaning": "旧含义"})
    seen = []

    async def fake(md5, llm=None):
        seen.append(md5)
        return {"ok": True, "item": {}}

    svc.analyze = fake
    progress = []
    result = await svc.analyze_all(progress=lambda *args: progress.append(args))
    assert result["total"] == 2
    assert result["done"] == 2
    assert set(seen) == {a["md5"], b["md5"]}
    assert progress[-1][:3] == (2, 2, 0)
