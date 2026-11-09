"""DSH 插件包必须带 dsh.bundle.patch，否则装不上。"""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "dsh-plugin" / "novabot"


def test_dsh_plugin_has_bundle_patch():
    pkg = json.loads((PLUGIN / "package.json").read_text(encoding="utf-8"))
    bundle = (pkg.get("dsh") or {}).get("bundle") or {}
    assert bundle.get("patch") == "./cordis.patch.yml"
    patch = PLUGIN / "cordis.patch.yml"
    assert patch.is_file()
    text = patch.read_text(encoding="utf-8")
    assert "insert:" in text
    assert "name: dsh-novabot" in text
    assert (PLUGIN / "index.js").is_file()
