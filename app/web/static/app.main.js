/* ============================================================
   导航
   ============================================================ */
const PAGES = [
  { id: 'overview', name: '概览',   icon: 'overview', render: renderOverview },
  { id: 'workbench', name: '工作台', icon: 'workbench', render: renderWorkbench },
  { id: 'people',   name: '人物',   icon: 'people',   render: renderPeople },
  { id: 'memory',   name: '记忆',   icon: 'memory',   render: renderMemory },
  { id: 'persona',  name: '人格',   icon: 'persona',  render: renderPersona },
  { id: 'chat',     name: '聊天',   icon: 'chat',     render: renderChat },
  { id: 'proactive', name: '生活', icon: 'life', render: renderProactive },
  { id: 'voice',    name: '语音',   icon: 'voice',    render: renderVoice },
  { id: 'platform', name: '平台',   icon: 'platform', render: renderPlatform },
  { id: 'modules',  name: '模块',   icon: 'modules',  render: renderModules },
  { id: 'gateway',  name: '接入',   icon: 'gateway',  render: renderGateway },
  { id: 'settings', name: '设置',   icon: 'settings', render: renderSettings },
  { id: 'backup',   name: '备份',   icon: 'backup',   render: renderBackup },
]

let current = 'overview'

function accountQuery(prefix = '?') {
  return currentAccount ? `${prefix}account_id=${encodeURIComponent(currentAccount)}` : ''
}

function buildNav() {
  const nav = $('#nav')
  nav.innerHTML = ''
  for (const p of PAGES) {
    nav.append(h('button', {
      class: 'nav-item',
      'data-page': p.id,
      title: p.name,
      'aria-label': p.name,
      onclick: () => go(p.id),
    }, icon(p.icon), h('span', {}, p.name)))
  }
}

function go(id) {
  current = id
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.page === id))
  const main = $('#main')
  main.innerHTML = ''
  main.append(h('div', { class: 'main-inner' },
    h('div', { class: 'empty' },
      h('div', { class: 'skeleton', style: 'width:140px' }),
      h('div', { class: 'skeleton', style: 'width:220px;height:11px' }))))
  const page = PAGES.find(p => p.id === id)
  page.render().catch(e => {
    main.innerHTML = ''
    main.append(h('div', { class: 'main-inner' },
      emptyState('加载失败', e.message, 'info')))
  })
}

/* 页面骨架 */
function pageHead(title, desc, ...actions) {
  return h('div', { class: 'page-head' },
    h('div', {},
      h('div', { class: 'page-title' }, title),
      desc ? h('div', { class: 'page-desc' }, desc) : null),
    h('div', { class: 'page-actions' }, ...actions),
  )
}

/* ============================================================
   账号（多开）
   ============================================================ */
let accountsCache = []
let currentAccount = ''

async function loadAccounts() {
  try {
    const r = await api('/api/accounts')
    accountsCache = r.accounts || []
    // 恢复上次选的；"全部"（空串）也是一个明确选择，不能当没选过
    if (!currentAccount) {
      try {
        const saved = localStorage.getItem('novabot-account')
        if (saved !== null) currentAccount = saved
      } catch {}
    }
    if (currentAccount && !accountsCache.find(a => a.id === currentAccount)) {
      currentAccount = ''
    }
    let hasSaved = false
    try { hasSaved = localStorage.getItem('novabot-account') !== null } catch {}
    if (!currentAccount && accountsCache.length && !hasSaved) {
      currentAccount = accountsCache[0].id
    }
    renderAccountSwitch()
  } catch (e) {
    console.warn('账号加载失败', e)
  }
}

