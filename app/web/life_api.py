"""生活模块接口：此刻、时间轴。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..life_service import LifeService
from ..settings import Settings

router = APIRouter(prefix="/api/life", tags=["life"])


class ScheduleIn(BaseModel):
    account_id: str = ""
    weekday: int
    slots: list[dict] = []
    reset: bool = False


def _life(request: Request, account_id: str) -> LifeService:
    return LifeService(request.app.state.db, Settings(request.app.state.db, account_id=account_id))


@router.get("/status")
def status(request: Request, account_id: str = ""):
    try:
        return _life(request, account_id).status(account_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


@router.get("/timeline")
def timeline(request: Request, account_id: str = ""):
    try:
        life = _life(request, account_id)
        data = life.status(account_id)
        return {"ok": True, "day": data["day"], "phases": data["phases"], "now": data["now"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


@router.get("/schedule")
def schedule(request: Request, account_id: str = ""):
    try:
        return _life(request, account_id).schedule(account_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


@router.post("/schedule")
def save_schedule(req: ScheduleIn, request: Request):
    life = _life(request, req.account_id)
    try:
        if req.reset:
            return life.reset_schedule(req.account_id, req.weekday)
        return life.save_schedule(req.account_id, req.weekday, req.slots)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}

