"""核心数据类。

所有模块之间传的就是这几个对象，不传裸 dict。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ChatType(StrEnum):
    PRIVATE = "private"
    GROUP = "group"
    TEMP = "temp"


class MsgType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    VIDEO = "video"
    FILE = "file"
    FORWARD = "forward"
    CARD = "card"


class SenderLevel(StrEnum):
    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"


# ---------------------------------------------------------------------------
# 入站消息
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MediaItem:
    """一条入站媒体；只存地址/元数据，字节在需要时下载。"""

    kind: str = "image"       # image / sticker / voice / video
    url: str = ""
    file: str = ""
    mime: str = ""
    size: int = 0
    duration: float = 0.0
    name: str = ""


@dataclass(slots=True)
class InboundMessage:
    """一条进来的消息。所有接入层都映射成这个。"""

    # 身份
    platform: str = "qq"
    account_id: str = ""
    # 来源
    chat_id: str = ""
    chat_type: ChatType = ChatType.PRIVATE
    group_id: str | None = None
    group_name: str | None = None
    # 说话人
    sender_qq: str = ""
    sender_uin: str | None = None
    sender_nick: str | None = None
    sender_card: str | None = None
    sender_remark: str | None = None
    sender_level: SenderLevel = SenderLevel.MEMBER
    # 内容
    text: str = ""
    message_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    msg_type: MsgType = MsgType.TEXT
    reply_to: int | None = None
    reply_message_id: str = ""
    at_list: list[str] = field(default_factory=list)
    media: list[MediaItem] = field(default_factory=list)
    # 戳一戳：不是普通消息，Attention 当唤醒处理；text 里带说明
    poke: bool = False
    # 时间
    event_at: float = field(default_factory=time.time)
    recv_at: float = field(default_factory=time.time)

    @property
    def is_group(self) -> bool:
        return self.chat_type == ChatType.GROUP

    @property
    def is_private(self) -> bool:
        return self.chat_type == ChatType.PRIVATE


# ---------------------------------------------------------------------------
# 出站消息
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OutboundMessage:
    """要发出去的一条。"""

    chat_id: str
    text: str = ""
    # 语音字节（有就发语音）
    voice: bytes | None = None
    # 只发语音，不重复发文字
    voice_only: bool = False
    # 语音发送失败时回退的文字
    voice_fallback_text: str = ""
    # 图片路径
    image: str | None = None
    # 普通文件路径 / 文件名（OneBot 文件上传）
    file: str | None = None
    file_name: str = ""
    # 表情名
    sticker: str | None = None
    # QQ 收藏表情的资源 ID；有它时优先走 send_custom_face
    sticker_emoji_id: str = ""
    # 要不要 @ 某人
    at: list[str] = field(default_factory=list)
    # 引用回复
    reply_to: str | None = None


# ---------------------------------------------------------------------------
# 记忆
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Episode:
    id: int = 0
    account_id: str = ""
    platform: str = "qq"
    chat_id: str = ""
    chat_type: str = "private"
    group_id: str | None = None
    group_name: str | None = None
    sender_qq: str = ""
    sender_nick: str | None = None
    sender_card: str | None = None
    sender_level: int = 0
    text: str = ""
    raw: str | None = None
    msg_type: str = "text"
    reply_to: int | None = None
    at_list: str | None = None
    event_at: float = 0.0
    recv_at: float = 0.0
    distilled: int = 0
    gone: int = 0


@dataclass(slots=True)
class Emotion:
    id: int = 0
    episode_id: int = 0
    sender_qq: str = ""
    chat_id: str = ""
    emotion: str = "neutral"
    valence: float = 0.0
    arousal: float = 0.3
    confidence: float = 0.3
    source: str = "rule"
    created_at: float = 0.0


@dataclass(slots=True)
class Fact:
    id: int = 0
    account_id: str = ""
    subject_type: str = "person"
    subject_id: str = ""
    predicate: str | None = None
    object: str | None = None
    text: str = ""
    tier: int = 2
    valid_from: float | None = None
    valid_until: float | None = None
    recorded_at: float = 0.0
    superseded_by: int | None = None
    supersedes: int | None = None
    hits: int = 0
    last_hit: float | None = None
    source_chat: str | None = None
    source_ep: int | None = None
    confidence: float = 0.8
    gone: int = 0


@dataclass(slots=True)
class Person:
    qq: str = ""
    account_id: str = ""
    platform: str = "qq"
    name: str | None = None
    alias: str | None = None
    nickname_hist: str | None = None
    card_hist: str | None = None
    style: str | None = None
    persona: str | None = None
    handle: str | None = None
    tags: str | None = None
    msg_count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    is_owner: int = 0
    relation_tag: str | None = None

    @property
    def display(self) -> str:
        return self.alias or self.name or self.qq


# ---------------------------------------------------------------------------
# 人格
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Trait:
    name: str
    weight: float = 0.5
    trigger: str = ""


@dataclass(slots=True)
class SpeechStyle:
    length: str = "medium"  # short / medium / long
    punctuation: str = ""
    habits: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    sass: float = 0.5
    emoji_level: float = 0.3


@dataclass(slots=True)
class ExampleDialogue:
    user: str
    assistant: str
    note: str | None = None


@dataclass(slots=True)
class PersonaCard:
    id: str = ""
    account_id: str = ""
    name: str = ""
    avatar: str = ""
    alias: list[str] = field(default_factory=list)
    identity: str = ""
    background: str = ""
    traits: list[Trait] = field(default_factory=list)
    speech: SpeechStyle = field(default_factory=SpeechStyle)
    boundaries: list[str] = field(default_factory=list)
    examples: list[ExampleDialogue] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    version: int = 1


@dataclass(slots=True)
class RuntimeState:
    mood: float = 0.0
    affection: float = 0.3
    closeness: float = 0.2
    energy: float = 0.7
    patience: float = 0.6

    def decay(self, dt: float, baseline: dict[str, float] | None = None) -> None:
        """往基线回，每秒调用一次。"""
        base = baseline or {}
        rate = 0.001 * dt
        for f in ("mood", "affection", "closeness", "energy", "patience"):
            cur = getattr(self, f)
            tgt = base.get(f, 0.0 if f == "mood" else 0.5)
            setattr(self, f, cur + (tgt - cur) * rate)


@dataclass(slots=True)
class PersonaSnapshot:
    """人格的当前快照。选语音、拼 prompt 都用这一份。"""

    persona_id: str = ""
    traits: dict[str, float] = field(default_factory=dict)
    style: SpeechStyle = field(default_factory=SpeechStyle)
    state: RuntimeState = field(default_factory=RuntimeState)
    matched_keywords: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScoredItem:
    """检索结果带分。"""

    kind: str  # fact / episode / gist / person
    ref_id: str
    text: str
    score: float = 0.0
    tier: int = 2
    event_at: float = 0.0
    hits: int = 0
    similarity: float = 0.0
    valence: float | None = None
    arousal: float | None = None


# ---------------------------------------------------------------------------
# 语音
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class VoiceRef:
    id: str = ""
    account_id: str = ""
    persona_id: str = ""
    name: str = ""
    engine: str = "gptsovits"
    audio_path: str = ""
    duration: float = 0.0
    prompt_text: str = ""
    params: dict = field(default_factory=dict)
    weight: float = 1.0
    tags: list[str] = field(default_factory=list)
    min_chars: int = 0
    max_chars: int = 9999
    emotions: list[str] = field(default_factory=list)
    condition: dict | None = None
    enabled: bool = True
    note: str = ""
