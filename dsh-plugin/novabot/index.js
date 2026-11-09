/**
 * Novabot DSH 单插件（Host-only bundle）
 *
 * - POST /novabot/api/chat：用 DSH 当前模型给 Novabot 回复
 * - 每轮自动 recall / remember（走 Novabot /api/platform/*）
 * - 按 bot 建 DSH 工作区：Novabot·<顶部标签名字>
 *
 * 只 inject DSH 确认存在的服务：llm / webServer / log。
 * 不碰 systemPrompt / hooks / tools，避免组件激活时被服务注入卡住。
 */
import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'novabot'
export const inject = ['llm', 'webServer']

const HERE = dirname(fileURLToPath(import.meta.url))
const DEFAULTS = {
  endpoint: 'http://127.0.0.1:19100',
  platformToken: '',
  token: '',
  accountId: '',
  botName: '',
  workspaceRoot: '',
  sharedWorkspace: false,
  agentPreset: '',
  model: { provider: '', model: '' },
}

function loadConfig() {
  const p = join(HERE, 'config.json')
  if (!existsSync(p)) return { ...DEFAULTS }
  try {
    const raw = JSON.parse(readFileSync(p, 'utf8'))
    return { ...DEFAULTS, ...raw, model: { ...DEFAULTS.model, ...(raw.model || {}) } }
  } catch {
    return { ...DEFAULTS }
  }
}

async function callNovabot(cfg, path, body, timeoutMs = 6000) {
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), timeoutMs)
  const headers = { 'Content-Type': 'application/json' }
  if (cfg.platformToken) headers['X-Platform-Token'] = cfg.platformToken
  try {
    const r = await fetch(cfg.endpoint.replace(/\/+$/, '') + path, {
      method: 'POST', headers, body: JSON.stringify(body), signal: ctl.signal,
    })
    if (!r.ok) return { ok: false, error: `HTTP ${r.status}` }
    return await r.json()
  } catch (e) {
    return { ok: false, error: String(e?.message || e) }
  } finally {
    clearTimeout(timer)
  }
}

function contentOf(m) {
  const c = m && m.content
  if (typeof c === 'string') return c
  if (Array.isArray(c)) return c.map((x) => (typeof x === 'string' ? x : x?.text || '')).join('')
  return ''
}

function lastUser(messages) {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i]?.role === 'user') return contentOf(messages[i])
  }
  return ''
}

function safeName(value) {
  return String(value || 'bot').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'bot'
}

async function ensureWorkspace(ctx, cfg, payload) {
  const reg = ctx.get?.('workspaceRegistry')
  if (!reg || typeof reg.create !== 'function') return null
  const botName = String(payload.bot_name || cfg.botName || 'Bot').trim() || 'Bot'
  const key = String(payload.account_id || botName)
  const root = String(cfg.workspaceRoot || '').trim() || join(homedir(), 'Novabot', 'workspaces')
  const shared = Boolean(cfg.sharedWorkspace) && root
  const dir = shared ? root : join(root, safeName(key))
  mkdirSync(dir, { recursive: true })
  const title = shared ? 'Novabot·共享工作区' : `Novabot·${botName}`
  const norm = (p) => String(p || '').replace(/[\\/]+$/, '').toLowerCase()
  let ws = null
  try { ws = (reg.list?.() || []).find((w) => norm(w.path) === norm(dir)) || null } catch {}
  if (!ws) {
    ws = await reg.create(dir, title)
    console.info(`建立 DSH 工作区「${title}」：${dir}`)
  } else if (ws.title !== title && typeof reg.rename === 'function') {
    try { await reg.rename(ws.id, title) } catch (e) { console.warn(`工作区改名失败：${e?.message || e}`) }
  }
  return ws
}

function sessionMapFile() {
  return join(HERE, 'sessions.json')
}

function loadSessionMap() {
  try { return JSON.parse(readFileSync(sessionMapFile(), 'utf8')) } catch { return {} }
}

function saveSessionMap(map) {
  try { writeFileSync(sessionMapFile(), JSON.stringify(map, null, 2)) } catch (e) { console.warn(`保存 DSH 会话映射失败：${e?.message || e}`) }
}

