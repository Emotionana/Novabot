"""把 OneBot 事件转成内部消息，把内部回复发出去。

这层是接入和业务之间的桥：
  OneBot 事件 → InboundMessage → Pipeline → OutboundMessage → OneBot 发送
"""

from __future__ import annotations

import logging
import re
import time

from ..models import ChatType, InboundMessage, MediaItem, MsgType, OutboundMessage, SenderLevel

log = logging.getLogger(__name__)


def parse_event(event: dict, *, impl: str = "snowluma") -> InboundMessage | None:
    """OneBot 事件 → InboundMessage。

    消息事件走普通解析；戳一戳通知也转成一条带 poke 标记的消息。
    """
    post_type = event.get("post_type")
    if post_type == "notice":
        return _parse_poke(event)
    if post_type != "message":
        return None

    mt = event.get("message_type")  # private / group
    if mt not in ("private", "group"):
        return None

    sender = event.get("sender") or {}
    user_id = str(event.get("user_id") or sender.get("user_id") or "")
    if not user_id:
        return None

    # 机器人自己发的不要
    self_id = str(event.get("self_id") or "")
    if self_id and user_id == self_id:
        return None

    text, at_list, msg_type, media = _parse_segments(event.get("message"))
    reply_message_id = _reply_message_id(event.get("message"))
    if not text and msg_type == MsgType.TEXT:
        # 纯图片/表情也进，text 空着
        pass

    ts = float(event.get("time") or time.time())

    if mt == "group":
        group_id = str(event.get("group_id") or "")
        if not group_id:
            return None
        chat_id = f"group:{group_id}"
        chat_type = ChatType.GROUP
        group_name = ""  # 事件里没有，需要另外查
    else:
        group_id = None
        chat_id = f"private:{user_id}"
        chat_type = ChatType.PRIVATE
        group_name = None

    # 群名片
    card = sender.get("card") or ""
    nick = sender.get("nickname") or ""
    level = _level_of(sender.get("role", ""))

    return InboundMessage(
        platform="qq",
        account_id="",          # 由上层填
        chat_id=chat_id,
        chat_type=chat_type,
        group_id=group_id,
        group_name=group_name,
        sender_qq=user_id,
        sender_nick=nick,
        sender_card=card,
        sender_level=level,
        text=text,
        message_id=str(event.get("message_id") or ""),
        raw=event,
        msg_type=msg_type,
        reply_to=None,          # 需要查被引用的消息
        reply_message_id=reply_message_id,
        at_list=at_list,
        media=media,
        event_at=ts,
        recv_at=time.time(),
    )


def _parse_poke(event: dict) -> InboundMessage | None:
    """把 OneBot 的戳一戳通知转成消息。

    SnowLuma/NapCat 常见两种形状：
      notice_type=poke
      notice_type=notify, sub_type=poke
    """
    notice = str(event.get("notice_type") or "")
    sub = str(event.get("sub_type") or "")
    if notice == "notify":
        if sub != "poke":
            return None
    elif notice != "poke":
        return None

    self_id = str(event.get("self_id") or "")
    target = str(event.get("target_id") or event.get("target") or "")
    if target and self_id and target != self_id:
        return None

    user_id = str(event.get("user_id") or event.get("sender_id") or "")
    if not user_id or (self_id and user_id == self_id):
        return None

    sender = event.get("sender") or {}
    nick = str(sender.get("nickname") or "")
    card = str(sender.get("card") or "")
    who = card or nick or user_id
    group_id = str(event.get("group_id") or "")
    if group_id:
        chat_id = f"group:{group_id}"
        chat_type = ChatType.GROUP
    else:
        group_id = ""
        chat_id = f"private:{user_id}"
        chat_type = ChatType.PRIVATE

    return InboundMessage(
        platform="qq",
        account_id="",
        chat_id=chat_id,
        chat_type=chat_type,
        group_id=group_id or None,
        group_name=None,
        sender_qq=user_id,
        sender_nick=nick,
        sender_card=card,
        sender_level=_level_of(sender.get("role", "")),
        text=f"[戳一戳] {who} 戳了戳你",
        raw=event,
        msg_type=MsgType.TEXT,
        at_list=[],
        media=[],
        poke=True,
        event_at=float(event.get("time") or time.time()),
        recv_at=time.time(),
    )


def _reply_message_id(message) -> str:
    """从 reply 段取被引用消息的 QQ message_id；取不到返回空。"""
    if isinstance(message, str):
        m = re.search(r"\[CQ:reply,([^\]]*)\]", message)
        if not m:
            return ""
        attrs = dict(re.findall(r"([\w_]+)=([^,\]]*)", m.group(1)))
        return str(attrs.get("id") or attrs.get("message_id") or "")
    if not isinstance(message, list):
        return ""
    for seg in message:
        if not isinstance(seg, dict) or seg.get("type") != "reply":
            continue
        data = seg.get("data") or {}
        return str(data.get("id") or data.get("message_id") or "")
    return ""


