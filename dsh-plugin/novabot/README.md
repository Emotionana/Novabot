# Novabot（DSH 单插件）

一个插件同时做两件事：

1. **记忆/人格**：DSH 每轮自动 recall、结束后 remember；给模型 3 个工具
2. **模型桥**：把 DSH 当前模型开成本地 HTTP `/novabot/api/chat`，给 Novabot 当 Agent LLM

装：整个目录作为插件包安装（或拷到 DSH 的 plugins），重启 DSH。
`package.json` 里已声明 `dsh.bundle.patch = ./cordis.patch.yml`，不会再有 `declares no dsh.bundle` 错误。
日志出现 `Novabot 插件已加载` 即成功。

## config.json

```json
{
  "endpoint": "http://127.0.0.1:19100",
  "platformToken": "",
  "token": "",
  "accountId": "",
  "botName": "",
  "workspaceRoot": "",
  "model": { "provider": "", "model": "" }
}
```

- `endpoint`：Novabot 地址
- `platformToken`：Novabot 设置 → 安全 → 平台令牌（Novabot 设了才需要）
- `token`：DSH 模型桥自己的入站 token（Novabot → DSH 时带）
- `model`：留空跟随 DSH 当前默认模型
- `accountId`：只记到某个 Novabot 账号，留空不隔离
- `workspaceRoot`：DSH 工作区根目录，留空 `~/Novabot/workspaces`
- 记忆自动 recall/remember，不需要额外开关
- `botName`：兜底名字；Novabot 请求里会带顶部标签名，优先用它

每个 bot 一个工作区，标题是 `Novabot·<顶部标签名字>`。

## Novabot 侧

设置 → 模型：
- 开启「用外部 Agent 平台」
- Agent 平台选 DSH
- Agent 地址：`http://127.0.0.1:<DSH端口>/novabot/api/chat`
- Agent 令牌：和插件里的 `token` 一致（没填就不用）

## 说明

- 旧的两个插件 `novabot-memory` / `novabot-agent` 已合并到这里，装这一个即可
- 只调 DSH `llm.stream`，不碰文件/工具/QQ
- 入站 token 不对返回 401；出错返回 JSON，不会让 QQ 卡住


## DSH 会话

- 每个 QQ 会话首次请求时，在对应 `Novabot·<bot>` 工作区里创建一个 DSH 会话
- 会话标题：`Novabot·<bot> · 私聊/群聊`
- 请求和回复会作为 user/assistant 消息写入该会话，DSH 界面能打开、查看
- 会话映射存 `sessions.json`；重启后优先恢复

> 这是 host 插件，不是客户端 HMR；更新插件后需要重启 DSH 一次。


## 0.4.0：真正走 DSH Agent

- 不再只镜像消息：通过 `agent.followup` 在 DSH 会话里跑真实回合
- 使用 DSH 自带工具（文件读写/删除等），DSH 界面能看到真实对话和工具调用
- workspaceRoot 默认 ~/Novabot/workspaces、sharedWorkspace=false，每个 bot 独立工作区
- agentTimeoutMs 默认 300000，给工具审批留时间
- 失败才回退到直连 llm.stream


## 0.4.1

- 修复 v4 会话 source kind：`novabot`，不再用非法的 `plugin`
- 增加按聊天权限档：读/写/删除/命令/file_send 对应的 DSH 工具限制
- 与 Novabot 的 /api/permissions 联动