function sessionTitle(payload, botName) {
  const sid = String(payload.session_id || 'chat')
  let tail = sid
  if (sid.startsWith('private:')) tail = `私聊 ${sid.slice(8)}`
  else if (sid.startsWith('group:')) tail = `群 ${sid.slice(6)}`
  else if (sid.startsWith('internal:')) tail = `内部 ${sid.slice(9)}`
  return `Novabot·${botName} · ${tail}`
}

function sessionPolicy(payload) {
  const policy = payload?.session_policy || {}
  return {
    compactTurns: Math.max(0, Number(policy.compact_turns) || 0),
    rotateTurns: Math.max(0, Number(policy.rotate_turns) || 0),
  }
}

function sessionMapKey(payload, cfg) {
  return `${payload.account_id || cfg.accountId || 'bot'}:${payload.session_id || 'chat'}`
}

async function ensureSession(ctx, cfg, payload, ws, sel, forceNew = false) {
  const ctl = ctx.get?.('sessionController')
  if (!ctl || typeof ctl.create !== 'function' || !ws?.id) return null
  const key = sessionMapKey(payload, cfg)
  const map = loadSessionMap()
  const entry = map[key] || {}
  const policy = sessionPolicy(payload)
  const old = entry.sessionId
  const rotate = Boolean(old) && policy.rotateTurns > 0 && Number(entry.turns || 0) >= policy.rotateTurns
  if (rotate) console.info(`DSH 会话轮数到 ${policy.rotateTurns}，切换新会话：${key}`)
  if (old && !rotate && !forceNew) {
    try {
      const r = await ctl.resolveAgent(old)
      if (r?.agent) return { agent: r.agent, sessionId: String(old), isNew: false, key }
    } catch (e) { console.warn(`恢复 DSH 会话失败，换新的：${e?.message || e}`) }
  }
  const agentOptions = (sel?.provider && sel?.model)
    ? { provider: String(sel.provider), model: String(sel.model), ...(sel.reasoningEffort ? { reasoningEffort: String(sel.reasoningEffort) } : {}) }
    : null
  const created = await ctl.create({
    workspaceId: ws.id,
    ...(cfg.agentPreset ? { agentPreset: cfg.agentPreset } : {}),
    ...(agentOptions ? { agentOptions } : {}),
  })
  const sid = String(created.sessionId || '')
  if (!sid) return null
  const botName = String(payload.bot_name || cfg.botName || 'Bot').trim() || 'Bot'
  try { if (typeof ctl.rename === 'function') await ctl.rename({ sessionId: sid, title: sessionTitle(payload, botName) }) } catch (e) { console.warn(`DSH 会话改名失败：${e?.message || e}`) }
  const r = await ctl.resolveAgent(sid)
  if (!r?.agent) return null
  map[key] = {
    sessionId: sid, title: sessionTitle(payload, botName),
    turns: 0, turnsSinceCompact: 0, createdAt: Date.now(), updatedAt: Date.now(),
  }
  saveSessionMap(map)
  return { agent: r.agent, sessionId: sid, isNew: true, key }
}

function invalidateSession(payload, cfg) {
  try {
    const key = sessionMapKey(payload, cfg)
    const map = loadSessionMap()
    if (map[key]) {
      delete map[key]
      saveSessionMap(map)
      console.warn(`DSH 会话映射已清除，下次请求建新会话：${key}`)
    }
  } catch (e) {
    console.warn(`清除 DSH 会话映射失败：${e?.message || e}`)
  }
}

function isSessionError(error) {
  const text = String(error?.message || error || '').toLowerCase()
  return ['session', '会话', 'archiv', '归档', 'not found', '不存在', 'closed',
          '已关闭', 'disposed', 'invalid', '无效'].some((x) => text.includes(x))
}

function bumpSessionTurns(payload, session) {
  if (!session?.key) return
  const map = loadSessionMap()
  const entry = map[session.key]
  if (!entry) return
  entry.turns = Number(entry.turns || 0) + 1
  entry.turnsSinceCompact = Number(entry.turnsSinceCompact || 0) + 1
  entry.updatedAt = Date.now()
  saveSessionMap(map)
}

