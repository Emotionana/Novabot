"""可安全复制到其他 bot 的设置。

只允许明确列出的行为项。语音引擎、TTS 端口、OneBot 地址/token、DSH 端口、
模型地址、账号凭据、文件路径、人格卡等一律不在这里。
"""

from __future__ import annotations

from .settings import Settings

# 分组 -> 允许同步的 key。不要用通配符，避免以后新增的接口/端口被误同步。
SYNC_GROUPS: dict[str, list[str]] = {
    "回复": [
        "reply.enabled", "reply.group_mode", "reply.max_per_min",
        "reply.min_gap_sec", "reply.owner_always", "reply.private_always",
        "reply.ignore_other_bots", "reply.blacklist_words", "reply.cooldown_sec",
        "reply.group_rate_window_sec", "reply.group_rate_max_msgs",
        "reply.defer_judge", "reply.followup_sec", "reply.ignore_links_only",
        "reply.ignore_min_chars", "reply.ignore_stickers",
        "reply.owner_max_per_min", "reply.probability",
        "reply.relevance_threshold",
    ],
    "表情": [
        "stickers.auto_send", "stickers.collect", "stickers.enabled",
        "stickers.auto_group", "stickers.auto_private",
        "stickers.collect_per_hour", "stickers.cooldown_sec",
        "stickers.max_items", "stickers.max_mb",
    ],
    "真实": [
        "truth.enabled", "truth.work_mode", "truth.allow_roleplay",
        "truth.guard_actions", "truth.strict_unknown", "truth.work_keywords",
    ],
    "媒体": [
        "media.enabled", "media.image_enabled", "media.voice_enabled",
        "media.video_enabled", "media.vision_auto", "media.max_mb",
        "media.video_frames",
    ],
    "文件": [
        "files.enabled", "files.receive_from", "files.require_approval",
        "files.send_to", "files.allowed_extensions", "files.blocked_names",
        "files.max_mb", "files.scan_secrets",
    ],
    "隐私": [
        "privacy.enabled", "privacy.block_contacts", "privacy.leak_action",
        "privacy.cross_private", "privacy.profile_detail",
        "privacy.documents", "privacy.secret_terms",
    ],
    "记忆": [
        "memory.distill_enabled", "memory.enabled",
        "memory.episode_retention_days", "memory.plasticity_max",
        "memory.plasticity_per_hit", "memory.recall_chars",
        "memory.recent_days", "memory.tier1_half_life_days",
        "memory.tier2_half_life_days", "memory.tier3_half_life_days",
        "memory.distill_batch", "memory.distill_interval_sec",
        "memory.distill_min_age_sec", "memory.distill_min_chars",
        "memory.emotion_batch", "memory.emotion_enabled",
        "memory.emotion_rule_threshold", "memory.emotion_window_days",
        "memory.fact_retention_days", "memory.facts_limit",
        "memory.graph_enabled", "memory.profile_chars",
        "memory.prune_enabled", "memory.recent_count",
        "memory.vector_enabled", "memory.vector_sim_threshold",
        "memory.walk_decay", "memory.walk_depth", "memory.walk_min_weight",
    ],
    "输出": [
        "output.clean_markdown", "output.split_enabled",
        "output.sticker_enabled", "output.filter_words",
        "output.typing_delay",
        "output.split_chars", "output.split_max",
        "output.group_split_chars", "output.group_split_max",
        "output.part_delay_min", "output.part_delay_max",
    ],
    "生活": [
        "proactive.enabled", "proactive.max_per_day",
        "proactive.min_silence_hours", "proactive.quiet_end",
        "proactive.quiet_start", "proactive.active_guard_min",
        "proactive.backoff_base_days", "proactive.dry_run",
        "proactive.global_max_per_day", "proactive.habit_days",
        "proactive.interval_hours", "proactive.max_chars",
        "proactive.min_days", "proactive.min_messages",
        "proactive.only_owner", "proactive.owner_enabled",
        "proactive.owner_max_per_day", "proactive.score_threshold",
        "proactive.tick_sec", "proactive.wait_reply_hours",
        "proactive.slot_morning", "proactive.slot_noon",
        "proactive.slot_afternoon", "proactive.slot_evening",
    ],
}


def available_groups() -> dict[str, list[str]]:
    return {name: list(keys) for name, keys in SYNC_GROUPS.items()}


def _targets(db, source_account: str) -> list[tuple[str, str]]:
    try:
        rows = db.query(
            "SELECT id, name FROM accounts WHERE enabled = 1 AND id <> ? "
            "ORDER BY sort_order, id",
            [source_account],
        )
    except Exception:  # noqa: BLE001
        return []
    return [(str(r["id"]), str(r["name"] or r["id"])) for r in rows]


def sync_settings(db, source_account: str, groups: list[str], target_ids: list[str] | None = None) -> dict:
    source_account = str(source_account or "").strip()
    if not source_account:
        return {"ok": False, "error": "缺少来源账号"}
    chosen = [g for g in (groups or []) if g in SYNC_GROUPS]
    if not chosen:
        return {"ok": False, "error": "没有可同步的分组"}
    source = Settings(db, account_id=source_account)
    targets = _targets(db, source_account)
    if target_ids is not None:
        wanted = {str(x) for x in target_ids}
        targets = [(i, n) for i, n in targets if i in wanted]
    if not targets:
        return {"ok": False, "error": "没有选择要同步的 bot"}

    keys = []
    for group in chosen:
        for key in SYNC_GROUPS[group]:
            if key not in keys:
                keys.append(key)

    values: dict[str, object] = {}
    for key in keys:
        try:
            values[key] = source.get(key)
        except KeyError:
            continue

    copied = 0
    for target_id, _ in targets:
        target = Settings(db, account_id=target_id)
        for key, value in values.items():
            try:
                target.set(key, value)
                copied += 1
            except Exception:  # noqa: BLE001
                continue

    return {
        "ok": True,
        "groups": chosen,
        "keys": list(values),
        "key_count": len(values),
        "targets": [{"id": i, "name": n} for i, n in targets],
        "copied": copied,
    }
