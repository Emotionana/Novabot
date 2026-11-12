"""参数定义。

按《后台配置对比与取舍.md》定的量：
  基础 30 项（界面平铺）
  高级 60 项（折叠）

分组：接入 / 模型 / 回复 / 人格 / 记忆 / 语音 / 工具 / 输出 / 安全 / 界面 / 调试
"""

from __future__ import annotations

from .settings import defparam

# ===========================================================================
# 接入
# ===========================================================================

defparam(
    "gateway.enabled", "启用接入", "bool", False,
    level="basic", group="接入",
    desc="关掉之后不连任何平台，只在网页里用",
    restart=True,
)
defparam(
    "gateway.platform", "平台", "enum", "qq",
    level="basic", group="接入",
    desc="接哪个平台",
    options=[("qq", "QQ"), ("wechat", "微信"), ("web", "只有网页")],
)
defparam(
    "gateway.bot_qq", "机器人 QQ", "str", "",
    level="basic", group="接入",
    desc="机器人自己的号",
)
defparam(
    "gateway.onebot_url", "OneBot 地址", "str", "ws://127.0.0.1:3001",
    level="basic", group="接入",
    desc="SnowLuma 之类的 OneBot 服务地址",
    restart=True,
)
defparam(
    "gateway.onebot_token", "OneBot Token", "str", "",
    level="basic", group="接入",
    desc="连接用的 token，没有就留空",
    secret=True,
    restart=True,
)
defparam(
    "gateway.reconnect_sec", "重连间隔", "int", 5,
    group="接入", unit="秒",
    desc="断线后多久重连",
    min=1, max=300,
)
defparam(
    "gateway.heartbeat_sec", "心跳间隔", "int", 30,
    group="接入", unit="秒",
    desc="太久没心跳就认为断了",
    min=5, max=300,
)
defparam(
    "gateway.backend_autostart", "自动拉起连接插件", "bool", False,
    level="basic", group="接入",
    desc="Novabot 启动时如果 OneBot 端口没监听，就自动运行下面配置的 SnowLuma/其他插件",
)
defparam(
    "gateway.backend_command", "连接插件命令", "str", "",
    level="advanced", group="接入",
    desc="例如 \"C:\\path\\to\\plugins\\SnowLuma-v2.4.0-win-x64\\node.exe\" index.mjs；留空不自启",
)
defparam(
    "gateway.backend_workdir", "连接插件工作目录", "str", "",
    level="advanced", group="接入",
    desc="SnowLuma 的安装目录，例如 C:\\path\\to\\plugins\\SnowLuma-v2.4.0-win-x64",
)
defparam(
    "gateway.backend_check_port", "插件端口检查", "int", 3001,
    level="advanced", group="接入", unit="端口",
    desc="启动前先看这个端口；已经监听就不重复拉起",
    min=0, max=65535,
)
defparam(
    "gateway.msg_buffer", "消息缓冲", "int", 100,
    group="接入", unit="条",
    desc="连上之前先攒着多少条",
    min=0, max=1000,
)

# ===========================================================================
# 模型
# ===========================================================================

defparam(
    "llm.default", "聊天模型", "str", "",
    level="basic", group="模型",
    desc="在 config/local.toml 的 [llm.xxx] 里定义，这里填名字",
)
defparam(
    "llm.cheap", "廉价模型", "str", "",
    group="模型",
    desc="蒸馏、判情绪这种粗活用它，留空就用聊天模型",
)
defparam(
    "llm.max_tokens", "单次输出上限", "int", 300,
    level="basic", group="模型", unit="token",
    desc="太小会说不完，太大会啰嗦",
    min=50, max=8000,
)
defparam(
    "llm.temperature", "温度", "float", 1.0,
    group="模型",
    desc="越低越稳定，越高越随机",
    min=0, max=2, step=0.1,
)
defparam(
    "llm.top_p", "top_p", "float", 1.0,
    group="模型",
    desc="核采样，一般不用改",
    min=0, max=1, step=0.05,
)
defparam(
    "llm.timeout_sec", "超时", "int", 120,
    group="模型", unit="秒",
    desc="超过就放弃这次调用",
    min=10, max=600,
)
defparam(
    "llm.retry", "失败重试", "int", 2,
    group="模型", unit="次",
    desc="调用失败重试几次",
    min=0, max=5,
)
defparam(
    "llm.private_session_compact_turns", "私聊多少轮压缩", "int", 30,
    level="basic", group="模型", unit="轮",
    desc="只影响私聊 DSH 会话；0 关闭", min=0, max=500,
)
defparam(
    "llm.private_session_rotate_turns", "私聊多少轮换会话", "int", 0,
    level="basic", group="模型", unit="轮",
    desc="私聊默认 0，不自动换 DSH 会话；群聊单独设置", min=0, max=1000,
)
defparam(
    "llm.group_session_compact_turns", "群聊多少轮压缩", "int", 0,
    level="basic", group="模型", unit="轮",
    desc="只影响群聊 DSH 会话；0 关闭", min=0, max=500,
)
defparam(
    "llm.group_session_rotate_turns", "群聊多少轮换会话", "int", 20,
    level="basic", group="模型", unit="轮",
    desc="只影响群聊 DSH 会话；0 关闭", min=0, max=1000,
)
defparam(
    "llm.session_compact_turns", "多少轮自动压缩会话", "int", 30,
    level="basic", group="模型", unit="轮", hidden=True,
    desc="DSH 会话到这么多轮自动压缩；0 关闭。长期记忆仍由 Novabot 保存",
    min=0, max=500,
)
defparam(
    "llm.session_rotate_turns", "多少轮自动切换会话", "int", 60,
    level="basic", group="模型", unit="轮", hidden=True,
    desc="到这么多轮换一个新的 DSH 会话；0 关闭。每个私聊/群聊分别计数",
    min=0, max=1000,
)
defparam(
    "llm.history_turns", "带几轮历史", "int", 8,
    level="basic", group="模型", unit="轮",
    desc="太多既费钱又让模型分心",
    min=0, max=50,
)
defparam(
    "llm.history_chars", "历史总字数上限", "int", 1000,
    group="模型", unit="字",
    desc="超出就从最早的开始砍",
    min=100, max=10000,
)