function renderAccountSwitch() {
  const box = $('#account-switch')
  if (!box) return
  box.innerHTML = ''

  // 「全部」选项
  box.append(h('button', {
    class: 'acc-tab' + (currentAccount === '' ? ' active' : ''),
    onclick: () => switchAccount(''),
  }, '全部'))

  if (!accountsCache.length) {
    box.append(h('span', { class: 'acc-empty-hint' }, '还没接账号 · 点「加账号」'))
  }

  for (const a of accountsCache) {
    const dotClass = a.connected ? 'on' : (a.enabled ? '' : 'err')
    const tab = h('button', {
      class: 'acc-tab' + (currentAccount === a.id ? ' active' : ''),
      onclick: () => switchAccount(a.id),
      ondblclick: (e) => { e.preventDefault(); renameAccount(a) },
      title: `${a.name}${a.bot_qq ? ' · ' + a.bot_qq : ''}${a.connected ? ' · 在线' : (a.configured ? ' · 离线' : ' · 未配置')} · 双击改名 · 拖动排序`,
    },
      h('span', { class: 'acc-dot ' + dotClass }),
      h('span', {}, a.name || a.id),
      a.bot_qq ? h('span', { class: 'acc-qq' }, a.bot_qq) : null,
    )
    tab.dataset.sortId = a.id
    box.append(tab)
  }
  makeSortable(box, '.acc-tab[data-sort-id]', async (ids) => {
    const byId = new Map(accountsCache.map(a => [a.id, a]))
    accountsCache = ids.map(id => byId.get(id)).filter(Boolean)
    renderAccountSwitch()
    try {
      await api('/api/accounts/order', { method: 'POST', body: { ids: accountsCache.map(a => a.id) } })
    } catch (e) { toast('排序失败：' + e.message, 'err') }
  }, { horizontal: true })
}

async function moveAccount(fromId, toId) {
  if (!fromId || !toId || fromId === toId) return
  const list = [...accountsCache]
  const from = list.findIndex(a => a.id === fromId)
  const to = list.findIndex(a => a.id === toId)
  if (from < 0 || to < 0) return
  const [item] = list.splice(from, 1)
  const insertAt = from < to ? to - 1 : to
  list.splice(Math.max(0, insertAt), 0, item)
  accountsCache = list
  renderAccountSwitch()
  try {
    await api('/api/accounts/order', { method: 'POST', body: { ids: list.map(a => a.id) } })
  } catch (e) { toast('排序失败：' + e.message, 'err') }
}

async function renameAccount(a) {
  const name = prompt('给这个 bot 改个名字（会用于 DSH 工作区 Novabot·名字）', a.name || '')
  if (name === null) return
  const clean = name.trim()
  if (!clean) return toast('名字不能为空', 'err')
  try {
    const r = await api(`/api/accounts/${a.id}`, { method: 'PATCH', body: { name: clean } })
    if (!r.ok) return toast(r.error || '改名失败', 'err')
    toast('已改名', 'ok')
    await loadAccounts()
    go(current)
  } catch (e) { toast('改名失败：' + e.message, 'err') }
}

function switchAccount(id) {
  currentAccount = id
  try { localStorage.setItem('novabot-account', id) } catch {}
  renderAccountSwitch()
  // 重新渲染当前页
  go(current)
}

function accQuery(sep = '?') {
  // 给接口加 account_id 参数；sep 用来区分是第一个参数还是追加
  return currentAccount ? sep + 'account_id=' + encodeURIComponent(currentAccount) : ''
}

function accountLabel() {
  if (!currentAccount) return '全部账号（全局）'
  const a = accountsCache.find(x => x.id === currentAccount)
  return a ? `账号：${a.name}` : `账号：${currentAccount}`
}