async function maybeCompact(ctx, payload, session) {
  const policy = sessionPolicy(payload)
  if (!session?.key || policy.compactTurns <= 0) return
  const map = loadSessionMap()
  const entry = map[session.key]
  if (!entry || Number(entry.turnsSinceCompact || 0) < policy.compactTurns) return
  const lastTry = Number(entry.lastCompactAttempt || 0)
  if (Date.now() - lastTry < 60_000) return
  const compaction = ctx.get?.('compaction')
  if (!compaction || typeof compaction.compactNow !== 'function') {
    console.warn('DSH 没有 compaction 服务，跳过自动压缩（仍会按轮数切换会话）')
    return
  }
  entry.lastCompactAttempt = Date.now()
  saveSessionMap(map)
  try {
    const controller = new AbortController()
    const result = await compaction.compactNow(session.agent, controller.signal, `novabot-${Date.now()}`)
    entry.turnsSinceCompact = 0
    entry.lastCompactAt = Date.now()
    saveSessionMap(map)
    console.info(`DSH 会话已自动压缩：${session.key}${result ? `（${result.shadowedTokenCount} tokens）` : ''}`)
  } catch (e) {
    console.warn(`DSH 自动压缩失败，下轮再试：${e?.message || e}`)
  }
}

function messageOf(role, text, source) {
  return { id: randomUUID(), role, content: [{ type: 'text', text }], source }
}

async function mirrorTurn(ctx, session, payload, out, sel) {
  if (!session?.agent?.session || !out.trim()) return
  try {
    const sess = session.agent.session
    const events = typeof sess.snapshotEvents === 'function' ? sess.snapshotEvents() : []
    let lastTurn = 0
    for (const e of events) {
      if (e?.type === 'turn/start' && Number(e.data?.turn) > lastTurn) lastTurn = Number(e.data.turn)
    }
    const turn = lastTurn + 1
    const userText = lastUser((Array.isArray(payload.messages) ? payload.messages : [])) || ''
    const user = messageOf('user', userText, { kind: 'novabot', speaker: { role: 'none' } })
    const assistant = messageOf('assistant', out.trim(), {
      kind: 'model', provider: sel.provider, model: sel.model,
    })
    sess.append('turn/start', { turn })
    sess.append('step/start', { turn, step: 1 })
    sess.append('user/message', user, { surfaceOp: 'append' })
    sess.append('assistant/message', { turn, step: 1, message: assistant, stream: [] })
    sess.append('step/end', { turn, step: 1 })
    sess.append('turn/end', { turn, reason: { kind: 'completed' } })
    const sessions = ctx.get?.('sessions')
    if (sessions && typeof sessions.flush === 'function') await sessions.flush(sess)
    console.info(`DSH 会话已记录：${session.sessionId}`)
  } catch (e) {
    console.warn(`DSH 会话记录失败（不影响聊天）：${e?.message || e}`)
  }
}

function json(res, status, body) {
  try {
    res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' })
    res.end(JSON.stringify(body))
  } catch {}
}

async function readBody(req) {
  let text = ''
  try {
    for await (const chunk of req) text += chunk
  } catch {}
  try { return JSON.parse(text || '{}') } catch { return {} }
}

function authOk(req, cfg) {
  if (!cfg.token) return true
  const auth = String(req.headers.authorization || '')
  const token = String(req.headers['x-platform-token'] || '')
  return auth === `Bearer ${cfg.token}` || token === cfg.token
}

const TURN_WAITERS = new Map()
const SESSION_CONTEXTS = new Map()
const SESSION_LOCKS = new Map()
const SESSION_POLICIES = new Map()
const APPROVAL_PERMISSIONS = new Map()
const ACTIVE_BY_NB = new Map()

function assistantText(message) {
  if (!message || !Array.isArray(message.content)) return ''
  if (message.content.some((b) => b && (b.type === 'tool-call' || b.type === 'tool_use'))) return ''
  return message.content
    .filter((b) => b && b.type === 'text' && typeof b.text === 'string')
    .map((b) => b.text).join('\n').trim()
}

function postProgress(progress, payload) {
  fetch(progress.url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      token: progress.token,
      turn_id: progress.turn_id,
      ...payload,
    }),
  }).catch(() => {})
}

function sendToolProgress(progress, event) {
  if (!progress?.url || !progress?.token) return
  const type = String(event?.type || '')
  if (!['tool/call', 'tool/start', 'tool/result', 'tool/end'].includes(type)) return
  const data = event?.data || {}
  const tool = String(data.name || data.tool?.name || data.call?.name || '')
  postProgress(progress, { type, tool })
}