defparam(
    "llm.agent_auto", "自动检测模型", "bool", True,
    level="basic", group="模型",
    desc="启动时和定时探测 DSH Agent 地址；发现可用就自动挂上",
)
defparam(
    "llm.agent_enabled", "用外部 Agent 平台", "bool", False,
    group="模型", level="basic",
    desc="开启后优先走 DSH / TRAE / CodeBuddy 的插件，而不是直连模型 API",
)
defparam(
    "llm.agent_type", "Agent 平台", "enum", "dsh",
    group="模型", level="basic",
    desc="决定协议名和会话映射前缀",
    options=[
        ("dsh", "DSH"),
        ("trae", "TRAE"),
        ("codebuddy", "CodeBuddy"),
        ("custom", "自定义 HTTP"),
    ],
)
defparam(
    "llm.agent_endpoint", "Agent 地址", "str", "",
    group="模型", level="basic",
    desc="插件的 HTTP 地址，如 http://127.0.0.1:55784/novabot/api/chat",
)
defparam(
    "llm.agent_token", "Agent 令牌", "str", "",
    group="模型", level="basic",
    desc="插件要求鉴权时填；同时发 Authorization 和 X-Platform-Token",
    secret=True,
)
defparam(
    "llm.dsh_model", "DSH 会话默认模型", "enum", "deepseek-flash",
    level="basic", group="模型",
    options=[
        ("deepseek-flash", "DeepSeek-V4.1-Flash"),
        ("deepseek-v4-pro", "DeepSeek-V4-Pro"),
    ],
    desc="新建 DSH 会话使用的模型；已有会话不切换",
)
defparam(
    "llm.dsh_reasoning_effort", "DSH 推理挡位", "enum", "low",
    level="basic", group="模型",
    options=[("low", "Low"), ("high", "High"), ("max", "Max")],
    desc="新建 DSH 会话的推理挡位，默认 Low",
)
defparam(
    "llm.agent_timeout_sec", "会话单轮时间上限", "int", 600,
    group="模型", level="basic", unit="秒",
    desc="DSH 单轮最多跑多久；超过必须回复进度，长任务分段做（默认 10 分钟）",
    min=10, max=3600,
)

defparam(
    "platform.auto_detect", "自动检测平台插件", "bool", True,
    group="平台", level="basic",
    desc="定时探测 DSH 桥接插件；TRAE/CodeBuddy 的 MCP 活动从会话映射看",
)
defparam(
    "platform.auto_connect", "检测到就自动连接", "bool", True,
    group="平台", level="basic",
    desc="DSH 桥接健康时自动启用 Agent、类型 DSH、写入地址",
)
defparam(
    "platform.dsh_url", "DSH 地址", "str", "http://127.0.0.1:55784",
    group="平台", level="basic",
    desc="DSH Web 地址；桥接健康检查走 /novabot/api/health",
)
defparam(
    "platform.probe_interval_sec", "检测间隔", "int", 30,
    group="平台", level="basic", unit="秒",
    desc="后台多久探测一次，0 表示只在启动/手动检测",
    min=0, max=600,
)

# ===========================================================================
# 回复
# ===========================================================================

defparam(
    "reply.enabled", "回复总开关", "bool", True,
    level="basic", group="回复",
    desc="关掉之后只记消息不回复",
)
defparam(
    "reply.private_always", "私聊必回", "bool", True,
    level="basic", group="回复",
    desc="关掉之后私聊也要按群里那套规则判",
)
defparam(
    "reply.group_mode", "群里怎么回", "enum", "mention",
    level="basic", group="回复",
    desc="默认只回应 @ 和唤醒词，最不容易烦人",
    options=[
        ("mention", "只回应 @ / 唤醒词"),
        ("smart", "判相关性决定"),
        ("random", "按概率随机"),
        ("always", "每条都回"),
        ("never", "从不主动回"),
    ],
)
defparam(
    "reply.wake_words", "旧唤醒词（兼容）", "list", [],
    level="advanced", group="回复", hidden=True,
    desc="已弃用；唤醒词改为当前人格卡本名 + 别名，这里只给没有激活人格卡时兜底",
)
defparam(
    "reply.poke_wake", "戳一戳也算唤醒", "bool", True,
    level="basic", group="回复",
    desc="有人戳 bot 时触发回复，并告诉模型对方戳了你",
)

defparam(
    "reply.thinking_progress", "DSH 中间说明", "bool", False,
    level="basic", group="回复",
    desc="只转发 DSH 自己产生的中间说明，不发固定“我理一下”文案",
)
defparam(
    "reply.thinking_tools", "中间说明", "bool", False,
    level="basic", group="回复",
    desc="仅在 DSH 有真实中间文本时转发，不再生成工具动作台词",
)
defparam(
    "reply.thinking_max", "最多进度条数", "int", 3,
    group="回复", unit="条",
    desc="包含工具进度，避免刷屏",
    min=1, max=5,
)
defparam(
    "reply.thinking_group", "群聊也显示", "bool", True,
    group="回复",
    desc="只在被 @、被叫到或戳一戳时显示，不打扰普通群聊",
)
defparam(
    "reply.recall_cancels", "撤回取消思考", "bool", True,
    level="basic", group="回复",
    desc="用户撤回原消息时，取消这次回复并删除进度",
)
defparam(
    "reply.progress_endpoint", "进度回调地址", "str", "",
    group="回复", hidden=True,
    desc="DSH 工具进度回调；留空用本机 19100 的默认地址",
)
defparam(
    "reply.owner_always", "主人必回", "bool", True,
    level="basic", group="回复",
    desc="主人在任何场合都回",
)
defparam(
    "reply.ignore_other_bots", "忽略其他 bot", "bool", False,
    level="basic", group="回复",
    desc="群里是否忽略其他 bot；关闭后由时间窗口和条数上限兜底",
)
defparam(
    "reply.min_gap_sec", "同一会话最小间隔", "float", 2.0,
    group="回复", unit="秒", hidden=True,
    desc="兼容旧配置；防抖实际使用 reply.cooldown_sec",
    min=0, max=600,
)
defparam(
    "reply.max_per_min", "每人每分钟上限", "int", 10,
    group="回复", unit="条", hidden=True,
    desc="旧参数；群聊频率限制改用 reply.group_rate_window_sec + reply.group_rate_max_msgs",
    min=0, max=100,
)
defparam(
    "reply.owner_max_per_min", "主人每分钟上限", "int", 0,
    group="回复", unit="条", hidden=True,
    desc="旧参数；群聊频率限制改用 reply.group_rate_window_sec + reply.group_rate_max_msgs",
    min=0, max=100,
)
defparam(
    "reply.group_rate_window_sec", "群聊每人时间窗口", "int", 60,
    level="basic", group="回复", unit="秒",
    desc="群里同一个人在这个时间窗口内最多能被回多少条",
    min=1, max=3600,
)
defparam(
    "reply.group_rate_max_msgs", "群聊每人最多", "int", 5,
    level="basic", group="回复", unit="条",
    desc="群里同一个人在时间窗口内的回复条数上限；0 = 不限",
    min=0, max=500,
)
defparam(
    "reply.cooldown_sec", "防抖", "float", 2.0,
    group="回复", unit="秒",
    desc="收到消息后等这么久；期间又有要回的消息就只回最后一条，所有回复都生效",
    min=0, max=300,
)
defparam(
    "reply.long_task_mode", "长任务轮次", "enum", "manual",
    level="basic", group="回复",
    options=[("off", "不开启"), ("manual", "手动轮次"), ("auto", "自动轮次")],
    desc="到单轮时间上限就取消当前 DSH 任务并返回真实进度；手动轮次等你回复继续，自动轮次自动开下一轮",
)
defparam(
    "reply.long_task_max_rounds", "最多自动轮次", "int", 3,
    level="advanced", group="回复", unit="轮",
    desc="自动轮次最多跑几轮，防止无限续跑", min=1, max=10,
)
defparam(
    "reply.followup_sender_only", "接话窗口只接唤醒者", "bool", True,
    level="basic", group="回复",
    desc="开启后，唤醒后的窗口只回复唤醒者；其他人的消息只进入上下文，不触发回复",
)
defparam(
    "reply.followup_sec", "唤醒保持时间", "float", 8.0,
    group="回复", unit="秒",
    desc="一次唤醒词/点名/@ 后的持续时间；普通消息和回复都不续期",
    min=0, max=120,
)
defparam(
    "reply.ignore_stickers", "纯表情不回", "bool", True,
    group="回复",
    desc="只发表情包不回复",
)
defparam(
    "reply.ignore_min_chars", "少于多少字不回", "int", 2,
    group="回复", unit="字",
    desc="只发一两个字时不接",
    min=0, max=20,
)
defparam(
    "reply.ignore_links_only", "纯链接不回", "bool", True,
    group="回复",
    desc="只发链接不回复",
)
defparam(
    "reply.probability", "随机回复概率", "float", 0.0,
    group="回复", unit="%",
    desc="只在「按概率随机」模式下有效",
    min=0, max=1, step=0.05,
    requires="reply.group_mode",
)
defparam(
    "reply.blacklist_words", "屏蔽词", "list", [],
    group="回复",
    desc="消息里含这些词就不回",
)
defparam(
    "reply.relevance_threshold", "相关性阈值", "float", 0.5,
    group="回复",
    desc="只在「判相关性」模式下有效，越高越不容易被勾话",
    min=0, max=1, step=0.05,
    requires="reply.group_mode",
)
defparam(
    "reply.defer_judge", "判不出时问模型", "bool", True,
    group="回复",
    desc="规则判不出来时，花一次极小调用让模型判要不要接；关掉就当作不回",
    requires="reply.group_mode",
)

