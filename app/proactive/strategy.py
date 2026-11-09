"""主动私聊：打分。

分数只回答一个问题：现在找这个人，值不值。
硬条件（安静时段、频控、退避）在 service 里先过，这里只算软分。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .habits import HabitProfile, habit_fit, relation_score, silence_fit

# 权重之和 = 1.0；调整时一起改这里
W_HABIT = 0.30
W_SILENCE = 0.20
W_RELATION = 0.15
W_TOPIC = 0.15
W_RESPONSE = 0.20


@dataclass(slots=True)
class Candidate:
    qq: str
    name: str
    is_owner: bool
    score: float
    silence_hours: float
    habit_fit: float
    silence_fit: float
    relation: float
    topic_fit: float
    response: float
    peak_hours: list[int]
    msg_count: int
    topics: dict = field(default_factory=dict)

    def reason(self) -> str:
        return (
            f"习惯 {self.habit_fit:.2f} / 沉默 {self.silence_fit:.2f} / "
            f"关系 {self.relation:.2f} / 话题 {self.topic_fit:.2f} / "
            f"回复率 {self.response:.2f} → {self.score:.2f}"
        )


def score_candidate(
    profile: HabitProfile,
    topics: dict,
    response: float,
    *,
    now: float,
    tz,
    ignored_streak: int = 0,
) -> Candidate:
    silence_hours = max(0.0, (now - profile.last_seen) / 3600)
    h_fit = habit_fit(now, profile.peaks_for(now, tz), tz)
    s_fit = silence_fit(silence_hours)
    rel = relation_score(profile, now)
    topic_fit = 1.0 if (topics.get("unfinished") or topics.get("facts")) else 0.55

    raw = (
        W_HABIT * h_fit
        + W_SILENCE * s_fit
        + W_RELATION * rel
        + W_TOPIC * topic_fit
        + W_RESPONSE * response
    )
    penalty = 0.15 * min(ignored_streak, 3)
    score = round(max(0.0, min(1.0, raw - penalty)), 3)

    return Candidate(
        qq=profile.qq,
        name=profile.name,
        is_owner=profile.is_owner,
        score=score,
        silence_hours=round(silence_hours, 1),
        habit_fit=h_fit,
        silence_fit=s_fit,
        relation=rel,
        topic_fit=topic_fit,
        response=response,
        peak_hours=list(profile.peak_hours),
        msg_count=profile.msg_count,
        topics=topics,
    )
