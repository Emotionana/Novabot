"""对话核主流程。

把各个模块串起来走一遍：
  收消息 → 注意筛 → 指代解析 → 记忆检索 → 人格装配 → 调模型 → 输出编排 → 写回

核心不该知道 QQ 是什么、模型是谁，只认接口。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field

from ..actions import ActionRouter
from ..adapters.llm.base import Reply
from ..file_service import FileService
from ..life_service import LifeService
from ..media_service import MediaService
from ..models import ChatType, InboundMessage, OutboundMessage
from ..permissions import PermissionService
from ..privacy import PrivacyGuard
from ..stickers import StickerService
from ..truth import TruthPolicy
from .attention import Action, Attention, ChatState, Decision
from .tokens import CallRecord, TokenMeter, count_tokens

log = logging.getLogger(__name__)

# 单条消息的字符上限，超了截断
MAX_TEXT_LEN = 8000

# 判相关性用的极小提示：只让它给一个分，别写理由
JUDGE_SYSTEM = "你判断群消息需不需要机器人接话。只输出一个 0-100 的整数，不要解释。"
JUDGE_TMPL = "机器人叫「{bot}」。群名「{group}」。消息：{text}"

# 主人私聊的额外提示（通用）：默认不追加任何人格、关系或越权锚点。


@dataclass
class TurnResult:
    """一次对话的结果。"""

    replied: bool = False
    decision: Decision | None = None
    text: str = ""
    messages: list[OutboundMessage] = field(default_factory=list)
    # 统计
    tokens: int = 0
    duration: float = 0.0
    episode_id: int | None = None
    bot_episode_id: int | None = None
    error: str | None = None


class Pipeline:
    """对话主流程。

    依赖全部从外面注入，方便替换和测试。
    """

    def __init__(
        self,
        *,
        settings,
        memory,
        persona=None,
        llm=None,
        output=None,
        meter: TokenMeter | None = None,
        owner_qq: str = "",
        bot_name: str = "",
        voice=None,
        gateway=None,
    ):
        self.settings = settings
        self.memory = memory
        self.persona = persona
        self.llm = llm
        self.output = output
        self.meter = meter or TokenMeter()
        self.owner_qq = owner_qq or (settings.get("security.owner_qq") or "")
        self.bot_name = bot_name or "self"
        self.voice = voice
        self.gateway = gateway

        self.attention = Attention(settings)
        self.privacy = PrivacyGuard(getattr(settings, "db", None), settings)
        self.permissions = PermissionService(getattr(settings, "db", None), settings) if getattr(settings, "db", None) else None
        self.truth = TruthPolicy(settings)
        self.actions = ActionRouter(getattr(settings, "db", None), settings, gateway, llm) if getattr(settings, "db", None) else None
        self.stickers = StickerService(getattr(settings, "db", None), settings, getattr(settings, "account_id", ""), llm=llm) if getattr(settings, "db", None) else None
        self.media = MediaService(getattr(settings, "db", None), settings, llm) if getattr(settings, "db", None) else None
        self.files = FileService(getattr(settings, "db", None), settings, media=self.media) if getattr(settings, "db", None) else None
        self.life = LifeService(getattr(settings, "db", None), settings) if getattr(settings, "db", None) else None
        self._resolver = None
        # chat_id -> 我上一条消息的 episode id
        self._last_bot_reply: dict[str, int] = {}
        # chat_id -> ChatState
        self._states: dict[str, ChatState] = {}

    # ==================================================================
    # 主入口
    # ==================================================================

    # ==================================================================
    # 聊天接入白名单
    # ==================================================================

    @staticmethod
    def _id_set(value) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            raw = re.split(r"[,，;；\s]+", value)
        elif isinstance(value, (list, tuple, set)):
            raw = value
        else:
            raw = [value]
        out = set()
        for item in raw:
            text = str(item or "").strip()
            for prefix in ("private:", "group:", "qq:"):
                if text.startswith(prefix):
                    text = text[len(prefix):]
            if text:
                out.add(text)
        return out

    def _chat_allowed(self, msg: InboundMessage) -> bool:
        """私聊/群聊接入白名单。主人永远允许私聊（除非 off）。"""
        is_owner = bool(self.owner_qq) and msg.sender_qq == self.owner_qq
        if msg.chat_type == ChatType.PRIVATE:
            mode = str(self.settings.get("chat.private_access") or "all")
            if mode == "off":
                return False
            if mode == "owner":
                return is_owner
            if mode == "whitelist":
                permissions = getattr(self, "permissions", None)
                is_admin = bool(permissions and permissions.is_admin(msg.sender_qq))
                return is_owner or is_admin or msg.sender_qq in self._id_set(
                    self.settings.get("security.private_whitelist")
                )
            return True

        mode = str(self.settings.get("chat.group_access") or "all")
        if mode == "off":
            return False
        if mode == "whitelist":
            group = str(msg.group_id or "")
            if not group and msg.chat_id.startswith("group:"):
                group = msg.chat_id.split(":", 1)[1]
            return group in self._id_set(self.settings.get("security.group_whitelist"))
        return True

    def _known_bot_qqs(self) -> set[str]:
        ids: set[str] = set()
        db = getattr(self.settings, "db", None)
        if db is not None:
            try:
                for row in db.query("SELECT bot_qq FROM accounts WHERE bot_qq IS NOT NULL AND bot_qq != ''"):
                    ids.add(str(row["bot_qq"]))
            except Exception:  # noqa: BLE001
                pass
        try:
            own = str(self.settings.get("gateway.bot_qq") or "")
            if own:
                ids.add(own)
        except Exception:  # noqa: BLE001
            pass
        return ids

    def _is_other_bot(self, msg: InboundMessage) -> bool:
        if msg.chat_type != ChatType.GROUP:
            return False
        return str(msg.sender_qq or "") in self._known_bot_qqs()

    async def steer(self, msg: InboundMessage) -> bool:
        """私聊任务执行中：把新消息直接注入当前 DSH 回合。"""
        steer = getattr(self.llm, "steer", None)
        if not callable(steer):
            return False
        # 任务执行中注入的图片/表情，也要先走媒体理解，不能只把“[图片]”塞进会话。
        if msg.media and self.media is not None:
            try:
                extra = await self.media.enrich(msg)
                if extra:
                    msg.text = (msg.text + "\n" + extra).strip()
            except Exception as e:  # noqa: BLE001
                log.warning("steer 媒体理解失败：%s", e)
        try:
            ok = await steer(
                text=msg.text or "",
                session_id=msg.chat_id,
                account_id=msg.account_id,
            )
        except Exception as e:  # noqa: BLE001
            log.debug("私聊 steer 失败：%s", e)
            return False
        if ok and self.memory is not None:
            try:
                self.memory.remember(msg)
            except Exception as e:  # noqa: BLE001
                log.debug("steer 消息记忆失败：%s", e)
        return bool(ok)

    async def handle(self, msg: InboundMessage, *, progress=None) -> TurnResult:
        """处理一条消息；progress 只用于长思考进度，不参与业务判断。"""
        start = time.time()
        result = TurnResult()

        try:
            # 0. 超长消息截断（转发大段文本、贴整本书、恶意刷屏）
            if msg.text and len(msg.text) > MAX_TEXT_LEN:
                log.warning(
                    "超长消息 %d 字，截断到 %d", len(msg.text), MAX_TEXT_LEN
                )
                msg.text = msg.text[:MAX_TEXT_LEN] + "…"

                # 0.5 聊天白名单：不在允许范围的消息直接丢弃，不记账也不回
            if not self._chat_allowed(msg):
                result.error = "不在聊天白名单"
                log.info("聊天白名单拦截：%s / %s", msg.chat_id, msg.sender_qq)
                return result

        # 1. 先记账（不管回不回都要存）
            if self.media is not None:
                try:
                    extra = await self.media.enrich(msg)
                    if extra:
                        msg.text = (msg.text + "\n" + extra).strip()
                except Exception as e:  # noqa: BLE001
                    log.warning("媒体理解失败：%s", e)

            if self.files is not None:
                try:
                    file_note = await self.files.ingest(msg)
                    if file_note:
                        msg.text = (msg.text + "\n" + file_note).strip()
                except Exception as e:  # noqa: BLE001
                    log.warning("文件接收失败：%s", e)

            if self.stickers is not None:
                try:
                    profile = self.permissions.profile_for(msg, self.owner_qq) if self.permissions else {}
                    await self.stickers.collect(msg, allowed=bool(profile.get("sticker_collect")))
                except Exception as e:  # noqa: BLE001
                    log.debug("收表情失败：%s", e)

            ep_id = self.memory.remember(msg) if self.memory else None
            result.episode_id = ep_id
            if progress is not None and ep_id is not None:
                progress.episode_id = ep_id

            # 2. 注意筛
            state = self._state_of(msg.chat_id)
            is_owner = bool(self.owner_qq) and msg.sender_qq == self.owner_qq
            if self._is_other_bot(msg):
                try:
                    ignore_bots = bool(self.settings.get("reply.ignore_other_bots"))
                except Exception:  # noqa: BLE001
                    ignore_bots = True
                if ignore_bots:
                    log.info("忽略其他 bot 的群消息：%s / %s", msg.chat_id, msg.sender_qq)
                    return result
            decision = self.attention.judge(
                msg, state, is_owner=is_owner,
                last_bot_reply_id=self._last_bot_reply.get(msg.chat_id),
                bot_names=self._bot_names(msg.account_id),
                defer_commit=True,
            )
            result.decision = decision

            # 3. 判不出才问模型；其余按不回处理
            if decision.action == Action.DEFER:
                if not await self._defer_should_reply(msg):
                    log.debug("不回：模型判定不接（%s）", decision.rule)
                    return result
                decision.reason = f"{decision.reason}（模型判定要接）"
            elif decision.action != Action.REPLY:
                log.debug("不回：%s（%s）", decision.reason, decision.rule)
                return result

            # 无论是必回、被叫到、接话还是模型判定要回，都先等防抖：
            # 等对方把话说完，后面还有“要回”的消息就只回最后一条。
            if not await self._wait_debounce(msg, state):
                log.info("防抖：%s 后面还有新消息，这条不回", msg.chat_id)
                return result
            self.attention.commit_pending_rate()

            # 4. 组装 prompt（历史里排除刚写进去的这条，否则会重复）
            # 明确自然语言命令：本地能执行就不绕过模型瞎答
            if self.actions is not None:
                try:
                    action = await self.actions.handle(msg, is_owner=is_owner)
                except Exception as e:  # noqa: BLE001
                    log.warning("动作识别失败：%s", e)
                    action = None
                if action is not None:
                    text = action.text.strip()
                    result.replied = True
                    result.text = text
                    result.messages = self._format_out(msg, text) if text else []
                    if getattr(action, "sticker", ""):
                        path = self.stickers.image(str(action.sticker)) if self.stickers else None
                        if path is not None:
                            result.messages.append(OutboundMessage(chat_id=msg.chat_id, sticker=str(path)))
                    self._remember_self_reply(msg, text or "（表情包）", result)
                    state.last_reply_at = time.time()
                    return result

            privacy = self.privacy.policy(msg, self.owner_qq)
            system, messages = await self._build_prompt(
                msg, is_owner=is_owner, exclude_id=result.episode_id
            )
            history_text = "\n".join(_content_of(m) for m in messages[-6:])
            work = self.truth.work_context(
                msg.text, history_text, is_owner=is_owner, is_private=msg.is_private
            ) if self.truth else False
            if self.truth:
                system = (system + "\n\n" + self.truth.prompt(work=work)).strip()
            tool_stats = {"calls": 0, "results": 0, "errors": 0}
            in_tokens = count_tokens(system) + sum(
                count_tokens(_content_of(m)) for m in messages
            )

            # 5. 调模型
            if self.llm is None:
                result.error = "没有配模型"
                return result

            tool_ctx = None
            tool_defs: list[dict] = []
            if self.settings.get("tools.enabled"):
                caps = getattr(self.llm, "capabilities", None)
                caps = caps() if callable(caps) else None
                if caps is None or getattr(caps, "tools", False):
                    from ..tools import ToolContext, resolve_tz, tool_specs

                    db = getattr(self.settings, "db", None) or getattr(self.memory, "db", None)
                    if db is not None:
                        tool_ctx = ToolContext(
                            db=db,
                            account_id=msg.account_id,
                            chat_id=msg.chat_id,
                            person_qq=msg.sender_qq,
                            is_owner=is_owner,
                            now=time.time(),
                            tz=resolve_tz(self.settings),
                            permissions=self.permissions.profile_for(msg, self.owner_qq) if self.permissions else {},
                        )
                        tool_defs = tool_specs(tool_ctx, self.settings)

            permission = self.permissions.profile_for(msg, self.owner_qq) if self.permissions else None
            reply: Reply = await self.llm.chat(
                system=system,
                messages=messages,
                tools=tool_defs or None,
                permissions=permission,
                session_id=msg.chat_id,
                account_id=msg.account_id,
                max_tokens=self.settings.get("llm.max_tokens"),
                temperature=self.settings.get("llm.temperature"),
                progress=progress,
            )

            # 工具循环：最多 2 轮，每轮最多 max_per_call 个，结果截断
            if reply.ok and reply.tool_calls and tool_ctx is not None and tool_defs:
                from ..tools import execute_tool

                max_calls = int(self.settings.get("tools.max_per_call") or 5)
                rounds = 0
                while reply.ok and reply.tool_calls and rounds < 2:
                    rounds += 1
                    calls = list(reply.tool_calls)[:max_calls]
                    messages.append(
                        {
                            "role": "assistant",
                            "content": reply.text or "",
                            "tool_calls": [
                                {
                                    "id": c.id or f"call_{rounds}_{i}",
                                    "type": "function",
                                    "function": {
                                        "name": c.name,
                                        "arguments": json.dumps(
                                            c.arguments or {}, ensure_ascii=False
                                        ),
                                    },
                                }
                                for i, c in enumerate(calls)
                            ],
                        }
                    )
                    for c in calls:
                        output = execute_tool(
                            c.name, c.arguments or {}, tool_ctx, self.settings
                        )
                        tool_stats["calls"] += 1
                        tool_stats["results"] += 1
                        if any(x in output for x in ("失败", "错误", "不允许", "没启用", "不能", "超时")):
                            tool_stats["errors"] += 1
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": c.id or c.name,
                                "content": output[:500],
                            }
                        )
                    reply = await self.llm.chat(
                        system=system,
                        messages=messages,
                        tools=tool_defs,
                        session_id=msg.chat_id,
                        account_id=msg.account_id,
                        max_tokens=self.settings.get("llm.max_tokens"),
                        temperature=self.settings.get("llm.temperature"),
                        progress=progress,
                    )
                if reply.ok and reply.tool_calls:
                    result.error = "工具调用轮次超限"
                    return result
                in_tokens = count_tokens(system) + sum(
                    count_tokens(_content_of(m)) for m in messages
                )

            if not reply.ok:
                result.error = reply.error
                return result

            text = (reply.text or "").strip()
            raw = getattr(reply, "raw", None)
            raw_tools = raw.get("tools") if isinstance(raw, dict) else None
            if isinstance(raw_tools, dict):
                for k in ("calls", "results", "errors"):
                    tool_stats[k] = max(int(tool_stats.get(k) or 0), int(raw_tools.get(k) or 0))
            if text and self.truth:
                fixed = self.truth.review(text, work=work, tools=tool_stats)
                if fixed != text:
                    log.warning("真实性拦截：chat=%s 原文=%s", msg.chat_id, text[:120])
                    text = fixed
            if text:
                blocked = self.privacy.check_output(text, msg, privacy)
                if blocked is not None:
                    text = blocked
            if not text:
                # 模型返回空：可能是拒答、可能是 content filter
                # 标成错误，别让上层以为是"正常不回"
                result.error = "模型返回空内容"
                log.warning("模型返回空：chat=%s", msg.chat_id)
                return result

            # 6. 输出编排（模型也可以写 <sticker:开心> / <file:文件名>）
            text, file_refs = self._extract_file_tags(text)
            manual_sticker = None
            marker = re.search(r"<sticker:([^>]{1,20})>", text)
            if marker and self.stickers is not None and (permission or {}).get("sticker_send"):
                manual_sticker = self.stickers.pick(marker.group(1), msg, owner_qq=self.owner_qq)
                text = (text[:marker.start()] + text[marker.end():]).strip()
            result.replied = True
            result.text = text
            result.messages = self._format_out(msg, text) if text else []
            if manual_sticker:
                path = self.stickers.image(str(manual_sticker.get("md5") or ""))
                if path is not None:
                    result.messages.append(OutboundMessage(chat_id=msg.chat_id, sticker=str(path)))
                    self.stickers.mark_used(str(manual_sticker.get("md5") or ""))
            elif self.stickers is not None:
                # 自动配图由 stickers.auto_send + 概率控制；
                # 不要求普通群成员拥有手动发表情权限，否则默认 tier1 永远发不出来。
                auto = self.stickers.auto_pick(text, msg, owner_qq=self.owner_qq)
                if auto:
                    path = self.stickers.image(str(auto.get("md5") or ""))
                    if path is not None:
                        result.messages.append(OutboundMessage(chat_id=msg.chat_id, sticker=str(path)))
                        self.stickers.mark_used(str(auto.get("md5") or ""))

            self._attach_files(msg, file_refs, result, is_owner=is_owner)

            # 7. 语气对了的话，把回复也做成语音（失败就发文字）
            await self._attach_voice(msg, text, result, is_owner=is_owner)

            # 8. 把回复也写进记忆——不写的话，下一轮模型看不到自己说过什么，
            #    也判断不出「对方在回复我」
            self._remember_self_reply(msg, text, result)

            # 9. 更新状态
            state.last_reply_at = time.time()
            if not state.recent_senders or state.recent_senders[-1] != msg.sender_qq:
                state.recent_senders.append(msg.sender_qq)
                state.recent_senders = state.recent_senders[-10:]

            # 10. 记账（优先用服务端返回的真实用量）
            est_in, est_out = in_tokens, count_tokens(text)
            usage = getattr(reply, "usage", None) or {}
            n_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0) or est_in
            n_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0) or est_out
            result.tokens = n_in + n_out
            self.meter.record(
                CallRecord(
                    label="reply",
                    chat_id=msg.chat_id,
                    account_id=msg.account_id,
                    model=getattr(self.llm, "name", ""),
                    n_in=n_in,
                    n_out=n_out,
                )
            )

        except Exception as e:
            log.exception("处理消息出错")
            result.error = str(e)

        result.duration = time.time() - start
        return result

    # ==================================================================
    # 组装
    # ==================================================================

    def _bot_names(self, account_id: str) -> list[str]:
        """我叫什么：当前人格卡本名 + 别名，给呼语判定用。"""
        names: list[str] = []
        if self.persona is not None:
            with contextlib.suppress(Exception):
                card = self.persona.load_card(account_id=account_id)
                if card is not None:
                    names.append(card.name)
                    names.extend(card.alias or [])
        out: list[str] = []
        for n in names:
            n = str(n).strip()
            if n and n not in out:
                out.append(n)
        return out

    async def _build_prompt(
        self, msg: InboundMessage, *, is_owner: bool = False, exclude_id: int | None = None
    ) -> tuple[str, list[dict]]:
        """拼 system prompt 和对话消息。"""
        parts: list[str] = []

        # 人格
        if self.persona is not None:
            try:
                p = self.persona.build_system_prompt(
                    chat_id=msg.chat_id,
                    sender_qq=msg.sender_qq,
                    text=msg.text,
                    account_id=msg.account_id,
                )
                if p:
                    parts.append(p)
            except Exception as e:
                log.warning("人格装配失败：%s", e)

        # 当前聊天的能力档位
        permission = self.permissions.profile_for(msg, self.owner_qq) if self.permissions else None
        if permission:
            parts.append(permission["prompt"])
            if self.stickers is not None and permission.get("sticker_send"):
                try:
                    n = len(self.stickers.list())
                except Exception:  # noqa: BLE001
                    n = 0
                if n:
                    parts.append(
                        "【表情包】只在确实贴合当前情绪/语境时用，不要为了发而发。"
                        "想发时可在回复里写 <sticker:情绪或场景>；没有合适的就不发。"
                    )

        # 隐私边界：当前会话只能看当前会话
        privacy = self.privacy.policy(msg, self.owner_qq)
        if privacy.enabled:
            parts.append(privacy.prompt())

        # 文件发送标记
        if self.files is not None:
            parts.append(
                "【发文件】确实要发文件时，在回复末尾写 <file:文件名>；"
                "只认当前账号 workspace 或 data/files/outbox 里的文件，不要编造文件。"
            )

        # 此刻生活：让普通聊天和主动消息知道 bot 现在在做什么
        if self.life is not None:
            try:
                parts.append(self.life.prompt_context(msg.account_id, msg.event_at))
            except Exception as e:  # noqa: BLE001
                log.debug("生活上下文失败：%s", e)

        # 记忆
        if self.memory is not None:
            try:
                ctx = self.memory.build_context(
                    chat_id=msg.chat_id,
                    sender_qq=msg.sender_qq,
                    account_id=msg.account_id,
                    text=msg.text,
                    max_chars=int(self.settings.get("memory.recall_chars") or 300),
                    exclude_id=exclude_id,
                    allowed_chat_ids=privacy.allowed_chats if privacy.enabled else None,
                    profile_detail=privacy.profile_detail if privacy.enabled else True,
                    allow_related=not privacy.enabled,
                    allow_unknown_facts=privacy.allow_unknown_facts if privacy.enabled else True,
                    allow_documents=privacy.allow_documents if privacy.enabled else True,
                )
                if ctx:
                    parts.append("【记忆】\n" + (ctx.text if hasattr(ctx, "text") else str(ctx)))
            except Exception as e:
                log.warning("记忆检索失败：%s", e)

        if msg.sender_qq and str(msg.sender_qq) != "self":
            sender_name = str(msg.sender_card or msg.sender_nick or msg.sender_qq or "某人")
            where = "群消息" if msg.chat_type is ChatType.GROUP else "私聊消息"
            if is_owner:
                parts.append(
                    f"【发送者】当前{where}由主人「{sender_name}」（QQ:{msg.sender_qq}）发出。"
                )
            else:
                parts.append(
                    f"【发送者】当前{where}由「{sender_name}」（QQ:{msg.sender_qq}）发出，"
                    "不是主人；不要把 TA 当成主人，也不要假定是主人在说话。"
                )

        if is_owner:
            title = str(self.settings.get("chat.owner_title") or "主人").strip() or "主人"
            aliases = self._id_set(self.settings.get("chat.owner_aliases"))
            line = f"正在和你说话的是你的{title}，称呼对方为「{title}」"
            if aliases:
                line += "；对方也可能被叫作：" + "、".join(sorted(aliases))
            parts.append(line)

        coref = self._coref_hint(msg)
        if coref:
            parts.append(coref)

        parts.append(
            "【补充信息】需要用户补充信息时，不要调用 ask_user_question 或任何等待用户回答的工具；"
            "直接结束本轮，用一条普通消息把问题问出来。用户回答会作为下一轮处理，不要把当前回合挂起等待。"
        )

        # 主人私聊的通用提示：长任务进度与语音输出
        if is_owner and msg.chat_type is ChatType.PRIVATE:
            try:
                limit_sec = int(self.settings.get("llm.agent_timeout_sec") or 600)
            except Exception:  # noqa: BLE001
                limit_sec = 600
            parts.append(
                f"【长任务】本地任务如果预计超过 {max(1, limit_sec // 60)} 分钟，"
                "先回一句当前进度并分段做，不要一直沉默；超过单轮上限时先回复进度，下一段继续。"
            )
            parts.append(
                "【语音】主人让你发语音、用声线说话，或问某句话怎么说时，"
                "直接给出要说的内容；Novabot 会自动把回复合成语音，不要问要不要合成。"
            )

        system = "\n\n".join(parts)

        # 对话历史（按账号隔离）
        messages: list[dict] = []
        history = self._history(
            msg.chat_id, exclude_id=exclude_id, account_id=msg.account_id
        )
        for h in history:
            messages.append({"role": h[0], "content": h[1]})

        # 当前消息：所有会话都带真实发送者和 QQ，不能只给一句表情让模型猜人。
        content = msg.text or ""
        if msg.sender_qq and str(msg.sender_qq) != "self":
            sender_name = str(msg.sender_card or msg.sender_nick or msg.sender_qq or "某人")
            content = f"{sender_name}（QQ:{msg.sender_qq}）：{content}"
        quote = ""
        if getattr(msg, "reply_message_id", ""):
            quote = self._quote_of_message_id(msg)
        elif msg.reply_to:
            quote = self._quote_of(msg.reply_to)
        if quote:
            content = f"{quote}\n{content}"
        messages.append({"role": "user", "content": content})

        # 历史总字数上限：默认 1000 字，防止长会话把 token 拖爆
        limit = int(self.settings.get("llm.history_chars") or 0)
        if limit:
            total = sum(len(m.get("content") or "") for m in messages)
            while total > limit and len(messages) > 1:
                dropped = messages.pop(0)
                total -= len(dropped.get("content") or "")

        return system, messages

    def _coref_hint(self, msg: InboundMessage) -> str:
        """群聊里「他/她」指谁：用 coref 模块解析，只加一行提示。

        解析不出、不是代词、或者指代的就是说话人自己，都不加。
        预算很小（一行），避免 token 膨胀。
        """
        if self.memory is None or msg.chat_type is not ChatType.GROUP:
            return ""
        try:
            if self._resolver is None:
                from ..memory.coref import AliasStore, Resolver

                self._resolver = Resolver(self.memory.db, AliasStore(self.memory.db))
            recent = self.memory.hippocampus.recent(
                msg.chat_id, limit=6, account_id=msg.account_id
            )
            result = self._resolver.resolve_text(
                msg.text or "",
                account_id=msg.account_id,
                chat_id=msg.chat_id,
                recent=recent,
                speaker=msg.sender_qq,
                bot_qq=str(self.settings.get("gateway.bot_qq") or ""),
            )
            if result.method != "pronoun" or not result.qq:
                return ""
            if result.qq == msg.sender_qq:
                return ""
            row = self.memory.db.query_one(
                "SELECT name, alias FROM people WHERE qq = ? AND account_id = ?",
                [result.qq, msg.account_id],
            )
            who = (row["alias"] or row["name"]) if row else result.qq
            return f"（这里的「{result.name}」指 {who}）"
        except Exception:  # noqa: BLE001
            return ""

    def _history(
        self, chat_id: str, *, exclude_id: int | None = None, account_id: str = ""
    ) -> list[tuple[str, str]]:
        """最近几轮对话。从记忆里取，不单独存。

        exclude_id 用来排掉「刚写进去的当前这条」——
        当前消息会在下面单独 append，不排会重复一遍。
        account_id 用来防多开串台：同一个群号在不同账号下是两份历史。
        """
        if self.memory is None:
            return []
        turns = self.settings.get("llm.history_turns") or 0
        if turns <= 0:
            return []

        try:
            eps = self.memory.hippocampus.recent(
                chat_id,
                limit=turns * 2 + (1 if exclude_id else 0),
                account_id=account_id,
            )
        except Exception:
            return []

        out: list[tuple[str, str]] = []
        for e in eps:
            if exclude_id is not None and e.id == exclude_id:
                continue
            if not e.text:
                continue
            role = "assistant" if e.sender_qq == "self" else "user"
            name = e.sender_card or e.sender_nick or e.sender_qq
            out.append((role, e.text if role == "assistant" else f"{name}（QQ:{e.sender_qq}）：{e.text}"))

        if len(out) > turns * 2:
            out = out[-turns * 2:]
        return out

    def _quote_of(self, ep_id: int) -> str:
        """被引用的原消息。"""
        if self.memory is None:
            return ""
        try:
            orig = self.memory.hippocampus.get(ep_id)
        except Exception:
            return ""
        if orig is None:
            return ""
        name = orig.sender_card or orig.sender_nick or orig.sender_qq
        return f"[引用 {name}：{(orig.text or '')[:60]}]"

    def _quote_of_message_id(self, msg: InboundMessage) -> str:
        """引用段只给 QQ message_id；按 raw 里的 message_id 找回本地 episode。"""
        if self.memory is None:
            return ""
        try:
            row = self.memory.db.query_one(
                "SELECT id FROM episodes WHERE account_id = ? AND chat_id = ? "
                "AND CAST(json_extract(raw, '$.message_id') AS TEXT) = ? ORDER BY id DESC LIMIT 1",
                [msg.account_id, msg.chat_id, str(msg.reply_message_id)],
            )
        except Exception:
            return ""
        if row is None:
            return ""
        return self._quote_of(int(row["id"]))

    # ==================================================================
    # 判相关性与回写
    # ==================================================================

    async def _wait_debounce(self, msg: InboundMessage, state: ChatState) -> bool:
        """等对方把话说完；期间又有要回的消息，就只让最后一条回。"""
        try:
            seconds = float(self.settings.get("reply.cooldown_sec") or 0)
        except Exception:  # noqa: BLE001
            seconds = 0
        if seconds <= 0:
            return True
        token = float(msg.recv_at or msg.event_at or time.time())
        key = str(msg.sender_qq or msg.chat_id or "")
        state.debounce_tokens[key] = max(state.debounce_tokens.get(key, 0.0), token)
        state.debounce_token = max(state.debounce_token, token)
        await asyncio.sleep(seconds)
        return state.debounce_tokens.get(key, 0.0) == token

    async def _defer_should_reply(self, msg: InboundMessage) -> bool:
        """判不出时，花一次极小调用问模型。

        失败 / 没模型 / 关掉开关，一律按「不回」处理——
        和 D9 一致：误回的代价比漏回大。
        """
        if self.llm is None:
            return False
        try:
            if not self.settings.get("reply.defer_judge"):
                return False
        except KeyError:
            return False

        prompt = JUDGE_TMPL.format(
            bot=self.bot_name,
            group=msg.group_name or "私聊",
            text=(msg.text or "")[:200],
        )
        try:
            reply = await self.llm.chat(
                system=JUDGE_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                account_id=msg.account_id,
                stateless=True,
                max_tokens=8,
                temperature=0,
            )
        except Exception as e:
            log.warning("判相关性出错，按不回处理：%s", e)
            return False

        if not getattr(reply, "ok", False):
            return False

        raw = (getattr(reply, "text", "") or "").strip()
        match = re.search(r"\d{1,3}", raw)
        if match is None:
            log.debug("判相关性返回看不懂的内容：%r", raw[:40])
            return False

        score = min(100, int(match.group())) / 100.0
        try:
            threshold = float(self.settings.get("reply.relevance_threshold") or 0.5)
        except KeyError:
            threshold = 0.5
        self.meter.record(
            CallRecord(
                label="prescreen",
                chat_id=msg.chat_id,
                account_id=msg.account_id,
                model=getattr(self.llm, "name", ""),
                n_in=count_tokens(prompt),
                n_out=count_tokens(raw),
            )
        )
        log.debug("判相关性 %.0f%%（阈值 %.0f%%）", score * 100, threshold * 100)
        return score >= threshold

    def _remember_self_reply(
        self, msg: InboundMessage, text: str, result: TurnResult
    ) -> None:
        """把 bot 的回复写进 episodes。

        两个用途：
          1. 下一轮拼历史时，模型能看到自己说过什么；
          2. 别人点「回复」这条时，注意筛能认出「在回复我」。
        """
        if self.memory is None:
            return
        try:
            ep_id = self.memory.remember(
                InboundMessage(
                    platform=msg.platform,
                    account_id=msg.account_id,
                    chat_id=msg.chat_id,
                    chat_type=msg.chat_type,
                    group_id=msg.group_id,
                    group_name=msg.group_name,
                    sender_qq="self",
                    sender_nick=self.bot_name,
                    text=text,
                    event_at=time.time(),
                    recv_at=time.time(),
                )
            )
        except Exception as e:
            log.warning("写回复记录失败：%s", e)
            return
        result.bot_episode_id = ep_id
        self.note_bot_reply(msg.chat_id, ep_id)

    @staticmethod
    def _extract_file_tags(text: str) -> tuple[str, list[str]]:
        refs = [m.group(1).strip() for m in re.finditer(r"<file:(.*?)>", text or "", re.S | re.I)]
        clean = re.sub(r"<file:.*?>", "", text or "", flags=re.S | re.I).strip()
        return clean, [r for r in refs if r]

    def _attach_files(self, msg, refs: list[str], result, *, is_owner: bool) -> None:
        if not refs or self.files is None:
            return
        if not self.files.enabled or not self.files._allowed_send(msg.chat_id):
            return
        for ref in refs:
            try:
                path = self.files.resolve_sendable(ref, account_id=msg.account_id)
                if is_owner or not self.files._get("files.require_approval", True):
                    result.messages.append(OutboundMessage(
                        chat_id=msg.chat_id, file=str(path), file_name=path.name,
                    ))
                else:
                    self.files.request(path.name, chat_id=msg.chat_id, account_id=msg.account_id, sender=msg.sender_qq)
            except Exception as e:  # noqa: BLE001
                log.warning("文件发送标记失败（%s）：%s", ref, e)

    async def _attach_voice(self, msg, text: str, result, *, is_owner: bool) -> None:
        """按 reply_mode 决定语音和文字怎么共存；合成失败保留文字。"""
        if self.voice is None:
            return

        mode = self.voice.reply_mode() if hasattr(self.voice, "reply_mode") else "both"
        force = self.voice.is_explicit_ask(msg.text or "") if hasattr(self.voice, "is_explicit_ask") else False
        if hasattr(self.voice, "should_convert") and not self.voice.should_convert(force=force):
            return

        messages = result.messages or []
        text_targets = [m for m in messages if (m.text or "").strip()]
        if not text_targets:
            return

        target = text_targets[0]
        voice_text = text
        if mode == "mixed":
            target = random.choice(text_targets)
            voice_text = target.text

        try:
            budget = float(self.settings.get("voice.reply_budget_sec") or 0)
            call = self.voice.speak(
                account_id=msg.account_id,
                chat_id=msg.chat_id,
                sender_qq=msg.sender_qq,
                text=voice_text,
                user_text=msg.text or "",
                is_owner=is_owner,
            )
            audio = await asyncio.wait_for(call, timeout=budget) if budget else await call
        except TimeoutError:
            log.warning("语音合成超过 %ss，改发文字", budget)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("语音合成出错（改发文字）：%s", e)
            return
        if not audio:
            return

        if mode == "both":
            target.voice = audio
            target.voice_fallback_text = target.text
            return

        if mode == "mixed":
            target.voice_fallback_text = target.text
            target.text = ""
            target.voice = audio
            target.voice_only = True
            return

        # voice：整条回复只发一条语音，文字留作发送失败时回退
        fallback = "\n".join(m.text for m in messages if (m.text or "").strip())
        for item in messages:
            item.text = ""
        target.voice_fallback_text = fallback
        target.voice = audio
        target.voice_only = True

    # ==================================================================
    # 输出
    # ==================================================================

    def _format_out(self, msg: InboundMessage, text: str) -> list[OutboundMessage]:
        """把回复拆成要发的消息。

        不固定回几条：句子/分句/换行自然拆；短句合并，长句才在
        逗号处断开。作者显式写的 ||| 优先。
        """
        if self.output is not None:
            try:
                return self.output.format(msg, text)
            except Exception as e:  # noqa: BLE001
                log.warning("输出编排失败：%s", e)

        from .splitter import split_natural

        if not self.settings.get("output.split_enabled"):
            return [OutboundMessage(chat_id=msg.chat_id, text=text)]
        split_chars = int(self.settings.get("output.split_chars") or 42)
        split_max = int(self.settings.get("output.split_max") or 4)
        if msg.is_group:
            # 群聊单独一套上限（默认 50 字 / 最多 3 条），私聊不受影响
            split_chars = int(self.settings.get("output.group_split_chars") or 50)
            split_max = int(self.settings.get("output.group_split_max") or 3)
        parts = split_natural(text, max_chars=split_chars, max_parts=split_max)
        return [OutboundMessage(chat_id=msg.chat_id, text=p) for p in parts]

    # ==================================================================
    # 状态
    # ==================================================================

    def _state_of(self, chat_id: str) -> ChatState:
        st = self._states.get(chat_id)
        if st is None:
            st = ChatState()
            self._states[chat_id] = st
        return st

    def note_bot_reply(self, chat_id: str, ep_id: int) -> None:
        """记下我上一条消息的 id，用来判断谁在回复我。"""
        self._last_bot_reply[chat_id] = ep_id

    def cleanup(self) -> None:
        """定期清掉过期状态。"""
        self.attention.cleanup()
        now = time.time()
        for chat_id, st in list(self._states.items()):
            if st.last_reply_at and now - st.last_reply_at > 3600:
                self._states.pop(chat_id, None)

    # ==================================================================
    # 调试
    # ==================================================================

    def explain(self, chat_id: str, sender_qq: str, text: str) -> dict:
        """为什么回 / 为什么不回。"""
        state = self._state_of(chat_id)
        is_owner = bool(self.owner_qq) and sender_qq == self.owner_qq

        from ..models import ChatType

        fake = InboundMessage(
            account_id="",
            chat_id=chat_id,
            chat_type=ChatType.PRIVATE if chat_id.startswith("private:") else ChatType.GROUP,
            sender_qq=sender_qq,
            text=text,
            event_at=time.time(),
        )
        d = self.attention.judge(
            fake, state, is_owner=is_owner,
            last_bot_reply_id=self._last_bot_reply.get(chat_id),
            bot_names=self._bot_names(fake.account_id),
        )
        return {
            "action": d.action.value,
            "reason": d.reason,
            "rule": d.rule,
            "importance": round(self.attention.score_importance(fake, is_owner=is_owner), 2),
        }


def _content_of(m) -> str:
    """消息对象可能是 dict 也可能是 Message，统一取内容。"""
    if isinstance(m, dict):
        return str(m.get("content") or "")
    return str(getattr(m, "content", "") or "")