function openAccountsModal() {
  const mask = h('div', { class: 'modal-mask' })
  const body = h('div', { class: 'modal-body' })
  if (!accountsCache.length) {
    body.append(h('div', { class: 'empty-desc', style: 'padding:8px 0 14px' },
      '还没有接账号。点「新增账号」填机器人 QQ 和 OneBot 地址。'))
  }
  for (const a of accountsCache) {
    const dot = h('span', { class: 'acc-dot ' + (a.connected ? 'on' : (a.enabled ? '' : 'err')) })
    body.append(h('div', { class: 'acc-manage-row' },
      h('div', { class: 'acc-manage-main' },
        h('div', { class: 'acc-manage-name' }, dot, ' ', a.name || a.id,
          h('span', { class: 'tag ' + (a.connected ? 'ok' : '') },
            a.connected ? '在线' : (a.configured ? (a.enabled ? '离线' : '停用') : '未配置')),
          a.locked ? h('span', { class: 'tag warn' }, '已锁定') : null),
        h('div', { class: 'acc-manage-sub' },
          `${a.bot_qq || '未填 QQ'} · ${a.impl || 'snowluma'}` +
          (a.onebot_port ? ` · 端口 ${a.onebot_port}` : '') +
          (a.persona ? ` · 人格 ${a.persona}` : '')),
      ),
      h('div', { class: 'acc-manage-actions' },
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: () => { close(); switchAccount(a.id) },
        }, currentAccount === a.id ? '当前' : '切换'),
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: () => { close(); openAccountEditModal(a) },
        }, '编辑'),
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: async () => {
            const r = await api(`/api/accounts/${a.id}`, { method: 'PATCH', body: { locked: !a.locked } }).catch(e => ({ ok: false, error: e.message }))
            if (!r.ok) return toast(r.error || '操作失败', 'err')
            toast(a.locked ? '已解锁' : '已锁定', 'ok')
            await loadAccounts(); close(); openAccountsModal()
          },
        }, a.locked ? '解锁' : '锁定'),
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: async () => {
            close()
            const path = a.connected ? 'disconnect' : 'connect'
            try {
              const r = await api(`/api/accounts/${a.id}/${path}`, { method: 'POST', body: {} })
              if (!r.ok) return toast(r.error || '操作失败', 'err')
              toast(a.connected ? '已断开' : '连接中…', 'ok')
              await loadAccounts()
              go(current)
            } catch (e) { toast('操作失败：' + e.message, 'err') }
          },
        }, a.connected ? '断开' : '连接'),
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: async () => { close(); await deleteAccount(a.id, a.name || a.id, a.locked) },
        }, a.locked ? '已锁定' : '删除'),
      ),
    ))
  }

  const maskEl = h('div', { class: 'modal' },
    h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, '账号管理')),
    body,
    h('div', { class: 'modal-foot' },
      h('button', { class: 'btn btn-ghost', onclick: () => close() }, '关闭'),
      h('button', { class: 'btn btn-primary', onclick: () => { close(); openAccountModal() } }, '新增账号'),
    ))
  mask.append(maskEl)
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))
  function close() {
    mask.classList.remove('show')
    setTimeout(() => mask.remove(), 180)
  }
  mask.addEventListener('click', e => { if (e.target === mask) close() })
}

/* 新增账号弹窗 */
function openAccountModal() {
  const mask = h('div', { class: 'modal-mask' })
  const implSel = h('select', {})
  const inputs = {}

  // 先拉实现列表
  api('/api/gateway/impls').then(r => {
    for (const i of (r.impls || [])) {
      implSel.append(h('option', { value: i.id }, i.name))
    }
  }).catch(() => {
    implSel.append(h('option', { value: 'snowluma' }, 'SnowLuma'))
  })

  function row(label, key, opts = {}) {
    const input = opts.options
      ? null
      : h('input', {
          type: opts.type || 'text',
          placeholder: opts.placeholder || '',
          value: opts.value || '',
          autocomplete: opts.autocomplete || 'off',
          autocapitalize: 'off',
          spellcheck: 'false',
        })
    if (opts.options) {
      inputs[key] = opts.el
    } else {
      inputs[key] = input
    }
    return h('div', { class: 'form-row' },
      h('label', {}, label),
      opts.options ? opts.el : input,
      opts.hint ? h('div', { class: 'hint' }, opts.hint) : null)
  }

  inputs.impl = implSel

  const body = h('div', { class: 'modal-body' },
    row('显示名', 'name', { placeholder: '', autocomplete: 'off' }),
    h('div', { class: 'form-grid' },
      row('机器人 QQ', 'bot_qq', { placeholder: '', autocomplete: 'off' }),
      row('OneBot 端口', 'onebot_port', { type: 'number', placeholder: '自动分配' }),
    ),
    row('实现', 'impl', { options: true, el: implSel,
      hint: 'SnowLuma / NapCat / Lagrange 都支持' }),
    row('连接地址', 'url', {
      placeholder: 'ws://127.0.0.1:6700',
      hint: '留空就按端口自动生成',
    }),
    row('Token', 'token', { type: 'password', placeholder: '没有就留空', autocomplete: 'new-password' }),
    h('div', { class: 'form-row' },
      h('label', { style: 'display:flex;align-items:center;gap:8px' },
        (() => {
          const sw = h('div', { class: 'switch on' })
          sw.addEventListener('click', () => sw.classList.toggle('on'))
          inputs.reverse = sw
          return sw
        })(),
        '正向 WS（它连我们）')),
  )

  const modal = h('div', { class: 'modal' },
    h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, '新增账号')),
    body,
    h('div', { class: 'modal-foot' },
      h('button', { class: 'btn btn-ghost', onclick: () => close() }, '取消'),
      h('button', { class: 'btn btn-primary', onclick: () => submit() }, '创建并连接')),
  )
  mask.append(modal)
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))

  function close() {
    mask.classList.remove('show')
    setTimeout(() => mask.remove(), 180)
  }

  async function submit() {
    const data = {
      name: inputs.name.value.trim(),
      bot_qq: inputs.bot_qq.value.trim(),
      onebot_port: Number(inputs.onebot_port.value) || 0,
      impl: inputs.impl.value,
      url: inputs.url.value.trim(),
      token: inputs.token.value,
      reverse: inputs.reverse.classList.contains('on'),
      enabled: true,
      auto_start: true,
    }
    if (!data.name) return toast('起个名字吧', 'err')
    if (!data.bot_qq) return toast('填一下机器人 QQ', 'err')

    try {
      const r = await api('/api/accounts', { method: 'POST', body: data })
      if (!r.ok) return toast(r.error || '没建成', 'err')
      toast('账号已创建', 'ok')
      close()
      await loadAccounts()
      switchAccount(r.account.id)
    } catch (e) {
      toast('失败：' + e.message, 'err')
    }
  }

  mask.addEventListener('click', e => { if (e.target === mask) close() })
}