# ===========================================================================
# 表情包
# ===========================================================================

defparam(
    "stickers.enabled", "启用表情包", "bool", True,
    level="basic", group="表情",
    desc="总开关；关掉后不发表情、不自动配图、不偷表情",
)
defparam(
    "stickers.collect", "偷表情包", "bool", False,
    level="basic", group="表情",
    desc="收到聊天里的表情时自动收进本账号表情库；默认关",
)
defparam(
    "stickers.auto_send", "回复后自动配表情", "bool", True,
    level="basic", group="表情",
    desc="只在回复成功且本轮没有手动表情时，按概率补一张",
)
defparam(
    "stickers.auto_private", "私聊概率", "int", 6,
    level="advanced", group="表情", unit="%", min=0, max=100,
    desc="私聊回复后自动配表情的概率",
)
defparam(
    "stickers.auto_group", "群聊概率", "int", 12,
    level="advanced", group="表情", unit="%", min=0, max=100,
    desc="群聊回复后自动配表情的概率；按你的设定高于私聊",
)
defparam(
    "stickers.cooldown_sec", "同一聊天冷却", "int", 0,
    level="advanced", group="表情", unit="秒", min=0, max=3600,
    desc="0 = 不冷却；大于 0 时同一聊天两次自动表情的最短间隔",
)
defparam(
    "stickers.collect_per_hour", "每小时最多收", "int", 10,
    level="advanced", group="表情", min=1, max=100,
    desc="偷表情包的每小时上限",
)
defparam(
    "stickers.max_mb", "单张上限", "int", 5,
    level="advanced", group="表情", unit="MB", min=1, max=20,
    desc="表情图片最大体积",
)
defparam(
    "stickers.max_items", "库上限", "int", 1000,
    level="advanced", group="表情", min=50, max=5000,
    desc="每个账号表情库最多保存多少张",
)

# ===========================================================================
# 真实性
# ===========================================================================

defparam(
    "truth.enabled", "真实性优先", "bool", True,
    level="basic", group="真实",
    desc="事实、能力、动作、记忆不许装懂；不知道要说不知道",
)
defparam(
    "truth.work_mode", "工作模式", "enum", "auto",
    level="basic", group="真实",
    options=[("auto", "主人私聊自动识别"), ("always", "始终严格"), ("off", "关闭工作模式")],
    desc="工作/文件/系统话题只讲可验证结果；社交聊天仍可保留语气",
)
defparam(
    "truth.strict_unknown", "不知道就直说", "bool", True,
    level="advanced", group="真实",
    desc="没有记忆/资料/工具结果时明确说不确定",
)
defparam(
    "truth.allow_roleplay", "允许角色扮演和玩笑", "bool", True,
    level="advanced", group="真实",
    desc="可以保留人设语气；但不能用编造事实撑人设",
)
defparam(
    "truth.guard_actions", "拦截假称完成", "bool", True,
    level="advanced", group="真实",
    desc="工作模式没有工具证据却声称已删除/已修改时改成诚实说明",
)
defparam(
    "truth.work_keywords", "工作关键词", "list",
    ["工作", "项目", "代码", "文件", "目录", "删除", "修改", "创建", "发送", "上传", "下载",
     "运行", "命令", "配置", "接口", "数据库", "日志", "测试", "审批", "权限", "任务", "DSH"],
    level="advanced", group="真实",
    desc="自动识别工作模式的关键词",
)

# ===========================================================================
# 媒体
# ===========================================================================

