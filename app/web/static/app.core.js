/* ============================================================
   工具
   ============================================================ */
const $ = (s, r = document) => r.querySelector(s)
const $$ = (s, r = document) => [...r.querySelectorAll(s)]

let panelToken = ''
try { panelToken = localStorage.getItem('novabot-token') || '' } catch {}

function h(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag)
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue
    if (k === 'class') n.className = v
    else if (k === 'html') n.innerHTML = v
    else if (k.startsWith('on') && typeof v === 'function') {
      n.addEventListener(k.slice(2).toLowerCase(), v)
    } else n.setAttribute(k, v)
  }
  for (const kid of kids.flat(9)) {
    if (kid == null || kid === false) continue
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)))
  }
  return n
}

/* 图标：内联 SVG，统一 24 网格 */
/* 通用拖动排序：条目留在原布局里滑动让位，不跟随鼠标漂浮 */
function makeSortable(container, itemSelector, onReorder, opts = {}) {
  if (!container || container.dataset.sortableBound === '1') return
  container.dataset.sortableBound = '1'
  let drag = null
  let suppressClick = false
  const all = () => [...container.querySelectorAll(itemSelector)]
  const itemOf = (target) => (target && target.closest ? target.closest(itemSelector) : null)

  const slide = (mutate) => {
    const moving = all().filter(el => el !== drag.item)
    const first = new Map(moving.map(el => [el, el.getBoundingClientRect()]))
    mutate()
    for (const el of moving) {
      const a = first.get(el)
      const b = el.getBoundingClientRect()
      const dx = a.left - b.left
      const dy = a.top - b.top
      if (!dx && !dy) continue
      el.style.transition = 'none'
      el.style.transform = `translate(${dx}px, ${dy}px)`
      void el.offsetWidth
      requestAnimationFrame(() => {
        el.style.transition = 'transform 150ms cubic-bezier(.2,.8,.2,1)'
        el.style.transform = ''
      })
      setTimeout(() => { el.style.transition = ''; el.style.transform = '' }, 220)
    }
  }

  const nearest = (x, y) => {
    let best = null
    let bestDist = Infinity
    for (const el of all()) {
      if (el === drag.item) continue
      const r = el.getBoundingClientRect()
      const dx = Math.max(r.left - x, 0, x - r.right)
      const dy = Math.max(r.top - y, 0, y - r.bottom)
      const d = dx * dx + dy * dy
      if (d < bestDist) { bestDist = d; best = el }
    }
    return best
  }

  container.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return
    const item = itemOf(e.target)
    if (!item || !container.contains(item) || item.parentElement !== container) return
    const control = e.target.closest && e.target.closest('button,input,textarea,select,a')
    if (control && control !== item) return
    drag = { item, x: e.clientX, y: e.clientY, moved: false, pointerId: e.pointerId }
    try { item.setPointerCapture(e.pointerId) } catch {}
  })

  container.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return
    if (!drag.moved) {
      if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 5) return
      drag.moved = true
      suppressClick = true
      drag.item.classList.add('sortable-dragging')
      document.body.classList.add('sortable-active')
    }
    e.preventDefault()
    const target = nearest(e.clientX, e.clientY)
    if (!target || target === drag.item) return
    const r = target.getBoundingClientRect()
    const before = opts.horizontal
      ? e.clientX < r.left + r.width / 2
      : e.clientY < r.top + r.height / 2
    const desired = before ? target : target.nextSibling
    if (desired === drag.item) return
    slide(() => container.insertBefore(drag.item, desired))
  })

  const finish = () => {
    if (!drag) return
    const d = drag
    drag = null
    document.body.classList.remove('sortable-active')
    d.item.classList.remove('sortable-dragging')
    if (!d.moved) { suppressClick = false; return }
    try { onReorder(all().map(el => el.dataset.sortId || el.textContent.trim())) } catch (e) { console.warn('排序保存失败', e) }
    setTimeout(() => { suppressClick = false }, 80)
  }
  container.addEventListener('pointerup', finish)
  container.addEventListener('pointercancel', finish)
  container.addEventListener('click', (e) => {
    if (!suppressClick) return
    e.preventDefault(); e.stopPropagation()
  }, true)
}