function sendAssistantProgress(progress, event) {
  if (!progress?.url || !progress?.token) return
  if (String(event?.type || '') !== 'assistant/message') return
  const message = event?.data?.message
  const blocks = Array.isArray(message?.content) ? message.content : []
  // 只转发“带工具调用的中间说明”，不把最终回答提前发出去，也不发原始 reasoning
  const hasTool = blocks.some((b) => b && (b.type === 'tool-call' || b.type === 'tool_use'))
  if (!hasTool) return
  const text = blocks.filter((b) => b?.type === 'text').map((b) => b.text || '').join('\n').trim()
  if (!text) return
  postProgress(progress, { type: 'assistant/commentary', text: text.slice(0, 240) })
}

function onSessionEvent(session, event) {
  if (!session || !event) return
  const key = String(session.id || '')
  const waiter = TURN_WAITERS.get(key)
  if (!waiter) return
  const pushLine = (line) => {
    const text = String(line || '').trim()
    if (text && waiter.lines[waiter.lines.length - 1] !== text) {
      waiter.lines.push(text.slice(0, 180))
      waiter.lines = waiter.lines.slice(-8)
    }
  }
  sendToolProgress(waiter.progress, event)
  sendAssistantProgress(waiter.progress, event)
  if (event.type === 'tool/call' || event.type === 'tool/start') {
    waiter.tools.calls += 1
    const data = event.data || {}
    waiter.lastTool = String(data.name || data.tool?.name || data.call?.name || waiter.lastTool || '')
    const args = data.arguments || data.args || data.input || {}
    let argText = ''
    try { argText = typeof args === 'string' ? args : Object.keys(args || {}).slice(0, 4).join(', ') } catch {}
    pushLine(`调用工具 ${waiter.lastTool || 'unknown'}${argText ? `（${argText}）` : ''}`)
    return
  }
  if (event.type === 'tool/result' || event.type === 'tool/end') {
    waiter.tools.results += 1
    const failed = Boolean(event.data?.isError || event.data?.error || event.data?.result?.isError || event.data?.result?.error)
    if (failed) waiter.tools.errors += 1
    const name = String(event.data?.name || waiter.lastTool || '工具')
    let doneText = ''
    try {
      const out = event.data?.result?.content || event.data?.result || event.data?.output
      doneText = typeof out === 'string' ? out : JSON.stringify(out || '')
    } catch {}
    pushLine(`${name} ${failed ? '失败' : '完成'}${doneText ? `：${doneText.slice(0, 100)}` : ''}`)
    return
  }
  if (event.type === 'turn/start') {
    if (waiter.turn == null) {
      waiter.turn = Number(event.data?.turn)
      waiter.started = true
    }
    return
  }
  if (event.type === 'assistant/message') {
    const turn = Number(event.data?.turn)
    if (waiter.turn == null || waiter.turn === turn) waiter.text = assistantText(event.data?.message) || waiter.text
    return
  }
  if (event.type === 'turn/end') {
    const turn = Number(event.data?.turn)
    if (waiter.turn != null && turn !== waiter.turn) return
    const done = event.data?.reason?.kind === 'completed'
    TURN_WAITERS.delete(key)
    clearTimeout(waiter.timer)
    if (done) waiter.resolve({ text: waiter.text || '', tools: waiter.tools, timed_out: false })
    else {
      const detail = String(event.data?.reason?.error?.message || event.data?.reason?.failure?.message || event.data?.reason?.kind || '')
      waiter.error = `DSH 这一轮没有正常结束${detail ? `：${detail.slice(0, 160)}` : ''}`
      waiter.resolve({ text: waiter.text || '', tools: waiter.tools, error: waiter.error, timed_out: false })
    }
  }
}

function withSessionLock(key, fn) {
  const prev = SESSION_LOCKS.get(key) || Promise.resolve()
  const next = prev.then(fn, fn)
  SESSION_LOCKS.set(key, next.finally(() => {
    if (SESSION_LOCKS.get(key) === next) SESSION_LOCKS.delete(key)
  }))
  return next
}

