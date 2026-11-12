"""QQ 底座/插件下载：版本目录、国内镜像、自动测速、断点式下载任务。

只下载官方 GitHub Release 资产；下载源是 GitHub 直连和公开加速前缀，
文件落 data/plugins/downloads，不自动执行。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
import shutil
import socket
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

TOOLS = {
    "snowluma": {
        "name": "SnowLuma",
        "repo": "SnowLuma/SnowLuma",
        "desc": "OneBot v11 QQ 底座，带 WebUI；Windows 完整版内置 Node.js",
        "local_prefixes": ("SnowLuma",),
    },
    "napcat": {
        "name": "NapCatQQ",
        "repo": "NapNeko/NapCatQQ",
        "desc": "NapCat OneBot 实现；Windows Shell / Node / 一键包",
        "local_prefixes": ("NapCat",),
    },
}

SOURCES = [
    {"id": "auto", "name": "自动测速", "prefix": "", "note": "同时探测直连和镜像，选最快"},
    {"id": "direct", "name": "GitHub 直连", "prefix": "", "note": "官方 release，海外/代理网络更稳"},
    {"id": "ghproxy", "name": "ghproxy.net", "prefix": "https://ghproxy.net/", "note": "国内公开加速"},
    {"id": "gh-proxy", "name": "gh-proxy.com", "prefix": "https://gh-proxy.com/", "note": "国内公开加速"},
    {"id": "ghfast", "name": "ghfast.top", "prefix": "https://ghfast.top/", "note": "国内公开加速；不可用时自动跳过"},
]

WINDOWS_KEYS = ("win", "windows")


def parse_releases(data: list, tool: str) -> list[dict]:
    rows = []
    for rel in data or []:
        tag = str(rel.get("tag_name") or "").strip()
        if not tag or rel.get("draft"):
            continue
        assets = []
        for a in rel.get("assets") or []:
            name = str(a.get("name") or "")
            low = name.lower()
            if not low.endswith((".zip", ".tar.gz")):
                continue
            if tool == "snowluma" and "win" not in low:
                continue
            if tool == "napcat" and not low.endswith(".zip"):
                continue
            if tool == "napcat" and not any(k in low for k in WINDOWS_KEYS) and name != "NapCat.Shell.zip":
                continue
            digest = str(a.get("digest") or "")
            assets.append({
                "name": name,
                "size": int(a.get("size") or 0),
                "url": str(a.get("browser_download_url") or ""),
                "sha256": digest[7:] if digest.startswith("sha256:") else "",
                "variant": (
                    "lite" if "lite" in low else
                    "onekey" if "onekey" in low else
                    "node" if "windows.node" in low else "full"
                ),
            })
        if not assets:
            continue
        rows.append({
            "tag": tag,
            "name": str(rel.get("name") or tag),
            "published_at": rel.get("published_at") or rel.get("created_at") or "",
            "prerelease": bool(rel.get("prerelease")),
            "assets": assets,
        })
    return rows


def fallback_assets(tool: str, tag: str) -> list[dict]:
    """GitHub API 限流/被墙时的稳定资产名。SnowLuma/NapCat 的 Windows 包名是固定的。"""
    if tool == "snowluma":
        names = [
            (f"SnowLuma-{tag}-win-x64.zip", "full"),
            (f"SnowLuma-{tag}-win-x64-lite.zip", "lite"),
        ]
        base = f"https://github.com/SnowLuma/SnowLuma/releases/download/{tag}/"
        return [{"name": n, "size": 0, "url": base + n, "sha256": "", "variant": v} for n, v in names]
    names = [
        ("NapCat.Shell.zip", "full"),
        ("NapCat.Shell.Windows.OneKey.zip", "onekey"),
        ("NapCat.Shell.Windows.Node.zip", "node"),
    ]
    base = f"https://github.com/NapNeko/NapCatQQ/releases/download/{tag}/"
    return [{"name": n, "size": 0, "url": base + n, "sha256": "", "variant": v} for n, v in names]


def parse_atom(text: str, tool: str) -> list[dict]:
    """releases.atom -> 版本列表；没有资产大小时用固定资产名补。"""
    rows = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return rows
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("a:entry", ns)[:12]:
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        link = entry.find("a:link", ns)
        href = (link.get("href") if link is not None else "") or ""
        tag = href.rstrip("/").split("/")[-1] or title
        if not tag.startswith("v"):
            continue
        assets = fallback_assets(tool, tag)
        if not assets:
            continue
        rows.append({
            "tag": tag, "name": title or tag,
            "published_at": entry.findtext("a:updated", default="", namespaces=ns),
            "prerelease": "pre" in title.lower(), "assets": assets,
        })
    return rows


def fallback_releases(tool: str) -> list[dict]:
    """最后兜底：GitHub 全挂时仍能选最近几个已知版本。"""
    versions = ["v2.4.2", "v2.4.1", "v2.4.0", "v2.3.2"] if tool == "snowluma" else ["v4.18.42"]
    return [{
        "tag": v, "name": v, "published_at": "", "prerelease": False,
        "assets": fallback_assets(tool, v),
    } for v in versions]


class PluginDownloader:
    def __init__(self, db, settings):
        self.db = db
        root = Path(getattr(db, "path", ".")).parent / "plugins"
        self.root = root / "downloads"
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.jobs: dict[str, dict] = {}
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    def tools(self) -> list[dict]:
        out = []
        for tid, meta in TOOLS.items():
            versions = self.installed_info(tid)
            out.append({
                "id": tid, "name": meta["name"], "repo": meta["repo"],
                "desc": meta["desc"], "installed": [x["version"] for x in versions],
                "versions": versions,
            })
        return out

    def _current_dir(self) -> Path | None:
        try:
            command = str(self.settings.get("gateway.backend_command") or "")
        except Exception:
            return None
        m = re.search(r'"([^"]*node\.exe)"', command, re.I)
        if m:
            return Path(m.group(1)).parent
        m = re.search(r'([A-Za-z]:\\[^"\s]+node\.exe)', command, re.I)
        return Path(m.group(1)).parent if m else None

    def installed_info(self, tool: str) -> list[dict]:
        meta = TOOLS.get(tool) or {}
        prefixes = meta.get("local_prefixes") or ()
        roots = [Path.home() / "dsh-plugins", self.root.parent]
        current = self._current_dir()
        seen: set[str] = set()
        found: list[dict] = []
        for root in roots:
            if not root.is_dir():
                continue
            for p in root.iterdir():
                if not p.is_dir() or not any(p.name.lower().startswith(x.lower()) for x in prefixes):
                    continue
                low_name = p.name.lower()
                if "backup" in low_name or ".bak" in low_name:
                    continue
                if not (p / "node.exe").is_file() or not (p / "index.mjs").is_file():
                    continue
                m = re.search(r"(\d+\.\d+\.\d+)", p.name)
                if not m:
                    continue
                version = m.group(1)
                key = str(p.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                found.append({
                    "version": version,
                    "path": str(p),
                    "current": bool(current and p.resolve() == current.resolve()),
                })
        found.sort(key=lambda x: [int(v) for v in x["version"].split(".")], reverse=True)
        return found

    def installed(self, tool: str) -> list[str]:
        return [x["version"] for x in self.installed_info(tool)]

    @staticmethod
    def _port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                return True
        except OSError:
            return False

    def _stop_dir(self, path: Path) -> None:
        target = str(path).replace("'", "''")
        script = (
            "$p = '" + target + "'; "
            "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.Contains($p) } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, timeout=20,
        )

    def _start_dir(self, path: Path) -> None:
        log_path = Path(getattr(self.db, "path", ".")).parent / "logs" / "backend-autostart.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = f'"{path / "node.exe"}" index.mjs'
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with log_path.open("ab") as out:
            out.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] switch: {command} (cwd={path})\n".encode()
            )
            out.flush()
            subprocess.Popen(
                command, cwd=str(path), shell=True, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, creationflags=flags, close_fds=True,
            )

    def _copy_state(self, old: Path | None, target: Path, stamp: str) -> None:
        # 目标版本自己的配置先留一份；切换后仍可回退检查
        for name in ("config", "data"):
            src = target / name
            if src.is_dir():
                shutil.copytree(src, target / f"{name}.switch-backup-{stamp}", dirs_exist_ok=True)
        if old and old.is_dir():
            for name in ("config", "data"):
                src = old / name
                if src.is_dir():
                    shutil.copytree(src, target / name, dirs_exist_ok=True)

    async def switch(self, tool: str, version: str) -> dict:
        if tool != "snowluma":
            return {"ok": False, "error": "目前一键切换只支持 SnowLuma"}
        info = next((x for x in self.installed_info(tool) if x["version"] == version), None)
        if info is None:
            return {"ok": False, "error": "没有安装这个版本"}
        target = Path(info["path"]).resolve()
        old = self._current_dir()
        if old and target == old.resolve():
            return {"ok": False, "error": "已经在这个版本上"}
        if not (target / "node.exe").is_file():
            return {"ok": False, "error": "目标版本缺少 node.exe"}
        try:
            old_command = str(self.settings.get("gateway.backend_command") or "")
            old_workdir = str(self.settings.get("gateway.backend_workdir") or "")
        except Exception:
            old_command, old_workdir = "", ""

        # 先停旧版本，避免运行时 SQLite/配置边写边拷。
        if old:
            await asyncio.to_thread(self._stop_dir, old)
        for _ in range(20):
            if not any(self._port_open(int(p)) for p in (3001, 3011, 3021)):
                break
            await asyncio.sleep(0.25)

        stamp = time.strftime("%Y%m%d-%H%M%S")
        await asyncio.to_thread(self._copy_state, old, target, stamp)

        command = f'"{target / "node.exe"}" index.mjs'
        self.settings.set("gateway.backend_command", command)
        self.settings.set("gateway.backend_workdir", str(target))
        try:
            await asyncio.to_thread(self._start_dir, target)
            ports = [3001, 3011, 3021]
            for _ in range(80):
                if sum(1 for p in ports if self._port_open(p)) >= 1:
                    return {"ok": True, "tool": tool, "version": version, "path": str(target), "rollback": False}
                await asyncio.sleep(0.25)
            raise RuntimeError("新版本启动后端口未就绪")
        except Exception as e:
            # 回滚到旧版本
            if old:
                self.settings.set("gateway.backend_command", old_command)
                self.settings.set("gateway.backend_workdir", old_workdir)
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self._stop_dir, target)
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self._start_dir, old)
            return {"ok": False, "error": f"切换失败，已尝试回滚：{e}", "rollback": bool(old)}

    async def releases(self, tool: str, *, refresh: bool = False) -> list[dict]:
        meta = TOOLS.get(tool)
        if meta is None:
            raise ValueError("不支持的插件")
        now = time.time()
        hit = self._cache.get(tool)
        if hit and not refresh and now - hit[0] < 600:
            return hit[1]
        rows: list[dict] = []
        # 1) GitHub API（有 60 次/小时限流，403 时不要让它挡住下载）
        try:
            url = f"https://api.github.com/repos/{meta['repo']}/releases?per_page=12"
            headers = {"Accept": "application/vnd.github+json", "User-Agent": "Novabot-plugin-downloader"}
            async with httpx.AsyncClient(timeout=20, headers=headers) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    rows = parse_releases(r.json(), tool)
        except Exception as e:  # noqa: BLE001
            log.info("GitHub API 取版本失败，改走 Atom：%s", e)
        # 2) releases.atom，不受 API 限流，但只有版本号和链接
        if not rows:
            try:
                atom_url = f"https://github.com/{meta['repo']}/releases.atom"
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                    r = await c.get(atom_url, headers={"User-Agent": "Mozilla/5.0"})
                    if r.status_code == 200:
                        rows = parse_atom(r.text, tool)
            except Exception as e:  # noqa: BLE001
                log.info("GitHub Atom 取版本失败：%s", e)
        # 3) 已知最近版本兜底
        if not rows:
            rows = fallback_releases(tool)
        self._cache[tool] = (now, rows)
        return rows

    @staticmethod
    def source_url(source_id: str, url: str) -> str:
        source = next((s for s in SOURCES if s["id"] == source_id), None)
        if source is None or not source["prefix"]:
            return url
        return source["prefix"] + url

    async def _probe(self, url: str, source_id: str, timeout: float = 4) -> tuple[str, float, str]:
        target = self.source_url(source_id, url)
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
                r = await c.get(target, headers={"Range": "bytes=0-1023", "User-Agent": "Novabot/0.1"})
                if r.status_code not in (200, 206):
                    return source_id, 9999, target
                return source_id, time.perf_counter() - start, target
        except Exception:  # noqa: BLE001
            return source_id, 9999, target

    async def _sources_for(self, source_id: str, url: str) -> list[str]:
        if source_id != "auto":
            return [source_id]
        candidates = [s["id"] for s in SOURCES if s["id"] != "auto"]
        results = await asyncio.gather(*[self._probe(url, sid) for sid in candidates])
        ok = sorted((r for r in results if r[1] < 9000), key=lambda r: r[1])
        return [r[0] for r in ok] + [r[0] for r in results if r[1] >= 9000]

    def start(self, *, tool: str, tag: str, asset_name: str, source: str = "auto") -> dict:
        rows = self._cache.get(tool, (0, []))[1]
        rel = next((r for r in rows if r["tag"] == tag), None)
        if rel is None:
            return {"ok": False, "error": "版本缓存里没有这个版本，先刷新版本"}
        asset = next((a for a in rel["assets"] if a["name"] == asset_name), None)
        if asset is None:
            return {"ok": False, "error": "这个版本没有该文件"}
        job_id = uuid.uuid4().hex[:12]
        safe = re.sub(r"[^\w.-]+", "_", asset_name)
        safe_tag = re.sub(r"[^\w.-]+", "_", tag)
        job = {
            "id": job_id, "tool": tool, "tag": tag, "asset": asset_name,
            "status": "queued", "source": source, "used_source": "",
            "downloaded": 0, "total": int(asset.get("size") or 0),
            "speed": 0.0, "file": "", "error": "", "started": time.time(),
        }
        self.jobs[job_id] = job
        asyncio.create_task(self._run(job, asset["url"], self.root / tool / f"{safe_tag}-{safe}", asset.get("sha256") or ""))
        return {"ok": True, "job": job}

    async def _run(self, job: dict, url: str, target: Path, sha: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        sources = await self._sources_for(job["source"], url)
        if not sources:
            sources = ["direct"]
        last = ""
        for sid in sources:
            target.unlink(missing_ok=True)
            job.update(status="downloading", used_source=sid, downloaded=0, speed=0.0, error="")
            try:
                await self._fetch(job, self.source_url(sid, url), target, sha)
                job.update(status="done", file=str(target), speed=0.0, finished=time.time())
                return
            except Exception as e:  # noqa: BLE001
                last = f"{sid}: {e}"
                log.warning("插件下载源 %s 失败：%s", sid, e)
        job.update(status="error", error=last[:300], finished=time.time())

    async def _fetch(self, job: dict, url: str, target: Path, sha: str) -> None:
        part = target.with_suffix(target.suffix + ".part")
        h = hashlib.sha256()
        downloaded = 0
        start = time.time()
        async with httpx.AsyncClient(timeout=httpx.Timeout(1200, connect=20), follow_redirects=True) as c, \
                c.stream("GET", url, headers={"User-Agent": "Novabot/0.1"}) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or job.get("total") or 0)
            job["total"] = total
            with part.open("wb") as f:  # noqa: SIM117
                async for chunk in r.aiter_bytes(65536):
                        if not chunk:
                            continue
                        f.write(chunk)
                        h.update(chunk)
                        downloaded += len(chunk)
                        elapsed = max(time.time() - start, 0.001)
                        job.update(downloaded=downloaded, speed=downloaded / elapsed)
        if sha and h.hexdigest().lower() != sha.lower():
            part.unlink(missing_ok=True)
            raise RuntimeError("SHA256 校验失败")
        part.replace(target)

    def cancel(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "任务不存在"}
        job["status"] = "cancelled"
        return {"ok": True}
