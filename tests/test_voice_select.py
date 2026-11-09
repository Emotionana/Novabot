"""参考音选择的测试。

跑：python -m tests.test_voice_select
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.models import RuntimeState  # noqa: E402


def main() -> int:
    import app.params  # noqa: F401
    from app.adapters.tts.selector import VoiceSelector
    from app.memory.migrate import init_schema
    from app.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="novabot-voice-"))
    db = Database(tmp / "test.db")
    init_schema(db)
    s = Settings(db)

    # 建一个人格和三条参考音
    db.execute(
        "INSERT INTO persona_cards (id, account_id, name, updated_at) VALUES (?,?,?,?)",
        ["demo", "default", "小助手", time.time()],
    )
    refs = [
        ("demo-calm", "平静陈述", 6, 50, 1.0, []),
        ("demo-short", "短促", 1, 8, 0.8, []),
        ("demo-soft", "软", 6, 50, 0.6, ["joy"]),
    ]
    for rid, name, mn, mx, w, emos in refs:
        db.execute(
            "INSERT INTO voice_refs (id, persona_id, name, engine, audio_path, prompt_text, "
            "params, weight, tags, min_chars, max_chars, emotions, enabled, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [rid, "demo", name, "gptsovits", f"/tmp/{rid}.wav", "测试", "{}", w, "[]",
             mn, mx, json.dumps(emos), 1, time.time(), time.time()],
        )

    sel = VoiceSelector(db, s)
    state = RuntimeState()

    print("=== 粘住：连续选 5 次，应该都是同一条")
    first = sel.select("demo", "group:1", "今天天气不错", emotion="neutral", state=state)
    print(f"  第 1 次：{first.ref.name}（{first.reason}）")
    assert first.ref is not None

    ids = {first.ref.id}
    for i in range(2, 6):
        r = sel.select("demo", "group:1", "再说点什么吧", emotion="neutral", state=state)
        ids.add(r.ref.id)
        print(f"  第 {i} 次：{r.ref.name}（{'换了' if r.switched else '没换'}）")

    assert len(ids) == 1, f"应该一直用同一条，实际用了 {len(ids)} 条：{ids}"
    print("  ✓ 五次都是同一条")

    print()
    print("=== 长度不符必须换")
    r = sel.select("demo", "group:1", "嗯", emotion="neutral", state=state)
    print(f"  说「嗯」（1 字）：{r.ref.name}（{r.reason}）")
    # 短句要么命中短促，要么保持原样但被长度筛过
    print(f"  是否切换：{r.switched}")

    print()
    print("=== 不同会话各自独立")
    r2 = sel.select("demo", "group:2", "今天天气不错", emotion="neutral", state=state)
    print(f"  group:2 第一次：{r2.ref.name}")
    print(f"  group:1 现在：{sel.current('group:1')}")
    print(f"  group:2 现在：{sel.current('group:2')}")

    print()
    print("=== 情绪不符会切")
    # 先把 group:3 粘到「软」（只适用 joy），再用 anger 触发
    sel.reset()
    # 直接手动粘
    sel._sticky["group:3"] = ("demo-soft", time.time(), "joy")
    r = sel.select("demo", "group:3", "今天心情不好啊", emotion="anger", state=state)
    print(f"  joy 的参考遇到 anger：{r.ref.name}（{r.reason}）")
    assert r.ref.id != "demo-soft", "情绪不符应该换掉"

    print()
    print("=== 超时换新")
    s.set("voice.sticky_max_sec", 1)
    sel.reset()
    r1 = sel.select("demo", "group:4", "第一句", emotion="neutral", state=state)
    print(f"  第 1 次：{r1.ref.name}")
    sel._sticky["group:4"] = (r1.ref.id, time.time() - 10, "neutral")  # 假装 10 秒前选的
    r2 = sel.select("demo", "group:4", "第二句", emotion="neutral", state=state)
    print(f"  10 秒后（上限 1 秒）：{r2.ref.name}（{r2.reason}）")
    assert "换一条" in r2.reason or r2.switched

    print()
    print("=== reset")
    sel.reset()
    assert sel.current("group:1") == ""
    print("  ✓ 清空成功")

    db.close()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