function installSystemContext(agent, system) {
  const key = String(agent?.session?.id || agent?.id || '')
  if (!key || !system) return false
  const prior = SESSION_CONTEXTS.get(key)
  if (prior) { prior.system = system; return true }
  try {
    const disposer = agent.ctx?.systemPrompt?.context?.({
      name: 'novabot-context',
      order: 900,
      text: () => {
        const state = SESSION_CONTEXTS.get(key)
        return state?.system || ''
      },
    })
    SESSION_CONTEXTS.set(key, { system, disposer })
    return true
  } catch (e) {
    console.warn(`Novabot 会话上下文注入失败：${e?.message || e}`)
    return false
  }
}

const TOOL_GROUPS = {
  read: ['read', 'read_file', 'view_file', 'cat', 'glob', 'grep', 'ls', 'list_dir', 'find'],
  write: ['write', 'write_file', 'create_file', 'edit', 'edit_file', 'apply_patch', 'str_replace'],
  delete: ['delete', 'delete_file', 'remove', 'rm', 'move_file', 'mv'],
  shell: ['bash', 'shell', 'exec', 'run_command', 'terminal', 'powershell', 'pwsh', 'cmd', 'run'],
}

function deniedToolsFor(permissions) {
  const p = permissions && typeof permissions === 'object'
    ? permissions
    : { read: true, write: false, delete: false, shell: false }
  const deny = []
  for (const [cap, names] of Object.entries(TOOL_GROUPS)) {
    if (!p[cap]) deny.push(...names)
  }
  // DSH 的 delete 没有独立工具；没有 shell 权限或 delete 权限时都禁用 pwsh
  if (!p.shell || !p.delete) deny.push('pwsh')
  return [...new Set(deny)]
}

function applyToolPolicy(agent, permissions) {
  const sessionId = String(agent?.session?.id || agent?.id || '')
  if (!sessionId || !agent?.ctx?.tools?.restrict) return
  const deny = deniedToolsFor(permissions)
  const key = sessionId
  const prior = SESSION_POLICIES.get(key)
  if (prior && prior.signature === deny.join(',')) return
  if (!deny.length) {
    try { if (prior?.dispose) prior.dispose() } catch (e) {}
    SESSION_POLICIES.delete(key)
    return
  }
  try {
    if (prior?.dispose) {
      try { prior.dispose() } catch (e) {}
    }
    const dispose = agent.ctx.tools.restrict({ deny })
    SESSION_POLICIES.set(key, { signature: deny.join(','), dispose })
  } catch (e) {
    console.warn(`Novabot 工具权限应用失败：${e?.message || e}`)
  }
}

async function runAgentTurn(ctx, cfg, payload, ws, system, userText, forceNew = false, enableTimeout = true) {
  const dshSession = await ensureSession(ctx, cfg, payload, ws, null, forceNew)
  if (!dshSession?.agent) throw new Error('DSH 会话没有可用 agent')
  await maybeCompact(ctx, payload, dshSession)
  const agent = dshSession.agent
  applyToolPolicy(agent, payload.permissions)
  const key = String(agent.session?.id || dshSession.sessionId)
  const nbKey = sessionMapKey(payload, cfg)
  APPROVAL_PERMISSIONS.set(key, payload.permissions || null)
  ACTIVE_BY_NB.set(nbKey, agent)
  const injected = installSystemContext(agent, system)
  return withSessionLock(key, () => new Promise((resolve, reject) => {
    const waiter = { turn: null, text: '', started: false, resolve, reject, error: '', timer: null, lastTool: '', tools: { calls: 0, results: 0, errors: 0 }, progress: payload.progress || null, lines: [] }
    const timeoutSec = Math.max(10, Math.min(3600, Number(payload.agent_timeout_sec) || Math.round((Number(cfg.agentTimeoutMs) || 600_000) / 1000)))
    if (enableTimeout) waiter.timer = setTimeout(() => {
      TURN_WAITERS.delete(key)
      // 到上限必须真的停掉当前 DSH 回合，不能只回一句话让任务在后台继续跑。
      try { agent.cancel({ kind: 'user' }, { keepInbox: false }) } catch (e) {
        console.warn(`DSH 超时取消失败：${e?.message || e}`)
      }
      waiter.error = ''
      const minutes = Math.max(1, Math.round(timeoutSec / 60))
      const progress = String(waiter.text || '').trim()
      const toolNote = waiter.tools.calls
        ? `已经调用工具 ${waiter.tools.calls} 次${waiter.lastTool ? `（最近：${waiter.lastTool}）` : ''}。`
        : '还没产生可读的工具结果。'
      const recent = (waiter.lines || []).slice(-6)
      const progressNote = progress
        ? `目前进度：${progress.slice(-700)}`
        : (recent.length ? `最近进度：\n- ${recent.join('\n- ')}` : '没有收到可读进度。')
      waiter.resolve({
        text: `这个任务超过 ${minutes} 分钟了，我先停在这里。\n${toolNote}\n${progressNote}\n太长了我分段做，下一段你让我继续。`,
        tools: waiter.tools,
        timed_out: true,
      })
    }, timeoutSec * 1000)
    TURN_WAITERS.set(key, waiter)
    const content = injected
      ? userText
      : `【Novabot 规则】\n${system || ''}\n\n【当前消息】\n${userText}`
    const message = {
      id: randomUUID(), role: 'user',
      content: [{ type: 'text', text: content }],
      source: { kind: 'novabot', speaker: { role: 'none' } },
    }
    try {
      agent.followup(message)
    } catch (e) {
      TURN_WAITERS.delete(key)
      clearTimeout(waiter.timer)
      waiter.error = String(e?.message || e)
      waiter.resolve({ text: waiter.text || '', tools: waiter.tools, error: waiter.error, timed_out: false })
    }
  })).finally(() => ACTIVE_BY_NB.delete(nbKey))
}