function openAccountEditModal(a) {
  const mask = h('div', { class: 'modal-mask' })
  const name = h('input', { type: 'text', class: 'wide', value: a.name || '' })
  const qq = h('input', { type: 'text', class: 'wide', value: a.bot_qq || '' })
  const url = h('input', { type: 'text', class: 'wide', value: a.url || '', placeholder: 'ws://127.0.0.1:6700' })
  const token = h('input', { type: 'password', class: 'wide', value: '', placeholder: a.token_set ? '已设置，留空不改' : '没有就留空' })
  const port = h('input', { type: 'number', value: a.onebot_port || 0, placeholder: '自动分配' })
  const enabled = h('div', { class: 'switch' + (a.enabled ? ' on' : '') })
  enabled.addEventListener('click', () => enabled.classList.toggle('on'))
  const auto = h('div', { class: 'switch' + (a.auto_start ? ' on' : '') })
  auto.addEventListener('click', () => auto.classList.toggle('on'))
  const locked = h('div', { class: 'switch' + (a.locked ? ' on' : '') })
  locked.addEventListener('click', () => locked.classList.toggle('on'))

  const body = h('div', { class: 'modal-body' },
    h('div', { class: 'form-row' }, h('label', {}, '显示名'), name),
    h('div', { class: 'form-grid' },
      h('div', { class: 'form-row' }, h('label', {}, '机器人 QQ'), qq),
      h('div', { class: 'form-row' }, h('label', {}, 'OneBot 端口'), port),
    ),
    h('div', { class: 'form-row' }, h('label', {}, '连接地址'), url),
    h('div', { class: 'form-row' }, h('label', {}, 'Token'), token),
    h('div', { class: 'form-row' },
      h('label', { style: 'display:flex;align-items:center;gap:8px' }, enabled, '启用'),
      h('label', { style: 'display:flex;align-items:center;gap:8px;margin-left:14px' }, auto, '自动连接'),
      h('label', { style: 'display:flex;align-items:center;gap:8px;margin-left:14px' }, locked, '锁定'),
    ),
    h('div', { class: 'hint' }, '锁定后不能删除；删除需要两次确认。'),
    h('div', { class: 'hint' }, '改完保存会重新连接；Token 留空表示不改。'),
  )

  const modal = h('div', { class: 'modal' },
    h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, '编辑账号')),
    body,
    h('div', { class: 'modal-foot' },
      h('button', { class: 'btn btn-ghost', onclick: () => close() }, '取消'),
      h('button', { class: 'btn btn-primary', onclick: () => submit() }, '保存'),
    ))
  mask.append(modal)
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))

  function close() {
    mask.classList.remove('show')
    setTimeout(() => mask.remove(), 180)
  }
  async function submit() {
    const data = {
      name: name.value.trim(),
      bot_qq: qq.value.trim(),
      url: url.value.trim(),
      onebot_port: Number(port.value) || 0,
      enabled: enabled.classList.contains('on'),
      auto_start: auto.classList.contains('on'),
      locked: locked.classList.contains('on'),
    }
    if (!data.name) return toast('名字不能为空', 'err')
    if (!data.bot_qq) return toast('机器人 QQ 不能为空', 'err')
    if (token.value) data.token = token.value
    try {
      const r = await api(`/api/accounts/${a.id}`, { method: 'PATCH', body: data })
      if (!r.ok) return toast(r.error || '保存失败', 'err')
      close()
      toast('已保存', 'ok')
      await loadAccounts()
      go(current)
    } catch (e) { toast('保存失败：' + e.message, 'err') }
  }
  mask.addEventListener('click', e => { if (e.target === mask) close() })
}

