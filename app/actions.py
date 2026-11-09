"""自然语言动作：头像、bot 名称、白名单、群管、翻唱。

只做明确命令；权限在 PermissionService 里按聊天档位判断。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass

import httpx

from .bili import find_video_id
from .models import ChatType, InboundMessage
from .permissions import PermissionService
from .settings import Settings
from .stickers import StickerService

log = logging.getLogger(__name__)

IMAGE_WORDS = ("头像", "QQ头像", "qq头像")
NAME_WORDS = ("名字", "昵称", "名称", "bot名", "机器人名", "你叫")
WHITELIST_WORDS = ("白名单", "允许名单")
ADMIN_WORDS = ("禁言", "踢出", "踢了", "设为管理", "设管理", "取消管理")
SONG_WORDS = ("翻唱", "唱一首", "唱个", "来一首", "来一首歌", "唱")


@dataclass
class ActionReply:
    text: str
    handled: bool = True
    sticker: str = ""


class ActionRouter:
    def __init__(self, db, settings, gateway=None, llm=None, *, account_id=""):
        self.db = db
        self.settings = settings
        self.gateway = gateway
        self.llm = llm
        self.account_id = account_id
        self.permissions = PermissionService(db, settings)
        self.stickers = StickerService(db, settings, getattr(settings, "account_id", ""))

    # ---------- 权限 ----------
    def _allowed(self, msg: InboundMessage, cap: str, is_owner: bool) -> tuple[bool, str]:
        p = self.permissions.profile_for(msg, self.settings.get("security.owner_qq") or "")
        if p.get(cap):
            return True, ""
        return False, f"当前权限档「{p.get('name') or p.get('id')}」不允许这个操作。"

    async def _song_intent(self, text: str) -> bool:
        """B站/翻唱候选先让模型确认，不靠正则直接抢任务。"""
        if self.llm is None:
            return False
        try:
            reply = await asyncio.wait_for(
                self.llm.chat(
                    system=(
                        "判断用户是不是在命令你实际执行唱歌/翻唱、B站搜索或音频提取。"
                        "例如“唱起风了”“来一首起风了”“翻唱这首歌”是 ACTION；"
                        "问你会不会唱、聊歌、让你本地跑 RVC/降噪/处理音频是 CHAT。"
                        "只回答 ACTION 或 CHAT。"
                    ),
                    messages=[{"role": "user", "content": text[:1200]}],
                    max_tokens=8,
                    temperature=0.0,
                    stateless=True,
                    session_id="internal:song-intent",
                ),
                timeout=8.0,
            )
        except Exception as e:  # noqa: BLE001
            log.debug("B站/翻唱意图判断失败：%s", e)
            return False
        answer = str(getattr(reply, "text", "") or "").strip().upper()
        return "ACTION" in answer and not getattr(reply, "error", "")

    async def handle(self, msg: InboundMessage, *, is_owner: bool) -> ActionReply | None:
        text = (msg.text or "").strip()
        if not text:
            return None
        # 只处理明确的动作句，不抢普通聊天
        if re.search(r"(发|来|用|整).{0,6}(表情包|表情)", text) and not re.search(r"(会不会|能不能|可以吗|怎么|是什么|吗[？?]?$)", text):
            return await self._sticker(msg, is_owner, text)
        if any(w in text for w in IMAGE_WORDS) and re.search(r"(换|改|设置|换成|改成)", text):
            return await self._avatar(msg, is_owner, text)
        if any(w in text for w in NAME_WORDS) and re.search(r"(换|改|设置|以后|从今以后|改名|改昵称)", text):
            return await self._name(msg, is_owner, text)
        if any(w in text for w in WHITELIST_WORDS) and re.search(r"(加|添加|开放|允许|开|加入|移除|删除|取消)", text):
            return await self._whitelist(msg, is_owner, text)
        if any(w in text for w in ADMIN_WORDS) and (msg.at_list or re.search(r"\b\d{5,12}\b", text)):
            return await self._group_admin(msg, is_owner, text)
        # “唱X”“来一首X”“提取B站音频”只做候选；执行与否由模型判断。
        has_song = any(w in text for w in SONG_WORDS)
        has_extract = (any(w in text for w in ("提取", "下载", "要", "拿"))
                       and any(w in text for w in ("音频", "声音", "音乐")))
        if has_song or has_extract:
            if await self._song_intent(text):
                if has_extract and not has_song:
                    return await self._bili_audio(msg, is_owner, text)
                return await self._song(msg, is_owner, text)
            return None
        return None

    # ---------- 表情包 ----------
    async def _sticker(self, msg, is_owner, text):
        ok, why = self._allowed(msg, "sticker_send", is_owner)
        if not ok:
            return ActionReply(why)
        query = re.sub(r"(发|来|用|整|给我|一个|一张|个|张|表情包|表情|吧|呗|嘛)", "", text).strip(" ，。！？")
        pick = self.stickers.pick(query, msg, owner_qq=str(self.settings.get("security.owner_qq") or ""))
        if not pick:
            return ActionReply("表情库里没有合适的；先让我偷几张，或者到聊天 → 表情包上传。")
        self.stickers.mark_used(str(pick.get("md5") or ""))
        return ActionReply("", sticker=str(pick.get("md5") or ""))

    # ---------- 头像 ----------
    async def _avatar(self, msg, is_owner, text):
        ok, why = self._allowed(msg, "avatar", is_owner)
        if not ok:
            return ActionReply(why)
        item = next((m for m in msg.media if m.kind in ("image", "sticker")), None)
        url = item.url if item else ""
        if not url:
            found = re.search(r"https?://\S+", text)
            url = found.group(0) if found else ""
        if not url:
            return ActionReply("把要换的头像图片发给我，或带一个图片链接。")
        client = self._client()
        if client is None:
            return ActionReply("当前账号没连上，换不了头像。")
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                r = await c.get(url)
                r.raise_for_status()
                if len(r.content) > 8 * 1024 * 1024:
                    return ActionReply("图片太大了，换一张 8MB 以内的。")
                b64 = base64.b64encode(r.content).decode()
            await client.call("set_qq_avatar", {"file": "base64://" + b64})
            return ActionReply("头像已经换了。")
        except Exception as e:  # noqa: BLE001
            log.warning("换头像失败：%s", e)
            return ActionReply(f"换头像失败：{str(e)[:160]}")

    # ---------- bot 名称 ----------
    async def _name(self, msg, is_owner, text):
        if "叫什么" in text or "名字是什么" in text:
            return None
        ok, why = self._allowed(msg, "profile", is_owner)
        if not ok:
            return ActionReply(why)
        name = ""
        for pat in (r"叫[「『“\"']?([^」』”\"'，。！？\s]{1,20})", r"(?:改成|换成|设置为)[「『“\"']?([^」』”\"'，。！？\s]{1,20})"):
            m = re.search(pat, text)
            if m:
                name = m.group(1).strip()
                break
        if not name:
            return ActionReply("要改成什么名字？比如：以后你叫小若。")
        client = self._client()
        if client is None:
            return ActionReply("当前账号没连上，改不了名称。")
        try:
            await client.call("set_qq_profile", {"nickname": name})
            return ActionReply(f"名称已经改成「{name}」。")
        except Exception as e:  # noqa: BLE001
            log.warning("改名称失败：%s", e)
            return ActionReply(f"改名称失败：{str(e)[:160]}")

    # ---------- 白名单 ----------
    async def _whitelist(self, msg, is_owner, text):
        ok, why = self._allowed(msg, "whitelist", is_owner)
        if not ok:
            return ActionReply(why)
        if re.search(r"(删|移除|取消|关闭).{0,6}白名单", text):
            return ActionReply("白名单移除要在面板里确认，聊天里先只做添加。")
        settings = Settings(self.db)
        key = "security.group_whitelist" if msg.chat_type == ChatType.GROUP else "security.private_whitelist"
        current = list(settings.get(key) or [])
        target = ""
        if msg.at_list:
            target = str(msg.at_list[0])
        elif msg.chat_type == ChatType.GROUP:
            target = str(msg.group_id or "")
        else:
            target = str(msg.sender_qq)
        m = re.search(r"\b(\d{5,12})\b", text)
        if m and not msg.at_list:
            target = m.group(1)
        if not target:
            return ActionReply("要把谁/哪个群加白名单？")
        if target in current:
            return ActionReply(f"已经在白名单里了：{target}")
        current.append(target)
        settings.set(key, current[-500:])
        return ActionReply(f"已加入白名单：{target}")

    # ---------- 群管 ----------
    async def _group_admin(self, msg, is_owner, text):
        ok, why = self._allowed(msg, "group_admin", is_owner)
        if not ok:
            return ActionReply(why)
        if msg.chat_type != ChatType.GROUP:
            return ActionReply("群管操作只能在群里做。")
        client = self._client()
        if client is None:
            return ActionReply("当前账号没连上，群管操作没法执行。")
        targets = [str(x) for x in (msg.at_list or []) if str(x).isdigit()]
        if not targets:
            m = re.search(r"\b(\d{5,12})\b", text)
            if m:
                targets = [m.group(1)]
        if not targets:
            return ActionReply("要操作谁？@ 一下对方。")
        try:
            if "禁言" in text:
                minutes = 10
                m = re.search(r"(\d+)\s*(秒|分钟|小时|天)", text)
                if m:
                    n = int(m.group(1))
                    minutes = n / 60 if m.group(2) == "秒" else n * 60 if m.group(2) == "小时" else n * 1440 if m.group(2) == "天" else n
                for uid in targets:
                    await client.call("set_group_ban", {
                        "group_id": int(msg.group_id or 0), "user_id": int(uid),
                        "duration": int(max(60, minutes * 60)),
                    })
                return ActionReply(f"已禁言 {', '.join(targets)} {int(minutes)} 分钟。")
            if "踢" in text:
                for uid in targets:
                    await client.call("set_group_kick", {
                        "group_id": int(msg.group_id or 0), "user_id": int(uid), "reject_add_request": False,
                    })
                return ActionReply(f"已踢出 {', '.join(targets)}。")
            if "管理" in text:
                enable = "取消" not in text
                for uid in targets:
                    await client.call("set_group_admin", {
                        "group_id": int(msg.group_id or 0), "user_id": int(uid), "enable": enable,
                    })
                return ActionReply(("已设为管理员：" if enable else "已取消管理员：") + ", ".join(targets))
        except Exception as e:  # noqa: BLE001
            log.warning("群管操作失败：%s", e)
            return ActionReply(f"群管操作失败：{str(e)[:160]}")
        return None

    # ---------- B 站音频提取 ----------
    async def _bili_audio(self, msg, is_owner, text):
        ok, why = self._allowed(msg, "song", is_owner)
        if not ok:
            return ActionReply(why)
        from .song_module import SongModule

        svc = SongModule(self.db, self.settings, getattr(self.settings, "account_id", ""))
        if not svc.status().get("enabled"):
            return ActionReply("B 站音频模块还没启用（设置 → 模块 → 翻唱）。")
        title = re.sub(r"(帮我|帮忙|请|从|去|在)?\s*(B站|b站|bilibili)\s*(上|里)?\s*", "", text)
        title = re.sub(r"(提取|下载|要|拿|一下|的)?\s*(音频|声音|音乐).*", "", title)
        title = re.sub(r"^(翻唱|唱一首|唱个|来一首歌?|来一首)\s*", "", title)
        title = title.strip(" 《》「」“”\"'")
        if not title:
            return ActionReply("要提取哪个 B 站视频？给我 BV 号、链接或歌名。")
        try:
            vid = find_video_id(text)
            if vid:
                info = await svc.bili.resolve(text)
                song = {"id": info["bvid"], "name": info["title"]}
            else:
                songs = await svc.search(title, 3)
                if not songs:
                    return ActionReply(f"B 站没搜到《{title}》。")
                song = songs[0]
            job = svc.start(song_id=song["id"], name=song["name"], voice="", pitch=0, extract_only=True)
            if not job.get("ok"):
                return ActionReply(job.get("error") or "提取任务没起来。")
            j = job["job"]
            asyncio.create_task(self._send_song_when_done(msg.chat_id, svc, j["id"]))
            return ActionReply(f"开始从 B 站提取《{song['name']}》的音频，好了发给你。")
        except Exception as e:  # noqa: BLE001
            log.warning("B 站提取失败：%s", e)
            return ActionReply(f"B 站提取失败：{str(e)[:160]}")

    # ---------- 翻唱 ----------
    async def _song(self, msg, is_owner, text):
        ok, why = self._allowed(msg, "song", is_owner)
        if not ok:
            return ActionReply(why)
        from .song_module import SongModule

        svc = SongModule(self.db, self.settings, getattr(self.settings, "account_id", ""))
        status = svc.status()
        if not status.get("ready"):
            if status.get("extract_ready"):
                return await self._bili_audio(msg, is_owner, text)
            return ActionReply(status.get("message") or "翻唱模块还没准备好。")
        title = re.sub(r".{0,3}(翻唱|唱一首|唱个|来一首歌?|来一首|唱)\s*", "", text).strip(" 《》「」“”\"'")
        if not title:
            return ActionReply("想翻唱哪首歌？比如：翻唱《起风了》。")
        try:
            songs = await svc.search(title, 3)
            if not songs:
                return ActionReply(f"没搜到《{title}》，换个歌名试试。")
            song = songs[0]
            voice = str(self.settings.get("module.song_default_voice") or "")
            job = svc.start(song_id=song["id"], name=song["name"], voice=voice, pitch=int(self.settings.get("module.song_pitch") or 0))
            if not job.get("ok"):
                return ActionReply(job.get("error") or "翻唱任务没起来。")
            j = job["job"]
            asyncio.create_task(self._send_song_when_done(msg.chat_id, svc, j["id"]))
            return ActionReply(f"开始翻唱《{song['name']}》，好了我发给你。")
        except Exception as e:  # noqa: BLE001
            log.warning("翻唱失败：%s", e)
            return ActionReply(f"翻唱失败：{str(e)[:160]}")

    async def _send_song_when_done(self, chat_id: str, svc, job_id: str) -> None:
        client = self._client()
        if client is None:
            return
        for _ in range(600):
            job = svc.jobs.get(job_id) or {}
            if job.get("status") == "done" and job.get("file"):
                path = svc.cache / job["file"]
                if path.is_file():
                    try:
                        from .models import OutboundMessage

                        if job.get("extract_only"):
                            await client.send(OutboundMessage(chat_id=chat_id, file=str(path), file_name=path.name))
                        else:
                            await client.send(OutboundMessage(chat_id=chat_id, voice=str(path)))
                    except Exception as e:  # noqa: BLE001
                        log.warning("翻唱发送失败：%s", e)
                return
            if job.get("status") == "error":
                return
            await asyncio.sleep(2)

    def _client(self):
        rt = self.gateway.get(self.account_id) if self.gateway else None
        return rt.client if rt else None
