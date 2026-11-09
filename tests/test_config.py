"""配置文件解析。

重点测两件容易错的事：
  1. `[gateway.accounts.xxx]` 是账号，不能把 `accounts` 自己当成一个账号；
  2. 文档里的 `onebot_url` / `onebot_token` 要能认，不能只认 `url` / `token`。
"""

from __future__ import annotations

import pytest

from app import config


def test_bom_config_is_accepted(tmp_path, monkeypatch):
    """记事本/PS5 写出来的带 BOM 的 TOML 也要能读。"""
    path = tmp_path / "default.toml"
    path.write_bytes(b"\xef\xbb\xbf" + b"[server]\nport = 19001\n")
    monkeypatch.setattr(config, "DEFAULT_CONFIG", path)
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.toml")

    cfg = config.Config.load()

    assert cfg.server.port == 19001


def test_broken_toml_says_which_file(tmp_path, monkeypatch):
    path = tmp_path / "default.toml"
    path.write_text("[server\nport = 1", encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULT_CONFIG", path)
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.toml")

    with pytest.raises(RuntimeError, match="配置文件解析失败"):
        config.Config.load()


def _load_from(tmp_path, monkeypatch, text: str) -> config.Config:
    path = tmp_path / "default.toml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULT_CONFIG", path)
    monkeypatch.setattr(config, "LOCAL_CONFIG", tmp_path / "local.toml")
    return config.Config.load()


def test_gateway_accounts_are_nested_one_level(tmp_path, monkeypatch):
    cfg = _load_from(
        tmp_path,
        monkeypatch,
        """
[gateway]
enabled = true

[gateway.accounts.demo]
platform = "qq"
bot_qq = "10002"
onebot_url = "ws://127.0.0.1:3001"
onebot_token = "t"
""",
    )

    assert cfg.gateway.enabled is True
    assert list(cfg.gateway.accounts) == ["demo"]
    assert cfg.gateway.accounts["demo"]["onebot_url"] == "ws://127.0.0.1:3001"


def test_voice_engines_are_nested_one_level(tmp_path, monkeypatch):
    cfg = _load_from(
        tmp_path,
        monkeypatch,
        """
[voice]
enabled = true
default_engine = "gptsovits"

[voice.engines.gptsovits]
url = "http://127.0.0.1:9880"
""",
    )

    assert cfg.voice.enabled is True
    assert list(cfg.voice.engines) == ["gptsovits"]


def test_llm_models(tmp_path, monkeypatch):
    cfg = _load_from(
        tmp_path,
        monkeypatch,
        """
[llm]
default = "deepseek"
fallback = ["ollama"]

[llm.deepseek]
type = "openai_compat"
base_url = "https://api.deepseek.com"
api_key = "sk-x"
model = "deepseek-chat"
""",
    )

    assert cfg.llm.default == "deepseek"
    assert cfg.llm.fallback == ["ollama"]
    assert cfg.llm.models["deepseek"]["model"] == "deepseek-chat"
    assert "default" not in cfg.llm.models


def test_gateway_account_alias_keys(monkeypatch, settings):
    """配置里写 onebot_url / onebot_token，网关要能用上。"""
    from app.gateway import manager as manager_mod
    from app.gateway import onebot

    async def noop(self):
        return None

    monkeypatch.setattr(onebot.OneBotClient, "start", noop)

    gm = manager_mod.GatewayManager(settings=settings)

    async def run():
        rt = await gm.add_account(
            "a",
            {
                "bot_qq": "1",
                "onebot_url": "ws://127.0.0.1:3001",
                "onebot_token": "secret",
            },
        )
        assert rt.client.cfg.url == "ws://127.0.0.1:3001"
        assert rt.client.cfg.token == "secret"
        await gm.stop_all()

    import asyncio

    asyncio.run(run())