defparam(
    "media.enabled", "读取图片/语音/视频", "bool", True,
    level="basic", group="媒体",
    desc="收到图片、表情包、语音、视频时，先下载再交给视觉/语音模型",
)
defparam(
    "media.vision_auto", "自动检索视觉模型", "bool", True,
    level="basic", group="媒体",
    desc="打开媒体页或启动时自动检索本机 Ollama/LM Studio 的视觉模型",
)
defparam(
    "media.image_enabled", "读图片/表情包", "bool", True,
    level="basic", group="媒体",
    desc="图片和表情包交给视觉模型；关闭后只当普通消息",
)
defparam(
    "media.voice_enabled", "读语音条", "bool", True,
    level="basic", group="媒体",
    desc="语音条先转成文字；接口留空时用本机 faster-whisper",
)
defparam(
    "media.video_enabled", "识别视频", "bool", True,
    level="basic", group="媒体",
    desc="视频抽帧 + 音轨转写；使用已配置的 ffmpeg、视觉和语音识别",
)
defparam(
    "media.vision_mode", "视觉模型", "enum", "dsh",
    level="basic", group="媒体",
    options=[("dsh", "DSH 模型直接看图"), ("chat", "跟聊天模型一样"), ("custom", "单独视觉接口"), ("off", "不读图")],
    desc="默认把图片直接交给 DSH 模型；失败时自动回退到单独视觉接口",
)
defparam(
    "media.image_vision_mode", "图片分析模型", "enum", "dsh",
    level="advanced", group="媒体", hidden=True,
    options=[("dsh", "DSH 模型直接看图"), ("chat", "跟聊天模型一样"), ("custom", "单独视觉接口"), ("off", "不读图")],
    desc="图片/表情包使用；留空或没有单独设置时跟随 media.vision_mode",
)
defparam(
    "media.image_vision_base_url", "图片接口地址", "str", "",
    level="advanced", group="媒体", hidden=True,
    desc="图片单独视觉接口地址；留空跟随回退接口",
)
defparam(
    "media.image_vision_model", "图片模型名", "str", "",
    level="advanced", group="媒体", hidden=True,
    desc="图片单独视觉模型名；留空跟随回退模型",
)
defparam(
    "media.image_vision_api_key", "图片接口密钥", "str", "",
    level="advanced", group="媒体", hidden=True, secret=True,
    desc="图片单独视觉接口密钥；留空表示不需要",
)
defparam(
    "media.video_vision_mode", "视频画面分析模型", "enum", "dsh",
    level="advanced", group="媒体", hidden=True,
    options=[("dsh", "DSH 模型直接看图"), ("chat", "跟聊天模型一样"), ("custom", "单独视觉接口"), ("off", "不读视频画面")],
    desc="视频抽帧使用；留空或没有单独设置时跟随 media.vision_mode",
)
defparam(
    "media.video_vision_base_url", "视频接口地址", "str", "",
    level="advanced", group="媒体", hidden=True,
    desc="视频抽帧单独视觉接口地址；留空跟随回退接口",
)
defparam(
    "media.video_vision_model", "视频模型名", "str", "",
    level="advanced", group="媒体", hidden=True,
    desc="视频抽帧单独视觉模型名；留空跟随回退模型",
)
defparam(
    "media.video_vision_api_key", "视频接口密钥", "str", "",
    level="advanced", group="媒体", hidden=True, secret=True,
    desc="视频抽帧单独视觉接口密钥；留空表示不需要",
)
defparam(
    "media.vision_base_url", "视觉接口地址", "str", "",
    level="advanced", group="媒体",
    desc="DSH 模式不用填；其他 OpenAI 兼容视觉接口的 base_url",
)
defparam(
    "media.vision_api_key", "视觉接口密钥", "str", "",
    level="advanced", group="媒体", secret=True,
    desc="单独视觉接口或回退接口的 API Key；DSH 模式可留空",
)
defparam(
    "media.vision_model", "视觉模型名", "str", "",
    level="advanced", group="媒体",
    desc="DSH 模式可留空；单独接口或回退模型的名称",
)
defparam(
    "media.asr_base_url", "语音识别接口", "str", "",
    level="advanced", group="媒体",
    desc="OpenAI Whisper 兼容 base_url；留空则用本机 faster-whisper",
)
defparam(
    "media.asr_api_key", "语音识别密钥", "str", "",
    level="advanced", group="媒体", secret=True,
    desc="Whisper 兼容接口的 API Key",
)
defparam(
    "media.asr_model", "语音识别模型", "str", "whisper-1",
    level="advanced", group="媒体",
    desc="Whisper 兼容接口的模型名",
)
defparam(
    "media.asr_language", "语音识别语言", "str", "zh",
    level="advanced", group="媒体",
    desc="识别语言，中文一般填 zh",
)
defparam(
    "media.asr_local_python", "本地语音识别 Python", "str", "",
    level="advanced", group="媒体",
    desc="本机 faster-whisper 的 python.exe；留空则不使用本地识别",
)
defparam(
    "media.asr_local_script", "本地语音识别脚本", "str", "",
    level="advanced", group="媒体",
    desc="接收音频路径并输出文字的脚本；配合上面的 Python 使用",
)
defparam(
    "media.ffmpeg_path", "ffmpeg 路径", "str", "ffmpeg",
    level="advanced", group="媒体",
    desc="读视频帧/音轨需要 ffmpeg；填绝对路径或 PATH 里的命令",
)
defparam(
    "media.video_frames", "视频抽帧数", "int", 3,
    level="advanced", group="媒体", min=1, max=6,
    desc="读视频时抽几张图交给视觉模型",
)
defparam(
    "media.max_mb", "单文件上限", "int", 20,
    level="advanced", group="媒体", unit="MB", min=1, max=200,
    desc="图片/语音/视频单个文件最大多少 MB",
)

# ===========================================================================
# 文件
# ===========================================================================

defparam(
    "files.enabled", "启用文件传输", "bool", True,
    level="basic", group="文件",
    desc="接收/发送文件；文件只能进 data/files，不能碰主人其他目录",
)
defparam(
    "files.receive_from", "允许谁发文件", "enum", "owner",
    level="basic", group="文件",
    options=[("owner", "只收主人"), ("whitelist", "白名单"), ("all", "所有人"), ("off", "不收")],
    desc="默认只收主人发的文件；群文件也按这个判断",
)
defparam(
    "files.send_to", "允许发给谁", "enum", "owner",
    level="basic", group="文件",
    options=[("owner", "只发主人"), ("whitelist", "白名单"), ("all", "任何人"), ("off", "不发")],
    desc="默认只允许发给主人私聊",
)
defparam(
    "files.require_approval", "发文件需要审批", "bool", True,
    level="basic", group="文件",
    desc="开启后模型/请求只能生成待审批，主人在面板确认后才真正上传",
)
defparam(
    "files.max_mb", "单文件上限", "int", 20,
    level="advanced", group="文件", unit="MB", min=1, max=200,
    desc="接收和发送都按这个上限",
)
defparam(
    "files.allowed_extensions", "允许的扩展名", "list",
    ["txt", "md", "pdf", "docx", "xlsx", "pptx", "csv", "json", "zip", "png", "jpg", "jpeg", "gif", "webp", "mp3", "wav", "mp4"],
    level="advanced", group="文件",
    desc="白名单；不在列表里的文件拒绝接收/发送",
)
defparam(
    "files.blocked_names", "禁止的文件名", "list",
    [".env", "id_rsa", "id_ed25519", "token", "password", "cookie", "wallet", ".ssh", ".git"],
    level="advanced", group="文件",
    desc="文件名/路径命中这些词直接拒绝",
)
defparam(
    "files.scan_secrets", "扫描敏感内容", "bool", True,
    level="advanced", group="文件",
    desc="发送前扫描私钥、API key、密码等；命中拒绝",
)

# ===========================================================================
# 隐私
# ===========================================================================

