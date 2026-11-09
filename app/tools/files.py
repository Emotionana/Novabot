"""文件工具：模型只能请求发送沙箱里的文件，不能读主机任意路径。"""

from __future__ import annotations

from ..file_service import FileService
from ..settings import Settings
from .base import REGISTRY, Tool, ToolContext


def request_file(ctx: ToolContext, args: dict) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ValueError("要发哪个文件？只写出站箱里的文件名")
    if ctx.permissions and not ctx.permissions.get("file_send"):
        return "当前权限档不允许发送文件"
    svc = FileService(ctx.db, Settings(ctx.db, account_id=ctx.account_id))
    if not svc.enabled:
        return "文件模块没启用"
    r = svc.request(name, chat_id=ctx.chat_id, account_id=ctx.account_id, sender=ctx.person_qq)
    return r.get("message") or r.get("error") or "提交失败"


REGISTRY.register(
    Tool(
        name="request_file_send",
        description=(
            "请求把 data/files/outbox 里的一个文件发给当前聊天。"
            "只能填出站箱里的文件名；不能填电脑上的任意路径。"
            "默认还要主人在面板里审批后才会真正上传。"
        ),
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "出站箱里的文件名"}},
            "required": ["name"],
        },
        handler=request_file,
        owner_only=True,
        write=True,
    )
)