async function askDsh(ctx, cfg, payload) {
  if (payload.steer) {
    const agent = ACTIVE_BY_NB.get(sessionMapKey(payload, cfg))
    if (!agent) return { reply: '', tools: { calls: 0, results: 0, errors: 0 }, accepted: false, busy: false }
    try {
      const text = lastUser((Array.isArray(payload.messages) ? payload.messages : []).map((m) => ({
        role: m?.role, content: contentOf(m),
      }))) || String(payload.text || '')
      const message = {
        id: randomUUID(), role: 'user',
        content: [{ type: 'text', text }],
        source: { kind: 'novabot', speaker: { role: 'none' } },
      }
      agent.steer(message)
      return { reply: '', tools: { calls: 0, results: 0, errors: 0 }, accepted: true, busy: true }
    } catch (e) {
      return { reply: '', tools: { calls: 0, results: 0, errors: 0 }, accepted: false, busy: true, error: String(e?.message || e) }
    }
  }
  const stateless = Boolean(payload.stateless)
  let ws = null
  if (!stateless) {
    try { ws = await ensureWorkspace(ctx, cfg, payload) } catch (e) { console.warn(`DSH 工作区没建起来：${e?.message || e}`) }
  }

  const llm = ctx.llm
  if (!llm || typeof llm.stream !== 'function') throw new Error('DSH 没有 llm 服务')
  const modelSvc = ctx.get?.('agentDefaultModel')
  const defaultSel = modelSvc && typeof modelSvc.currentSelection === 'function' ? modelSvc.currentSelection() : null
  let sel = null
  if (payload.model?.model) {
    sel = {
      ...(defaultSel || {}),
      ...(payload.model.provider ? { provider: String(payload.model.provider) } : {}),
      model: String(payload.model.model),
      ...(payload.model.reasoningEffort ? { reasoningEffort: (['low', 'high', 'max'].includes(String(payload.model.reasoningEffort)) ? String(payload.model.reasoningEffort) : 'low') } : {}),
    }
  } else if (cfg.model?.provider && cfg.model?.model) sel = cfg.model
  else sel = defaultSel
  if (!sel?.provider || !sel?.model) throw new Error('拿不到 DSH 默认模型')

  let dshSession = null
  if (!stateless) {
    try { dshSession = await ensureSession(ctx, cfg, payload, ws, sel) } catch (e) { console.warn(`DSH 会话没建起来：${e?.message || e}`) }
  }

  const messages = (Array.isArray(payload.messages) ? payload.messages : [])
    .filter((m) => m && (m.role === 'user' || m.role === 'assistant'))
    .map((m) => ({
      role: m.role,
      id: randomUUID(),
      content: [{ type: 'text', text: contentOf(m) }],
      source: { kind: 'novabot', speaker: { role: 'none' } },
    }))
  if (!messages.length) throw new Error('没有 messages')

  const userText = lastUser(messages)
  const sessionId = String(payload.session_id || '')
  let system = String(payload.system || '')
  if (userText && !stateless) {
    const r = await callNovabot(cfg, '/api/platform/recall', {
      platform: 'dsh', session_id: sessionId, sender: payload.sender || 'dsh',
      text: userText, account_id: payload.account_id || cfg.accountId, max_chars: 300,
    })
    if (r.ok && r.memory) system = `【记忆】\n${r.memory}\n\n${system}`
  }

  let out = ''
  let tools = { calls: 0, results: 0, errors: 0 }
  const useAgent = Boolean(dshSession?.agent && userText)
  if (useAgent) {
    const mode = ['off', 'manual', 'auto'].includes(String(payload.long_task_mode))
      ? String(payload.long_task_mode) : 'manual'
    const maxRounds = Math.max(1, Math.min(10, Number(payload.long_task_max_rounds) || 3))
    let turn
    let round = 0
    while (true) {
      round += 1
      const text = round === 1 ? userText : '继续上一段任务，从上次中断处接着做，不要重复已经完成的步骤。'
      try {
        turn = await runAgentTurn(ctx, cfg, payload, ws, system, text, false, mode !== 'off')
      } catch (e) {
        if (!isSessionError(e)) throw e
        console.warn(`DSH 会话不可用，切换新会话重试：${e?.message || e}`)
        invalidateSession(payload, cfg)
        turn = await runAgentTurn(ctx, cfg, payload, ws, system, text, true, mode !== 'off')
      }
      if (turn.error) throw new Error(turn.error)
      if (!turn.timed_out || mode !== 'auto' || round >= maxRounds) break
      // 本轮已取消；等 agent 真正空闲后自动开下一轮同一会话。
      try {
        await Promise.race([
          dshSession.agent.whenIdle?.(),
          new Promise((resolve) => setTimeout(resolve, 1500)),
        ])
      } catch {}
    }
    out = turn.text
    if (mode === 'auto' && round > 1 && turn.timed_out) out += `\n（已自动跑 ${round} 轮，达到轮次上限，可以让我继续下一段）`
    tools = turn.tools || tools
    bumpSessionTurns(payload, dshSession)
  } else {
    for await (const chunk of llm.stream({
      provider: sel.provider,
      model: sel.model,
      system,
      messages,
      maxTokens: Number(payload.max_tokens) || 600,
      temperature: payload.temperature == null ? 0.3 : Number(payload.temperature),
    })) {
      if (!chunk) continue
      if (chunk.type === 'text-delta') out += chunk.text
      if (chunk.type === 'finish' && chunk.reason?.kind === 'error') {
        const f = chunk.reason.failure || {}
        throw new Error(f.message || f.code || '模型报错')
      }
    }
    await mirrorTurn(ctx, dshSession, payload, out, sel)
  }

  if (userText && !stateless) {
    callNovabot(cfg, '/api/platform/remember', {
      platform: 'dsh', session_id: sessionId, sender: payload.sender || 'dsh',
      text: userText, account_id: payload.account_id || cfg.accountId,
    }).catch(() => {})
  }
  if (out.trim()) {
    callNovabot(cfg, '/api/platform/remember', {
      platform: 'dsh', session_id: sessionId, sender: 'self',
      text: out.trim(), is_self: true, account_id: payload.account_id || cfg.accountId,
    }).catch(() => {})
  }
  return { reply: out.trim(), tools }
}