defparam(
    "privacy.enabled", "启用隐私隔离", "bool", True,
    level="basic", group="隐私", order=10,
    desc="群聊和别人的私聊不能读取主人私聊/其他私聊的记忆；默认开",
)
defparam(
    "privacy.cross_private", "允许同一个人跨私聊回忆", "bool", False,
    level="advanced", group="隐私", order=40,
    desc="默认关：A 的私聊不能读取 A 在另一个私聊里的内容；开了也只限同一个人自己的私聊",
)
defparam(
    "privacy.profile_detail", "群聊带人物资料", "bool", True,
    level="advanced", group="隐私", order=50,
    desc="允许查人物是谁、昵称、别名和公开画像；这些不是聊天原文",
)
defparam(
    "privacy.documents", "带知识文档", "bool", False,
    level="advanced", group="隐私", order=60,
    desc="知识文档没有会话来源，隐私模式下默认不进群聊/别人私聊",
)
defparam(
    "privacy.block_contacts", "脱敏联系方式", "bool", True,
    level="basic", group="隐私", order=20,
    desc="回复里明确出现手机号/邮箱等联系方式时，用……替换",
)
defparam(
    "privacy.leak_action", "检测到敏感内容", "enum", "refuse",
    level="basic", group="隐私", order=30,
    options=[("refuse", "自动脱敏"), ("off", "只记录不脱敏")],
    desc="回复里出现联系方式或保密词时用……替换，不整段拒答",
)
defparam(
    "privacy.secret_terms", "额外保密词", "list", [],
    level="advanced", group="隐私", order=70,
    desc="这些词/短语在回复里出现时会用……替换",
)

# ===========================================================================
# 聊天
# ===========================================================================

defparam(
    "chat.private_access", "私聊接入", "enum", "all",
    level="basic", group="聊天", order=10,
    options=[("all", "所有人都回"), ("whitelist", "仅白名单"), ("owner", "只回主人"), ("off", "关闭私聊")],
    desc="控制谁能私聊；白名单模式下主人始终允许",
)
defparam(
    "chat.group_access", "群聊接入", "enum", "all",
    level="basic", group="聊天", order=30,
    options=[("all", "所有群"), ("whitelist", "仅白名单群"), ("off", "关闭群聊")],
    desc="控制机器人进入哪些群；关闭后群消息不记录也不回",
)
defparam(
    "chat.owner_title", "主人称呼", "str", "主人",
    level="basic", group="聊天", order=50,
    desc="主人说话时，提示模型这样称呼对方，比如「老板」「哥哥」",
)
defparam(
    "chat.owner_aliases", "主人别名", "list", [],
    level="advanced", group="聊天", order=60,
    desc="主人可能被叫的其他称呼，逗号分隔；只影响提示，不改变主人 QQ 判断",
)

# ===========================================================================
# 模块
# ===========================================================================

defparam(
    "module.song_enabled", "启用翻唱模块", "bool", False,
    level="basic", group="模块",
    desc="打开后可以搜 B 站视频、提取音频；配好引擎/音色后可 RVC 翻唱",
)
defparam(
    "module.song_engine_url", "翻唱引擎地址", "str", "",
    level="basic", group="模块",
    desc="外部换声服务，POST 音频 + voice/pitch；留空只搜索和导入音色",
)
defparam(
    "module.song_engine_token", "翻唱引擎令牌", "str", "",
    level="advanced", group="模块", secret=True,
    desc="引擎要求鉴权时填，作为 Bearer 发送",
)
defparam(
    "module.song_default_voice", "默认音色", "str", "",
    level="basic", group="模块",
    desc="音色 id，可在翻唱模块里导入 .pth / .index / zip",
)
defparam(
    "module.song_pitch", "默认变调", "int", 0,
    level="basic", group="模块", unit="半音", min=-24, max=24,
    desc="女声原唱一般 0；男声原唱一般 +12",
)
defparam(
    "module.song_owner_only", "只允许主人使用", "bool", True,
    level="advanced", group="模块",
    desc="翻唱任务只接受主人发起的请求",
)
defparam(
    "module.song_timeout_sec", "翻唱超时", "int", 1200,
    level="advanced", group="模块", unit="秒", min=60, max=7200,
    desc="下载 + 换声的总超时",
)
defparam(
    "module.bili_cookie", "B 站 Cookie", "str", "",
    level="advanced", group="模块", secret=True,
    desc="可选；填 SESSDATA 等可提高搜索/取流成功率，不填也能匿名提取",
)

defparam(
    "module.netease_cookie", "网易云 Cookie", "str", "",
    level="advanced", group="模块", secret=True,
    desc="VIP/版权歌需要 MUSIC_U；留空只能搜免费歌",
)

# ===========================================================================
# 人格
# ===========================================================================

defparam(
    "persona.current", "当前人格", "str", "",
    level="basic", group="人格",
    desc="人格卡的 id，在人格页管理",
)
defparam(
    "persona.keyword_enabled", "启用关键词触发", "bool", True,
    level="basic", group="人格",
    desc="命中关键词时按百分比调整人格表现",
)
defparam(
    "persona.state_initial_mood", "初始心情", "float", 0.0,
    group="人格",
    desc="-1 到 1，0 是平静",
    min=-1, max=1, step=0.1,
)
defparam(
    "persona.state_initial_affection", "初始好感", "float", 0.3,
    group="人格",
    desc="对陌生人的默认好感",
    min=0, max=1, step=0.1,
)
defparam(
    "persona.state_initial_energy", "初始精力", "float", 0.7,
    group="人格",
    desc="精力低时话会变少",
    min=0, max=1, step=0.1,
)
defparam(
    "persona.state_decay_rate", "状态回落速度", "float", 0.001,
    group="人格",
    desc="每秒往基准值回落多少，越小越慢",
    min=0, max=0.1, step=0.0005,
)
defparam(
    "persona.raw_budget_chars", "手写区字数预算", "int", 200,
    group="人格", unit="字",
    desc="手写片段拼进 prompt 的总字数上限",
    min=0, max=2000,
)
defparam(
    "persona.examples_limit", "带几组示例对话", "int", 2,
    group="人格", unit="组",
    desc="示例效果好但费 token",
    min=0, max=10,
)
defparam(
    "persona.examples_until_turn", "前几轮才带示例", "int", 10,
    group="人格", unit="轮",
    desc="聊久了就不再带示例，省 token",
    min=0, max=100,
)
defparam(
    "persona.switch_cooldown", "切换冷却", "int", 0,
    group="人格", unit="秒",
    desc="防止频繁切人格",
    min=0, max=3600,
)

# ===========================================================================
# 记忆
# ===========================================================================

