# Novabot

一个跑在自己电脑上的 QQ 机器人：Python 后端 + Web 面板，支持多账号、长期记忆、人格卡和可插拔模型。

> 这是干净的默认发行版：不包含账号、聊天记录、人格卡、语音素材、API Key 或任何本机配置。
> 第一次启动会自动建一个空数据库，按「首次设置」向导填入自己的信息即可。

## 功能

- **QQ 接入**：OneBot v11（SnowLuma / NapCat / Lagrange / LLOneBot 等）
- **多账号隔离**：每个号独立连接、参数、人格、记忆、表情、文件、语音参考音
- **长期记忆**：原话、事实、摘要、人物画像、情绪、联想图、指代消解
- **人格卡**：身份 / 背景 / 性格 / 说话方式 / 边界 / 示例，另有触发关键词与手写片段
- **可插拔模型**：OpenAI 兼容接口、本地 Ollama、假模型；也可接 DSH Agent
- **语音**：GPT-SoVITS / CosyVoice / OpenAI 兼容 / 自定义 HTTP
- **工作台**：笔记、提醒、文档问答
- **主动私聊**（默认关）、**AI 秘书工具**（默认关）
- **对外接口**：DSH 插件、MCP、HTTP 平台接口
- 默认只监听 `127.0.0.1`，网页可设置面板密码

## 环境要求

- Python 3.11+
- Windows / Linux / macOS；桌面窗口目前仅 Windows（需要 WebView2）
- SQLite（Python 自带，建议启用 FTS5）

## 快速开始

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -e .
# 跑测试再装：pip install -e ".[dev]"
```

复制一份本地配置（`local.toml` 不会进版本库）：

```bash
# Windows
copy config\default.toml config\local.toml
# Linux / macOS
cp config/default.toml config/local.toml
```

在 `config/local.toml` 里至少配一个模型：

```toml
[llm]
default = "deepseek"

[llm.deepseek]
type = "openai_compat"
base_url = "https://api.deepseek.com"
api_key = "sk-..."
model = "deepseek-chat"
```

不想先花 API 费用，可以先用假模型把界面跑起来：

```toml
[llm]
default = "mock"

[llm.mock]
type = "mock"
```

启动：

```bash
python -m app.main
# 或
python -m uvicorn app.main:app --host 127.0.0.1 --port 19100
```

打开 <http://127.0.0.1:19100>，按「首次设置」填入主人 QQ，再在「接入」页添加一个 QQ 号。

Windows 也可以双击 `启动Novabot.bat` 打开桌面窗口，双击 `停止Novabot.bat` 停止。
第一次双击会自动创建 `.venv` 并安装依赖（需要 Python 3.11+ 和网络），之后再启动就直接复用。

## 接入 QQ

1. 启动一个 OneBot v11 实现（SnowLuma / NapCat / Lagrange / LLOneBot），让它监听 WebSocket
2. 在网页「接入」页添加账号，填机器人 QQ、OneBot 地址和 Token
3. 也可以在 `config/local.toml` 里写：

```toml
[gateway]
enabled = true

[gateway.accounts.mybot]
bot_qq = "你的机器人QQ"
onebot_url = "ws://127.0.0.1:3001"
onebot_token = ""
```

## 数据与隐私

- 运行时数据都在 `data/`：`novabot.db`（SQLite）、日志、上传文件、表情、语音参考音
- `config/local.toml` 和 `data/` 默认不进版本库（见 `.gitignore`）
- 发行包不附带数据库；第一次启动时自动建表
- 面板默认只监听 `127.0.0.1`；开到局域网或公网前，请先设置面板密码和平台令牌
- 不要把 `config/local.toml`、数据库、日志或包含真人信息的截图提交到公开仓库

## 目录

```text
app/                 应用本体
├── gateway/         OneBot v11 接入
├── core/            对话主流程、注意筛、拆条、计费
├── memory/          记忆、画像、情绪、联想图、蒸馏
├── persona/         人格卡、关键词、手写区、运行状态
├── adapters/        模型 / 平台 / TTS 适配
├── web/             Web 后端与静态页面
└── tools/           工具、笔记、提醒
config/              默认配置（local.toml 自行创建）
data/                运行时数据（dict/ 为内置词典）
dsh-plugin/          DSH 插件
scripts/             运维与冒烟脚本
tests/               测试
```

## 测试

```bash
pytest -q
ruff check app tests scripts
```

冒烟脚本见 `使用指南.txt`。

## 文档

- 使用指南：`使用指南.txt`
- MCP 示例：`mcp-config.example.json`

## 许可

MIT，见 `LICENSE`。