async function deleteAccount(id, name, isLocked = false) {
  if (isLocked) return toast('账号已锁定，先编辑解锁再删除', 'err')
  if (!confirm(`警告：即将删除账号「${name}」。\n\n删除后这个 bot 会下线，当前运行状态会丢失。`)) return
  if (!confirm(`第二次确认：真的删除「${name}」？\n\n聊天记录会保留，以后重建能接上。`)) return
  try {
    const r = await api('/api/accounts/' + id, { method: 'DELETE' })
    if (!r.ok) return toast(r.error || '删不掉', 'err')
    toast('已删除', 'ok')
    if (currentAccount === id) currentAccount = ''
    await loadAccounts()
    go(current)
  } catch (e) {
    toast('失败：' + e.message, 'err')
  }
}

/* ============================================================
   启动
   ============================================================ */
async function checkStatus() {
  try {
    const [r, safety] = await Promise.all([
      api('/api/gateway/status'),
      api('/api/system/safety').catch(() => ({})),
    ])
    const pill = $('#safety-pill')
    if (pill) {
      const dry = Boolean(safety.dry_run)
      const master = safety.master_enabled !== false
      pill.textContent = !master ? '总开关关' : (dry ? '试运行' : '运行中')
      pill.className = 'btn btn-ghost btn-sm ' + (!master ? 'tag err' : (dry ? 'tag warn' : 'tag ok'))
    }
    const sum = r.summary || {}
    const dot = $('#status-dot')
    const txt = $('#status-text')
    if (r.enabled) {
      if (sum.online > 0) {
        dot.className = 'dot ok'
        txt.textContent = `${sum.online} 个账号在线`
      } else {
        dot.className = 'dot'
        txt.textContent = '接入已开，无在线账号'
      }
    } else {
      dot.className = 'dot'
      txt.textContent = '接入未启用'
    }
  } catch {
    $('#status-dot').className = 'dot err'
    $('#status-text').textContent = '连不上'
  }
}

let shuttingDown = false
const automatedBrowser = Boolean(navigator.webdriver) || /HeadlessChrome|HeadlessEdge/i.test(navigator.userAgent || '')
const desktopMode = new URLSearchParams(location.search).get('desktop') === '1'

async function shutdownNovabot() {
  if (!confirm('你确定关闭 Novabot 吗？\n\n关闭后回复立即停止，Python 进程会全部退出，不会残留后台。')) return
  shuttingDown = true
  try {
    await api('/api/system/shutdown', { method: 'POST' })
  } catch {}
  document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font:15px system-ui;color:#555">Novabot 正在关闭，进程停止后可以关闭窗口。</div>'
  setTimeout(() => { try { window.close() } catch {} }, 600)
}

if (!automatedBrowser && !desktopMode) {
  window.addEventListener('beforeunload', (e) => {
    if (shuttingDown) return
    e.preventDefault()
    e.returnValue = ''
  })
  window.addEventListener('pagehide', () => {
    if (shuttingDown) return
    try {
      fetch('/api/system/pagehide', {
        method: 'POST',
        keepalive: true,
        headers: panelToken ? { 'X-Panel-Token': panelToken } : {},
      }).catch(() => {})
    } catch {}
  })
}

async function heartbeat() {
  if (automatedBrowser || desktopMode) return
  try {
    await api('/api/system/heartbeat', { method: 'POST' })
  } catch {}
}

(async function init() {
  // 先恢复主题，避免闪一下
  try {
    const saved = localStorage.getItem('novabot-theme')
    if (saved) document.documentElement.dataset.theme = saved
  } catch {}

  buildNav()
  await loadAccounts()
  checkStatus()
  heartbeat()
  setInterval(heartbeat, 1000)
  setInterval(async () => {
    await checkStatus()
    await loadAccounts()
  }, 15000)
  go('overview')
  try {
    const s = await api('/api/system/safety')
    if (!s.setup_done) openSetupWizard()
  } catch {}
})()