defparam(
    "memory.enabled", "启用记忆", "bool", True,
    level="basic", group="记忆",
    desc="关掉之后不记也不查",
)
defparam(
    "memory.recall_chars", "上下文长度", "int", 300,
    level="basic", group="记忆", unit="字",
    desc="拼给模型的记忆最多多少字",
    min=50, max=3000,
)
defparam(
    "memory.recent_days", "最近取几天", "int", 7,
    level="basic", group="记忆", unit="天",
    desc="查「最近说了什么」的时间范围",
    min=1, max=90,
)
defparam(
    "memory.episode_retention_days", "原文明细保留", "int", 7,
    level="basic", group="记忆", unit="天",
    desc="超过就标记过期，不物理删",
    min=1, max=365,
)
defparam(
    "memory.distill_enabled", "自动蒸馏", "bool", True,
    level="basic", group="记忆",
    desc="定时把对话提炼成事实和摘要",
)
defparam(
    "memory.profile_chars", "单个人物画像字数", "int", 240,
    group="记忆", unit="字",
    desc="一个人最多带多少字的画像",
    min=50, max=1000,
)
defparam(
    "memory.recent_count", "最近取几条", "int", 5,
    group="记忆", unit="条",
    desc="「最近说了什么」取几条原话",
    min=1, max=30,
)
defparam(
    "memory.facts_limit", "带几条事实", "int", 6,
    group="记忆", unit="条",
    desc="关于这个人带几条已记住的事实",
    min=0, max=30,
)
defparam(
    "memory.fact_retention_days", "事实保留天数", "int", 180,
    group="记忆", unit="天",
    desc="tier 低于 2 的事实在库里留多久",
    min=7, max=3650,
)
defparam(
    "memory.tier1_half_life_days", "短期记忆半衰期（tier1）", "int", 7,
    group="记忆", level="basic", unit="天",
    desc="短期事实：多久不用，权重掉一半（永久档 tier0 不受影响）",
    min=1, max=365,
)
defparam(
    "memory.tier2_half_life_days", "中期记忆半衰期（tier2）", "int", 30,
    group="记忆", level="basic", unit="天",
    desc="中期事实：多久不用，权重掉一半",
    min=1, max=3650,
)
defparam(
    "memory.tier3_half_life_days", "长期记忆半衰期（tier3）", "int", 90,
    group="记忆", level="basic", unit="天",
    desc="长期事实：多久不用，权重掉一半",
    min=1, max=3650,
)
defparam(
    "memory.plasticity_per_hit", "命中延寿系数", "float", 0.5,
    group="记忆", level="basic",
    desc="每被命中一次，遗忘半衰期按 log 增加；0 表示不延寿",
    min=0, max=2, step=0.05,
)
defparam(
    "memory.plasticity_max", "最大延寿倍数", "float", 5.0,
    group="记忆", level="basic", unit="倍",
    desc="可塑性上限：命中再多，半衰期最多乘这个倍数",
    min=1, max=20, step=0.5,
)
defparam(
    "memory.distill_interval_sec", "蒸馏间隔", "int", 180,
    group="记忆", unit="秒",
    desc="多久检查一次有没有要蒸的",
    min=30, max=3600,
    restart=True,
)
defparam(
    "memory.distill_batch", "每次蒸几条", "int", 20,
    group="记忆", unit="条",
    desc="一次喂给模型多少条对话",
    min=1, max=100,
)
defparam(
    "memory.distill_min_chars", "少于多少字不蒸", "int", 10,
    group="记忆", unit="字",
    desc="「嗯」「哦」这种不进队列",
    min=0, max=100,
)
defparam(
    "memory.distill_min_age_sec", "至少多久前的才蒸", "int", 60,
    group="记忆", unit="秒",
    desc="留点时间让话说完，别半句就被蒸掉",
    min=0, max=3600,
)
defparam(
    "memory.emotion_enabled", "情绪判定", "bool", True,
    group="记忆",
    desc="给每条消息打情绪标签",
)
defparam(
    "memory.emotion_rule_threshold", "规则置信度阈值", "float", 0.6,
    group="记忆",
    desc="规则判到这个置信度以上就不调模型",
    min=0, max=1, step=0.05,
)
defparam(
    "memory.emotion_batch", "模型判定批量", "int", 20,
    group="记忆", unit="条",
    desc="一次判多少条，批量省 token",
    min=1, max=100,
)
defparam(
    "memory.emotion_window_days", "情绪基线窗口", "int", 30,
    group="记忆", unit="天",
    desc="用最近多少天算这个人的情绪常态",
    min=1, max=365,
)
defparam(
    "memory.graph_enabled", "联想图", "bool", True,
    group="记忆",
    desc="把人与事连成网，检索时能顺着想到别的",
)
defparam(
    "memory.walk_depth", "游走步数", "int", 2,
    group="记忆", unit="步",
    desc="从一条记忆往外走几步，超过 2 会带出噪音",
    min=1, max=4,
)
defparam(
    "memory.walk_decay", "每步衰减", "float", 0.6,
    group="记忆",
    desc="走一步权重打几折",
    min=0.1, max=1, step=0.05,
)
defparam(
    "memory.walk_min_weight", "剪枝阈值", "float", 0.15,
    group="记忆",
    desc="权重低于这个就不往下走了",
    min=0, max=0.5, step=0.05,
)
defparam(
    "memory.vector_enabled", "向量检索", "bool", False,
    group="记忆",
    desc="需要额外模型，默认关",
)
defparam(
    "memory.vector_sim_threshold", "相似度建边阈值", "float", 0.85,
    group="记忆",
    desc="两条记忆相似到这个程度就自动连边",
    min=0.5, max=1, step=0.01,
    requires="memory.vector_enabled",
)
defparam(
    "memory.prune_enabled", "自动清理", "bool", True,
    group="记忆",
    desc="定期清掉没用的边和过期的向量",
)

# ===========================================================================
# 语音
# ===========================================================================

