"""人格系统。

四层：
  人格卡（静态）    身份、性格带权重、说话方式、边界、示例
  关键词触发（动态） 命中时按百分比偏移
  手写区（补充）    用户自己写的片段
  运行时状态（当下） 心情、好感、精力、耐心、亲密
"""

from __future__ import annotations

import copy
import json
import logging
import random
import re
import time
from dataclasses import asdict, dataclass, field

from ..db import Database
from ..models import (
    ExampleDialogue,
    PersonaCard,
    PersonaSnapshot,
    RuntimeState,
    SpeechStyle,
    Trait,
)

log = logging.getLogger(__name__)


@dataclass
class Effect:
    target: str
    delta: float = 0.0
    duration: int = 0


@dataclass
class KeywordRule:
    id: str = ""
    keywords: list[str] = field(default_factory=list)
    match_mode: str = "any"
    effects: list[Effect] = field(default_factory=list)
    chance: float = 1.0
    cooldown: int = 0
    priority: int = 0
    once_per_chat: bool = False
    enabled: bool = True
    note: str = ""


@dataclass
class RawBlock:
    id: int = 0
    title: str = ""
    kind: str = "other"
    content: str = ""
    sort_order: int = 0
    weight: float = 1.0


class PersonaSystem:
    """人格系统的总入口。"""

    def __init__(self, db: Database, settings=None):
        self.db = db
        self.settings = settings
        self._cooldowns: dict[str, dict[str, float]] = {}
        self._once_fired: dict[str, set[str]] = {}

    # ==================================================================
    # 加载
    # ==================================================================

    def _settings_for(self, account_id: str = ""):
        """账号作用域的参数视图；没传账号就用全局。"""
        if not self.settings or not account_id:
            return self.settings
        from ..settings import Settings

        return Settings(self.db, account_id=account_id)

    def load_card(self, persona_id: str = "", account_id: str = "") -> PersonaCard | None:
        settings = self._settings_for(account_id)
        if not persona_id and settings:
            persona_id = settings.get("persona.current") or ""

        if persona_id:
            row = self.db.query_one("SELECT * FROM persona_cards WHERE id = ?", [persona_id])
        else:
            row = self.db.query_one(
                "SELECT * FROM persona_cards ORDER BY updated_at DESC LIMIT 1"
            )
        return _row_to_card(row) if row else None

    def save_card(self, card: PersonaCard) -> None:
        self.db.execute(
            """
            INSERT OR REPLACE INTO persona_cards
              (id, account_id, name, avatar, alias, identity, background, traits, speech,
               boundaries, examples, tags, version, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                card.id, card.account_id, card.name, card.avatar or "",
                json.dumps(card.alias, ensure_ascii=False),
                card.identity, card.background,
                json.dumps([asdict(t) for t in card.traits], ensure_ascii=False),
                json.dumps(asdict(card.speech), ensure_ascii=False),
                json.dumps(card.boundaries, ensure_ascii=False),
                json.dumps([asdict(e) for e in card.examples], ensure_ascii=False),
                json.dumps(card.tags, ensure_ascii=False),
                card.version, time.time(),
            ],
        )

    def load_keywords(self, persona_id: str) -> list[KeywordRule]:
        rows = self.db.query(
            "SELECT * FROM persona_keywords WHERE persona_id = ? AND enabled = 1 "
            "ORDER BY priority DESC",
            [persona_id],
        )
        out: list[KeywordRule] = []
        for r in rows:
            try:
                effects = [Effect(**e) for e in json.loads(r["effects"] or "[]")]
            except Exception:
                effects = []
            out.append(
                KeywordRule(
                    id=r["id"],
                    keywords=json.loads(r["keywords"] or "[]"),
                    match_mode=r["match_mode"] or "any",
                    effects=effects,
                    chance=r["chance"] if r["chance"] is not None else 1.0,
                    cooldown=r["cooldown"] or 0,
                    priority=r["priority"] or 0,
                    once_per_chat=bool(r["once_per_chat"]),
                    note=r["note"] or "",
                )
            )
        return out

    def load_blocks(self, persona_id: str) -> list[RawBlock]:
        rows = self.db.query(
            "SELECT * FROM persona_raw_blocks WHERE persona_id = ? AND enabled = 1 "
            "ORDER BY sort_order, id",
            [persona_id],
        )
        return [
            RawBlock(
                id=r["id"], title=r["title"], kind=r["kind"],
                content=r["content"], sort_order=r["sort_order"],
                weight=r["weight"] if r["weight"] is not None else 1.0,
            )
            for r in rows
        ]

    # ==================================================================
    # 状态
    # ==================================================================

    def load_state(self, persona_id: str, chat_id: str) -> RuntimeState:
        row = self.db.query_one(
            "SELECT * FROM persona_state WHERE persona_id = ? AND chat_id = ?",
            [persona_id, chat_id],
        )
        if row:
            return RuntimeState(
                mood=row["mood"] if row["mood"] is not None else 0.0,
                affection=row["affection"] if row["affection"] is not None else 0.3,
                closeness=row["closeness"] if row["closeness"] is not None else 0.2,
                energy=row["energy"] if row["energy"] is not None else 0.7,
                patience=row["patience"] if row["patience"] is not None else 0.6,
            )

        st = RuntimeState()
        if self.settings:
            st.mood = float(self.settings.get("persona.state_initial_mood") or 0.0)
            st.affection = float(self.settings.get("persona.state_initial_affection") or 0.3)
            st.energy = float(self.settings.get("persona.state_initial_energy") or 0.7)
        return st

    def save_state(self, persona_id: str, chat_id: str, st: RuntimeState) -> None:
        self.db.execute(
            """
            INSERT OR REPLACE INTO persona_state
              (persona_id, chat_id, mood, affection, closeness, energy, patience, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            [persona_id, chat_id, st.mood, st.affection, st.closeness,
             st.energy, st.patience, time.time()],
        )

    def tick_state(self, persona_id: str, chat_id: str, dt: float = 1.0) -> RuntimeState:
        """状态往基线回落。"""
        st = self.load_state(persona_id, chat_id)
        rate = 0.001
        if self.settings:
            rate = float(self.settings.get("persona.state_decay_rate") or 0.001)
        base = {"mood": 0.0, "affection": 0.3, "closeness": 0.2, "energy": 0.7, "patience": 0.6}
        for f in ("mood", "affection", "closeness", "energy", "patience"):
            cur = getattr(st, f)
            tgt = base[f]
            setattr(st, f, cur + (tgt - cur) * rate * dt)
        self.save_state(persona_id, chat_id, st)
        return st

    # ==================================================================
    # 关键词
    # ==================================================================

    def apply_keywords(
        self, text: str, chat_id: str, card: PersonaCard, state: RuntimeState, *, settings=None
    ) -> tuple[PersonaCard, RuntimeState, list[str]]:
        settings = settings or self.settings
        if settings and not settings.get("persona.keyword_enabled"):
            return card, state, []

        rules = self.load_keywords(card.id)
        if not rules:
            return card, state, []

        card = copy.deepcopy(card)
        state = copy.deepcopy(state)
        extras: list[str] = []
        now = time.time()
        matched: list[KeywordRule] = []

        for rule in rules:
            if not self._match(rule, text):
                continue
            if self._in_cooldown(rule, chat_id, now):
                continue
            if rule.once_per_chat and chat_id in self._once_fired.get(rule.id, set()):
                continue
            if rule.chance < 1.0 and random.random() > rule.chance:
                continue
            matched.append(rule)

        for rule in matched:
            for eff in rule.effects:
                self._apply_effect(eff, card, state, extras)
            self._mark_fired(rule, chat_id, now)

        return card, state, extras

    def _match(self, rule: KeywordRule, text: str) -> bool:
        if not rule.keywords:
            return False
        if rule.match_mode == "regex":
            for pat in rule.keywords:
                try:
                    if re.search(pat, text):
                        return True
                except re.error:
                    continue
            return False
        if rule.match_mode == "all":
            return all(k in text for k in rule.keywords)
        return any(k in text for k in rule.keywords)

    def _in_cooldown(self, rule: KeywordRule, chat_id: str, now: float) -> bool:
        if rule.cooldown <= 0:
            return False
        last = self._cooldowns.get(rule.id, {}).get(chat_id, 0)
        return (now - last) < rule.cooldown

    def _mark_fired(self, rule: KeywordRule, chat_id: str, now: float) -> None:
        self._cooldowns.setdefault(rule.id, {})[chat_id] = now
        if rule.once_per_chat:
            self._once_fired.setdefault(rule.id, set()).add(chat_id)
        try:
            self.db.execute(
                "INSERT INTO keyword_fires (rule_id, chat_id, fired_at) VALUES (?,?,?)",
                [rule.id, chat_id, now],
            )
        except Exception as e:
            # 记日志失败不该影响回复，但至少留个痕迹
            log.debug("记关键词触发失败：%s", e)

    def _apply_effect(
        self, eff: Effect, card: PersonaCard, state: RuntimeState, extras: list[str]
    ) -> None:
        t = eff.target

        if t.startswith("trait."):
            name = t.split(".", 1)[1]
            for tr in card.traits:
                if tr.name == name:
                    tr.weight = _clamp(tr.weight * (1 + eff.delta), 0, 1)
                    return
            card.traits.append(
                Trait(name=name, weight=_clamp(eff.delta if eff.delta > 0 else 0.5, 0, 1),
                      trigger="关键词触发")
            )
            return

        if t == "style.length":
            levels = ["short", "medium", "long"]
            cur = card.speech.length if card.speech.length in levels else "medium"
            idx = levels.index(cur)
            if eff.delta > 0:
                idx = min(idx + 1, 2)
            elif eff.delta < 0:
                idx = max(idx - 1, 0)
            card.speech.length = levels[idx]
            return

        if t == "style.sass":
            card.speech.sass = _clamp(card.speech.sass * (1 + eff.delta), 0, 1)
            return

        if t == "style.emoji":
            card.speech.emoji_level = _clamp(card.speech.emoji_level * (1 + eff.delta), 0, 1)
            return

        if t.startswith("state."):
            key = t.split(".", 1)[1]
            if hasattr(state, key):
                cur = getattr(state, key)
                lo = -1 if key == "mood" else 0
                setattr(state, key, _clamp(cur + eff.delta, lo, 1))
            return

        if t == "prompt.extra" and eff.delta:
            extras.append(str(eff.delta))

    # ==================================================================
    # 快照
    # ==================================================================

    def snapshot(
        self,
        *,
        chat_id: str = "",
        sender_qq: str = "",
        text: str = "",
        persona_id: str = "",
        account_id: str = "",
    ) -> PersonaSnapshot | None:
        card = self.load_card(persona_id, account_id)
        if card is None:
            return None

        state = self.load_state(card.id, chat_id or "default")
        card2, state2, extras = self.apply_keywords(
            text, chat_id or "default", card, state, settings=self._settings_for(account_id)
        )

        return PersonaSnapshot(
            persona_id=card2.id,
            traits={t.name: t.weight for t in card2.traits},
            style=card2.speech,
            state=state2,
            matched_keywords=extras,
        )

    # ==================================================================
    # 拼 prompt
    # ==================================================================

    def build_system_prompt(
        self,
        *,
        chat_id: str = "",
        sender_qq: str = "",
        text: str = "",
        persona_id: str = "",
        turn_count: int = 0,
        account_id: str = "",
    ) -> str:
        settings = self._settings_for(account_id)
        card = self.load_card(persona_id, account_id)
        if card is None:
            return ""

        state = self.load_state(card.id, chat_id or "default")
        card, state, extras = self.apply_keywords(
            text, chat_id or "default", card, state, settings=settings
        )

        parts: list[str] = []

        head = f"你是{card.name}。"
        if card.identity:
            head += card.identity
        parts.append(head)
        if card.background:
            parts.append(card.background)

        if card.traits:
            traits = sorted(card.traits, key=lambda t: -t.weight)
            desc = "；".join(
                f"{t.name}（{int(t.weight * 100)}%{'，' + t.trigger if t.trigger else ''}）"
                for t in traits
            )
            parts.append(f"你的性格：{desc}")

        s = card.speech
        line = f"你说话{_length_desc(s.length)}"
        if s.punctuation:
            line += f"，{s.punctuation}"
        if s.habits:
            line += f"。常用：{'、'.join(s.habits)}"
        if s.forbidden:
            line += f"。绝不说：{'、'.join(s.forbidden)}"
        parts.append(line + "。")

        if card.boundaries:
            parts.append("你绝不：" + "；".join(card.boundaries))

        blocks = self.load_blocks(card.id)
        if blocks:
            budget = 200
            if settings:
                budget = int(settings.get("persona.raw_budget_chars") or 200)
            parts.extend(
                _render_blocks(
                    blocks, budget,
                    sender=sender_qq, name=card.name,
                    alias=(card.alias[0] if card.alias else ""),
                )
            )

        limit = 2
        until_turn = 10
        if settings:
            limit = int(settings.get("persona.examples_limit") or 2)
            until_turn = int(settings.get("persona.examples_until_turn") or 10)
        if card.examples and limit > 0 and turn_count <= until_turn:
            ex = "\n".join(
                f"对方：{e.user}\n你：{e.assistant}" for e in card.examples[:limit]
            )
            parts.append(f"说话范例：\n{ex}")

        flavor = _state_flavor(state)
        if flavor:
            parts.append(flavor)

        if extras:
            parts.append("\n".join(extras))

        return "\n\n".join(p for p in parts if p)

    # ==================================================================
    # 给界面
    # ==================================================================

    def describe(self, persona_id: str = "") -> dict:
        card = self.load_card(persona_id)
        if card is None:
            return {}
        return {
            "id": card.id,
            "name": card.name,
            "alias": card.alias,
            "identity": card.identity,
            "background": card.background,
            "traits": [{"name": t.name, "weight": t.weight, "trigger": t.trigger} for t in card.traits],
            "speech": asdict(card.speech),
            "boundaries": card.boundaries,
            "examples": [{"user": e.user, "assistant": e.assistant} for e in card.examples],
            "blocks": [
                {"title": b.title, "kind": b.kind, "weight": b.weight}
                for b in self.load_blocks(card.id)
            ],
            "keywords": len(self.load_keywords(card.id)),
        }

    def list_cards(self) -> list[dict]:
        rows = self.db.query(
            "SELECT id, name, version, updated_at FROM persona_cards ORDER BY updated_at DESC"
        )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _length_desc(length: str) -> str:
    return {"short": "短，一句话说完", "long": "可以长一点，说清楚"}.get(length, "不长不短")


def _state_flavor(st: RuntimeState) -> str:
    bits: list[str] = []
    if st.energy < 0.3:
        bits.append("很累，句子要短，能一个字的不用两个字")
    if st.patience < 0.3:
        bits.append("没耐心，别绕弯子，别客套")
    if st.mood < -0.4:
        bits.append("心情差，别装开心，可以冷淡")
    elif st.mood > 0.6:
        bits.append("心情不错，可以说得松一点")
    if st.affection > 0.7:
        bits.append("对这个人有好感，可以主动一点")
    return "。".join(bits) + "。" if bits else ""


def _render_blocks(
    blocks: list[RawBlock], budget: int, *, sender: str = "",
    name: str = "", alias: str = "",
) -> list[str]:
    ctx = {"name": name, "alias": alias, "user": sender, "time": time.strftime("%H:%M")}
    by_kind: dict[str, list[str]] = {}
    used = 0

    for b in blocks:
        if b.weight < 0.4:
            continue
        text = _substitute(b.content, ctx)
        if b.weight < 0.8:
            text = f"（可选）{text}"
        if used + len(text) > budget:
            break
        by_kind.setdefault(b.kind, []).append(text)
        used += len(text)

    out: list[str] = []
    for kind in ("style", "taboo", "lore", "other"):
        out.extend(by_kind.get(kind, []))
    return out


def _substitute(text: str, ctx: dict) -> str:
    for k, v in ctx.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def _row_to_card(row) -> PersonaCard:
    def jload(s, default):
        try:
            return json.loads(s) if s else default
        except Exception:
            return default

    traits = []
    for t in jload(row["traits"], []):
        if isinstance(t, dict):
            traits.append(
                Trait(
                    name=t.get("name", ""),
                    weight=float(t.get("weight", 0.5)),
                    trigger=t.get("trigger", ""),
                )
            )

    sr = jload(row["speech"], {})
    speech = SpeechStyle(
        length=sr.get("length", "medium"),
        punctuation=sr.get("punctuation", ""),
        habits=sr.get("habits", []),
        forbidden=sr.get("forbidden", []),
        sass=float(sr.get("sass", 0.5)),
        emoji_level=float(sr.get("emoji_level", 0.3)),
    )

    examples = []
    for e in jload(row["examples"], []):
        if isinstance(e, dict):
            examples.append(
                ExampleDialogue(
                    user=e.get("user", ""),
                    assistant=e.get("assistant", ""),
                    note=e.get("note"),
                )
            )

    return PersonaCard(
        id=row["id"],
        account_id=row["account_id"] or "",
        name=row["name"] or "",
        avatar=row["avatar"] or "",
        alias=jload(row["alias"], []),
        identity=row["identity"] or "",
        background=row["background"] or "",
        traits=traits,
        speech=speech,
        boundaries=jload(row["boundaries"], []),
        examples=examples,
        tags=jload(row["tags"], []),
        version=row["version"] or 1,
    )


# ---------------------------------------------------------------------------
# 全局
# ---------------------------------------------------------------------------

_persona: PersonaSystem | None = None


def get_persona() -> PersonaSystem:
    global _persona
    if _persona is None:
        from ..db import get_db
        from ..settings import Settings

        db = get_db()
        _persona = PersonaSystem(db, Settings(db))
    return _persona
