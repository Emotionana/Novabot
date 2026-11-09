"""拉群 / 推荐联系人 / 退群 / 删好友的访问确认。

事件先记录成 pending，由模型生成“要不要同意”的询问；
用户回复后由模型判断同意/拒绝，再执行 OneBot 请求并同步白名单。
"""

from __future__ import annotations

import logging
import re
import time

from .models import ChatType, InboundMessage
from .settings import Settings

log = logging.getLogger(__name__)

PENDING_TTL = 600


class AccessService:
    def __init__(self, manager):
        self.manager = manager
        self.db = getattr(manager.settings, "db", None)
        if self.db is not None:
            self._ensure_table()

    def _ensure_table(self) -> None:
        with self.db.tx():
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS access_requests ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " account_id TEXT NOT NULL,"
                " kind TEXT NOT NULL,"
                " target_id TEXT NOT NULL DEFAULT '',"
                " requester_qq TEXT NOT NULL DEFAULT '',"
                " chat_id TEXT NOT NULL DEFAULT '',"
                " flag TEXT NOT NULL DEFAULT '',"
                " status TEXT NOT NULL DEFAULT 'pending',"
                " created_at REAL NOT NULL,"
                " expires_at REAL NOT NULL)"
            )

    def _settings(self, account_id: str) -> Settings:
        return Settings(self.db, account_id=account_id)

    def _profile(self, rt, requester: str, chat_id: str) -> dict:
        pipeline = getattr(rt, "pipeline", None)
        permissions = getattr(pipeline, "permissions", None)
        if permissions is None:
            return {}
        msg = InboundMessage(
            account_id=rt.id,
            chat_id=chat_id or f"private:{requester}",
            chat_type=ChatType.PRIVATE if (chat_id or "").startswith("private:") else ChatType.GROUP,
            sender_qq=requester,
            text="",
            event_at=time.time(),
        )
        return permissions.profile_for(msg, str(self._settings(rt.id).get("security.owner_qq") or ""))

    def _allowed(self, rt, requester: str, cap: str, chat_id: str = "") -> bool:
        return bool(self._profile(rt, requester, chat_id).get(cap))

    def _whitelist_add(self, account_id: str, key: str, value: str) -> None:
        value = str(value or "").strip()
        if not value:
            return
        st = self._settings(account_id)
        current = list(st.get(key) or [])
        if value not in current:
            current.append(value)
            st.set(key, current[-500:])

    def _whitelist_remove(self, account_id: str, key: str, value: str) -> None:
        value = str(value or "").strip()
        if not value:
            return
        st = self._settings(account_id)
        current = [x for x in (st.get(key) or []) if str(x) != value]
        st.set(key, current)

    async def _model_text(self, rt, system: str, user: str, *, max_tokens: int = 80) -> str:
        llm = getattr(getattr(rt, "pipeline", None), "llm", None)
        if llm is None:
            return ""
        try:
            reply = await llm.chat(
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=max_tokens,
                temperature=0.4,
            )
            return str(getattr(reply, "text", "") or "").strip()[:300]
        except Exception as e:  # noqa: BLE001
            log.warning("访问确认模型失败：%s", e)
            return ""

    async def _question(self, rt, subject: str) -> str:
        st = self._settings(rt.id)
        title = str(st.get("chat.owner_title") or "主人")
        text = await self._model_text(
            rt,
            f"你是聊天里的 bot。用一句自然中文询问对方是否同意，不要解释，不要 Markdown。对方是你的{title}或经{title}授权的人。",
            f"事件：{subject}。只输出一句询问。",
        )
        return text or f"{subject}，要同意吗？"

    async def _classify(self, rt, text: str) -> str:
        result = await self._model_text(
            rt,
            "判断用户是否同意一个访问请求。只回答 YES、NO 或 UNCLEAR，不要输出别的。",
            f"用户回复：{text[:300]}",
            max_tokens=5,
        )
        val = result.upper()
        if "YES" in val or "同意" in result or "可以" in result:
            return "YES"
        if "NO" in val or "拒绝" in result or "不行" in result:
            return "NO"
        return "UNCLEAR"

    async def _create(self, rt, *, kind: str, target_id: str, requester: str, chat_id: str,
                      flag: str, subject: str) -> bool:
        now = time.time()
        self.db.execute(
            "UPDATE access_requests SET status='expired' WHERE account_id=? AND status='pending' AND expires_at<?",
            [rt.id, now],
        )
        self.db.execute(
            "INSERT INTO access_requests "
            "(account_id,kind,target_id,requester_qq,chat_id,flag,status,created_at,expires_at) "
            "VALUES (?,?,?,?,?,?,'pending',?,?)",
            [rt.id, kind, str(target_id), str(requester), chat_id, flag, now, now + PENDING_TTL],
        )
        question = await self._question(rt, subject)
        ok = await self.manager.send_to_chat(rt.id, chat_id, question)
        if not ok:
            log.warning("访问确认问题发送失败：%s / %s", rt.id, chat_id)
        return True

    async def handle_event(self, acc_id: str, event: dict) -> bool:
        rt = self.manager.accounts.get(acc_id)
        if rt is None or rt.pipeline is None:
            return False
        post = str(event.get("post_type") or "")
        self_id = str(event.get("self_id") or "")

        if post == "request":
            req = str(event.get("request_type") or "")
            requester = str(event.get("user_id") or "")
            flag = str(event.get("flag") or "")
            if req == "group":
                group_id = str(event.get("group_id") or "")
                if not group_id or not requester:
                    return True
                if not self._allowed(rt, requester, "invite_group", f"private:{requester}"):
                    return True
                return await self._create(
                    rt, kind="group_invite", target_id=group_id, requester=requester,
                    chat_id=f"private:{requester}", flag=flag,
                    subject=f"有人邀请我加入群 {group_id}",
                )
            if req == "friend":
                if not requester:
                    return True
                if not self._allowed(rt, requester, "recommend_contact", f"private:{requester}"):
                    return True
                return await self._create(
                    rt, kind="friend_request", target_id=requester, requester=requester,
                    chat_id=f"private:{requester}", flag=flag,
                    subject=f"有人推荐/请求加我好友 QQ {requester}",
                )

        if post == "notice":
            notice = str(event.get("notice_type") or "")
            if notice == "group_increase" and self_id and str(event.get("user_id") or "") == self_id:
                group_id = str(event.get("group_id") or "")
                requester = str(event.get("operator_id") or event.get("invitor_id") or "")
                if group_id and requester and self._allowed(rt, requester, "invite_group", f"group:{group_id}"):
                    return await self._create(
                        rt, kind="group_join", target_id=group_id, requester=requester,
                        chat_id=f"group:{group_id}", flag="",
                        subject=f"我被拉进群 {group_id}",
                    )
                return True

            if notice == "friend_add":
                peer = str(event.get("user_id") or "")
                if peer:
                    return await self._create(
                        rt, kind="friend_add", target_id=peer, requester=peer,
                        chat_id=f"private:{peer}", flag="",
                        subject=f"我通过了 QQ {peer} 的好友请求",
                    )
                return True

            if notice == "group_decrease" and self_id and str(event.get("user_id") or "") == self_id:
                group_id = str(event.get("group_id") or "")
                if group_id:
                    self._whitelist_remove(rt.id, "security.group_whitelist", group_id)
                    log.info("已退出群 %s，同步移除白名单", group_id)
                return True

            if notice in ("friend_remove", "friend_decrease"):
                peer = str(event.get("user_id") or "")
                if peer:
                    self._whitelist_remove(rt.id, "security.private_whitelist", peer)
                return True
        return False

    def _pending_for(self, acc_id: str, msg: InboundMessage) -> dict | None:
        row = self.db.query_one(
            "SELECT * FROM access_requests WHERE account_id=? AND status='pending' "
            "AND expires_at>? AND (chat_id=? OR (requester_qq=? AND chat_id LIKE 'private:%')) "
            "ORDER BY id DESC LIMIT 1",
            [acc_id, time.time(), msg.chat_id, msg.sender_qq],
        )
        return dict(row) if row else None

    async def _execute(self, rt, pending: dict, ok: bool) -> None:
        kind = pending["kind"]
        target = str(pending["target_id"] or "")
        requester = str(pending["requester_qq"] or "")
        account_id = rt.id
        client = rt.client

        if kind == "group_invite":
            if client is not None and pending["flag"]:
                await client.call("set_group_add_request", {
                    "flag": pending["flag"], "sub_type": "invite", "approve": ok,
                })
            if ok:
                self._whitelist_add(account_id, "security.group_whitelist", target)
        elif kind == "friend_request":
            if client is not None and pending["flag"]:
                await client.call("set_friend_add_request", {
                    "flag": pending["flag"], "approve": ok,
                })
            if ok:
                self._whitelist_add(account_id, "security.private_whitelist", target or requester)
        elif kind in ("group_join",):
            if ok:
                self._whitelist_add(account_id, "security.group_whitelist", target)
            else:
                self._whitelist_remove(account_id, "security.group_whitelist", target)
        elif kind in ("friend_add",):
            if ok:
                self._whitelist_add(account_id, "security.private_whitelist", target)
        elif kind == "leave_group":
            if client is not None and target:
                await client.call("set_group_leave", {"group_id": int(target) if target.isdigit() else target})
            self._whitelist_remove(account_id, "security.group_whitelist", target)
        elif kind == "delete_friend":
            if client is not None and target:
                await client.call("delete_friend", {"user_id": int(target) if target.isdigit() else target})
            self._whitelist_remove(account_id, "security.private_whitelist", target)

        self.db.execute(
            "UPDATE access_requests SET status=? WHERE id=?",
            ["accepted" if ok else "rejected", pending["id"]],
        )

    async def handle_message(self, acc_id: str, rt, msg: InboundMessage) -> bool:
        pending = self._pending_for(acc_id, msg)
        if pending is not None:
            if str(msg.sender_qq) not in (str(pending["requester_qq"]), str(self._settings(acc_id).get("security.owner_qq") or "")):
                return False
            verdict = await self._classify(rt, msg.text or "")
            if verdict == "UNCLEAR":
                return False
            await self._execute(rt, pending, verdict == "YES")
            answer = await self._model_text(
                rt,
                "用一句自然中文回复对方，不要解释流程，不要 Markdown。",
                f"用户对访问请求的回答是：{msg.text[:200]}。现在{'同意并已处理' if verdict == 'YES' else '拒绝并已处理'}。",
                max_tokens=60,
            ) or ("好，已处理。" if verdict == "YES" else "好，已取消。")
            await self.manager.send_to_chat(acc_id, msg.chat_id, answer)
            return True

        text = (msg.text or "").strip()
        if not text:
            return False
        if msg.chat_type is ChatType.GROUP and re.search(r"(退群|离开这个群|别在这个群)", text):
            if not self._allowed(rt, str(msg.sender_qq), "invite_group", msg.chat_id):
                return False
            return await self._create(
                rt, kind="leave_group", target_id=str(msg.group_id or ""), requester=str(msg.sender_qq),
                chat_id=msg.chat_id, flag="", subject="要让我退出这个群吗",
            )
        if re.search(r"(删好友|删除联系人|删了.*好友|把.*删掉)", text):
            if not self._allowed(rt, str(msg.sender_qq), "recommend_contact", msg.chat_id):
                return False
            target = ""
            if msg.at_list:
                target = str(msg.at_list[0])
            else:
                m = re.search(r"\b(\d{5,12})\b", text)
                if m:
                    target = m.group(1)
            if not target:
                return False
            return await self._create(
                rt, kind="delete_friend", target_id=target, requester=str(msg.sender_qq),
                chat_id=msg.chat_id, flag="", subject=f"要删除好友 {target} 吗",
            )
        return False