async function askDshVision(ctx, cfg, payload) {
  const llm = ctx.llm
  if (!llm || typeof llm.stream !== 'function') throw new Error('DSH 没有 llm 服务')
  const modelSvc = ctx.get?.('agentDefaultModel')
  const defaultSel = modelSvc && typeof modelSvc.currentSelection === 'function' ? modelSvc.currentSelection() : null
  const sel = (payload.model && payload.model.model)
    ? {
        ...(defaultSel || {}),
        ...(payload.model.provider ? { provider: String(payload.model.provider) } : {}),
        model: String(payload.model.model),
      }
    : (cfg.model?.provider && cfg.model?.model ? cfg.model : defaultSel)
  if (!sel?.provider || !sel?.model) throw new Error('拿不到 DSH 默认模型')
  const image = payload.image || {}
  if (!image.data || !image.mimeType) throw new Error('缺少图片数据')
  const prompt = String(payload.prompt || '用中文简短描述这张图片。')
  const messages = [{
    role: 'user',
    content: [
      { type: 'text', text: prompt },
      { type: 'image', data: String(image.data), mimeType: String(image.mimeType) },
    ],
  }]
  let out = ''
  for await (const chunk of llm.stream({
    provider: sel.provider,
    model: sel.model,
    system: '',
    messages,
    maxTokens: Number(payload.max_tokens) || 500,
    temperature: payload.temperature == null ? 0.1 : Number(payload.temperature),
  })) {
    if (!chunk) continue
    if (chunk.type === 'text-delta') out += chunk.text
    if (chunk.type === 'finish' && chunk.reason?.kind === 'error') {
      const f = chunk.reason.failure || {}
      throw new Error(f.message || f.code || 'DSH 视觉模型报错')
    }
  }
  return out.trim()
}

