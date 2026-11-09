// 不带 DSH 的本地自测：mock ctx，验证 /novabot/api/chat 能拿到模型回复。
import { apply } from './index.js'

const handlers = {}
const ctx = {
  llm: {
    async *stream() {
      yield { type: 'text-delta', text: 'DSH 测试回复' }
      yield { type: 'finish' }
    },
  },
  get(name) {
    if (name === 'agentDefaultModel') return { currentSelection: () => ({ provider: 'p', model: 'm' }) }
    return null
  },
  webServer: { register(o) { handlers[o.path] = o.handler; return () => {} } },
  effect(fn) { return fn() },
  log: { info() {}, warn() {} },
  tool() {},
  systemPrompt: { context() {} },
  hooks: { afterTurn() {} },
}

apply(ctx)
if (!handlers['/novabot/api/chat']) {
  console.error('没有注册 /novabot/api/chat')
  process.exit(1)
}

const req = {
  method: 'POST',
  headers: {},
  async *[Symbol.asyncIterator]() {
    yield Buffer.from(JSON.stringify({
      system: 'sys', session_id: 's1',
      messages: [{ role: 'user', content: '你好' }],
    }))
  },
}
let out = ''
const res = {
  writeHead() {},
  end(s) { out = String(s || '') },
}
await handlers['/novabot/api/chat'](req, res)
const data = JSON.parse(out)
if (!data.ok || data.reply !== 'DSH 测试回复' || data.session_id !== 's1') {
  console.error('返回值不对', out)
  process.exit(1)
}
console.log('DSH 插件自测通过')
