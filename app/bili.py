"""B 站视频搜索 + 音频提取（不依赖 yt-dlp/ffmpeg）。

用 B 站网页端接口：搜索视频、取 cid、取 DASH 音频流，直接下载 m4a。
需要更高成功率时可在设置里填 B 站 Cookie。
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
         33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
         61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62,
         11, 36, 20, 34, 44, 52]


def strip_html(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", str(s or ""))).strip()


def find_video_id(text: str) -> dict | None:
    s = str(text or "")
    m = re.search(r"\b(BV[0-9A-Za-z]{10})\b", s)
    if m:
        return {"bvid": m.group(1)}
    m = re.search(r"\bav(\d{3,})\b", s, re.I)
    if m:
        return {"aid": m.group(1)}
    m = re.search(r"https?://b23\.tv/[0-9A-Za-z]+", s)
    if m:
        return {"short": m.group(0)}
    return None


def _key(url: str) -> str:
    return Path(urlparse(str(url or "")).path).stem


def wbi_sign(params: dict, img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    mixin = "".join(raw[i] for i in MIXIN if i < len(raw))[:32]
    p = {**params, "wts": int(time.time())}
    # B 站要求 URL 编码后的值；这里用 urlencode 保证空格/中文
    query = urlencode([
        (k, str(p[k]).replace("!", "").replace("'", "").replace("(", "").replace(")", "").replace("*", ""))
        for k in sorted(p)
    ])
    return query + "&w_rid=" + hashlib.md5((query + mixin).encode()).hexdigest()


class BiliClient:
    def __init__(self, settings):
        self.settings = settings
        self._wbi: tuple[str, str, float] | None = None
        self._buvid = ""

    def _cookie(self) -> str:
        raw = str(self.settings.get("module.bili_cookie") or "").strip()
        if self._buvid and "buvid3=" not in raw:
            raw = (raw + "; " if raw else "") + "buvid3=" + self._buvid
        return raw

    def _headers(self) -> dict:
        h = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
        if self._cookie():
            h["Cookie"] = self._cookie()
        return h

    async def _get_json(self, url: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url, params=params, headers=self._headers())
            if r.status_code != 200:
                raise RuntimeError(f"B 站 HTTP {r.status_code}")
            try:
                data = r.json()
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"B 站返回不是 JSON（可能被风控，HTTP {r.status_code}）") from e
            code = data.get("code")
            if code not in (0, -101):
                raise RuntimeError(f"B 站返回 {code}：{data.get('message') or ''}")
            return data

    async def _ensure_buvid(self) -> None:
        if self._buvid:
            return
        try:
            d = await self._get_json("https://api.bilibili.com/x/frontend/finger/spi")
            self._buvid = str((d.get("data") or {}).get("b_3") or "")
        except Exception as e:  # noqa: BLE001
            log.debug("申请 buvid 失败：%s", e)

    async def _nav(self) -> None:
        self._wbi = None
        try:
            d = await self._get_json("https://api.bilibili.com/x/web-interface/nav")
            w = (d.get("data") or {}).get("wbi_img") or {}
            img, sub = _key(w.get("img_url")), _key(w.get("sub_url"))
            if img and sub:
                self._wbi = (img, sub, time.time())
        except Exception as e:  # noqa: BLE001
            log.debug("取 B 站 WBI key 失败：%s", e)

    async def _signed(self, base: str, params: dict) -> str:
        await self._ensure_buvid()
        if not self._wbi or time.time() - self._wbi[2] > 6 * 3600:
            await self._nav()
        if not self._wbi:
            raise RuntimeError("拿不到 B 站 WBI 签名 key")
        return base + "?" + wbi_sign(params, self._wbi[0], self._wbi[1])

    async def search(self, keyword: str, limit: int = 10) -> list[dict]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        data = None
        try:
            url = await self._signed("https://api.bilibili.com/x/web-interface/wbi/search/type",
                                     {"search_type": "video", "keyword": keyword, "page": 1})
            data = await self._get_json(url)
        except Exception as e:  # noqa: BLE001
            log.debug("WBI 搜索失败，改直连：%s", e)
            data = await self._get_json(
                "https://api.bilibili.com/x/web-interface/search/type",
                {"search_type": "video", "keyword": keyword, "page": 1},
            )
        out = []
        for v in ((data.get("data") or {}).get("result") or [])[:limit]:
            if v.get("type") not in (None, "video"):
                continue
            dur = v.get("duration") or ""
            if isinstance(dur, str) and ":" in dur:
                parts = [int(x) for x in dur.split(":") if x.isdigit()]
                sec = 0
                for x in parts:
                    sec = sec * 60 + x
            else:
                sec = int(dur or 0)
            bvid = str(v.get("bvid") or "")
            if not bvid:
                continue
            out.append({
                "id": bvid, "bvid": bvid, "aid": v.get("aid"),
                "name": strip_html(v.get("title")),
                "artists": strip_html(v.get("author")),
                "duration_sec": sec, "play": int(v.get("play") or 0),
                "pic": str(v.get("pic") or ""),
                "url": f"https://www.bilibili.com/video/{bvid}",
                "source": "bilibili", "vip": False,
            })
        return out

    async def view(self, *, bvid: str = "", aid: str = "") -> dict:
        params = {"bvid": bvid} if bvid else {"aid": aid}
        data = None
        try:
            data = await self._get_json(await self._signed("https://api.bilibili.com/x/web-interface/wbi/view", params))
        except Exception as e:  # noqa: BLE001
            log.debug("WBI view 失败，改直连：%s", e)
            data = await self._get_json("https://api.bilibili.com/x/web-interface/view", params)
        d = data.get("data") or {}
        pages = d.get("pages") or []
        cid = d.get("cid") or (pages[0].get("cid") if pages else 0)
        if not cid:
            raise RuntimeError("这个视频没有可用的 cid")
        return {"bvid": d.get("bvid") or bvid, "aid": d.get("aid") or aid,
                "cid": cid, "title": strip_html(d.get("title")), "author": (d.get("owner") or {}).get("name") or "",
                "duration": int(d.get("duration") or 0), "pic": d.get("pic") or ""}

    async def playurl(self, *, bvid: str, cid: int) -> dict:
        params = {"bvid": bvid, "cid": cid, "fnval": 4048, "fnver": 0, "fourk": 1}
        data = None
        try:
            data = await self._get_json(await self._signed("https://api.bilibili.com/x/player/wbi/playurl", params))
        except Exception as e:  # noqa: BLE001
            log.debug("WBI playurl 失败，改直连：%s", e)
            data = await self._get_json("https://api.bilibili.com/x/player/playurl",
                                        {"bvid": bvid, "cid": cid, "fnval": 16, "fourk": 1})
        d = data.get("data") or {}
        audio = []
        for x in ((d.get("dash") or {}).get("audio") or []):
            u = x.get("baseUrl") or x.get("base_url") or ""
            if u:
                audio.append({"url": u, "bandwidth": int(x.get("bandwidth") or 0),
                              "mime": x.get("mimeType") or x.get("mime_type") or "audio/mp4"})
        audio.sort(key=lambda x: x["bandwidth"], reverse=True)
        return {"audio": audio, "durl": d.get("durl") or []}

    async def resolve(self, text: str) -> dict:
        vid = find_video_id(text)
        if not vid:
            raise RuntimeError("没认出 BV 号或视频链接")
        if vid.get("short"):
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=self._headers()) as c:
                r = await c.get(vid["short"])
                vid = find_video_id(str(r.url)) or {}
        if vid.get("bvid"):
            return await self.view(bvid=vid["bvid"])
        if vid.get("aid"):
            return await self.view(aid=str(vid["aid"]))
        raise RuntimeError("没认出 BV 号")

    async def download_audio(self, *, bvid: str, dest: Path) -> dict:
        info = await self.view(bvid=bvid)
        play = await self.playurl(bvid=info["bvid"], cid=info["cid"])
        if not play["audio"]:
            raise RuntimeError("B 站没有返回 DASH 音频流（可能需要登录 Cookie 或该视频受限制）")
        url = play["audio"][0]["url"]
        async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers=self._headers()) as c:
            r = await c.get(url)
            r.raise_for_status()
            if len(r.content) < 1024:
                raise RuntimeError("B 站返回的音频太小，可能被风控")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
        return {**info, "audio_url": url, "size": dest.stat().st_size}