export function apply(ctx) {
  const cfg = loadConfig()
  const log = console

  ;(async () => {
    try {
      const r = await fetch(cfg.endpoint.replace(/\/+$/, '') + '/healthz', {
        signal: AbortSignal.timeout(3000),
      })
      if (r.ok) {
        const j = await r.json()
        log.info(`Novabot 已连接：${cfg.endpoint}（v${j.version}）`)
      } else {
        log.warn(`Novabot 返回 ${r.status}，记忆功能不可用`)
      }
    } catch (e) {
      log.warn(`连不上 Novabot（${cfg.endpoint}）：${e?.message || e}`)
    }
  })()

  try {
    if (typeof ctx.on === 'function') {
      ctx.on('session/event', (session, event) => {
        try { onSessionEvent(session, event) } catch (e) { log.warn(`会话事件处理失败：${e?.message || e}`) }
      })
      ctx.on('approval/request', (request, next) => {
        try {
          const key = String(request?.agent?.session?.id || '')
          const p = APPROVAL_PERMISSIONS.get(key)
          if (request?.callId === undefined || !p) return next()
          const ownerLocal = Boolean(p.read && p.write && p.delete && p.shell)
          if (!ownerLocal) return next()
          return 'allowed-once'
        } catch (e) {
          log.warn(`自动审批失败：${e?.message || e}`)
          return next()
        }
      })
    }
  } catch (e) {
    log.warn(`注册 DSH 会话事件失败：${e?.message || e}`)
  }

  const route = (path, handler) => {
    try {
      const register = () => ctx.webServer.register({
        kind: 'exact',
        path,
        handler: (req, res) => Promise.resolve(handler(req, res)).catch((e) => json(res, 500, { ok: false, error: String(e?.message || e) })),
      })
      if (typeof ctx.effect === 'function') ctx.effect(register, `novabot: ${path}`)
      else register()
    } catch (e) {
      log.warn(`注册 ${path} 失败：${e?.message || e}`)
    }
  }

  route('/novabot/api/health', async (_req, res) => {
    json(res, 200, { ok: true, name: 'novabot', version: '0.4.1' })
  })
  route('/novabot/api/vision', async (req, res) => {
    if (req.method !== 'POST') return json(res, 405, { ok: false, error: '只支持 POST' })
    if (!authOk(req, cfg)) return json(res, 401, { ok: false, error: 'token 不对' })
    const body = await readBody(req)
    try {
      const reply = await askDshVision(ctx, cfg, body)
      json(res, 200, { ok: true, reply, usage: {} })
    } catch (e) {
      json(res, 500, { ok: false, error: String(e?.message || e) })
    }
  })
  route('/novabot/api/chat', async (req, res) => {
    if (req.method !== 'POST') return json(res, 405, { ok: false, error: '只支持 POST' })
    if (!authOk(req, cfg)) return json(res, 401, { ok: false, error: 'token 不对' })
    const body = await readBody(req)
    try {
      const result = await askDsh(ctx, cfg, body)
      json(res, 200, { ok: true, reply: result.reply, tools: result.tools, accepted: result.accepted === true, busy: result.busy === true, session_id: String(body.session_id || ''), usage: {} })
    } catch (e) {
      json(res, 500, { ok: false, error: String(e?.message || e) })
    }
  })

  log.info('Novabot 插件已加载（模型桥 + 自动记忆）')
}