const ICONS = {
  overview: '<path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/>',
  people:   '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6.5 6.5-6.5s6.5 2.9 6.5 6.5"/><circle cx="17.5" cy="9" r="2.5"/><path d="M16 14.2c2.6.3 4.5 1.9 5 4.3"/>',
  memory:   '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v18H6.5A2.5 2.5 0 0 1 4 18.5z"/><path d="M4 17.5h16"/>',
  trash:    '<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14"/><path d="M10 11v6M14 11v6"/>',
  persona:  '<path d="M12 3l2.2 5.6L20 10l-4.4 3.9L16.8 20 12 16.9 7.2 20l1.2-6.1L4 10l5.8-1.4z"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>',
  voice:    '<path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/>',
  platform: '<path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7L13 18"/>',
  modules:  '<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"/>',
  life: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  proactive: '<path d="M12 3a7 7 0 0 0-7 7v4l-2 3h18l-2-3V10a7 7 0 0 0-7-7zM9.5 20.5a2.8 2.8 0 0 0 5 0"/>',
  backup: '<path d="M4 5h16v14H4zM8 5v6h8V5M8 15h8"/>',
  gateway: '<path d="M4 6h16v12H4zM8 10l2 2-2 2M12 14h4"/>',
  music:    '<path d="M9 18V5l10-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="16" cy="16" r="3"/>',
  file:     '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  chat:     '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.5 9 9 0 0 1-3.8-.9L3 21l1.9-5.1A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z"/>',
  search:   '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  chevron:  '<path d="M9 6l6 6-6 6"/>',
  plus:     '<path d="M12 5v14M5 12h14"/>',
  refresh:  '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v5h-5"/>',
  arrow:    '<path d="M19 12H5M12 19l-7-7 7-7"/>',
  info:     '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 8h.01"/>',
  inbox:    '<path d="M3 12h5l2 3h4l2-3h5"/><path d="M5 5h14l2 7v7H3v-7z"/>',
  user:     '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8"/>',
}

function icon(name, cls = '') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  svg.setAttribute('viewBox', '0 0 24 24')
  svg.setAttribute('fill', 'none')
  svg.setAttribute('stroke', 'currentColor')
  svg.setAttribute('stroke-width', '1.6')
  svg.setAttribute('stroke-linecap', 'round')
  svg.setAttribute('stroke-linejoin', 'round')
  if (cls) svg.setAttribute('class', cls)
  svg.innerHTML = ICONS[name] || ''
  return svg
}

/* 提示 */
let toastTimer
function toast(msg, kind = '') {
  const t = $('#toast')
  t.className = 'toast show ' + kind
  t.textContent = ''
  t.append(document.createTextNode(msg))
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { t.className = 'toast ' + kind }, 2400)
}

/* 请求 */
async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) }
  if (panelToken) headers['X-Panel-Token'] = panelToken
  const r = await fetch(path, {
    ...opts,
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  if (r.status === 401) {
    showLogin()
    throw new Error('需要面板密码')
  }
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

function showLogin() {
  let mask = $('#login-mask')
  if (!mask) {
    const input = h('input', {
      type: 'password', id: 'login-token', placeholder: '面板密码', style: 'width:100%',
    })
    input.addEventListener('keydown', e => { if (e.key === 'Enter') loginSubmit() })
    mask = h('div', { class: 'modal-mask', id: 'login-mask' },
      h('div', { class: 'modal' },
        h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, '需要面板密码')),
        h('div', { class: 'modal-body' },
          h('div', { class: 'field-desc', style: 'margin-bottom:10px' },
            '服务端设置了面板密码。输入后只存在这台浏览器。'),
          input),
        h('div', { class: 'modal-foot' },
          h('button', { class: 'btn btn-primary', onclick: loginSubmit }, '进入')),
      ))
    document.body.append(mask)
  }
  mask.classList.add('show')
  setTimeout(() => $('#login-token')?.focus(), 50)
}

async function loginSubmit() {
  const token = ($('#login-token')?.value || '').trim()
  if (!token) return
  try {
    const r = await fetch('/api/info', { headers: { 'X-Panel-Token': token } })
    if (!r.ok) throw new Error('bad token')
    panelToken = token
    try { localStorage.setItem('novabot-token', token) } catch {}
    $('#login-mask')?.classList.remove('show')
    toast('已解锁', 'ok')
    try { await loadAccounts() } catch {}
    try { await checkStatus() } catch {}
    go(current)
  } catch (e) {
    toast('密码不对', 'err')
  }
}

/* 时间 */
function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

/* 绝对时间（提醒/未来时间用） */
function fmtDateTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

/* 数字格式化 */
function fmtNum(n) {
  if (n == null) return '—'
  if (n >= 10000) return (n / 10000).toFixed(1) + ' 万'
  return String(n)
}

/* 空态 */
function emptyState(title, desc, iconName = 'inbox') {
  return h('div', { class: 'empty' },
    h('div', { class: 'empty-icon' }, icon(iconName)),
    h('div', { class: 'empty-title' }, title),
    desc ? h('div', { class: 'empty-desc' }, desc) : null,
  )
}

