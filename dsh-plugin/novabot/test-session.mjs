import { apply } from './index.js'

const events = []
const listeners = []
const session = {
  id: 'session-test', snapshotEvents: () => [],
  append(type, data) { events.push({ type, data }); return { type, data } },
}
let denied = []
const agent = {
  session, close() {},
  ctx: { tools: { restrict({ deny }) { denied = deny; return () => {} } } },
  followup() {
    setTimeout(() => {
      for (const fn of listeners) {
        fn(session, { type: 'turn/start', data: { turn: 1 } })
        fn(session, { type: 'assistant/message', data: { turn: 1, step: 1, message: { role: 'assistant', content: [{ type: 'text', text: '收到。' }] } } })
        fn(session, { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })
      }
    }, 0)
  },
}
const ws = { id: 'ws-test', path: process.cwd() }
let created = 0
let createOptions = null
const ctx = {
  llm: { async *stream() { yield { type: 'text-delta', text: 'fallback' }; yield { type: 'finish', reason: { kind: 'stop' } } } },
  get(name) {
    if (name === 'agentDefaultModel') return { currentSelection: () => ({ provider: 'p', model: 'm' }) }
    if (name === 'workspaceRegistry') return { list: () => [ws], create: async () => ws, get: () => ws }
    if (name === 'sessionController') return {
      create: async (opts) => { created += 1; createOptions = opts; return { sessionId: 'session-test' } },
      resolveAgent: async () => ({ agent }),
      rename: async () => ({}),
    }
    if (name === 'sessions') return { flush: async () => {} }
    return null
  },
  on(name, fn) { if (name === 'session/event') listeners.push(fn) },
  webServer: { register(o) { handlers[o.path] = o.handler; return () => {} } },
  effect(fn) { return fn() },
  log: { info() {}, warn() {} },
}
const handlers = {}
apply(ctx)
await new Promise((r) => setTimeout(r, 100))
const req = {
  method: 'POST', headers: {},
  async *[Symbol.asyncIterator]() {
    yield Buffer.from(JSON.stringify({
      system: 'sys', session_id: 'private:model-test-' + Date.now(),
      messages: [{ role: 'user', content: '你好' }], bot_name: '小助手', account_id: 'acc-demo',
      permissions: { read: true, write: false, delete: false, shell: false, file_send: false },
      model: { model: 'deepseek-flash', reasoningEffort: 'low' },
    }))
  },
}
let out = ''
const res = { writeHead() {}, end(s) { out = String(s || '') } }
await handlers['/novabot/api/chat'](req, res)
const data = JSON.parse(out)
if (!data.ok || data.reply !== '收到。') { console.error('reply fail', out); process.exit(1) }
if (!created) { console.error('session not created'); process.exit(1) }
if (createOptions?.agentOptions?.model !== 'deepseek-flash' || createOptions?.agentOptions?.reasoningEffort !== 'low') {
  console.error('model/effort not applied', createOptions); process.exit(1)
}
if (!denied.includes('write') || !denied.includes('bash')) { console.error('permission deny fail', denied); process.exit(1) }
console.log('DSH actual-agent test passed', data.reply)