defparam(
    "voice.reply_budget_sec", "语音合成预算", "float", 10.0,
    group="语音", unit="秒",
    desc="超过这个时间就只发文字，别让整条回复等 TTS；0 表示不限",
    min=0, max=120, step=0.5,
)
defparam(
    "voice.enabled", "启用语音", "bool", False,
    level="basic", group="语音",
    desc="默认关，开了会占显存",
)
defparam(
    "voice.trigger_mode", "什么时候发语音", "enum", "never",
    level="basic", group="语音",
    desc="默认从不，需要时再开",
    options=[
        ("never", "从不"),
        ("asked", "对方要求时"),
        ("boss_only", "只对主人"),
        ("always", "每条都发"),
    ],
)
defparam(
    "voice.reply_mode", "语音发送方式", "enum", "both",
    level="basic", group="语音",
    options=[
        ("both", "语音+文字一起发"),
        ("voice", "只发语音"),
        ("mixed", "多条里部分转语音"),
    ],
    desc="both 保留文字；voice 只发语音；mixed 只把其中一条转语音",
)
defparam(
    "voice.convert_rate", "转换概率", "int", 100,
    level="basic", group="语音", unit="%", hidden=True,
    desc="满足触发条件后，这条回复转语音的概率；0 关闭，100 全部",
    min=0, max=100,
)
defparam(
    "voice.engine_url", "引擎地址（本账号）", "str", "",
    level="advanced", group="语音",
    desc="留空用引擎列表里的地址；每个账号可以填自己的 GPT-SoVITS 端口",
)
defparam(
    "voice.default_engine", "默认引擎", "str", "",
    level="basic", group="语音",
    desc="在 config 的 [voice.engines.xxx] 里定义",
)
defparam(
    "voice.min_chars", "至少多少字才发", "int", 6,
    level="basic", group="语音", unit="字",
    desc="太短的话合成出来很怪",
    min=1, max=100,
)
defparam(
    "voice.max_chars", "超过多少字截断", "int", 60,
    level="basic", group="语音", unit="字",
    desc="太长了合成慢，先截断",
    min=10, max=500,
)
defparam(
    "voice.sticky_max_sec", "参考音粘住上限", "int", 1800,
    group="语音", unit="秒",
    desc="同一条参考音最多连用多久。会话内不会每条都换，否则听着像几个人轮着说话。0 表示一直不换",
    min=0, max=86400,
)
defparam(
    "voice.sticky_on_emotion", "情绪变了就换", "bool", True,
    group="语音",
    desc="情绪明显变化时允许切到更合适的参考音",
)
defparam(
    "voice.fallback_engine", "回退引擎", "str", "",
    group="语音",
    desc="主引擎挂了用这个，注意音色会变",
)
defparam(
    "voice.allow_fallback", "允许回退", "bool", True,
    group="语音",
    desc="关掉的话主引擎挂了就发文字",
)
defparam(
    "voice.log_enabled", "记录合成日志", "bool", True,
    group="语音",
    desc="每次合成记一条，方便查哪条参考音效果好",
)

# ===========================================================================
# 工具
# ===========================================================================

defparam(
    "tools.enabled", "启用工具", "bool", False,
    level="basic", group="工具",
    desc="默认关：开了会多几百 token 的工具说明；关着只能聊天，不能记笔记/提醒",
)
defparam(
    "tools.enabled_list", "启用哪些", "list", [],
    level="basic", group="工具",
    desc="留空表示全部启用",
)
defparam(
    "tools.danger_confirm", "危险操作要确认", "bool", True,
    level="basic", group="工具",
    desc="删文件、执行命令这类操作先问一句",
)
defparam(
    "tools.max_per_call", "单次最多挂几个", "int", 5,
    group="工具", unit="个",
    desc="工具定义很占 token，别贪多",
    min=0, max=30,
)
defparam(
    "tools.lazy_select", "按需选工具", "bool", True,
    group="工具",
    desc="只在话题相关时才挂对应工具",
)
defparam(
    "tools.timeout_sec", "工具超时", "int", 30,
    group="工具", unit="秒",
    desc="单个工具最多跑多久",
    min=1, max=600,
)

# ===========================================================================
# 输出
# ===========================================================================

defparam(
    "output.split_enabled", "长回复拆条", "bool", True,
    level="basic", group="输出",
    desc="太长的话拆成几条发，像真人打字",
)
defparam(
    "output.clean_markdown", "去掉 Markdown", "bool", True,
    level="basic", group="输出",
    desc="QQ 不渲染 Markdown，留着反而难看",
)
defparam(
    "output.sticker_enabled", "允许发表情", "bool", True,
    level="basic", group="输出",
    desc="按情绪挑表情包",
)
defparam(
    "output.split_chars", "超过多少字拆", "int", 42,
    group="输出", unit="字", order=10,
    desc="一条消息多长之后开始拆",
    min=10, max=500,
)
defparam(
    "output.split_max", "最多拆几条", "int", 4,
    group="输出", unit="条", order=20,
    desc="拆太多像刷屏",
    min=1, max=20,
)
defparam(
    "output.group_split_chars", "群聊消息字数上限", "int", 50,
    level="basic", group="输出", unit="字", order=11,
    desc="群聊里一条消息超过这个字数就拆；私聊仍按「超过多少字拆」。内容特别长时优先保证条数上限，单条可能略超。",
    min=10, max=500,
)
defparam(
    "output.group_split_max", "群聊拆分条数上限", "int", 3,
    level="basic", group="输出", unit="条", order=21,
    desc="群聊最多拆几条，防止刷屏；私聊不受影响",
    min=1, max=20,
)
defparam(
    "output.part_delay_min", "拆条最小间隔", "float", 0.25,
    group="输出", unit="秒", min=0, max=5, step=0.05, order=30,
    desc="拆成多条时，第一条之后每条前至少等多久",
)
defparam(
    "output.part_delay_max", "拆条最大间隔", "float", 0.7,
    group="输出", unit="秒", min=0, max=5, step=0.05, order=40,
    desc="拆条间隔上限；会按每条长度随机，避免机械连发",
)
defparam(
    "output.filter_words", "屏蔽词", "list", [],
    group="输出",
    desc="回复里出现这些词就替换掉",
)
defparam(
    "output.typing_delay", "模拟打字延迟", "bool", False,
    group="输出",
    desc="按字数等一会儿再发，更像真人",
)

# ===========================================================================
# 安全
# ===========================================================================

defparam(
    "security.owner_qq", "主人 QQ", "str", "",
    level="basic", group="安全", hidden=True,
    desc="主人有全部权限；在运行安全里修改",
)
defparam(
    "security.private_whitelist", "私聊白名单（QQ）", "list", [],
    level="basic", group="聊天", order=20,
    desc="private_access=whitelist 时生效；主人始终允许",
)
defparam(
    "security.group_whitelist", "群白名单（群号）", "list", [],
    level="basic", group="聊天", order=40,
    desc="group_access=whitelist 时生效；留空表示不允许任何群",
)
defparam(
    "security.danger_confirm", "危险操作确认", "bool", True,
    level="basic", group="安全",
    desc="删文件、改配置这类操作前先问一句",
)
defparam(
    "runtime.master_enabled", "总开关", "bool", True,
    level="basic", group="安全", hidden=True,
    desc="关掉后不向 QQ 发送任何消息，连接和面板仍可用",
)
defparam(
    "runtime.dry_run", "全局试运行", "bool", False,
    level="basic", group="安全", hidden=True,
    desc="只写日志，不真正发到 QQ",
)
defparam(
    "runtime.setup_done", "首次设置已完成", "bool", False,
    group="安全", hidden=True,
    desc="首次设置向导是否已经完成",
)
defparam(
    "security.panel_remote", "允许远程访问面板", "bool", False,
    group="安全",
    desc="关掉只能本机访问",
    restart=True,
)
defparam(
    "platform.token", "平台令牌", "str", "",
    group="安全",
    desc="给 /api/platform/* 用的长期令牌，和面板密码分开；留空则退回面板密码",
    secret=True,
)
defparam(
    "security.panel_password", "面板密码", "str", "",
    group="安全",
    desc="允许远程访问时必填",
    secret=True,
    restart=True,
)
defparam(
    "security.rate_limit", "请求限流", "int", 60,
    group="安全", unit="次/分",
    desc="面板 API 每分钟最多调几次",
    min=1, max=1000,
)

