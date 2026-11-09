"""工作台模块状态必须按当前账号读，而不是只看全局设置。"""

from __future__ import annotations

from app.settings import Settings
from app.system_status import collect


def test_modules_use_account_switches(db):
    st = Settings(db, account_id="acc-a")
    st.set("proactive.enabled", True)
    st.set("tools.enabled", True)
    rows = collect(db, "acc-a")
    status = {r["id"]: r["status"] for r in rows}
    assert status["proactive"] == "ok"
    assert status["tools"] == "ok"
    assert status["gateway"] in ("ok", "off")
