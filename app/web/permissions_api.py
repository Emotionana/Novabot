"""权限分级 API。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..permissions import PermissionService

router = APIRouter(prefix="/api/permissions", tags=["permissions"])


def _svc(request: Request, account_id: str = "") -> PermissionService:
    from ..settings import Settings

    return PermissionService(request.app.state.db, Settings(request.app.state.db, account_id=account_id))


class ProfileIn(BaseModel):
    key: str
    values: dict = {}
    name: str = ""


class DefaultIn(BaseModel):
    tier: str


class UserIn(BaseModel):
    qq: str
    tier: str = ""


class AdminIn(BaseModel):
    qq: str
    enabled: bool = True


class TierAddIn(BaseModel):
    name: str = ""


class TierDeleteIn(BaseModel):
    key: str


class OverrideIn(BaseModel):
    key: str
    tier: str = ""


@router.get("")
def view(request: Request, account_id: str = ""):
    data = _svc(request, account_id).view()
    rows = []
    try:
        from ..settings import Settings

        st = Settings(request.app.state.db, account_id=account_id)
        def ids(key):
            values = st.get(key) or []
            if isinstance(values, str):
                values = values.replace("，", ",").split(",")
            out = set()
            for x in values:
                text = str(x or "").strip()
                for prefix in ("private:", "group:", "qq:"):
                    if text.startswith(prefix):
                        text = text[len(prefix):]
                if text:
                    out.add(text)
            return out
        private_ok = ids("security.private_whitelist")
        private_ok.update(str(x) for x in (data.get("admins") or []))
        group_ok = ids("security.group_whitelist")
        owner = str(st.get("security.owner_qq") or "")
        for r in request.app.state.db.query(
            "SELECT account_id, chat_id, chat_type, title, last_seen FROM chats "
            "WHERE account_id = ? ORDER BY last_seen DESC LIMIT 500",
            [account_id],
        ):
            cid = r["chat_id"]
            if r["chat_type"] == "group":
                gid = cid[6:] if cid.startswith("group:") else cid
                if gid not in group_ok:
                    continue
            else:
                qq = cid[8:] if cid.startswith("private:") else cid
                if not qq or qq == owner or qq not in private_ok:
                    continue
            rows.append({
                "account_id": r["account_id"], "chat_id": cid,
                "chat_type": r["chat_type"], "title": r["title"] or "",
                "last_seen": r["last_seen"],
                "key": f"{r['account_id']}|{cid}",
            })
    except Exception:  # noqa: BLE001
        rows = []
    data["chats"] = rows
    return data


@router.post("/profile")
def update_profile(req: ProfileIn, request: Request, account_id: str = ""):
    return _svc(request, account_id).update_profile(req.key, {**req.values, "name": req.name})


@router.post("/default")
def set_default(req: DefaultIn, request: Request, account_id: str = ""):
    return _svc(request, account_id).set_default(req.tier)


@router.post("/user")
def set_user(req: UserIn, request: Request, account_id: str = ""):
    return _svc(request, account_id).set_user(req.qq, req.tier)


@router.post("/admin")
def set_admin(req: AdminIn, request: Request, account_id: str = ""):
    return _svc(request, account_id).set_admin(req.qq, req.enabled)


@router.post("/tier/add")
def add_tier(req: TierAddIn, request: Request, account_id: str = ""):
    return _svc(request, account_id).add_tier(req.name)


@router.post("/tier/delete")
def delete_tier(req: TierDeleteIn, request: Request, account_id: str = ""):
    return _svc(request, account_id).delete_tier(req.key)


@router.post("/override")
def set_override(req: OverrideIn, request: Request, account_id: str = ""):
    return _svc(request, account_id).set_override(req.key, req.tier)