def _parse_segments(message) -> tuple[str, list[str], MsgType, list[MediaItem]]:
    """解析消息段，返回 (纯文本, @列表, 消息类型)。"""
    if message is None:
        return "", [], MsgType.TEXT, []

    # 字符串格式（CQ 码）
    if isinstance(message, str):
        return _parse_cq(message)

    segs = message if isinstance(message, list) else []
    texts: list[str] = []
    ats: list[str] = []
    media: list[MediaItem] = []
    has_image = False
    has_voice = False
    has_video = False
    has_reply = False

    for seg in segs:
        if not isinstance(seg, dict):
            continue
        t = seg.get("type")
        data = seg.get("data") or {}

        if t == "text":
            texts.append(str(data.get("text") or ""))
        elif t == "at":
            # @ 只进 at_list，不进正文
            # 正文里留着 "@123456" 会让模型以为是在念号码
            qq = str(data.get("qq") or "")
            if qq:
                ats.append(qq if qq != "all" else "all")
        elif t == "image":
            has_image = True
            media.append(_media_item("sticker" if str(data.get("sub_type") or "") == "1" else "image", data))
            texts.append("[图片]")
        elif t == "record":
            has_voice = True
            media.append(_media_item("voice", data))
            texts.append("[语音]")
        elif t == "video":
            has_video = True
            media.append(_media_item("video", data))
            texts.append("[视频]")
        elif t in ("face", "mface"):
            if t == "mface" and (data.get("url") or data.get("file")):
                media.append(_media_item("sticker", data))
            texts.append("[表情]")
        elif t == "reply":
            has_reply = True
        elif t == "forward":
            texts.append("[合并转发]")
        elif t == "file":
            media.append(_media_item("file", data))
            texts.append("[文件]")

    text = "".join(texts).strip()

    if has_voice:
        mt = MsgType.VOICE
    elif has_video:
        mt = MsgType.VIDEO
    elif has_image:
        mt = MsgType.IMAGE
    elif any(m.kind == "file" for m in media):
        mt = MsgType.FILE
    elif has_reply:
        mt = MsgType.TEXT
    else:
        mt = MsgType.TEXT

    return text, ats, mt, media


def _parse_cq(message: str) -> tuple[str, list[str], MsgType, list[MediaItem]]:
    """解析 CQ 码格式。"""
    import re

    ats = re.findall(r"\[CQ:at,qq=(\d+)\]", message)
    text = re.sub(r"\[CQ:[^\]]+\]", "", message).strip()
    media: list[MediaItem] = []
    has_image = "[CQ:image" in message
    has_voice = "[CQ:record" in message
    has_video = "[CQ:video" in message
    has_file = "[CQ:file" in message
    for tag in ("image", "record", "video", "file"):
        for raw in re.findall(r"\[CQ:" + tag + r"([^\]]*)\]", message):
            attrs = dict(re.findall(r"([\w_]+)=([^,\]]*)", raw))
            kind = "image"
            if tag == "record":
                kind = "voice"
            elif tag == "video":
                kind = "video"
            elif tag == "file":
                kind = "file"
            elif attrs.get("sub_type") == "1":
                kind = "sticker"
            media.append(_media_item(kind, attrs))

    if has_voice:
        mt = MsgType.VOICE
    elif has_video:
        mt = MsgType.VIDEO
    elif has_image:
        mt = MsgType.IMAGE
    elif has_file:
        mt = MsgType.FILE
    else:
        mt = MsgType.TEXT

    return text, ats, mt, media


def _media_item(kind: str, data: dict) -> MediaItem:
    return MediaItem(
        kind=kind,
        url=str(data.get("url") or ""),
        file=str(data.get("file") or data.get("path") or ""),
        mime=str(data.get("mime") or ""),
        size=int(data.get("size") or 0),
        duration=float(data.get("duration") or 0),
        name=str(data.get("name") or ""),
    )


def _level_of(role: str) -> SenderLevel:
    return {
        "owner": SenderLevel.OWNER,
        "admin": SenderLevel.ADMIN,
        "member": SenderLevel.MEMBER,
    }.get(role or "member", SenderLevel.MEMBER)


# ---------------------------------------------------------------------------
# 出站
# ---------------------------------------------------------------------------


def to_outbound(chat_id: str, text: str, **kw) -> OutboundMessage:
    return OutboundMessage(chat_id=chat_id, text=text, **kw)


def split_message(text: str, *, max_chars: int = 500) -> list[str]:
    """QQ 单条消息有长度上限，长的要拆。

    优先在句号、问号、感叹号处断。
    """
    text = (text or "").strip()
    if len(text) <= max_chars:
        return [text] if text else []

    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if len(buf) >= max_chars:
            # 找最后一个句末标点
            cut = max(buf.rfind(c) for c in "。！？\n")
            if cut > max_chars // 2:
                parts.append(buf[: cut + 1].strip())
                buf = buf[cut + 1 :]
            else:
                parts.append(buf.strip())
                buf = ""
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if p]