# ===========================================================================
# 主动私聊
# ===========================================================================

defparam(
    "proactive.enabled", "启用主动私聊", "bool", False,
    level="basic", group="生活",
    desc="关着就不会主动找人；开了也要满足下面的时间和频率条件",
)
defparam(
    "proactive.quiet_start", "安静时段开始", "int", 23,
    level="basic", group="生活", unit="点",
    desc="这个点之后不主动（跨午夜）",
    min=0, max=23,
)
defparam(
    "proactive.quiet_end", "安静时段结束", "int", 8,
    level="basic", group="生活", unit="点",
    desc="这个点之前不主动",
    min=0, max=23,
)
defparam(
    "proactive.min_silence_hours", "至少多久没说话", "float", 2.0,
    level="basic", group="生活", unit="小时",
    desc="刚聊完不打扰",
    min=0.5, max=720, step=0.5,
)
defparam(
    "proactive.max_per_day", "每人每天上限", "int", 6,
    level="basic", group="生活", unit="条",
    desc="同一个人一天最多主动几次",
    min=0, max=10,
)
defparam(
    "proactive.score_threshold", "分数门槛", "float", 0.45,
    group="生活",
    desc="低于这个分就不发；调高更挑剔，调低更频繁",
    min=0, max=1, step=0.01,
)
defparam(
    "proactive.tick_sec", "检查间隔", "int", 300,
    group="生活", unit="秒",
    desc="每隔多久算一次",
    min=60, max=3600, restart=True,
)
defparam(
    "proactive.min_messages", "最少消息数", "int", 30,
    group="生活", unit="条",
    desc="收到过这么多条才考虑主动，避免对陌生人搭话",
    min=1, max=10000,
)
defparam(
    "proactive.min_days", "最少认识天数", "int", 2,
    group="生活", unit="天",
    desc="认识太久之前的也不算",
    min=0, max=365,
)
defparam(
    "proactive.active_guard_min", "刚说话不打扰", "int", 10,
    group="生活", unit="分钟",
    desc="对方最近 N 分钟说过话就不插话",
    min=0, max=240,
)
defparam(
    "proactive.owner_enabled", "也找主人", "bool", True,
    group="生活",
    desc="关掉就只找非主人",
)
defparam(
    "proactive.owner_max_per_day", "主人每天上限", "int", 6,
    group="生活", unit="条",
    desc="主人单独的上限",
    min=0, max=20,
)
defparam(
    "proactive.global_max_per_day", "全局每天上限", "int", 15,
    group="生活", unit="条",
    desc="所有账号加起来一天最多发几条",
    min=0, max=100,
)
defparam(
    "proactive.slot_morning", "上午主动次数", "int", 1,
    level="basic", group="生活", unit="次",
    desc="08:00–11:00 每个人最多主动几次；0 = 这个时段不发",
    min=0, max=5,
)
defparam(
    "proactive.slot_noon", "中午主动次数", "int", 1,
    level="basic", group="生活", unit="次",
    desc="11:00–14:00 每个人最多主动几次；0 = 这个时段不发",
    min=0, max=5,
)
defparam(
    "proactive.slot_afternoon", "下午主动次数", "int", 2,
    level="basic", group="生活", unit="次",
    desc="14:00–18:00 每个人最多主动几次；0 = 这个时段不发",
    min=0, max=5,
)
defparam(
    "proactive.slot_evening", "晚上主动次数", "int", 2,
    level="basic", group="生活", unit="次",
    desc="18:00–23:00 每个人最多主动几次；0 = 这个时段不发",
    min=0, max=5,
)
defparam(
    "proactive.wait_reply_hours", "等回复多久", "int", 24,
    group="生活", unit="小时",
    desc="超过这个时间没回，算被无视并进入退避",
    min=1, max=240,
)
defparam(
    "proactive.interval_hours", "回复后再找间隔", "int", 1,
    group="生活", unit="小时",
    desc="对方回复后，至少隔这么久才可能再主动",
    min=0, max=720,
)
defparam(
    "proactive.backoff_base_days", "被无视后降频", "int", 2,
    group="生活", unit="次",
    desc="被无视后每天上限降低几次，最多降两档",
    min=1, max=4,
)
defparam(
    "proactive.max_chars", "消息长度上限", "int", 60,
    group="生活", unit="字",
    desc="太长显得像小作文",
    min=10, max=200,
)
defparam(
    "proactive.habit_days", "习惯统计天数", "int", 30,
    group="生活", unit="天",
    desc="用最近多少天的消息学作息",
    min=3, max=365,
)
defparam(
    "proactive.dry_run", "只算不发", "bool", False,
    group="生活",
    desc="调试用：算出该找谁、说什么，但不真的发出去",
)
defparam(
    "proactive.only_owner", "只找主人", "bool", False,
    group="生活",
    desc="开了就完全不碰其他人",
)

# ===========================================================================
# 界面
# ===========================================================================

defparam(
    "server.port", "端口", "int", 19100,
    level="basic", group="界面", unit="",
    desc="网页面板的端口",
    min=1024, max=65535,
    restart=True,
)
defparam(
    "server.host", "监听地址", "str", "127.0.0.1",
    group="界面",
    desc="改成 0.0.0.0 才能局域网访问",
    restart=True,
)
defparam(
    "server.timezone", "时区", "str", "Asia/Shanghai",
    group="界面",
    desc="时间显示和统计用，服务器在别的时区也不会错",
    restart=True,
)
defparam(
    "server.log_level", "日志级别", "enum", "info",
    group="界面",
    desc="排查问题时调到 debug",
    options=[("debug", "debug"), ("info", "info"), ("warning", "warning"), ("error", "error")],
)

# ===========================================================================
# 调试
# ===========================================================================

defparam(
    "debug.token_log", "记录 token 用量", "bool", True,
    group="调试",
    desc="每次模型调用记一条，能看出钱花在哪",
)
defparam(
    "debug.decision_log", "记录决策日志", "bool", False,
    group="调试",
    desc="记下每条消息为什么回为什么不回",
)
defparam(
    "debug.memory_log", "记录检索结果", "bool", False,
    group="调试",
    desc="记下每次检索取了哪几条，调记忆时用",
)
defparam(
    "debug.log_retention_days", "日志保留天数", "int", 7,
    group="调试", unit="天",
    desc="超过就删",
    min=1, max=365,
)
