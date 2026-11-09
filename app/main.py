"""Web 服务入口。

第 0 阶段：只起个空服务，能打开网页，能查健康状态。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config, db
from . import params as _params  # noqa: F401  注册所有参数
from .adapters.llm.factory import build_llm
from .core.tokens import get_meter
from .settings import Settings

log = logging.getLogger("novabot")

STATIC = Path(__file__).parent / "web" / "static"



def _persona_or_none():
    """尝试加载人格系统，失败就返回 None（不阻塞启动）。"""
    try:
        from .persona import get_persona

        return get_persona()
    except Exception as e:
        log.warning("人格系统没起来：%s", e)
        return None


def _mirror_config(settings, cfg) -> None:
    """把配置文件里"界面也该看到"的值补进参数表。

    只补用户没显式改过的项：TOML 是初始值，参数表是用户的决定。
    不镜像的话会出现"实际在跑、界面显示关着"这种自相矛盾的画面。
    """
    pairs = (
        ("gateway.enabled", cfg.gateway.enabled),
        ("llm.default", cfg.llm.default),
    )
    for key, value in pairs:
        if not value:
            continue
        try:
            if not settings.is_custom(key):
                settings.set(key, value)
        except Exception as e:  # noqa: BLE001
            log.debug("镜像配置 %s 失败：%s", key, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = config.get()
    log.info("Novabot v%s 启动，数据目录 %s", __version__, cfg.db_path().parent)

    # 备份恢复必须在打开数据库之前替换文件
    try:
        from .backup_service import apply_pending_restore

        restored = apply_pending_restore(cfg.db_path().parent)
        if restored:
            log.info("已恢复备份：%d 个文件（旧文件在 data/backups/pre-restore-*）", restored)
    except Exception as e:  # noqa: BLE001
        log.exception("应用待恢复备份失败：%s", e)

    # 初始化数据库：建表 + 迁移
    d = db.get_db()
    from .memory.migrate import init_schema, table_counts
    from .memory.system import get_memory

    init_schema(d)
    log.info("数据库就绪：%s", {k: v for k, v in table_counts(d).items() if v})

    app.state.db = d
    # 全进程共用一个记账器：网关里每个 Pipeline 都用它，网页才能看到用量
    app.state.meter = get_meter()
    # 用量落过库就读回来，重启不丢"今日"
    try:
        row = d.query_one("SELECT v FROM meta WHERE k = 'tokens.today'")
        if row:
            app.state.meter.restore_today(json.loads(row["v"]))
    except Exception as e:  # noqa: BLE001
        log.debug("恢复用量失败：%s", e)
    settings = Settings(d)
    _mirror_config(settings, cfg)
    # 老库已经有账号，视为已完成首次设置；全新空库才弹向导
    try:
        n = d.query_one("SELECT COUNT(*) AS n FROM accounts")
        count = int(n["n"] or 0) if n else 0
        if count > 0:
            if not settings.get("runtime.setup_done"):
                settings.set("runtime.setup_done", True)
            # 老安装默认继续运行；全新空库才保持 dry-run 默认开
            if not settings.is_custom("runtime.dry_run"):
                settings.set("runtime.dry_run", False)
        elif not settings.is_custom("runtime.dry_run"):
            settings.set("runtime.dry_run", True)
    except Exception:  # noqa: BLE001
        pass

    persona = _persona_or_none()

    # 语音：参考音从配置同步进库，预览接口用 app.state.voice
    try:
        from .voice import VoiceService, sync_refs_from_config
        from .voice_engines import seed_from_config as seed_engines_from_config

        seed_engines_from_config(d, cfg)
        account_ids = list((getattr(cfg.gateway, "accounts", None) or {}).keys())
        sync_refs_from_config(d, cfg, account_ids[0] if account_ids else "")
        app.state.voice = VoiceService(d, persona, cfg, Settings(d))
        log.info("语音引擎：%s", app.state.voice.pool.names() or "无（未配置）")
    except Exception as e:  # noqa: BLE001
        log.warning("语音没起来：%s", e)

    # 平台适配（给 DSH 之类的外部 agent 用）
    try:
        from .adapters.platform.dsh import PlatformProvider

        app.state.platform_provider = PlatformProvider(d, get_memory(), persona)
        log.info("平台适配已启用")
    except Exception as e:
        log.warning("平台适配未启用：%s", e)

    # 额外模块：翻唱（任务状态常驻 app.state）
    try:
        from .song_module import SongModule

        app.state.song_module = SongModule(d, settings)
        log.info("模块已启用：翻唱")
    except Exception as e:  # noqa: BLE001
        log.warning("翻唱模块没起来：%s", e)

    # 权限分级（主人私聊 / 三档 / 每聊天覆盖）
    try:
        from .permissions import PermissionService

        app.state.permissions = PermissionService(d, settings)
    except Exception as e:  # noqa: BLE001
        log.warning("权限模块没起来：%s", e)

    # 文件传输（严格沙箱；API 负责上传/审批）
    try:
        from .file_service import FileService

        app.state.file_service = FileService(d, settings)
    except Exception as e:  # noqa: BLE001
        log.warning("文件模块没起来：%s", e)

    # 插件下载器（SnowLuma / NapCat）
    try:
        from .plugin_downloader import PluginDownloader

        app.state.plugin_downloader = PluginDownloader(d, settings)
    except Exception as e:  # noqa: BLE001
        log.warning("插件下载器没起来：%s", e)

    # 平台插件探测（DSH 桥接）；每 N 秒一次，健康时自动接上
    try:
        from .platform_probe import probe

        async def _platform_probe_loop():
            while True:
                try:
                    result = await probe(d, Settings(d))
                    app.state.platform_probe = result
                except Exception as e:  # noqa: BLE001
                    log.debug("平台探测失败：%s", e)
                interval = int(settings.get("platform.probe_interval_sec") or 30)
                if interval <= 0:
                    break
                await asyncio.sleep(interval)

        if bool(settings.get("platform.auto_detect")):
            probe_task = asyncio.create_task(_platform_probe_loop(), name="platform-probe")
            app.state.platform_probe_task = probe_task
    except Exception as e:  # noqa: BLE001
        log.warning("平台探测没起来：%s", e)

    # 网关（QQ 接入）
    try:
        from .gateway.manager import GatewayManager

        gw = GatewayManager(
            settings=settings,
            memory=get_memory(),
            persona=persona,
            llm_factory=lambda acc: build_llm(cfg, Settings(d, account_id=acc), acc),
        )
        app.state.gateway = gw
        # 优先用数据库里的账号；没有才读配置文件
        try:
            from .web.accounts_api import AccountStore, _to_onebot_cfg

            # 总开关：用户显式改过就听用户的，否则听 TOML
            try:
                gateway_on = (
                    bool(settings.get("gateway.enabled"))
                    if settings.is_custom("gateway.enabled")
                    else bool(cfg.gateway.enabled)
                )
            except (KeyError, AttributeError):
                gateway_on = bool(cfg.gateway.enabled)

            store = AccountStore(d)
            app.state.account_store = store
            db_accounts = store.list()

            # 接入插件自动拉起：SnowLuma 等不是 Novabot 子进程，启动时按配置补一次
            if gateway_on:
                try:
                    from .gateway.backend import ensure_backend

                    backend_launch = ensure_backend(d)
                    app.state.backend_launch = backend_launch
                    if backend_launch.get("started"):
                        log.info("已自动拉起接入插件：PID=%s", backend_launch.get("pid"))
                    else:
                        log.info("接入插件未拉起：%s", backend_launch.get("reason"))
                except Exception as e:  # noqa: BLE001
                    log.warning("接入插件自动拉起失败：%s", e)

            # 模型自动检测：DSH 已经起来就自动挂 Agent，不要等用户手填
            try:
                from .model_detect import detect_and_apply

                detected = await detect_and_apply(d)
                if detected.get("ok"):
                    log.info("模型自动检测成功：%s（账号 %s）", detected["endpoint"], ",".join(detected.get("accounts") or []))
                else:
                    log.info("模型自动检测：%s", detected.get("error") or "未发现")
            except Exception as e:  # noqa: BLE001
                log.warning("模型自动检测失败：%s", e)

            # 视觉自动检测：Agent 聊天模型本身没有 OpenAI 视觉接口，补本地 Ollama
            try:
                from .media_detect import apply_detected_vision

                vision = await apply_detected_vision(d)
                if vision.get("ok"):
                    log.info("视觉自动检测：%s / %s（账号 %s）", vision["base_url"], vision["model"], ",".join(vision.get("accounts") or []))
                else:
                    log.info("视觉自动检测：%s", vision.get("error") or "未发现")
            except Exception as e:  # noqa: BLE001
                log.warning("视觉自动检测失败：%s", e)

            # 第一次启动（库里还没有账号）时，把配置文件里的账号导进库。
            # 之后库就是唯一事实来源：界面上加删都落库，重启也在。
            if not db_accounts and gateway_on and cfg.gateway.accounts:
                for acc_id, acc_cfg in cfg.gateway.accounts.items():
                    try:
                        store.create(
                            {
                                "id": acc_id,
                                "name": acc_cfg.get("name") or acc_cfg.get("bot_qq") or acc_id,
                                "bot_qq": acc_cfg.get("bot_qq") or "",
                                "url": acc_cfg.get("url") or acc_cfg.get("onebot_url") or "",
                                "token": acc_cfg.get("token")
                                or acc_cfg.get("onebot_token")
                                or "",
                                "impl": acc_cfg.get("impl") or "snowluma",
                                "reverse": bool(acc_cfg.get("reverse")),
                                "enabled": True,
                                "auto_start": True,
                            }
                        )
                    except Exception as e:
                        log.warning("导入配置账号 %s 失败：%s", acc_id, e)
                db_accounts = store.list()
                log.info("已把配置文件里的 %d 个账号导入数据库", len(db_accounts))

            if db_accounts and gateway_on:
                for acc in db_accounts:
                    if not acc.get("enabled") or not acc.get("auto_start"):
                        continue
                    try:
                        await gw.add_account(acc["id"], _to_onebot_cfg(store.get_raw(acc["id"]) or acc))
                    except Exception as e:
                        log.warning("账号 %s 连接失败：%s", acc["id"], e)
                gw.active = bool(db_accounts)
                log.info("从数据库加载了 %d 个账号", len(gw.accounts))
            elif db_accounts:
                log.info("接入总开关关着：%d 个账号只入库、不连接", len(db_accounts))
            else:
                await gw.start_all()
        except Exception as e:
            log.warning("账号加载出错，退回配置文件：%s", e)
            await gw.start_all()
        log.info("网关已启动：%s", gw.summary())
        try:
            from .model_detect import probe_loop

            async def _refresh_models():
                for rt in getattr(gw, "accounts", {}).values():
                    with contextlib.suppress(Exception):
                        await gw._ensure_llm(rt)

            app.state.model_probe_task = asyncio.create_task(
                probe_loop(d, refresh=_refresh_models), name="model-probe"
            )
        except Exception as e:  # noqa: BLE001
            log.warning("模型自动探测任务没起来：%s", e)
    except Exception as e:
        log.warning("网关没起来：%s", e)

    # 主动私聊
    proactive = None
    proactive_task = None
    try:
        gw = getattr(app.state, "gateway", None)
        if gw is not None:
            from .proactive import ProactiveService

            proactive = ProactiveService(d, get_memory(), persona, Settings(d), manager=gw)
            app.state.proactive = proactive
            gw.proactive = proactive
            proactive_task = asyncio.create_task(
                proactive.run_forever(), name="proactive"
            )
            app.state.proactive_task = proactive_task
            log.info(
                "主动私聊：%s（在线账号 %d）",
                "开" if proactive.enabled() else "关（主动搭话页里打开）",
                len(gw.online_accounts()),
            )
    except Exception as e:  # noqa: BLE001
        log.warning("主动私聊没起来：%s", e)

    # 提醒服务：到点直接发，不经过模型
    reminder_service = None
    reminder_task = None
    try:
        gw = getattr(app.state, "gateway", None)
        if gw is not None:
            from .tools import ReminderService

            reminder_service = ReminderService(d, gw)
            app.state.reminders = reminder_service
            reminder_task = asyncio.create_task(
                reminder_service.run_forever(), name="reminders"
            )
            app.state.reminder_task = reminder_task
            log.info("提醒服务已启动")
    except Exception as e:  # noqa: BLE001
        log.warning("提醒服务没起来：%s", e)

    # 蒸馏（把原话压成事实）：没模型就不起
    distill_llm = None
    distill_task = None
    try:
        # 蒸馏是后台 token 大户：配了 llm.cheap 就用便宜的
        cheap = ""
        try:
            cheap = settings.get("llm.cheap") or ""
        except Exception:  # noqa: BLE001
            cheap = ""
        distill_llm = build_llm(cfg, settings, model_name=cheap) if cheap else None
        if distill_llm is None:
            distill_llm = build_llm(cfg, settings)
        if distill_llm is None:
            log.info("蒸馏未启用：没有可用模型")
        elif not settings.get("memory.distill_enabled"):
            log.info("蒸馏未启用：memory.distill_enabled 关着")
        else:
            from .memory.distill import Distiller

            app.state.distiller = Distiller(get_memory(), distill_llm, settings)
            distill_task = asyncio.create_task(
                app.state.distiller.run_forever(), name="distiller"
            )
            app.state.distill_task = distill_task
            log.info(
                "蒸馏已启用，每 %s 秒一轮，一次最多 %s 条",
                settings.get("memory.distill_interval_sec"),
                settings.get("memory.distill_batch"),
            )
    except Exception as e:
        log.warning("蒸馏没起来：%s", e)

    yield

    # 优雅退出：先停主动私聊（不再发消息），再停网关，最后停蒸馏
    log.info("Novabot 关闭")
    if proactive_task is not None:
        proactive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await proactive_task

    if reminder_task is not None:
        reminder_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reminder_task
    probe_task = getattr(app.state, "platform_probe_task", None)
    if probe_task is not None:
        probe_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await probe_task
    model_probe = getattr(app.state, "model_probe_task", None)
    if model_probe is not None:
        model_probe.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await model_probe
    gw = getattr(app.state, "gateway", None)
    if gw is not None:
        try:
            await gw.stop_all()
        except Exception as e:
            log.warning("停网关出错：%s", e)

    if distill_task is not None:
        distill_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await distill_task

    # 关掉预览用的语音引擎
    voice = getattr(app.state, "voice", None)
    if voice is not None:
        with contextlib.suppress(Exception):
            await voice.close()

    if distill_llm is not None:
        closer = getattr(distill_llm, "close", None)
        if callable(closer):
            with contextlib.suppress(Exception):
                result = closer()
                if asyncio.iscoroutine(result):
                    await result

    # 用量落库，下次启动接着算
    try:
        meter = getattr(app.state, "meter", None)
        if meter is not None:
            d.execute(
                "INSERT OR REPLACE INTO meta (k, v, updated_at) VALUES ('tokens.today', ?, ?)",
                [json.dumps(meter.snapshot_today()), time.time()],
            )
    except Exception as e:  # noqa: BLE001
        log.debug("保存用量失败：%s", e)

    d.close()


def _setup_logging(cfg) -> None:
    """配一次日志。

    `python -m app.main` 和 `uvicorn app.main:app` 都要能看到应用自己的日志——
    只靠 cli() 里配 basicConfig 的话，用 uvicorn 启动时 info 级别的日志会全丢。
    """
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(
        level=getattr(logging, str(cfg.server.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def create_app() -> FastAPI:
    cfg = config.get()
    _setup_logging(cfg)
    app = FastAPI(
        title="Novabot",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.cfg = cfg
    auth_settings = Settings(db.get_db())

    @app.middleware("http")
    async def _auth_middleware(request, call_next):
        """面板密码/平台令牌。密码为空时完全不拦，和以前一样。"""
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        # DSH 工具进度回调没有浏览器 Origin/CSRF，用一次性 token 在接口内校验
        if path == "/api/internal/progress":
            return await call_next(request)
        try:
            from .web.auth import check_request
            from .web.panel_guard import check_panel_request

            panel_remote = bool(auth_settings.get("security.panel_remote"))
            guard = check_panel_request(
                request.method,
                path,
                request.headers,
                request.client.host if request.client else "",
                panel_remote=panel_remote,
            )
            if guard:
                return JSONResponse({"detail": "禁止访问", "reason": guard}, status_code=403)
            if panel_remote and not str(auth_settings.get("security.panel_password") or ""):
                from .web.panel_guard import LOOPBACK

                host = str(request.client.host if request.client else "").split("%", 1)[0].strip("[]")
                if host not in LOOPBACK:
                    return JSONResponse({"detail": "禁止访问", "reason": "远程访问必须先设置面板密码"}, status_code=403)
            reason = check_request(
                path,
                request.headers,
                request.query_params,
                panel_password=str(auth_settings.get("security.panel_password") or ""),
                platform_token=str(auth_settings.get("platform.token") or ""),
            )
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"detail": "安全校验失败", "reason": str(e)[:120]}, status_code=403)
        if reason:
            return JSONResponse({"detail": "未授权", "reason": reason}, status_code=401)
        return await call_next(request)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "version": __version__}

    @app.get("/api/info")
    async def info(account_id: str = ""):
        c = config.get()
        try:
            db_display = str(c.db_path().relative_to(Path.cwd()))
        except Exception:  # noqa: BLE001
            db_display = c.db_path().name
        try:
            database = db.get_db()
            acc_id = account_id
            if not acc_id:
                row = database.query_one(
                    "SELECT id FROM accounts WHERE enabled = 1 ORDER BY sort_order, id LIMIT 1"
                )
                acc_id = str(row["id"]) if row else ""
            st = Settings(database, account_id=acc_id)
            agent_on = bool(st.get("llm.agent_enabled"))
            agent_endpoint = str(st.get("llm.agent_endpoint") or "").strip()
            agent_type = str(st.get("llm.agent_type") or "dsh").upper()
            persona_id = st.get("persona.current") or c.persona.default or None
        except Exception:  # noqa: BLE001
            agent_on, agent_endpoint, agent_type, persona_id = False, "", "DSH", c.persona.default or None
        if agent_on and agent_endpoint:
            model_label = f"{agent_type} Agent · {agent_endpoint.split('//')[-1]}"
        elif agent_on:
            model_label = f"{agent_type} Agent · 未填地址"
        else:
            model_label = c.llm.default or None
        gw = getattr(app.state, "gateway", None)
        gateway_on = bool(getattr(gw, "active", False) or (gw is not None and getattr(gw, "accounts", None)))
        return {
            "version": __version__,
            "db": db_display,
            "llm_default": model_label,
            "model_kind": "agent" if agent_on and agent_endpoint else ("api" if c.llm.default else ""),
            "persona_default": persona_id,
            "voice_enabled": c.voice.enabled,
            "gateway_enabled": gateway_on,
        }

    @app.get("/api/stats")
    async def stats():
        from .memory.migrate import table_counts

        return table_counts(db.get_db())

    # 路由
    for name, mod, attr in (
        ("平台适配", ".web.platform_api", "router"),
        ("设置", ".web.settings_api", "router"),
        ("数据", ".web.data_api", "router"),
        ("网关", ".web.gateway_api", "router"),
        ("账号", ".web.accounts_api", "router"),
        ("语音", ".web.voice_api", "router"),
        ("主动", ".web.proactive_api", "router"),
        ("生活", ".web.life_api", "router"),
        ("内部进度", ".web.progress_api", "router"),
        ("系统", ".web.system_api", "router"),
        ("工具", ".web.tools_api", "router"),
        ("文档", ".web.knowledge_api", "router"),
        ("人格", ".web.persona_api", "router"),
        ("模块", ".web.modules_api", "router"),
        ("插件下载", ".web.plugins_api", "router"),
        ("文件", ".web.files_api", "router"),
        ("权限", ".web.permissions_api", "router"),
        ("表情", ".web.stickers_api", "router"),
    ):
        try:
            import importlib
            m = importlib.import_module(mod, package="app")
            app.include_router(getattr(m, attr))
            log.info("%s 路由已挂", name)
        except Exception as e:
            log.warning("%s 路由没挂上：%s", name, e)

    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

        @app.get("/")
        async def index():
            f = STATIC / "index.html"
            if f.is_file():
                return FileResponse(f)
            return JSONResponse({"ok": True, "hint": "static/index.html 不存在"})

    @app.exception_handler(Exception)
    async def on_error(request, exc):
        rid = uuid.uuid4().hex[:8]
        log.exception("[%s] 请求出错：%s %s", rid, request.method, request.url.path)
        # 只给前端一个引用号，细节留在日志里——别把内部异常原文吐给调用方
        return JSONResponse(
            {"ok": False, "error": "服务器内部错误", "trace_id": rid}, status_code=500
        )

    return app


app = create_app()


def cli() -> None:
    """命令行入口。"""
    import os
    import sys

    import uvicorn

    # 日志里可能有中文，Windows 控制台默认 GBK 会乱码
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cfg = config.get()
    logging.basicConfig(
        level=getattr(logging, cfg.server.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run(
        "app.main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=cfg.server.reload,
        log_level=cfg.server.log_level,
    )


if __name__ == "__main__":
    cli()
