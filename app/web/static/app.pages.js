/* ============================================================
   概览
   ============================================================ */
async function renderOverview() {
  const [info, stats, gw, pro] = await Promise.all([
    api('/api/info' + accQuery()),
    api('/api/data/stats' + accQuery()),
    api('/api/gateway/status').catch(() => ({ enabled: false })),
    api('/api/proactive/status' + accQuery()).catch(() => null),
  ])
  const main = $('#main')
  main.innerHTML = ''

  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)

  wrap.append(pageHead('概览', `v${info.version}`))

  const t = stats.tables || {}
  const total = Object.values(t).reduce((a, b) => a + b, 0)

  wrap.append(h('div', { class: 'stat-grid' },
    stat('今日消息', stats.today_episodes ?? 0, '条'),
    stat('总记录', total, '条'),
    stat('人物', t.people ?? 0, '个'),
    stat('事实', t.facts ?? 0, '条'),
    stat('数据库', stats.db_size || '—', ''),
    stat('在线账号', gw.summary?.online ?? 0, '个'),
  ))

  // 配置
  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'card-title' }, '配置'),
    h('dl', { class: 'kv-grid' },
      h('dt', {}, '数据库'), h('dd', {}, info.db || '—'),
      h('dt', {}, '模型'), h('dd', {}, info.llm_default || h('span', { class: 'tag' }, '未配置')),
      h('dt', {}, '人格'), h('dd', {}, info.persona_default || h('span', { class: 'tag' }, '未配置')),
      h('dt', {}, '语音'), h('dd', {}, tagOf(info.voice_enabled, '开', '关')),
      h('dt', {}, '接入'), h('dd', {}, tagOf(info.gateway_enabled, '开', '关')),
    )))

  // 生活主动
  wrap.append(proactiveCard(pro))

  // 数据分布
  const rows = Object.entries(t).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1])
  if (rows.length) {
    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '数据分布'),
      h('div', { class: 'table-wrap' },
        h('table', {},
          h('thead', {}, h('tr', {},
            h('th', {}, '表'), h('th', { class: 'num' }, '行数'))),
          h('tbody', {}, ...rows.map(([k, v]) => h('tr', {},
            h('td', {}, k), h('td', { class: 'num' }, v))))))))
  }
}

function stat(label, value, unit) {
  return h('div', { class: 'stat' },
    h('div', { class: 'stat-label' }, label),
    h('div', { class: 'stat-value' },
      typeof value === 'number' ? fmtNum(value) : value,
      unit ? h('span', { class: 'stat-unit' }, unit) : null))
}

function tagOf(on, yes, no) {
  return h('span', { class: 'tag ' + (on ? 'ok' : '') }, on ? yes : no)
}

function proactiveCard(pro) {
  const card = h('div', { class: 'card' },
    h('div', { class: 'card-title' }, '生活'))
  if (!pro || !pro.ok) {
    card.append(h('div', { class: 'proactive-hint' }, '接口没响应'))
    return card
  }

  const accounts = pro.accounts || []
  const candidates = accounts
    .flatMap(a => (a.candidates || []).map(c => ({ ...c, account_id: a.id })))
    .sort((a, b) => b.score - a.score)
  const top = candidates[0]

  card.append(h('div', { class: 'proactive-head' },
    h('span', { class: 'tag ' + (pro.enabled ? 'ok' : '') }, pro.enabled ? '开' : '关'),
    h('span', { class: 'proactive-meta' }, `今日已发 ${pro.today_sent || 0} 条`),
    h('span', { class: 'proactive-meta' },
      accounts.length ? `在线 ${accounts.length} 个账号` : '没有在线账号'),
  ))

  if (!pro.enabled) {
    card.append(h('div', { class: 'proactive-hint' },
      '关着。建议先开「只算不发」跑一天，看候选和理由，再真发。'))
  } else if (!top) {
    card.append(h('div', { class: 'proactive-hint' },
      '现在没有合适的候选人（安静时段 / 沉默不够 / 今日上限 / 退避中）。'))
  } else {
    card.append(h('div', { class: 'proactive-cand' },
      h('b', {}, top.name),
      h('span', { class: 'proactive-score' }, Number(top.score).toFixed(2)),
      h('div', { class: 'proactive-reason' }, top.reason || ''),
    ))
  }

  const previewBox = h('div', { class: 'proactive-preview' })
  const previewBtn = h('button', {
    class: 'btn btn-ghost btn-sm',
    onclick: async () => {
      previewBtn.disabled = true
      previewBox.textContent = '算一下…'
      try {
        const r = await api('/api/proactive/preview', { method: 'POST', body: {} })
        const d = (r.decisions || [])[0]
        if (!d) previewBox.textContent = '没有候选'
        else if (d.status === 'preview') previewBox.textContent = `「${d.name}」→ ${d.message}`
        else previewBox.textContent = `不发：${d.reason || d.status}`
      } catch (e) {
        previewBox.textContent = '预览失败：' + e.message
      } finally {
        previewBtn.disabled = false
      }
    },
  }, '预览一下')

  card.append(h('div', { class: 'proactive-actions' },
    previewBtn,
    h('button', { class: 'btn btn-ghost btn-sm', onclick: () => go('proactive') }, '打开生活'),
    previewBox,
  ))
  return card
}

function openReminderEditModal(r) {
  const mask = h('div', { class: 'modal-mask' })
  const text = h('input', { type: 'text', class: 'wide', value: r.text })
  const d = new Date(r.due_at * 1000)
  const pad = n => String(n).padStart(2, '0')
  const when = h('input', {
    type: 'text', class: 'wide',
    value: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`,
    placeholder: '10分钟后 / 明天 09:30',
  })
  const modal = h('div', { class: 'modal' },
    h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, '修改提醒')),
    h('div', { class: 'modal-body' },
      h('div', { class: 'form-row' }, h('label', {}, '内容'), text),
      h('div', { class: 'form-row' }, h('label', {}, '时间'), when)),
    h('div', { class: 'modal-foot' },
      h('button', { class: 'btn btn-ghost', onclick: () => close() }, '取消'),
      h('button', {
        class: 'btn btn-primary',
        onclick: async () => {
          try {
            const r2 = await api(`/api/tools/reminders/${r.id}`, {
              method: 'PATCH', body: { text: text.value.trim(), when: when.value.trim() },
            })
            if (!r2.ok) return toast(r2.error || '保存失败', 'err')
            close(); toast('已改到 ' + r2.due, 'ok'); go('workbench')
          } catch (e) { toast('保存失败：' + e.message, 'err') }
        },
      }, '保存')),
  )
  mask.append(modal)
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))
  function close() {
    mask.classList.remove('show')
    setTimeout(() => mask.remove(), 180)
  }
  mask.addEventListener('click', e => { if (e.target === mask) close() })
}

async function openDocPreview(id, title) {
  try {
    const r = await api(`/api/knowledge/docs/${id}`)
    if (!r.ok) return toast(r.error || '读不到文档', 'err')
    const mask = h('div', { class: 'modal-mask' })
    const body = h('div', {
      class: 'modal-body',
      style: 'max-height:56vh;overflow:auto;white-space:pre-wrap;font-size:12.5px;line-height:1.65',
    }, r.doc.text || '')
    const modal = h('div', { class: 'modal' },
      h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, title || r.doc.title)),
      body,
      h('div', { class: 'modal-foot' },
        h('button', { class: 'btn btn-ghost', onclick: () => close() }, '关闭')),
    )
    mask.append(modal)
    document.body.append(mask)
    requestAnimationFrame(() => mask.classList.add('show'))
    function close() {
      mask.classList.remove('show')
      setTimeout(() => mask.remove(), 180)
    }
    mask.addEventListener('click', e => { if (e.target === mask) close() })
  } catch (e) { toast('读取失败：' + e.message, 'err') }
}

function openNoteEditModal(n) {
  const mask = h('div', { class: 'modal-mask' })
  const title = h('input', { type: 'text', class: 'wide', value: n.title || '' })
  const body = h('textarea', { style: 'min-height:120px' })
  body.value = n.body || ''
  const modal = h('div', { class: 'modal' },
    h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, '编辑笔记')),
    h('div', { class: 'modal-body' },
      h('div', { class: 'form-row' }, h('label', {}, '标题'), title),
      h('div', { class: 'form-row' }, h('label', {}, '内容'), body)),
    h('div', { class: 'modal-foot' },
      h('button', { class: 'btn btn-ghost', onclick: () => close() }, '取消'),
      h('button', {
        class: 'btn btn-primary',
        onclick: async () => {
          try {
            const r = await api(`/api/tools/notes/${n.id}`, {
              method: 'PATCH', body: { title: title.value.trim(), body: body.value.trim() },
            })
            if (!r.ok) return toast(r.error || '保存失败', 'err')
            close(); toast('已保存', 'ok'); go('workbench')
          } catch (e) { toast('保存失败：' + e.message, 'err') }
        },
      }, '保存')),
  )
  mask.append(modal)
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))
  function close() {
    mask.classList.remove('show')
    setTimeout(() => mask.remove(), 180)
  }
  mask.addEventListener('click', e => { if (e.target === mask) close() })
}

/* ============================================================
   工作台
   ============================================================ */
const MODULE_TARGETS = {
  hippocampus: { group: '记忆' }, cortex: { group: '记忆' },
  amygdala: { group: '记忆' }, graph: { group: '记忆' },
  profile: { group: '记忆' }, recall: { group: '记忆' },
  coref: { group: '记忆' }, distill: { group: '记忆' },
  vector: { group: '记忆' },
  persona: { page: 'persona' },
  voice: { page: 'voice' },
  knowledge: { page: 'workbench' },
  llm: { group: '模型' },
  tokens: { group: '模型' },
  gateway: { group: '接入' },
  proactive: { group: '生活' },
  tools: { group: '工具' },
  asr: { group: '语音' },
}

function gotoModule(m) {
  const target = MODULE_TARGETS[m.id] || MODULE_TARGETS[m.id.toLowerCase()]
  if (!target) return toast('这个模块没有可调参数', '')
  if (target.page) return go(target.page)
  try { localStorage.setItem('novabot-settings-group', target.group) } catch {}
  go('settings')
}

async function renderWorkbench() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)

  const [notesRes, remRes, modRes, callRes, docRes] = await Promise.all([
    api('/api/tools/notes?limit=50' + accQuery('&')).catch(() => ({ notes: [] })),
    api('/api/tools/reminders?limit=50' + accQuery('&')).catch(() => ({ reminders: [] })),
    api('/api/system/modules' + accQuery()).catch(() => ({ modules: [], summary: {} })),
    api('/api/tools/calls?limit=50' + accQuery('&')).catch(() => ({ calls: [] })),
    api('/api/knowledge/docs' + accQuery()).catch(() => ({ docs: [] })),
  ])
  const notes = notesRes.notes || []
  const reminders = remRes.reminders || []
  const modules = modRes.modules || []
  const calls = callRes.calls || []
  const docs = docRes.docs || []
  const pending = reminders.filter(r => r.status === 'pending')
  const okModules = modules.filter(m => m.status === 'ok').length

  wrap.append(pageHead('工作台', `笔记、提醒和模块状态 · ${accountLabel()}`))

  wrap.append(h('div', { class: 'stat-grid' },
    stat('笔记', notes.length, '条'),
    stat('待提醒', pending.length, '条'),
    stat('文档', docs.length, '篇'),
    stat('工具调用', calls.length, '次'),
    stat('模块正常', `${okModules}/${modules.length}`, ''),
  ))

  // 左侧：笔记
  const noteList = h('div', { class: 'wb-list' })
  const noteSearch = h('input', {
    type: 'text', class: 'wide', placeholder: '搜索笔记',
    oninput: () => {
      const q = noteSearch.value.trim().toLowerCase()
      for (const row of noteList.children) {
        if (row.dataset.text === undefined) continue
        row.style.display = !q || row.dataset.text.includes(q) ? '' : 'none'
      }
    },
  })
  const noteTitle = h('input', { type: 'text', class: 'wide', placeholder: '标题（可空）' })
  const noteBody = h('textarea', { placeholder: '笔记内容', style: 'min-height:84px' })
  const editor = h('div', { class: 'wb-editor' }, noteTitle, noteBody,
    h('div', { style: 'display:flex;gap:8px;justify-content:flex-end' },
      h('button', {
        class: 'btn btn-ghost btn-sm',
        onclick: () => { editor.style.display = 'none' },
      }, '取消'),
      h('button', {
        class: 'btn btn-primary btn-sm',
        onclick: async () => {
          const body = noteBody.value.trim()
          if (!body) return toast('内容不能为空', 'err')
          try {
            const r = await api('/api/tools/notes', {
              method: 'POST',
              body: { title: noteTitle.value.trim(), body, account_id: currentAccount },
            })
            if (!r.ok) return toast(r.error || '保存失败', 'err')
            toast('已记下', 'ok')
            go('workbench')
          } catch (e) { toast('保存失败：' + e.message, 'err') }
        },
      }, '保存'),
    ))
  editor.style.display = 'none'

  const newBtn = h('button', {
    class: 'btn btn-ghost btn-sm',
    onclick: () => {
      editor.style.display = editor.style.display === 'none' ? 'grid' : 'none'
      if (editor.style.display === 'grid') noteBody.focus()
    },
  }, '新建笔记')

  for (const n of notes) {
    const row = h('div', { class: 'wb-row' },
      h('div', { class: 'wb-row-main' },
        h('div', { class: 'wb-row-title' }, n.title || '（无标题）'),
        h('div', { class: 'wb-row-sub' }, n.body || ''),
      ),
      h('div', { class: 'wb-row-side' },
        h('span', { class: 'wb-time' }, fmtTime(n.created_at)),
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: () => openNoteEditModal(n),
        }, '编辑'),
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: async () => {
            if (!confirm('删除这条笔记？')) return
            await api(`/api/tools/notes/${n.id}`, { method: 'DELETE' })
            toast('已删除', 'ok')
            go('workbench')
          },
        }, '删除'),
      ),
    )
    row.dataset.text = `${n.title || ''} ${n.body || ''}`.toLowerCase()
    noteList.append(row)
  }
  if (!notes.length) noteList.append(h('div', { class: 'wb-empty' }, '还没有笔记。点「新建笔记」写第一条。'))

  const notesCard = h('div', { class: 'card' },
    h('div', { class: 'wb-card-head' },
      h('div', { class: 'card-title' }, '笔记'),
      newBtn,
    ),
    noteSearch,
    editor,
    noteList,
  )

  // 右侧：提醒
  const remList = h('div', { class: 'wb-list' })
  const remText = h('input', { type: 'text', class: 'wide', placeholder: '提醒内容' })
  const remWhen = h('input', { type: 'text', class: 'wide', placeholder: '10分钟后 / 明天 09:30' })
  const remPresets = h('div', { class: 'wb-presets' },
    ...['10分钟后', '1小时后', '明天 09:00'].map(x => h('button', {
      class: 'btn btn-ghost btn-sm', type: 'button',
      onclick: () => { remWhen.value = x; remWhen.focus() },
    }, x)))
  const remForm = h('div', { class: 'wb-editor' },
    remText, remWhen, remPresets,
    h('div', { style: 'display:flex;justify-content:flex-end' },
      h('button', {
        class: 'btn btn-primary btn-sm',
        onclick: async () => {
          try {
            const r = await api('/api/tools/reminders', {
              method: 'POST',
              body: {
                text: remText.value.trim(),
                when: remWhen.value.trim(),
                account_id: currentAccount,
              },
            })
            if (!r.ok) return toast(r.error || '添加失败', 'err')
            toast('好，' + r.due + ' 提醒你', 'ok')
            go('workbench')
          } catch (e) { toast('添加失败：' + e.message, 'err') }
        },
      }, '添加'),
    ))
  remForm.style.display = 'none'

  const remBtn = h('button', {
    class: 'btn btn-ghost btn-sm',
    onclick: () => {
      remForm.style.display = remForm.style.display === 'none' ? 'grid' : 'none'
      if (remForm.style.display === 'grid') remText.focus()
    },
  }, '加提醒')

  for (const r of reminders.slice(0, 20)) {
    const tag = r.status === 'pending' ? h('span', { class: 'tag accent' }, '待提醒')
      : r.status === 'sent' ? h('span', { class: 'tag ok' }, '已发送')
      : h('span', { class: 'tag' }, r.status)
    remList.append(h('div', { class: 'wb-row' },
      h('div', { class: 'wb-row-main' },
        h('div', { class: 'wb-row-title' }, r.text),
        h('div', { class: 'wb-row-sub' }, fmtDateTime(r.due_at)),
      ),
      h('div', { class: 'wb-row-side' }, tag,
        r.status === 'pending' ? h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: async () => {
            await api(`/api/tools/reminders/${r.id}`, { method: 'DELETE' })
            toast('已取消', 'ok')
            go('workbench')
          },
        }, '取消') : null,
      ),
    ))
  }
  if (!reminders.length) remList.append(h('div', { class: 'wb-empty' }, '没有提醒。加一条试试。'))

  const remCard = h('div', { class: 'card' },
    h('div', { class: 'wb-card-head' },
      h('div', { class: 'card-title' }, '提醒'),
      remBtn,
    ),
    remForm,
    remList,
  )

  wrap.append(h('div', { class: 'wb-grid' }, notesCard, remCard))

  // 文档知识库
  const docList = h('div', { class: 'wb-list' })
  const docResults = h('div', { class: 'wb-list' })
  docResults.style.display = 'none'
  let docTimer = 0
  const docSearch = h('input', {
    type: 'search', class: 'wb-search', placeholder: '搜文档内容',
    oninput: () => {
      clearTimeout(docTimer)
      docTimer = setTimeout(async () => {
        const q = docSearch.value.trim()
        if (!q) {
          docResults.style.display = 'none'
          docList.style.display = ''
          return
        }
        docList.style.display = 'none'
        docResults.style.display = ''
        docResults.innerHTML = ''
        try {
          const r = await api('/api/knowledge/search?q=' + encodeURIComponent(q) + accQuery('&'))
          const hits = r.hits || []
          if (!hits.length) {
            docResults.append(h('div', { class: 'wb-empty' }, `没搜到「${q}」`))
            return
          }
          for (const hit of hits) {
            docResults.append(h('div', { class: 'wb-row' },
              h('div', { class: 'wb-row-main' },
                h('div', { class: 'wb-row-title' }, `《${hit.title}》`),
                h('div', { class: 'wb-row-sub' }, String(hit.text || '').slice(0, 180)),
              ),
              h('div', { class: 'wb-row-side' },
                h('span', { class: 'wb-time' }, `${hit.source || ''} 第${hit.line_start}-${hit.line_end}行`)),
            ))
          }
        } catch (e) {
          docResults.append(h('div', { class: 'wb-empty' }, '搜索失败：' + e.message))
        }
      }, 250)
    },
  })
  const docTitle = h('input', { type: 'text', class: 'wide', placeholder: '文档标题' })
  const docSource = h('input', { type: 'text', class: 'wide', placeholder: '来源（文件名/链接，可空）' })
  const docText = h('textarea', { placeholder: '粘贴 .md / .txt 内容，或选文件（pdf/docx 直接导入）', style: 'min-height:96px' })
  const docFile = h('input', {
    type: 'file', accept: '.md,.markdown,.txt,.pdf,.docx',
    onchange: async () => {
      const f = docFile.files?.[0]
      if (!f) return
      const ext = (f.name.split('.').pop() || '').toLowerCase()
      if (ext === 'pdf' || ext === 'docx') {
        const fd = new FormData()
        fd.append('file', f)
        fd.append('account_id', currentAccount)
        try {
          const r = await fetch('/api/knowledge/import_file', {
            method: 'POST', body: fd,
            headers: panelToken ? { 'X-Panel-Token': panelToken } : {},
          })
          if (r.status === 401) { showLogin(); return }
          const data = await r.json()
          if (!data.ok) return toast(data.error || '导入失败', 'err')
          toast(`已导入 ${data.chunks} 片`, 'ok')
          go('workbench')
        } catch (e) { toast('导入失败：' + e.message, 'err') }
        return
      }
      docText.value = await f.text()
      if (!docTitle.value) docTitle.value = f.name.replace(/\.(md|markdown|txt)$/i, '')
      if (!docSource.value) docSource.value = f.name
    },
  })
  const docForm = h('div', { class: 'wb-editor' }, docFile, docTitle, docSource, docText,
    h('div', { style: 'display:flex;justify-content:flex-end' },
      h('button', {
        class: 'btn btn-primary btn-sm',
        onclick: async () => {
          const text = docText.value.trim()
          if (!text) return toast('内容不能为空', 'err')
          try {
            const r = await api('/api/knowledge/import', {
              method: 'POST',
              body: {
                title: docTitle.value.trim(),
                source: docSource.value.trim(),
                text,
                account_id: currentAccount,
              },
            })
            if (!r.ok) return toast(r.error || '导入失败', 'err')
            toast(`已导入 ${r.chunks} 片`, 'ok')
            go('workbench')
          } catch (e) { toast('导入失败：' + e.message, 'err') }
        },
      }, '导入'),
    ))
  docForm.style.display = 'none'
  const docBtn = h('button', {
    class: 'btn btn-ghost btn-sm',
    onclick: () => {
      docForm.style.display = docForm.style.display === 'none' ? 'grid' : 'none'
      if (docForm.style.display === 'grid') docTitle.focus()
    },
  }, '导入文档')

  for (const d of docs) {
    docList.append(h('div', { class: 'wb-row' },
      h('div', { class: 'wb-row-main' },
        h('div', {
          class: 'wb-row-title', style: 'cursor:pointer',
          onclick: () => openDocPreview(d.id, d.title),
        }, d.title),
        h('div', { class: 'wb-row-sub' },
          `${d.source || '未标来源'} · ${d.chunks} 片 · ${fmtDateTime(d.created_at)}`),
      ),
      h('div', { class: 'wb-row-side' },
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: async () => {
            if (!confirm(`删除文档「${d.title}」？`)) return
            await api(`/api/knowledge/docs/${d.id}`, { method: 'DELETE' })
            toast('已删除', 'ok')
            go('workbench')
          },
        }, '删除'),
      ),
    ))
  }
  if (!docs.length) docList.append(h('div', { class: 'wb-empty' }, '还没有文档。粘贴一篇 md/txt 试试，回答会带来源。'))

  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'wb-card-head' },
      h('div', { class: 'card-title' }, '文档知识库'),
      h('div', { class: 'wb-head-actions' }, docSearch, docBtn),
    ),
    docForm,
    docResults,
    docList,
  ))

  // 模块状态：默认只显示不是 ok 的，点按钮看全部
  const tone = { ok: 'ok', warn: 'warn', missing: 'err', off: '' }
  let showAllModules = false
  const modBody = h('tbody')
  const paintModules = () => {
    modBody.innerHTML = ''
    const list = showAllModules ? modules : modules.filter(m => m.status === 'warn' || m.status === 'missing')
    if (!list.length) {
      modBody.append(h('tr', {}, h('td', { colspan: '3', style: 'color:var(--text-tertiary)' },
        '没有需要处理的模块')))
      return
    }
    for (const m of list) {
      modBody.append(h('tr', {
        class: 'module-row',
        title: '打开这个模块的设置',
        onclick: () => gotoModule(m),
      },
        h('td', {}, m.name),
        h('td', {}, h('span', { class: 'tag ' + tone[m.status] }, m.status)),
        h('td', { style: 'color:var(--text-tertiary)' }, m.detail || '',
          h('span', { class: 'module-arrow' }, ' →')),
      ))
    }
  }
  paintModules()
  const modToggle = h('button', {
    class: 'btn btn-ghost btn-sm',
    onclick: () => {
      showAllModules = !showAllModules
      modToggle.textContent = showAllModules ? '只看异常' : '显示全部'
      paintModules()
    },
  }, '显示全部')

  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'wb-card-head' },
      h('div', { class: 'card-title' }, '模块状态'),
      modToggle,
    ),
    h('div', { class: 'table-wrap' },
      h('table', {},
        h('thead', {}, h('tr', {}, h('th', {}, '模块'), h('th', {}, '状态'), h('th', {}, '说明'))),
        modBody,
      )),
  ))
}

/* ============================================================
   人物
   ============================================================ */

function personAvatar(p, size = 'md') {
  const name = String(p.name || p.qq || '?').trim()
  const initial = (name.slice(0, 1) || '?').toUpperCase()
  const box = h('div', { class: `avatar avatar-${size}`, 'aria-hidden': 'true' },
    h('span', { class: 'avatar-initial' }, initial))
  const qq = String(p.qq || '')
  const url = p.avatar || (/^\d+$/.test(qq) ? `https://q1.qlogo.cn/g?b=qq&nk=${qq}&s=100` : '')
  if (url) {
    const img = h('img', { src: url, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' })
    img.addEventListener('error', () => img.remove())
    box.append(img)
  }
  return box
}

function personTitle(p) {
  return h('div', { class: 'person-title' },
    h('span', { class: 'person-name' }, p.name || p.qq || '未命名'),
    p.is_owner ? h('span', { class: 'tag accent' }, '主人') : null)
}

async function renderPeople() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)

  let sort = 'recent'
  let layout = 'list'
  try {
    sort = localStorage.getItem('novabot-people-sort') || 'recent'
    layout = localStorage.getItem('novabot-people-layout') || 'list'
  } catch {}
  if (!['recent', 'messages', 'name', 'first'].includes(sort)) sort = 'recent'
  if (!['list', 'two', 'three'].includes(layout)) layout = 'list'

  wrap.append(pageHead('人物', '记住的每个人 · 头像 / 排序 / 三种视图'))

  const search = h('input', {
    type: 'search', class: 'people-search', autocomplete: 'off',
    placeholder: '搜名字或 QQ',
  })
  const sortSel = h('select', { class: 'people-sort', 'aria-label': '人物排序' },
    h('option', { value: 'recent' }, '最近出现'),
    h('option', { value: 'messages' }, '消息最多'),
    h('option', { value: 'name' }, '名字'),
    h('option', { value: 'first' }, '最早认识'))
  sortSel.value = sort

  const layoutBar = h('div', { class: 'people-layout', role: 'group', 'aria-label': '人物视图' })
  const layouts = [['list', '单列'], ['two', '双列'], ['three', '三列']]
  const layoutBtns = {}
  for (const [id, label] of layouts) {
    const b = h('button', {
      type: 'button', class: 'people-layout-btn' + (layout === id ? ' on' : ''),
      title: id === 'list' ? '单列列表' : (id === 'two' ? '双列宽卡片' : '三列卡片'),
      onclick: () => {
        layout = id
        try { localStorage.setItem('novabot-people-layout', id) } catch {}
        for (const [k, el] of Object.entries(layoutBtns)) el.classList.toggle('on', k === id)
        if (peopleCache.length) render(peopleCache, totalCache)
      },
    }, label)
    layoutBtns[id] = b
    layoutBar.append(b)
  }

  const count = h('span', { class: 'people-count' }, '')
  wrap.append(h('div', { class: 'card people-toolbar' },
    h('div', { class: 'people-search-box' }, icon('search'), search),
    h('div', { class: 'people-tools' }, sortSel, layoutBar, count),
  ))

  const box = h('div', {})
  wrap.append(box)
  let peopleCache = []
  let totalCache = 0
  let timer

  const empty = (q) => emptyState(
    q ? '没搜到' : '还没有记录到人',
    q ? '换个名字或 QQ 试试' : '收到消息、蒸馏人物之后，这里会出现头像和画像',
    'user')

  const ownerBadge = (p) => p.is_owner ? h('span', { class: 'tag accent' }, '主人') : null

  const personLine = (p) => h('div', { class: 'person-line' },
    personAvatar(p, 'sm'),
    h('div', { class: 'person-line-body' },
      h('div', { class: 'person-line-name' }, p.name || p.qq || '未命名', ownerBadge(p)),
      h('div', { class: 'person-line-sub' }, p.style || '—')));

  function renderList(ps) {
    return h('div', { class: 'card people-list-card' },
      h('div', { class: 'table-wrap' },
        h('table', { class: 'people-table' },
          h('thead', {}, h('tr', {},
            h('th', {}, '称呼'), h('th', {}, 'QQ'), h('th', {}, '说话方式'),
            h('th', { class: 'num' }, '消息'), h('th', { class: 'num' }, '最后出现'))),
          h('tbody', {}, ...ps.map(p => h('tr', {
            class: 'clickable', onclick: () => showPerson(p.qq),
          },
            h('td', {}, personLine(p)),
            h('td', { class: 'num person-qq' }, p.qq),
            h('td', { class: 'muted person-style' }, p.style || '—'),
            h('td', { class: 'num' }, fmtNum(p.msg_count || 0)),
            h('td', { class: 'num' }, fmtTime(p.last_seen)),
          ))))))
  }

  function personRow(p) {
    return h('div', { class: 'person-card person-row clickable', onclick: () => showPerson(p.qq) },
      personAvatar(p, 'md'),
      h('div', { class: 'person-row-body' },
        personTitle(p),
        h('div', { class: 'person-row-style' }, p.style || p.persona || '还没有说话方式记录'),
      ),
      h('div', { class: 'person-row-side' },
        h('div', { class: 'person-stat' }, h('b', {}, fmtNum(p.msg_count || 0)), h('span', {}, '条消息')),
        h('div', { class: 'person-stat' }, fmtTime(p.last_seen)),
      ),
    )
  }

  function personTile(p) {
    return h('div', { class: 'person-card person-tile clickable', onclick: () => showPerson(p.qq) },
      h('div', { class: 'person-tile-head' },
        personAvatar(p, 'lg'),
        h('div', { class: 'person-tile-title' }, personTitle(p), h('div', { class: 'person-qq' }, 'QQ ' + p.qq))),
      h('div', { class: 'person-tile-style' }, p.style || p.persona || '还没有说话方式记录'),
      h('div', { class: 'person-tile-foot' },
        h('span', {}, `${fmtNum(p.msg_count || 0)} 条消息`),
        h('span', {}, fmtTime(p.last_seen))),
    )
  }

  function render(ps, total) {
    peopleCache = ps
    totalCache = total
    box.innerHTML = ''
    count.textContent = `显示 ${ps.length} / ${total}`
    if (!ps.length) { box.append(empty(search.value.trim())); return }
    if (layout === 'two') {
      box.append(h('div', { class: 'people-grid people-grid-two' }, ...ps.map(personRow)))
    } else if (layout === 'three') {
      box.append(h('div', { class: 'people-grid people-grid-three' }, ...ps.map(personTile)))
    } else {
      box.append(renderList(ps))
    }
  }

  async function load() {
    box.innerHTML = ''
    box.append(h('div', { class: 'empty' }, h('div', { class: 'skeleton', style: 'width:180px' })))
    const q = search.value.trim()
    const r = await api(`/api/data/people?limit=300&sort=${encodeURIComponent(sort)}&q=${encodeURIComponent(q)}` + accQuery('&'))
    render(r.people || [], r.total || 0)
  }

  search.addEventListener('input', () => {
    clearTimeout(timer)
    timer = setTimeout(load, 260)
  })
  sortSel.addEventListener('change', () => {
    sort = sortSel.value
    try { localStorage.setItem('novabot-people-sort', sort) } catch {}
    load()
  })
  await load()
}

async function showPerson(qq) {
  const r = await api('/api/data/person/' + qq + accQuery())
  if (!r.ok) return toast(r.error || '读不到', 'err')
  const p = r.person

  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)

  wrap.append(pageHead(p.name || p.qq, `QQ ${p.qq}`,
    h('button', { class: 'btn btn-ghost', onclick: () => go('people') },
      icon('arrow'), '返回')))

  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'person-detail-head' }, personAvatar(p, 'xl'),
      h('div', { class: 'card-title' }, '画像')),
    h('dl', { class: 'kv-grid' },
      h('dt', {}, '说话方式'), h('dd', {}, p.style || '—'),
      h('dt', {}, '性格'), h('dd', {}, p.persona || '—'),
      h('dt', {}, '怎么应付'), h('dd', {}, p.handle || '—'),
      h('dt', {}, '消息数'), h('dd', {}, String(p.msg_count || 0)),
      h('dt', {}, '最后出现'), h('dd', {}, fmtTime(p.last_seen)),
    )))

  if ((r.group_names || []).length) {
    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '群聊名片', h('span', { class: 'badge' }, String(r.group_names.length))),
      h('div', { class: 'group-name-list' },
        ...r.group_names.map(g => h('div', { class: 'group-name-row' },
          h('div', { class: 'group-name-main' },
            h('div', { class: 'group-name-title' }, g.title ? `群 ${g.title}` : `群 ${g.group_id}`),
            h('div', { class: 'group-name-card' }, g.card)),
          h('div', { class: 'group-name-time' }, fmtTime(g.last_seen)))))))
  }

  if ((r.facts || []).length) {
    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '记得的事',
        h('span', { class: 'badge' }, String(r.facts.length))),
      h('div', { class: 'table-wrap' },
        h('table', {},
          h('thead', {}, h('tr', {},
            h('th', {}, '内容'), h('th', {}, '重要度'), h('th', {}, '状态'), h('th', { class: 'num' }, '记于'))),
          h('tbody', {}, ...r.facts.map(f => h('tr', {},
            h('td', {}, f.text),
            h('td', {}, h('span', { class: 'tag' }, 'tier ' + f.tier)),
            h('td', {}, f.valid_until
              ? h('span', { class: 'tag' }, '已过期')
              : h('span', { class: 'tag ok' }, '有效')),
            h('td', { class: 'num' }, fmtTime(f.recorded_at)),
          )))))))
  }

  if ((r.emotions || []).length) {
    const total = r.emotions.reduce((a, e) => a + e.count, 0)
    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '情绪分布'),
      h('dl', { class: 'kv-grid' },
        ...r.emotions.flatMap(e => [
          h('dt', {}, e.emotion),
          h('dd', {}, `${e.count} 次（${(e.count / total * 100).toFixed(0)}%）`),
        ]))))
  }

  if ((r.recent || []).length) {
    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '最近说过'),
      h('div', { class: 'table-wrap' },
        h('table', {},
          h('tbody', {}, ...r.recent.map(e => h('tr', {},
            h('td', {}, e.text || ''),
            h('td', { class: 'num', style: 'width:110px' }, fmtTime(e.event_at)),
          )))))))
  }
}

/* ============================================================
   记忆
   ============================================================ */
async function renderMemory() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)

  const ov = await api('/api/data/overview' + accQuery()).catch(() => ({ counts: {} }))
  const counts = ov.counts || {}
  const SECTIONS = [
    ['overview', '概览', ''],
    ['episodes', '对话', counts.episodes],
    ['facts', '事实', counts.facts],
    ['gists', '摘要', counts.gists],
    ['people', '人物', counts.people],
    ['documents', '知识', counts.documents],
  ]
  let section = 'overview'
  try { section = localStorage.getItem('novabot-memory-section') || 'overview' } catch {}
  if (!SECTIONS.some(s => s[0] === section)) section = 'overview'
  let q = ''
  let timer = null
  let seq = 0
  let unlocked = false

  wrap.append(pageHead('记忆', '按分区查：对话、事实、摘要、人物、知识',
    h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openGroupSettingsModal(['记忆'], '记忆参数') }, '记忆参数')))

  const safetyToggle = h('input', { type: 'checkbox' })
  const safetyState = h('span', { class: 'memory-safety-state' }, '已锁定')
  safetyToggle.addEventListener('change', () => {
    unlocked = safetyToggle.checked
    safetyState.textContent = unlocked ? '已解锁，可编辑/删除' : '已锁定，只读'
    safetyState.classList.toggle('on', unlocked)
    renderSection(section)
  })
  wrap.append(h('div', { class: 'card memory-safety' },
    h('div', { class: 'memory-safety-main' },
      h('div', { class: 'card-title' }, '编辑保险'),
      h('div', { class: 'muted' }, '默认锁定。打开后才能点文字改、点垃圾桶删；刷新页面会重新锁定。')),
    h('label', { class: 'memory-safety-switch' }, safetyToggle, safetyState)))

  wrap.append(h('div', { class: 'card memory-privacy' },
    h('div', { class: 'memory-privacy-head' },
      h('div', {},
        h('div', { class: 'card-title' }, '隐私边界'),
        h('div', { class: 'page-desc', style: 'margin:4px 0 0' },
          '群聊和别人的私聊只读自己的记忆；输出层会拦截其他会话内容。')),
      h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openGroupSettingsModal(['隐私'], '隐私参数') }, '隐私设置'))))

  const statGrid = h('div', { class: 'stat-grid memory-stat-grid' })
  const goSection = (id) => {
    q = ''
    search.value = ''
    show(id)
  }
  for (const [id, label, n] of SECTIONS.slice(1)) {
    statGrid.append(h('button', {
      class: 'stat memory-stat', type: 'button', onclick: () => goSection(id),
    },
      h('div', { class: 'stat-label' }, label),
      h('div', { class: 'stat-value' }, fmtNum(n || 0)),
    ))
  }
  wrap.append(statGrid)

  const search = h('input', {
    type: 'search', class: 'wide memory-search-input',
    placeholder: '搜对话、事实、摘要、人物、知识……',
    autocomplete: 'off',
  })
  wrap.append(h('div', { class: 'card memory-search-card' },
    h('span', { class: 'memory-search-icon' }, icon('search')),
    search,
    h('span', { class: 'memory-search-hint' }, '实时搜索'),
  ))

  const tabs = h('div', { class: 'memory-tabs' })
  const chips = {}
  for (const [id, label, n] of SECTIONS) {
    const chip = h('button', {
      class: 'memory-tab' + (id === section ? ' on' : ''), type: 'button',
      onclick: () => { q = ''; search.value = ''; show(id) },
    }, label, n ? h('span', { class: 'memory-tab-count' }, fmtNum(n)) : null)
    chips[id] = chip
    tabs.append(chip)
  }
  wrap.append(tabs)

  const content = h('div', { class: 'memory-content' })
  wrap.append(content)

  const setActive = (id) => {
    for (const [k, chip] of Object.entries(chips)) chip.classList.toggle('on', k === id)
  }

  const loading = () => {
    content.innerHTML = ''
    content.append(h('div', { class: 'memory-loading' }, '加载中…'))
  }

  const table = (headers, rows) => h('div', { class: 'table-wrap memory-table' },
    h('table', {},
      h('thead', {}, h('tr', {}, ...headers.map(x => h('th', {}, x)))),
      h('tbody', {}, ...rows.map(cells => h('tr', {}, ...cells.map(cell =>
        h('td', {}, cell)))))))

  const editHeaders = { 'X-Novabot-Edit': '1' }
  const editBase = { episodes: 'episodes', facts: 'facts', gists: 'gists' }
  const editableCell = (kind, row) => {
    const text = row.text || ''
    if (!unlocked) return h('div', { class: 'memory-clamp' }, text || '（空）')
    const view = h('div', { class: 'memory-clamp memory-editable', title: '点击编辑', role: 'button', tabindex: '0' }, text || '（空）')
    const begin = () => {
      const input = h('textarea', { class: 'memory-edit-input', rows: '2' }, text)
      input.value = text
      let closed = false
      const restore = () => { if (closed) return; closed = true; input.replaceWith(view) }
      const commit = async () => {
        if (closed) return
        const value = input.value.trim()
        if (!value) { toast('内容不能为空', 'err'); input.focus(); return }
        if (value === text) { restore(); return }
        closed = true
        try {
          await api(`/api/data/${editBase[kind]}/${row.id}` + accQuery(), {
            method: 'PATCH', headers: editHeaders,
            body: { text: value, account_id: currentAccount || '' },
          })
          view.textContent = value
          view.classList.remove('memory-editable-error')
          input.replaceWith(view)
          toast('已保存', 'ok')
        } catch (e) {
          closed = false
          input.focus()
          toast('保存失败：' + e.message, 'err')
        }
      }
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Escape') { ev.preventDefault(); restore() }
        else if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); commit() }
      })
      input.addEventListener('blur', () => setTimeout(commit, 120))
      view.replaceWith(input)
      input.focus()
      input.select()
    }
    view.addEventListener('click', begin)
    view.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); begin() } })
    return view
  }

  const deleteCell = (kind, row) => {
    if (!unlocked) return null
    const btn = h('button', {
      class: 'btn btn-ghost memory-delete', title: '删除这条记录', 'aria-label': '删除',
    }, icon('trash'))
    btn.addEventListener('click', async () => {
      if (!confirm('删除这条记录？删除后记忆检索里不会再出现。')) return
      btn.disabled = true
      try {
        await api(`/api/data/${editBase[kind]}/${row.id}` + accQuery(), {
          method: 'DELETE', headers: editHeaders,
        })
        const tr = btn.closest('tr')
        if (tr) tr.remove()
        toast('已删除', 'ok')
      } catch (e) {
        btn.disabled = false
        toast('删除失败：' + e.message, 'err')
      }
    })
    return h('div', { class: 'memory-actions' }, btn)
  }

  const empty = (text) => h('div', { class: 'memory-empty' }, text)

  const tierTag = (tier) => {
    const n = Number(tier)
    if (n === 0) return h('span', { class: 'tag ok' }, '永久')
    if (n === 1) return h('span', { class: 'tag' }, '长期')
    if (n === 2) return h('span', { class: 'tag' }, '中期')
    return h('span', { class: 'tag' }, '短期')
  }

  const item = (who, text, at, extra) => h('div', { class: 'memory-item' },
    h('div', { class: 'memory-item-head' },
      h('span', { class: 'memory-who' }, who || '未知'),
      h('span', { class: 'memory-item-meta' }, extra || '', at ? fmtTime(at) : '')),
    h('div', { class: 'memory-item-text' }, text || '（空）'))

  const overview = () => {
    content.innerHTML = ''
    const recent = h('div', { class: 'memory-overview-grid' })
    if ((ov.recent_episodes || []).length) {
      recent.append(h('div', { class: 'memory-overview-col' },
        h('div', { class: 'memory-col-title' }, '最近对话',
          h('button', { class: 'btn btn-ghost btn-sm', onclick: () => goSection('episodes') }, '全部')),
        ...ov.recent_episodes.map(e => item(e.sender_nick || e.sender_qq, e.text, e.event_at))))
    }
    if ((ov.recent_facts || []).length) {
      recent.append(h('div', { class: 'memory-overview-col' },
        h('div', { class: 'memory-col-title' }, '最近事实',
          h('button', { class: 'btn btn-ghost btn-sm', onclick: () => goSection('facts') }, '全部')),
        ...ov.recent_facts.map(f => item(f.subject_name || f.subject_id, f.text, f.recorded_at, tierTag(f.tier)))))
    }
    if (!recent.children.length) {
      recent.append(empty('还没有记忆。收到消息、蒸馏或导入文档后会分区出现。'))
    }
    content.append(h('div', { class: 'card memory-overview' },
      h('div', { class: 'card-title' }, '记忆概览', h('span', { class: 'badge' }, ov.db_size || '')),
      recent))
    content.append(h('div', { class: 'card memory-tip' },
      h('div', { class: 'card-title' }, '分区说明'),
      h('div', { class: 'memory-tip-row' }, h('b', {}, '对话'), '原始消息，按时间倒序；蒸馏后仍保留。'),
      h('div', { class: 'memory-tip-row' }, h('b', {}, '事实'), '从对话提炼的长期事实，T0 永久、T1 长期、T2 中期、T3 短期。'),
      h('div', { class: 'memory-tip-row' }, h('b', {}, '摘要'), '按时间段压缩的 gist，层级越高越概括。'),
      h('div', { class: 'memory-tip-row' }, h('b', {}, '人物'), '每个人的画像、别名、消息数和最后出现时间。'),
      h('div', { class: 'memory-tip-row' }, h('b', {}, '知识'), '导入的 md/txt/pdf/docx 文档知识库。'),
    ))
  }

  const renderEpisodes = (r) => {
    if (!(r.episodes || []).length) return empty('这个分区还没有对话。')
    return table(unlocked ? ['谁', '说了什么', '会话', '时间', '操作'] : ['谁', '说了什么', '会话', '时间'], r.episodes.map(e => [
      h('span', { class: 'strong' }, e.sender || e.sender_qq || '未知'),
      editableCell('episodes', e),
      h('span', { class: 'memory-mono' }, e.chat_id),
      h('span', { class: 'num' }, fmtTime(e.at)),
      deleteCell('episodes', e),
    ].filter(x => x != null)))
  }

  const renderFacts = (r) => {
    if (!(r.facts || []).length) return empty('这个分区还没有提炼出事实。')
    return table(unlocked ? ['关于', '事实', '层级', '记录时间', '操作'] : ['关于', '事实', '层级', '记录时间'], r.facts.map(f => [
      h('div', {}, h('span', { class: 'strong' }, f.subject_name || f.subject_id || '—'),
        f.subject_name ? h('span', { class: 'muted memory-mono' }, ' ' + f.subject_id) : null),
      editableCell('facts', f),
      tierTag(f.tier),
      h('span', { class: 'num' }, fmtTime(f.recorded_at)),
      deleteCell('facts', f),
    ].filter(x => x != null)))
  }

  const renderGists = (r) => {
    if (!(r.gists || []).length) return empty('这个分区还没有摘要。')
    return table(unlocked ? ['层级', '摘要', '时间段', '操作'] : ['层级', '摘要', '时间段'], r.gists.map(g => [
      h('span', { class: 'tag' }, 'L' + (g.level || 1)),
      editableCell('gists', g),
      h('span', { class: 'num' }, `${fmtTime(g.period_from)} → ${fmtTime(g.period_to)}`),
      deleteCell('gists', g),
    ].filter(x => x != null)))
  }

  const renderPeople = (r) => {
    if (!(r.people || []).length) return empty('这个分区还没有人物。')
    return table(['名字', 'QQ', '消息', '最后出现'], r.people.map(p => [
      h('div', {}, h('span', { class: 'strong' }, p.name || p.alias || '未命名'),
        p.alias && p.name ? h('span', { class: 'muted' }, ' · ' + p.alias) : null),
      h('span', { class: 'memory-mono' }, p.qq),
      h('span', { class: 'num' }, fmtNum(p.msg_count || 0)),
      h('span', { class: 'num' }, fmtTime(p.last_seen)),
    ]))
  }

  const renderDocs = (r) => {
    if (!(r.docs || []).length) return empty('还没有导入知识文档。')
    return table(['标题', '来源', '更新时间'], r.docs.map(d => [
      h('span', { class: 'strong' }, d.title || '无标题'),
      h('span', { class: 'muted memory-clamp' }, d.source || '—'),
      h('span', { class: 'num' }, fmtTime(d.updated_at || d.created_at)),
    ]))
  }

  const loaders = {
    episodes: () => api('/api/data/episodes?limit=100' + accQuery('&')),
    facts: () => api('/api/data/facts?limit=100' + accQuery('&')),
    gists: () => api('/api/data/gists?limit=100' + accQuery('&')),
    people: () => api('/api/data/people?limit=100' + accQuery('&')),
    documents: () => api('/api/knowledge/docs' + accQuery()),
  }
  const renderers = {
    episodes: renderEpisodes,
    facts: renderFacts,
    gists: renderGists,
    people: renderPeople,
    documents: renderDocs,
  }

  async function renderSection(id) {
    const my = ++seq
    setActive(id)
    if (id === 'overview') { overview(); return }
    loading()
    try {
      const r = await loaders[id]()
      if (my !== seq) return
      content.innerHTML = ''
      content.append(h('div', { class: 'card memory-section' },
        h('div', { class: 'card-title' }, SECTIONS.find(s => s[0] === id)?.[1] || '',
          h('span', { class: 'badge' }, fmtNum((r[id] || r.docs || []).length))),
        renderers[id](r)))
    } catch (e) {
      if (my !== seq) return
      content.innerHTML = ''
      content.append(empty('加载失败：' + e.message))
    }
  }

  async function renderSearch(query) {
    const my = ++seq
    setActive('')
    loading()
    try {
      const r = await api('/api/data/search?q=' + encodeURIComponent(query) + '&limit=20' + accQuery('&'))
      if (my !== seq) return
      content.innerHTML = ''
      const total = r.total || 0
      content.append(h('div', { class: 'memory-search-head' },
        h('span', {}, `搜索「${query}」`),
        h('span', { class: 'badge' }, `${total} 条`)))
      if (!total) {
        content.append(empty('没有匹配的记忆。换个关键词试试。'))
        return
      }
      const group = (title, items, render) => {
        if (!(items || []).length) return
        content.append(h('div', { class: 'card memory-search-group' },
          h('div', { class: 'card-title' }, title, h('span', { class: 'badge' }, String(items.length))),
          ...items.map(render)))
      }
      group('对话', r.episodes, e => item(e.sender_nick || e.sender_qq, e.text, e.event_at))
      group('事实', r.facts, f => item(f.subject_name || f.subject_id, f.text, f.recorded_at, tierTag(f.tier)))
      group('摘要', r.gists, g => item('L' + (g.level || 1), g.text, g.period_to))
      group('人物', r.people, p => item(p.name || p.qq, p.style || '', p.last_seen))
      group('知识', r.documents, d => item(d.title || '无标题', d.source || '', d.created_at))
    } catch (e) {
      if (my !== seq) return
      content.innerHTML = ''
      content.append(empty('搜索失败：' + e.message))
    }
  }

  function show(id) {
    section = id
    try { localStorage.setItem('novabot-memory-section', id) } catch {}
    if (q) renderSearch(q)
    else renderSection(id)
  }

  search.addEventListener('input', () => {
    clearTimeout(timer)
    timer = setTimeout(() => {
      q = search.value.trim()
      if (q) renderSearch(q)
      else renderSection(section)
    }, 260)
  })
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { search.value = ''; q = ''; renderSection(section) }
  })

  show(section)
}
/* ============================================================
   接入
   ============================================================ */
function fmtBytes(n) {
  n = Number(n) || 0
  if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(1) + ' GB'
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n >= 1024) return (n / 1024).toFixed(0) + ' KB'
  return n + ' B'
}

async function pluginDownloaderCard() {
  const cat = await api('/api/plugins').catch(() => ({ tools: [], sources: [] }))
  const tools = cat.tools || []
  const sources = cat.sources || []
  const toolSel = h('select', {})
  for (const t of tools) toolSel.append(h('option', { value: t.id }, `${t.name}${t.installed?.length ? ' · 已装 v' + t.installed[0] : ''}`))
  const verSel = h('select', {})
  const assetSel = h('select', {})
  const srcSel = h('select', {})
  for (const s of sources) srcSel.append(h('option', { value: s.id, selected: s.id === 'auto' || null }, s.name + (s.id === 'auto' ? '（推荐）' : '')))
  const status = h('div', { class: 'plugin-status muted' }, '选择版本后开始下载')
  const bar = h('div', { class: 'plugin-bar' }, h('span', {}))
  const pathLine = h('div', { class: 'plugin-path muted' }, '')
  const installedBox = h('div', { class: 'plugin-installed' })
  let releases = []
  let jobTimer = null

  const refreshInstalled = (tool) => {
    const info = tools.find(x => x.id === tool)
    return info?.installed?.length ? '已安装：v' + info.installed.join(' / v') : '本机未检测到安装'
  }

  const switchVersion = async (version) => {
    if (!confirm(`切换到 SnowLuma v${version}？\n会重启底座，三个 QQ 会短暂离线。`)) return
    status.textContent = `正在切换到 v${version}…`
    const r = await api('/api/plugins/switch', { method: 'POST', body: { tool: 'snowluma', version } })
      .catch(e => ({ ok: false, error: e.message }))
    if (!r.ok) {
      status.textContent = r.error || '切换失败'
      return toast(r.error || '切换失败', 'err')
    }
    toast(`已切换到 v${version}`, 'ok')
    setTimeout(() => renderGateway(), 1200)
  }

  const renderInstalled = () => {
    installedBox.innerHTML = ''
    const info = tools.find(x => x.id === toolSel.value)
    const versions = info?.versions || []
    installedBox.append(h('div', { class: 'plugin-installed-title' }, '本机已安装版本'))
    if (!versions.length) {
      installedBox.append(h('div', { class: 'muted' }, '本机没有检测到带 node.exe 的安装目录。'))
      return
    }
    for (const v of versions) {
      const current = Boolean(v.current)
      installedBox.append(h('div', { class: 'plugin-installed-row' },
        h('span', { class: 'plugin-version' }, 'v' + v.version),
        h('span', { class: 'muted' }, v.path),
        current ? h('span', { class: 'tag ok' }, '当前运行') : h('button', {
          class: 'btn btn-ghost btn-sm', onclick: () => switchVersion(v.version),
        }, '切换')))
    }
  }
  toolSel.addEventListener('change', () => { renderInstalled(); loadReleases() })

  const fillAssets = () => {
    assetSel.innerHTML = ''
    const rel = releases.find(r => r.tag === verSel.value)
    if (!rel) return
    for (const a of rel.assets || []) {
      const label = a.variant === 'lite' ? 'Lite（需 Node.js）'
        : a.variant === 'onekey' ? '一键包' : a.variant === 'node' ? 'Node 版' : '完整版'
      assetSel.append(h('option', { value: a.name }, `${a.name} · ${label}${a.size ? ' · ' + fmtBytes(a.size) : ' · 大小待测'}`))
    }
  }

  const loadReleases = async (refresh = false) => {
    status.textContent = refresh ? '正在刷新版本…' : '正在读取版本…'
    const r = await api(`/api/plugins/${encodeURIComponent(toolSel.value)}/releases${refresh ? '?refresh=1' : ''}`)
    if (!r.ok) {
      status.textContent = r.error || '读取版本失败（GitHub API 可能被拦截）'
      return
    }
    releases = r.releases || []
    verSel.innerHTML = ''
    for (const rel of releases) {
      verSel.append(h('option', { value: rel.tag }, rel.tag + (rel.prerelease ? ' · pre' : '') + (rel.published_at ? ' · ' + rel.published_at.slice(0, 10) : '')))
    }
    if (!releases.length) { status.textContent = '没有找到 Windows 版本'; return }
    fillAssets()
    status.textContent = refreshInstalled(toolSel.value)
  }

  verSel.addEventListener('change', fillAssets)
  const refresh = h('button', { class: 'btn btn-ghost btn-sm', onclick: () => loadReleases(true) }, '刷新版本')
  const dl = h('button', {
    class: 'btn btn-primary', onclick: async () => {
      if (!verSel.value || !assetSel.value) return toast('先选版本和文件', 'err')
      dl.disabled = true
      status.textContent = '正在准备下载…'
      const r = await api('/api/plugins/download', { method: 'POST', body: {
        tool: toolSel.value, tag: verSel.value, asset: assetSel.value, source: srcSel.value,
      } })
      if (!r.ok) { dl.disabled = false; return toast(r.error || '下载失败', 'err') }
      poll(r.job.id)
    },
  }, '下载')

  const poll = async (id) => {
    clearTimeout(jobTimer)
    const r = await api('/api/plugins/jobs/' + id)
    if (!r.ok) { dl.disabled = false; status.textContent = r.error || '任务不存在'; return }
    const j = r.job
    const pct = j.total ? Math.min(100, j.downloaded / j.total * 100) : 0
    bar.firstElementChild.style.width = pct.toFixed(1) + '%'
    bar.classList.toggle('done', j.status === 'done')
    const speed = j.speed ? fmtBytes(j.speed) + '/s' : ''
    status.textContent = j.status === 'done'
      ? `下载完成 · 来源 ${j.used_source}`
      : j.status === 'error' ? ('下载失败：' + j.error)
      : `下载中 ${pct.toFixed(1)}% · ${fmtBytes(j.downloaded)}${j.total ? ' / ' + fmtBytes(j.total) : ''} ${speed}`
    if (j.status === 'done') {
      dl.disabled = false
      pathLine.textContent = '文件：' + j.file
      const info = tools.find(x => x.id === toolSel.value)
      if (info) info.installed = info.installed || []
      toast('下载完成', 'ok')
      return
    }
    if (j.status === 'error' || j.status === 'cancelled') { dl.disabled = false; return }
    jobTimer = setTimeout(() => poll(id), 900)
  }

  loadReleases()
  renderInstalled()
  return h('div', { class: 'card plugin-download' },
    h('div', { class: 'card-title' }, 'QQ 底座 / 插件下载与切换',
      h('span', { class: 'tag ok' }, '国内源可切换')),
    h('div', { class: 'page-desc' }, 'SnowLuma / NapCatQQ 官方 Release；可自动测速或手动换源。文件只下载到 data/plugins/downloads，不自动执行。'),
    installedBox,
    h('div', { class: 'plugin-grid' },
      h('label', {}, '插件', toolSel),
      h('label', {}, '版本', verSel),
      h('label', { class: 'plugin-wide' }, '文件', assetSel),
      h('label', {}, '下载源', srcSel),
    ),
    h('div', { class: 'plugin-actions' }, refresh, dl, status),
    bar, pathLine,
    h('div', { class: 'hint' }, '自动测速会同时探测 GitHub 直连、ghproxy.net、gh-proxy.com、ghfast.top；哪个通用哪个。手动选择只走该源。'),
  )
}

async function renderGateway() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)

  wrap.append(pageHead('接入', 'QQ 连接', h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openGroupSettingsModal(['接入'], '接入参数') }, '接入参数')))
  wrap.append(await pluginDownloaderCard())

  const [st, impls] = await Promise.all([
    api('/api/gateway/status').catch(() => ({ enabled: false, accounts: [] })),
    api('/api/gateway/impls').catch(() => ({ impls: [] })),
  ])

  if (!st.enabled) {
    wrap.append(h('div', { class: 'notice' },
      icon('info'),
      h('div', {}, '接入总开关关着。到「设置 - 接入」把「启用接入」打开，再回来这里加账号。')))
    return
  }

  const accounts = st.accounts || []
  if (!accounts.length) {
    wrap.append(emptyState('还没有接任何账号',
      '点右上角「+ 加账号」新建；也可以在 config/local.toml 里写 [gateway.accounts]，首次启动会自动导入', 'chat'))
  } else {
    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '账号', h('span', { class: 'badge' }, String(accounts.length))),
      h('div', { class: 'table-wrap' },
        h('table', {},
          h('thead', {}, h('tr', {},
            h('th', {}, '名称'), h('th', {}, 'QQ'), h('th', {}, '状态'),
            h('th', { class: 'num' }, '收'), h('th', { class: 'num' }, '发'),
            h('th', { class: 'num' }, '最后消息'), h('th', {}, '操作'))),
          h('tbody', {}, ...accounts.map(a => h('tr', {},
            h('td', {}, h('div', { class: 'strong' }, a.name || a.id), h('div', { class: 'muted', style: 'font-size:10.5px;font-family:var(--font-mono)' }, a.id)),
            h('td', { class: 'num' }, a.bot_qq || '—'),
            h('td', {}, a.connected
              ? h('span', { class: 'tag ok' }, '已连接')
              : (!a.enabled ? h('span', { class: 'tag' }, '未启用') : (a.auto_start ? h('span', { class: 'tag warn' }, '重连中') : h('span', { class: 'tag' }, '未连接')))),
            h('td', { class: 'num' }, a.received),
            h('td', { class: 'num' }, a.sent),
            h('td', { class: 'num' }, fmtTime(a.last_message_at)),
            h('td', {}, h('button', { class: 'btn btn-ghost btn-sm', disabled: !a.configured || a.connected || null, onclick: async () => { const r = await api(`/api/accounts/${a.id}/connect`, { method: 'POST', body: {} }); if (!r.ok) return toast(r.error || '连接失败', 'err'); toast('正在连接…', 'ok'); go('gateway') } }, a.connected ? '已连接' : (a.configured ? '连接' : '未配置'))),
          )))))))
  }

  // 支持的实现
  if ((impls.impls || []).length) {
    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '支持的实现'),
      h('div', { class: 'table-wrap' },
        h('table', {},
          h('tbody', {}, ...impls.impls.map(i => h('tr', {},
            h('td', { class: 'strong' }, i.name),
            h('td', { class: 'muted', style: 'font-family:var(--font-mono);font-size:11.5px' }, i.id),
            h('td', { class: 'muted' }, i.profile.array_format ? 'array 格式' : 'CQ 码'),
          )))))))
  }
}

/* ============================================================
   设置
   ============================================================ */
let settingsCache = null

async function renderProactive() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)
  const [pro, vals, life, sched] = await Promise.all([
    api('/api/proactive/status' + accQuery()).catch(() => ({ ok: false, error: '生活接口没响应' })),
    api('/api/settings/values' + accQuery()).catch(() => ({ values: {} })),
    api('/api/life/status' + accQuery()).catch(() => ({ ok: false })),
    api('/api/life/schedule' + accQuery()).catch(() => ({ ok: false })),
  ])
  const v = vals.values || {}
  const acc = currentAccount
  const mine = (pro.accounts || []).find(x => x.id === acc) || (pro.accounts || [])[0] || {}
  const enabled = Boolean(v['proactive.enabled'])
  const candidates = mine.candidates || []
  const top = candidates[0]

  async function saveVal(key, value, reload = false) {
    const r = await api('/api/settings/set', { method: 'POST', body: { key, value, account_id: acc } }).catch(e => ({ ok: false, error: e.message }))
    if (!r.ok) return toast(r.error || '保存失败', 'err')
    toast('已保存', 'ok')
    if (reload) renderProactive()
  }
  const toggle = (key, label, desc, checked) => {
    const cb = h('input', { type: 'checkbox' }); cb.checked = Boolean(checked)
    cb.addEventListener('change', () => saveVal(key, cb.checked, true))
    return h('label', { class: 'perm-row' }, cb, h('span', {}, label), h('span', { class: 'muted', style: 'margin-left:auto;font-size:10px' }, desc))
  }
  const number = (key, label, desc, step = 1, unit = '') => {
    const input = h('input', { type: 'number', class: 'wide', step, value: v[key] ?? '', style: 'width:110px' })
    input.addEventListener('change', () => {
      const n = Number(input.value)
      if (input.value === '' || !Number.isFinite(n)) return toast('请输入数字', 'err')
      saveVal(key, n)
    })
    return h('div', { class: 'perm-row' }, h('span', {}, label), h('span', { class: 'muted', style: 'margin-left:auto;font-size:10px' }, desc),
      input, unit ? h('span', { class: 'muted' }, unit) : null)
  }

  wrap.append(pageHead('生活', `按作息、记忆和习惯主动私聊 · ${accountLabel()}`,
    h('button', { class: 'btn btn-ghost btn-sm', onclick: () => renderProactive() }, '刷新')))

  if (life && life.ok) {
    const now = life.now || {}
    const phaseNames = { sleep: '睡觉', wake: '起床', morning: '上午', lunch: '午饭', afternoon: '下午', evening: '晚饭', leisure: '自由时间', winddown: '睡前' }
    const energy = Math.round(Number(now.energy || 0) * 100)
    const nextText = life.next?.at
      ? new Date(Number(life.next.at) * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
      : ''
    wrap.append(h('div', { class: 'card life-card' },
      h('div', { class: 'card-title' }, '此刻生活', h('span', { class: 'tag ok' }, phaseNames[now.phase] || now.phase || '')),
      h('div', { class: 'life-now-activity' }, now.activity || '在过自己的时间'),
      h('div', { class: 'life-now-meta' },
        h('span', {}, `精力 ${energy}%`),
        h('span', {}, `心情 ${now.mood || '平静'}`),
        h('span', {}, nextText ? `下一个节点 ${nextText}` : '准备休息')),
      h('div', { class: 'life-energy' }, h('i', { style: `width:${Math.max(0, Math.min(100, energy))}%` }))))
    const phases = life.phases || []
    if (phases.length) {
      const timeline = h('div', { class: 'card' },
        h('div', { class: 'card-title' }, '今日作息', h('span', { class: 'badge' }, life.weekend ? '周末' : '工作日')))
      for (const p of phases) {
        timeline.append(h('div', { class: 'life-phase' + (p.key === now.phase ? ' current' : '') },
          h('span', { class: 'life-phase-time' }, `${p.start}–${p.end}`),
          h('span', { class: 'life-phase-activity' }, p.activity || ''),
          h('span', { class: 'life-phase-mood' }, p.mood || '')))
      }
      wrap.append(timeline)
    }
  }

  if (sched && sched.ok && Array.isArray(sched.days)) {
    const days = sched.days
    let selected = Number(sched.today ?? 0)
    if (!(selected >= 0 && selected < 7)) selected = 0
    const dayTabs = h('div', { class: 'life-day-tabs' })
    const editor = h('div', { class: 'life-schedule-editor' })
    const drawDay = () => {
      dayTabs.innerHTML = ''
      for (const d of days) {
        dayTabs.append(h('button', {
          class: 'btn btn-sm ' + (d.weekday === selected ? 'btn-primary' : 'btn-ghost'),
          onclick: () => { selected = d.weekday; drawDay() },
        }, d.name))
      }
      const day = days[selected]
      const slots = JSON.parse(JSON.stringify(day.slots || []))
      const mins = (v) => {
        const m = String(v || '').match(/^(\d{1,2}):(\d{2})$/)
        return m ? Number(m[1]) * 60 + Number(m[2]) : NaN
      }
      const clock = (n) => n >= 24 * 60 ? '24:00' : `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`
      editor.innerHTML = ''
      editor.append(h('div', { class: 'life-schedule-head' },
        h('span', {}, `${day.name} · ${day.custom ? '自定义' : '模板'}`),
        h('span', { class: 'muted' }, '时间从 00:00 到 24:00 首尾相接')))
      const rows = h('div', { class: 'life-schedule-rows' })
      const drawRows = () => {
        rows.innerHTML = ''
        slots.forEach((slot, idx) => {
          const start = h('input', { type: 'text', class: 'wide', value: slot.start || '00:00', placeholder: 'HH:MM', autocomplete: 'off' })
          start.addEventListener('change', () => { slot.start = start.value.trim() })
          const end = h('input', { type: 'text', class: 'wide', value: slot.end || '24:00', placeholder: 'HH:MM', autocomplete: 'off' })
          end.addEventListener('change', () => { slot.end = end.value.trim() })
          const activity = h('input', { type: 'text', class: 'wide', value: slot.activity || '', placeholder: '在做什么' })
          activity.addEventListener('change', () => { slot.activity = activity.value })
          const energy = h('input', { type: 'number', min: '0', max: '1', step: '0.05', value: slot.energy ?? 0.6 })
          energy.addEventListener('change', () => { slot.energy = Number(energy.value) })
          const mood = h('input', { type: 'text', value: slot.mood || '平静', placeholder: '心情' })
          mood.addEventListener('change', () => { slot.mood = mood.value })
          const canChat = h('input', { type: 'checkbox' })
          canChat.checked = slot.can_chat !== false
          canChat.addEventListener('change', () => { slot.can_chat = canChat.checked })
          rows.append(h('div', { class: 'life-schedule-row' },
            start, h('span', { class: 'life-schedule-dash' }, '–'), end,
            activity, energy, mood,
            h('label', { class: 'life-schedule-check' }, canChat, h('span', {}, '可聊')),
            h('button', { class: 'btn btn-ghost btn-sm', onclick: () => { slots.splice(idx, 1); drawRows() } }, '删')))
        })
      }
      drawRows()
      const add = h('button', { class: 'btn btn-ghost btn-sm', onclick: () => {
        const last = slots[slots.length - 1]
        if (!last) return
        const a = mins(last.start)
        const b = mins(last.end)
        if (!Number.isFinite(a) || !Number.isFinite(b) || b - a < 30) return toast('最后一段至少要 30 分钟', 'err')
        const mid = Math.floor((a + b) / 2 / 5) * 5
        last.end = clock(mid)
        slots.push({ start: clock(mid), end: clock(b), activity: '自由时间', energy: 0.6, mood: '平静', can_chat: true })
        drawRows()
      } }, '添加时段')
      const save = h('button', { class: 'btn btn-primary btn-sm', onclick: async () => {
        save.disabled = true
        const r = await api('/api/life/schedule', { method: 'POST', body: { account_id: acc, weekday: selected, slots } })
          .catch(e => ({ ok: false, error: e.message }))
        save.disabled = false
        if (!r.ok) return toast(r.error || '保存失败', 'err')
        toast('作息已保存', 'ok'); renderProactive()
      } }, '保存这一天')
      const reset = h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
        if (!confirm(`把${day.name}恢复成默认模板？`)) return
        const r = await api('/api/life/schedule', { method: 'POST', body: { account_id: acc, weekday: selected, reset: true } })
          .catch(e => ({ ok: false, error: e.message }))
        if (!r.ok) return toast(r.error || '重置失败', 'err')
        toast('已恢复模板', 'ok'); renderProactive()
      } }, '恢复模板')
      editor.append(rows, h('div', { class: 'life-schedule-actions' }, add, save, reset))
    }
    drawDay()
    wrap.append(h('div', { class: 'card life-schedule-card' },
      h('div', { class: 'card-title' }, '一周作息', h('span', { class: 'tag' }, '每天单独')),
      h('div', { class: 'hint' }, '按周一到周日分别安排；保存后今天的时间轴会按新安排重建。'),
      dayTabs, editor))
  }

  const stateCard = h('div', { class: 'card' },
    h('div', { class: 'card-title' }, '现在会不会主动找',
      h('span', { class: 'tag ' + (enabled ? 'ok' : '') }, enabled ? '开' : '关')),
    h('div', { class: 'proactive-head' },
      h('span', { class: 'proactive-meta' }, `今日已发 ${mine.today_sent ?? 0} 条`),
      h('span', { class: 'proactive-meta' }, pro.quiet_now ? '当前是安静时段' : '当前可发'),
      h('span', { class: 'proactive-meta' }, `候选 ${candidates.length} 人`)),
    h('div', { class: 'hint' }, '生活只走私聊；会参考作息、最近聊天、事实、没聊完的话题、沉默时长和回复习惯。'),
    top ? h('div', { class: 'proactive-cand' },
      h('b', {}, top.name || top.qq),
      h('span', { class: 'proactive-score' }, Number(top.score || 0).toFixed(2)),
      h('div', { class: 'proactive-reason' }, top.reason || '')) : h('div', { class: 'proactive-hint' }, '当前没有候选：可能还没聊够、沉默不够、今天到上限，或还在退避。'),
  )
  const previewBox = h('div', { class: 'proactive-preview' })
  const previewBtn = h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
    previewBtn.disabled = true; previewBox.textContent = '算一下…'
    try {
      const r = await api('/api/proactive/preview', { method: 'POST', body: { account_id: acc } })
      const d = (r.decisions || [])[0]
      if (!d) previewBox.textContent = '没有候选'
      else if (d.status === 'preview') previewBox.textContent = `【${d.name || d.qq}】→ ${d.message || ''}`
      else previewBox.textContent = `不发：${d.reason || d.status || ''}`
    } catch (e) { previewBox.textContent = '预览失败：' + e.message }
    finally { previewBtn.disabled = false }
  } }, '预览一条')
  stateCard.append(h('div', { class: 'proactive-actions' }, previewBtn, previewBox))
  wrap.append(stateCard)

  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'card-title' }, '开关'),
    toggle('proactive.enabled', '启用生活主动', '关着时完全不会主动发', enabled),
    toggle('proactive.owner_enabled', '也主动找主人', '主人的私聊也算候选', v['proactive.owner_enabled']),
    toggle('proactive.only_owner', '只找主人', '关闭其他人的候选', v['proactive.only_owner']),
    toggle('proactive.dry_run', '只预览不发送', '用于观察一段时间', v['proactive.dry_run']),
    h('div', { class: 'hint' }, '建议先开「只预览不发送」跑一天，看日志和候选，再真正发送。')))

  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'card-title' }, '频率'),
    number('proactive.owner_max_per_day', '主人每天最多', '单独给主人的上限', 1, '条/天'),
    number('proactive.max_per_day', '其他人每天最多', '每个普通联系人', 1, '条/天'),
    number('proactive.global_max_per_day', '全局每天最多', '所有联系人合计', 1, '条/天'),
    number('proactive.slot_morning', '上午 08–11 点', '每人这个时段最多主动几次', 1, '次'),
    number('proactive.slot_noon', '中午 11–14 点', '每人这个时段最多主动几次', 1, '次'),
    number('proactive.slot_afternoon', '下午 14–18 点', '每人这个时段最多主动几次', 1, '次'),
    number('proactive.slot_evening', '晚上 18–23 点', '每人这个时段最多主动几次', 1, '次'),
    number('proactive.min_silence_hours', '至少安静', '对方多久没说话才考虑', 0.5, '小时'),
    number('proactive.interval_hours', '回复后再找间隔', '对方回复后至少隔多久', 0.5, '小时'),
    number('proactive.wait_reply_hours', '多久没回算无视', '超过这个时间进入退避', 1, '小时'),
    number('proactive.active_guard_min', '刚聊过不打扰', '最后一次消息后保护时间', 1, '分钟')))

  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'card-title' }, '时间与门槛'),
    number('proactive.quiet_start', '安静开始', '23 = 晚上 11 点', 1, '点'),
    number('proactive.quiet_end', '安静结束', '8 = 早上 8 点', 1, '点'),
    number('proactive.min_messages', '至少聊过', '低于这个数不主动', 1, '条'),
    number('proactive.min_days', '认识至少', '新联系人先观察', 1, '天'),
    number('proactive.score_threshold', '候选分阈值', '越高越挑人', 0.05, '分'),
    number('proactive.max_chars', '单条长度', '主动消息最多多长', 1, '字'),
    h('div', { class: 'hint' }, '主动时段按账号时区：08–11、11–14、14–18、18–23；每个时段单独限额，23–08 安静。')))

  const logs = await api('/api/proactive/logs?limit=40').catch(() => ({ logs: [] }))
  const mineLogs = (logs.logs || []).filter(x => (x.account_id || '') === acc).slice(0, 15)
  const logCard = h('div', { class: 'card' }, h('div', { class: 'card-title' }, '最近主动记录', h('span', { class: 'badge' }, String(mineLogs.length))))
  if (!mineLogs.length) logCard.append(h('div', { class: 'muted' }, '还没有主动记录。'))
  for (const r of mineLogs) {
    logCard.append(h('div', { class: 'perm-chat-row' },
      h('div', { class: 'perm-chat-name' }, `${r.person_name || r.person_qq} · ${r.status}`,
        h('div', { class: 'muted', style: 'font-size:10px' }, new Date((r.sent_at || 0) * 1000).toLocaleString())),
      h('div', { class: 'muted', style: 'max-width:60%;font-size:11px' }, r.message || r.error || '')))
  }
  wrap.append(logCard)
}

async function renderBackup() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)
  let items = []
  const listBox = h('div', { class: 'wb-list' })
  const refresh = async () => {
    const r = await api('/api/system/backups').catch(() => ({ items: [] }))
    items = r.items || []
    countBadge.textContent = String(items.length)
    listBox.innerHTML = ''
    if (!items.length) listBox.append(h('div', { class: 'muted' }, '还没有备份。'))
    for (const it of items) {
      const row = h('div', { class: 'wb-row' },
        h('div', { class: 'wb-row-main' },
          h('div', { class: 'wb-row-title' }, it.name),
          h('div', { class: 'muted' }, `${fmtBytes(it.size)} · ${new Date((it.created_at || 0) * 1000).toLocaleString()}`)),
        h('div', { class: 'page-actions' },
          h('button', { class: 'btn btn-ghost btn-sm', onclick: () => download(it.name) }, '下载'),
          h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
            if (!confirm('删除这个备份？')) return
            const r = await api('/api/system/backups/' + encodeURIComponent(it.name), { method: 'DELETE' })
            if (!r.ok) return toast(r.error || '删除失败', 'err')
            toast('已删除', 'ok'); refresh()
          } }, '删除')))
      listBox.append(row)
    }
  }
  async function download(name) {
    const res = await fetch('/api/system/backups/' + encodeURIComponent(name), {
      headers: panelToken ? { 'X-Panel-Token': panelToken } : {},
    })
    if (!res.ok) return toast('下载失败', 'err')
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob); a.download = name; a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 3000)
  }
  const createBtn = h('button', { class: 'btn btn-primary btn-sm', onclick: async () => {
    createBtn.disabled = true
    try {
      const r = await api('/api/system/backups', { method: 'POST', body: {} }).catch(e => ({ ok: false, error: e.message }))
      if (!r.ok) return toast(r.error || '备份失败', 'err')
      toast('备份完成：' + r.name, 'ok'); refresh()
    } finally { createBtn.disabled = false }
  } }, '立即备份')
  const upload = h('input', { type: 'file', accept: '.zip,application/zip', style: 'display:none' })
  upload.addEventListener('change', async () => {
    const f = upload.files?.[0]; if (!f) return
    if (!confirm('导入备份？会先解到暂存区，重启 Novabot 后替换当前数据。')) { upload.value = ''; return }
    const fd = new FormData(); fd.append('file', f)
    const res = await fetch('/api/system/backups/restore', {
      method: 'POST', headers: panelToken ? { 'X-Panel-Token': panelToken } : {}, body: fd,
    })
    const r = await res.json().catch(() => ({ ok: false, error: '上传失败' }))
    upload.value = ''
    if (!r.ok) return toast(r.error || '恢复失败', 'err')
    toast('已暂存；重启 Novabot 后生效', 'ok')
  })
  const countBadge = h('span', { class: 'badge' }, String(items.length))
  wrap.append(pageHead('备份与恢复', '配置、记忆、人格、表情、权限和会话数据打成一个包',
    createBtn, h('button', { class: 'btn btn-ghost btn-sm', onclick: () => upload.click() }, '导入备份'), upload))
  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'card-title' }, '备份文件', countBadge),
    h('div', { class: 'hint' }, '备份放在 data/backups。导入只暂存，重启后才替换；替换前会自动留一份 pre-restore 备份。'),
    listBox))
  await refresh()
}

async function openSetupWizard() {
  const [safety, vals, gw] = await Promise.all([
    api('/api/system/safety').catch(() => ({})),
    api('/api/settings/values').catch(() => ({ values: {} })),
    api('/api/gateway/status').catch(() => ({ accounts: [] })),
  ])
  const v = vals.values || {}
  const mask = h('div', { class: 'modal-mask' })
  const owner = h('input', { type: 'text', class: 'wide', value: v['security.owner_qq'] || '', placeholder: '你的 QQ 号' })
  const dry = h('input', { type: 'checkbox' }); dry.checked = safety.dry_run !== false
  const master = h('input', { type: 'checkbox' }); master.checked = safety.master_enabled !== false
  const close = () => { mask.classList.remove('show'); setTimeout(() => mask.remove(), 160) }
  const modal = h('div', { class: 'modal', style: 'width:min(600px,92vw)' },
    h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, '首次设置'), h('button', { class: 'btn btn-ghost btn-sm', onclick: close }, '稍后')),
    h('div', { class: 'modal-body', style: 'max-height:65vh;overflow:auto' },
      h('div', { class: 'card-title' }, '1. 主人'),
      h('div', { class: 'persona-field' }, h('label', {}, '主人 QQ'), owner),
      h('div', { class: 'hint' }, '主人拥有完整权限；群聊权限仍按账号权限表。'),
      h('div', { class: 'card-title', style: 'margin-top:16px' }, '2. 安全'),
      h('label', { class: 'perm-row' }, master, h('span', {}, '总开关'), h('span', { class: 'muted', style: 'margin-left:auto' }, '关闭后不向 QQ 发任何消息')),
      h('label', { class: 'perm-row' }, dry, h('span', {}, '先试运行'), h('span', { class: 'muted', style: 'margin-left:auto' }, '只写日志，不真实发送')),
      h('div', { class: 'card-title', style: 'margin-top:16px' }, '3. 接入与模型'),
      h('div', { class: 'muted' }, `当前账号 ${(gw.accounts || []).length} 个；模型由设置 → 模型页检测。`),
      h('div', { class: 'hint' }, '接入页填 QQ/OneBot，模型页检测 DSH；都可以稍后再补。'),
      h('div', { class: 'proactive-actions', style: 'margin-top:20px' },
        h('button', { class: 'btn btn-primary', onclick: async () => {
          const set = async (key, value) => api('/api/settings/set', { method: 'POST', body: { key, value, account_id: '' } })
          const r1 = await api('/api/system/owner', { method: 'POST', body: { qq: owner.value.trim() } }).catch(e => ({ ok: false, error: e.message }))
          const r2 = await set('runtime.master_enabled', master.checked)
          const r3 = await set('runtime.dry_run', dry.checked)
          const r4 = await set('runtime.setup_done', true)
          if (![r1, r2, r3, r4].every(x => x && x.ok)) return toast('保存失败', 'err')
          close(); toast('设置已保存', 'ok'); go('overview')
        } }, '完成'),
        h('button', { class: 'btn btn-ghost', onclick: async () => {
          await api('/api/settings/set', { method: 'POST', body: { key: 'runtime.setup_done', value: true, account_id: '' } }).catch(() => null)
          close()
        } }, '以后再说'))))
  mask.append(modal)
  mask.addEventListener('click', e => { if (e.target === mask) close() })
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))
}

async function renderSettings() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)

  wrap.append(pageHead('设置', `改了立刻生效，标 ⚠ 的要重启 · ${accountLabel()}`, h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => { const r = await api('/api/system/model/detect', { method: 'POST', body: {} }).catch(e => ({ ok: false, error: e.message })); if (!r.ok) return toast(r.error || '没有检测到可用模型', 'err'); toast('已接入 ' + (r.endpoint || 'Agent'), 'ok'); go('settings') } }, '检测模型'), h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openGroupSettingsModal(null, '全部参数') }, '全部参数')))

  const search = h('input', {
    type: 'text', class: 'wide', placeholder: '搜索参数（名称 / 说明 / key）',
  })
  search.addEventListener('input', () => filterSettings(search.value))
  wrap.append(h('div', { class: 'card settings-search' }, search))

  // 主题切换
  const THEMES = [
    { id: 'light',    name: '纸白', color: 'linear-gradient(135deg,#f6f4ef 0 46%,#4f6bed 54% 100%)' },
    { id: 'mist',     name: '晨雾', color: 'linear-gradient(135deg,#f2f5f8 0 46%,#0f9d8f 54% 100%)' },
    { id: 'rose',     name: '藕荷', color: 'linear-gradient(135deg,#fbf4f6 0 46%,#b35c86 54% 100%)' },
    { id: 'dark',     name: '石墨', color: 'linear-gradient(135deg,#17161a 0 46%,#7c9cff 54% 100%)' },
    { id: 'midnight', name: '深蓝', color: 'linear-gradient(135deg,#10182b 0 46%,#5b9cff 54% 100%)' },
    { id: 'forest',   name: '墨绿', color: 'linear-gradient(135deg,#131b16 0 46%,#5cc08a 54% 100%)' },
  ]
  const cur = document.documentElement.dataset.theme || 'light'
  const themeRow = h('div', { class: 'theme-row' },
    ...THEMES.map(t => h('button', {
      class: 'theme-chip' + (t.id === cur ? ' on' : ''),
      'data-theme-id': t.id,
      onclick: () => setTheme(t.id),
    },
      h('span', { class: 'theme-swatch', style: `background:${t.color}` }),
      h('span', {}, t.name))))
  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'card-title' }, '外观'),
    themeRow))

  const safety = await api('/api/system/safety').catch(() => ({}))
  const setGlobal = async (key, value) => {
    const r = await api('/api/settings/set', { method: 'POST', body: { key, value, account_id: '' } })
      .catch(e => ({ ok: false, error: e.message }))
    if (!r.ok) return toast(r.error || '保存失败', 'err')
    toast('已保存', 'ok')
  }
  const safetySwitch = (key, label, desc, on) => {
    const cb = h('input', { type: 'checkbox' }); cb.checked = Boolean(on)
    cb.addEventListener('change', async () => {
      await setGlobal(key, cb.checked)
      renderSettings()
    })
    return h('label', { class: 'perm-row' }, cb, h('span', {}, label),
      h('span', { class: 'muted', style: 'margin-left:auto;font-size:10px' }, desc))
  }
  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'card-title' }, '运行安全',
      h('span', { class: 'tag ' + (safety.master_enabled ? 'ok' : 'warn') }, safety.master_enabled ? '总开关开' : '总开关关'),
      safety.dry_run ? h('span', { class: 'tag warn' }, '试运行') : null),
    h('div', { class: 'perm-row' },
      h('span', { style: 'min-width:90px' }, '主人 QQ'),
      (() => {
        const inp = h('input', { type: 'text', class: 'wide', value: safety.owner_qq || '', style: 'max-width:220px' })
        const btn = h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
          const r = await api('/api/system/owner', { method: 'POST', body: { qq: inp.value.trim() } }).catch(e => ({ ok: false, error: e.message }))
          if (!r.ok) return toast(r.error || '保存失败', 'err')
          toast('主人账号已更新', 'ok'); renderSettings()
        } }, '保存')
        return h('div', { style: 'display:flex;gap:8px;align-items:center;margin-left:auto' }, inp, btn)
      })()),
    h('div', { class: 'hint' }, '主人 QQ 是全局身份：改一次会同步到三个 bot、权限和人物归属。'),
    safetySwitch('runtime.master_enabled', '总开关', '关闭后不向 QQ 发送任何消息，面板和连接仍可用', safety.master_enabled),
    safetySwitch('runtime.dry_run', '全局试运行', '只写日志，不真正发送', safety.dry_run),
    h('div', { class: 'proactive-actions' },
      h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openSetupWizard() }, '重新走设置向导'),
      h('button', { class: 'btn btn-ghost btn-sm', onclick: () => go('backup') }, '备份与恢复'))))

  const [schema, valuesResp] = await Promise.all([
    api('/api/settings/schema'),
    api('/api/settings/values' + accQuery()),
  ])
  values = valuesResp.values || {}
  settingsCache = { schema, values }
  const CONTEXT_GROUPS = new Set(['接入', '平台', '回复', '表情', '真实', '媒体', '文件', '隐私', '聊天', '模块', '人格', '记忆', '语音', '输出', '生活'])
  const visibleGroups = schema.groups.filter(g => !CONTEXT_GROUPS.has(g.name))

  const tabs = h('div', { class: 'settings-tabs' })
  const cards = {}
  let selected = ''
  try { selected = localStorage.getItem('novabot-settings-group') || '' } catch {}
  if (!visibleGroups.some(g => g.name === selected)) selected = visibleGroups[0]?.name || ''

  const showGroup = (name) => {
    selected = name
    try { localStorage.setItem('novabot-settings-group', name) } catch {}
    for (const chip of tabs.querySelectorAll('.settings-tab')) {
      chip.classList.toggle('on', chip.dataset.group === name)
    }
    filterSettings(search.value)
  }

  for (const g of visibleGroups) {
    const chip = h('button', {
      class: 'settings-tab' + (g.name === selected ? ' on' : ''),
      'data-group': g.name,
      onclick: () => { search.value = ''; showGroup(g.name) },
    }, g.name)
    tabs.append(chip)

    const card = h('div', { class: 'card settings-group' },
      h('div', { class: 'card-title' }, g.name,
        h('span', { class: 'badge' }, `${g.basic_count} 基础 · ${g.advanced_count} 高级`)),
      ...renderGroupBody(g, values))
    card.dataset.group = g.name
    cards[g.name] = card
    wrap.append(card)
  }
  wrap.insertBefore(tabs, wrap.children[2] || null)
  window.__settingsShowGroup = showGroup
  window.__settingsSelected = () => selected
  showGroup(selected)
}

function renderGroupBody(g, values) {
  const basic = g.params.filter(p => p.level === 'basic')
  const adv = g.params.filter(p => p.level === 'advanced')
  const out = []

  for (const p of basic) out.push(renderField(p, values))

  if (adv.length) {
    const head = h('div', { class: 'collapse-head' },
      icon('chevron'),
      h('span', {}, `高级选项（${adv.length} 项）`))
    const body = h('div', { class: 'collapse-body' },
      ...adv.map(p => renderField(p, values)))
    head.addEventListener('click', () => {
      head.classList.toggle('open')
      body.classList.toggle('open')
    })
    out.push(head, body)
  }
  return out
}

function renderField(p, values) {
  const val = values[p.key]
  let ctl

  if (p.kind === 'bool') {
    const sw = h('div', { class: 'switch' + (val ? ' on' : '') })
    sw.addEventListener('click', () => {
      const nv = !sw.classList.contains('on')
      sw.classList.toggle('on', nv)
      save(p.key, nv)
    })
    ctl = sw
  } else if (p.kind === 'enum') {
    const sel = h('select', {})
    for (const [v, label] of (p.options || [])) {
      sel.append(h('option', { value: v, selected: v === val || null }, label))
    }
    sel.addEventListener('change', () => save(p.key, sel.value))
    ctl = sel
  } else if (p.kind === 'int' || p.kind === 'float') {
    const inp = h('input', {
      type: 'number',
      value: val ?? p.default,
      min: p.min ?? null,
      max: p.max ?? null,
      step: p.step ?? (p.kind === 'int' ? 1 : 0.01),
    })
    inp.addEventListener('change', () => save(p.key, Number(inp.value)))
    ctl = inp
  } else if (p.kind === 'list') {
    const tags = (Array.isArray(val) ? val : String(val || '').split(/[,，;；\s]+/))
      .map(x => String(x).trim()).filter(Boolean)
    const summary = h('div', { class: 'list-editor-summary' })
    const scroll = h('div', { class: 'list-editor-scroll' })
    const count = h('span', { class: 'list-editor-count' }, '')
    const toggle = h('button', { type: 'button', class: 'btn btn-ghost btn-sm list-editor-toggle' }, '展开')
    const body = h('div', { class: 'list-editor-body' })
    const inp = h('input', {
      type: 'text',
      placeholder: p.key.includes('whitelist') ? '输入 QQ / 群号，回车添加' : (p.key.includes('aliases') ? '输入别名，回车添加' : '输入后回车添加'),
      autocomplete: 'off',
    })
    const addBtn = h('button', { type: 'button', class: 'btn btn-ghost btn-sm' }, '添加')
    let expanded = false

    const sync = () => save(p.key, [...tags])
    const draw = () => {
      count.textContent = `${tags.length} 项`
      toggle.textContent = expanded ? '收起' : (tags.length ? '展开' : '添加')
      body.hidden = !expanded
      summary.hidden = expanded
      summary.innerHTML = ''
      if (!tags.length) {
        summary.append(h('span', { class: 'list-editor-empty' }, '还没有配置'))
      } else {
        for (const x of tags.slice(0, 4)) summary.append(h('span', { class: 'tag' }, x))
        if (tags.length > 4) summary.append(h('span', { class: 'tag' }, `+${tags.length - 4}`))
      }
      scroll.innerHTML = ''
      for (let i = 0; i < tags.length; i += 1) {
        scroll.append(h('div', { class: 'list-editor-row' },
          h('span', { class: 'list-editor-value', title: tags[i] }, tags[i]),
          h('button', {
            type: 'button', class: 'tag-chip-x', title: '移除',
            onclick: () => { tags.splice(i, 1); draw(); sync() },
          }, '×')))
      }
      if (!tags.length) scroll.append(h('div', { class: 'list-editor-empty' }, '列表为空'))
    }
    const add = () => {
      const raw = inp.value.trim()
      if (!raw) return
      for (const x of raw.split(/[,，;；\s]+/)) {
        const v = x.trim()
        if (v && !tags.includes(v)) tags.push(v)
      }
      inp.value = ''
      draw(); sync()
      if (!expanded) { expanded = true; draw() }
      inp.focus()
    }
    const setExpanded = (next) => {
      expanded = next
      draw()
      if (expanded) inp.focus()
    }
    toggle.addEventListener('click', () => setExpanded(!expanded))
    addBtn.addEventListener('click', add)
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',' || e.key === '，') { e.preventDefault(); add() }
      else if (e.key === 'Backspace' && !inp.value && tags.length) { tags.pop(); draw(); sync() }
    })
    body.append(scroll, h('div', { class: 'list-editor-add' }, inp, addBtn))
    draw()
    ctl = h('div', { class: 'list-editor' },
      h('div', { class: 'list-editor-head' }, count, toggle),
      summary, body)
  } else {
    const inp = h('input', {
      type: 'text',
      class: 'wide',
      value: val ?? '',
      placeholder: p.default != null ? String(p.default) : '',
    })
    inp.addEventListener('change', () => save(p.key, inp.value))
    ctl = inp
  }

  const field = h('div', { class: 'field' },
    h('div', { class: 'field-info' },
      h('div', { class: 'field-name' },
        p.label,
        p.restart ? h('span', { class: 'tag warn' }, '⚠ 重启生效') : null),
      p.desc ? h('div', { class: 'field-desc' }, p.desc) : null),
    h('div', { class: 'field-control' },
      ctl,
      p.unit ? h('span', { class: 'muted', style: 'font-size:11.5px;color:var(--text-tertiary)' }, p.unit) : null))
  if (p.kind === 'list') field.classList.add('field-list')
  field.dataset.search = `${p.key} ${p.label} ${p.desc || ''}`.toLowerCase()
  return field
}

function filterSettings(q) {
  q = (q || '').trim().toLowerCase()
  const selected = window.__settingsSelected ? window.__settingsSelected() : ''
  for (const card of $$('.settings-group')) {
    const groupHit = (card.dataset.group || '').toLowerCase().includes(q)
    const selectedCard = card.dataset.group === selected
    let visible = 0
    for (const f of card.querySelectorAll('.field')) {
      const hit = !q || groupHit || (f.dataset.search || '').includes(q)
      f.style.display = hit ? '' : 'none'
      if (hit) visible += 1
    }
    card.style.display = (!q && !selectedCard) ? 'none' : ((!q || visible) ? '' : 'none')
    if (q) {
      for (const head of card.querySelectorAll('.collapse-head')) {
        head.classList.add('open')
        const body = head.nextElementSibling
        if (body && body.classList.contains('collapse-body')) body.classList.add('open')
      }
    }
  }
}

function setTheme(id) {
  document.documentElement.dataset.theme = id
  try { localStorage.setItem('novabot-theme', id) } catch {}
  $$('.theme-chip').forEach(c => c.classList.toggle('on', c.dataset.themeId === id))
  toast('主题已切换', 'ok')
}

async function save(key, value) {
  try {
    const r = await api('/api/settings/set', { method: 'POST', body: { key, value, account_id: currentAccount } })
    if (!r.ok) return toast(r.error || '没存上', 'err')
    if (settingsCache) settingsCache.values[key] = r.value
    toast('已保存', 'ok')
  } catch (e) {
    toast('失败：' + e.message, 'err')
  }
}

/* ============================================================
   语音
   ============================================================ */

let voiceAudio = null
function ensureVoiceAudio() {
  if (!voiceAudio) voiceAudio = h('audio', { controls: true, style: 'width:100%;margin-top:10px' })
  return voiceAudio
}

async function voicePlayPreview(payload, label, metaBox) {
  const headers = { 'Content-Type': 'application/json' }
  if (panelToken) headers['X-Panel-Token'] = panelToken
  const r = await fetch('/api/voice/preview', {
    method: 'POST', headers, body: JSON.stringify(payload),
  })
  if (r.status === 401) { showLogin(); return }
  if (!r.ok) {
    let msg = `HTTP ${r.status}`
    try { msg = (await r.json()).error || msg } catch {}
    toast(msg, 'err')
    return
  }
  const decoded = (name) => {
    const v = r.headers.get(name) || ''
    try { return decodeURIComponent(v) } catch { return v }
  }
  const blob = await r.blob()
  const audio = ensureVoiceAudio()
  if (audio.dataset.url) URL.revokeObjectURL(audio.dataset.url)
  const url = URL.createObjectURL(blob)
  audio.dataset.url = url
  audio.src = url
  await audio.play().catch(() => {})
  if (metaBox) {
    metaBox.textContent = `${label || '试听'} · 参考音 ${decoded('X-Voice-Ref') || '—'} · 引擎 ${decoded('X-Voice-Engine') || '—'} · ${decoded('X-Voice-Duration') || '?'}s · ${decoded('X-Voice-Reason') || ''}`
  }
}

function openEngineModal(engine, types) {
  const isNew = !engine
  const e = engine || {}
  const mask = h('div', { class: 'modal-mask' })
  const id = h('input', { type: 'text', class: 'wide', value: e.id || '', placeholder: '小写 id，如 fish' })
  const name = h('input', { type: 'text', class: 'wide', value: e.name || '', placeholder: '显示名' })
  const type = h('select', {})
  for (const t of types) type.append(h('option', { value: t.id, selected: (e.type || 'gptsovits') === t.id || null }, t.name))
  const url = h('input', { type: 'text', class: 'wide', value: e.url || '', placeholder: 'http://127.0.0.1:8000' })
  const key = h('input', { type: 'password', class: 'wide', value: '', placeholder: e.api_key_set ? '已设置，留空不改' : '没有就留空' })
  const model = h('input', { type: 'text', class: 'wide', value: e.model || '', placeholder: '模型名（OpenAI 兼容用）' })
  const voice = h('input', { type: 'text', class: 'wide', value: e.voice || '', placeholder: '音色/voice（OpenAI 兼容用）' })
  const timeout = h('input', { type: 'number', class: 'wide', value: e.timeout || 120, min: 5, max: 600 })
  const defaults = h('textarea', { style: 'min-height:64px', placeholder: 'JSON，如 {"top_k":5,"seed":-1}' })
  defaults.value = JSON.stringify(e.defaults || {}, null, 2)
  const enabled = h('div', { class: 'switch' + (e.enabled !== false ? ' on' : '') })
  enabled.addEventListener('click', () => enabled.classList.toggle('on'))

  const body = h('div', { class: 'modal-body' },
    h('div', { class: 'form-grid' },
      h('div', { class: 'form-row' }, h('label', {}, 'id'), id),
      h('div', { class: 'form-row' }, h('label', {}, '显示名'), name)),
    h('div', { class: 'form-row' }, h('label', {}, '类型'), type),
    h('div', { class: 'form-row' }, h('label', {}, '服务地址'), url),
    h('div', { class: 'form-grid' },
      h('div', { class: 'form-row' }, h('label', {}, 'API Key'), key),
      h('div', { class: 'form-row' }, h('label', {}, '超时（秒）'), timeout)),
    h('div', { class: 'form-grid' },
      h('div', { class: 'form-row' }, h('label', {}, '模型'), model),
      h('div', { class: 'form-row' }, h('label', {}, '音色'), voice)),
    h('div', { class: 'form-row' }, h('label', {}, '默认参数 JSON'), defaults),
    h('div', { class: 'hint' },
      '自定义 HTTP：defaults 里写 {"method":"POST","body":"{\\"text\\":\\"{text}\\"}"}；OpenAI 兼容填模型/音色。'),
    h('div', { class: 'form-row' }, h('label', { style: 'display:flex;align-items:center;gap:8px' }, enabled, '启用')),
  )
  const modal = h('div', { class: 'modal' },
    h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, isNew ? '新增语音引擎' : '编辑语音引擎')),
    body,
    h('div', { class: 'modal-foot' },
      h('button', { class: 'btn btn-ghost', onclick: () => close() }, '取消'),
      h('button', {
        class: 'btn btn-primary',
        onclick: async () => {
          let parsed = {}
          try { parsed = JSON.parse(defaults.value || '{}') } catch { return toast('默认参数不是合法 JSON', 'err') }
          const payload = {
            id: id.value.trim() || ('eng-' + Date.now().toString(36)),
            name: name.value.trim(), type: type.value, url: url.value.trim(),
            api_key: key.value, model: model.value.trim(), voice: voice.value.trim(),
            timeout: Number(timeout.value) || 120, defaults: parsed,
            enabled: enabled.classList.contains('on'),
          }
          try {
            const r = await api('/api/voice/engines', { method: 'POST', body: payload })
            if (!r.ok) return toast(r.error || '保存失败', 'err')
            close(); toast('已保存', 'ok'); go('voice')
          } catch (err) { toast('保存失败：' + err.message, 'err') }
        },
      }, '保存')),
  )
  mask.append(modal)
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))
  function close() {
    mask.classList.remove('show')
    setTimeout(() => mask.remove(), 180)
  }
  mask.addEventListener('click', ev => { if (ev.target === mask) close() })
}

function openRefModal(ref, engines) {
  const isNew = !ref
  const r0 = ref || {}
  const mask = h('div', { class: 'modal-mask' })
  const id = h('input', { type: 'text', class: 'wide', value: r0.id || '', placeholder: 'ref 唯一 id' })
  const name = h('input', { type: 'text', class: 'wide', value: r0.name || '', placeholder: '平静陈述 / 短促 / 冷淡' })
  const engine = h('select', {})
  for (const e of engines) {
    const label = (e.name && e.name !== e.id) ? e.name : ((engines.find(x => x.id === e.type) || {}).name || e.type || e.id)
    engine.append(h('option', { value: e.id, selected: (r0.engine || '') === e.id || null }, label))
  }
  if (!engines.length) engine.append(h('option', { value: '' }, '先加一个引擎'))
  const audio = h('input', { type: 'text', class: 'wide', value: r0.audio_path || '', placeholder: 'C:\\path\\to\\ref.wav' })
  const prompt = h('input', { type: 'text', class: 'wide', value: r0.prompt_text || '', placeholder: '参考音频里说的那句话' })
  const minChars = h('input', { type: 'number', value: r0.min_chars ?? 0, min: 0, max: 9999 })
  const maxChars = h('input', { type: 'number', value: r0.max_chars ?? 9999, min: 0, max: 9999 })
  const weight = h('input', { type: 'number', value: r0.weight ?? 1, min: 0, max: 10, step: 0.1 })
  const emotions = h('input', { type: 'text', class: 'wide', value: (r0.emotions || []).join(', '), placeholder: 'joy, anger, sadness（可空）' })
  const params = h('textarea', { style: 'min-height:56px', placeholder: 'JSON，如 {"seed":-1,"top_k":5,"speed":1.05}' })
  params.value = JSON.stringify(r0.params || {}, null, 2)
  const condition = h('textarea', { style: 'min-height:56px', placeholder: 'JSON 条件，如 {"state.mood":"<-0.3"}；可空' })
  condition.value = r0.condition ? JSON.stringify(r0.condition, null, 2) : ''
  const enabled = h('div', { class: 'switch' + (r0.enabled !== false ? ' on' : '') })
  enabled.addEventListener('click', () => enabled.classList.toggle('on'))

  const body = h('div', { class: 'modal-body' },
    h('div', { class: 'form-grid' },
      h('div', { class: 'form-row' }, h('label', {}, 'id'), id),
      h('div', { class: 'form-row' }, h('label', {}, '显示名'), name)),
    h('div', { class: 'form-row' }, h('label', {}, '引擎'), engine),
    h('div', { class: 'form-row' }, h('label', {}, '参考音频路径'), audio),
    h('div', { class: 'form-row' }, h('label', {}, '参考文本'), prompt),
    h('div', { class: 'form-grid form-grid-3' },
      h('div', { class: 'form-row' }, h('label', {}, '最短字数'), minChars),
      h('div', { class: 'form-row' }, h('label', {}, '最长字数'), maxChars),
      h('div', { class: 'form-row' }, h('label', {}, '权重'), weight)),
    h('div', { class: 'form-row' }, h('label', {}, '情绪标签'), emotions),
    h('div', { class: 'form-row' }, h('label', {}, '合成参数 JSON'), params),
    h('div', { class: 'hint' }, '参数按引擎能力自动过滤；不同引擎不支持的字段会被丢掉，不会报错。'),
    h('div', { class: 'form-row' }, h('label', {}, '选择条件 JSON'), condition),
    h('div', { class: 'form-row' }, h('label', { style: 'display:flex;align-items:center;gap:8px' }, enabled, '启用')),
  )
  const modal = h('div', { class: 'modal' },
    h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, isNew ? '新增参考音' : '编辑参考音')),
    body,
    h('div', { class: 'modal-foot' },
      h('button', { class: 'btn btn-ghost', onclick: () => close() }, '取消'),
      h('button', {
        class: 'btn btn-primary',
        onclick: async () => {
          let p = {}, c = null
          try { p = JSON.parse(params.value || '{}') } catch { return toast('合成参数不是合法 JSON', 'err') }
          if (condition.value.trim()) {
            try { c = JSON.parse(condition.value) } catch { return toast('选择条件不是合法 JSON', 'err') }
          }
          if (!audio.value.trim()) return toast('参考音频路径不能为空', 'err')
          const payload = {
            id: id.value.trim() || ('ref-' + Date.now().toString(36)), account_id: currentAccount,
            name: name.value.trim() || id.value.trim(),
            engine: engine.value, audio_path: audio.value.trim(),
            prompt_text: prompt.value.trim(),
            min_chars: Number(minChars.value) || 0,
            max_chars: Number(maxChars.value) || 9999,
            weight: Number(weight.value) || 1,
            emotions: emotions.value.split(',').map(x => x.trim()).filter(Boolean),
            params: p, condition: c,
            enabled: enabled.classList.contains('on'),
          }
          try {
            const r = await api('/api/voice/refs', { method: 'POST', body: payload })
            if (!r.ok) return toast(r.error || '保存失败', 'err')
            close(); toast('已保存', 'ok'); go('voice')
          } catch (err) { toast('保存失败：' + err.message, 'err') }
        },
      }, '保存')),
  )
  mask.append(modal)
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))
  function close() {
    mask.classList.remove('show')
    setTimeout(() => mask.remove(), 180)
  }
  mask.addEventListener('click', ev => { if (ev.target === mask) close() })
}

async function renderVoice() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)
  wrap.append(pageHead('语音', '引擎、参考音和调试台 · 试听不会发到 QQ', h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openGroupSettingsModal(['语音'], '语音参数') }, '语音参数')))

  const vq = currentAccount ? ('?account_id=' + encodeURIComponent(currentAccount)) : ''
  const [status, eng, refs] = await Promise.all([
    api('/api/voice/status' + vq).catch(() => ({})),
    api('/api/voice/engines' + vq).catch(() => ({ engines: [], types: [] })),
    api('/api/voice/refs' + vq).catch(() => ({ refs: [] })),
  ])
  const engines = eng.engines || []
  const types = eng.types || []
  const refList = refs.refs || []
  const audio = ensureVoiceAudio()
  const meta = h('div', { class: 'muted', style: 'font-size:12px;margin-top:6px' }, '')

  wrap.append(h('div', { class: 'stat-grid' },
    stat('语音', status.enabled ? '开' : '关'),
    stat('引擎', engines.length),
    stat('参考音', refList.length),
    stat('最近合成', (status.logs || []).length),
  ))

  const rateInput = h('input', { type: 'number', min: '0', max: '100', step: '1', value: String(Number(status.convert_rate ?? 100)), style: 'width:90px' })
  const rateSave = async () => {
    const value = Math.max(0, Math.min(100, Number(rateInput.value) || 0))
    rateInput.value = String(value)
    const r = await api('/api/settings/set', { method: 'POST', body: { key: 'voice.convert_rate', value, account_id: currentAccount } })
    if (!r.ok) return toast(r.error || '保存失败', 'err')
    toast('概率已保存：' + value + '%', 'ok')
  }
  rateInput.addEventListener('change', rateSave)
  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'card-title' }, '发语音概率'),
    h('div', { class: 'perm-row' }, h('span', {}, '满足触发条件后，这条回复转语音的概率'), rateInput, h('span', {}, '%')),
    h('div', { class: 'hint' }, '满足条件时转成语音的概率；0 = 不转，100 = 全部转。')))

  const engBody = h('div', { class: 'wb-list' })
  const addEng = h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openEngineModal(null, types) }, '加引擎')
  for (const e of engines) {
    const typeName = (types.find(t => t.id === e.type) || {}).name || e.type
    const displayName = (e.name && e.name !== e.id) ? e.name : typeName
    engBody.append(h('div', { class: 'wb-row voice-row' },
      h('div', { class: 'wb-row-main' },
        h('div', { class: 'wb-row-title' }, displayName,
          h('span', { class: 'tag ' + (e.enabled ? 'ok' : '') }, e.enabled ? '启用' : '停用')),
        h('div', { class: 'wb-row-sub' }, `${typeName} · ${e.url || '未填地址'}`)),
      h('div', { class: 'wb-row-side' },
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: async () => {
            const r = await api(`/api/voice/engines/${e.id}/health${vq}`, { method: 'POST', body: {} })
            toast(r.ok ? (r.healthy ? '连接正常' : '连不上') : (r.error || '测试失败'), r.healthy ? 'ok' : 'err')
          },
        }, '测试'),
        h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openEngineModal(e, types) }, '编辑'),
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: async () => {
            if (!confirm(`删除引擎「${e.name || e.id}」？`)) return
            const r = await api(`/api/voice/engines/${e.id}${vq}`, { method: 'DELETE' })
            if (!r.ok) return toast(r.error || '删除失败', 'err')
            toast('已删除', 'ok'); go('voice')
          },
        }, '删除'),
      )))
  }
  if (!engines.length) engBody.append(h('div', { class: 'wb-empty' }, '还没有引擎。加一个 GPT-SoVITS / CosyVoice / OpenAI 兼容 / 自定义 HTTP。'))
  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'wb-card-head' }, h('div', { class: 'card-title' }, '引擎'), addEng),
    engBody))

  const refBody = h('div', { class: 'wb-list' })
  const addRef = h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openRefModal(null, engines) }, '加参考音')
  for (const r of refList) {
    refBody.append(h('div', { class: 'wb-row voice-row' },
      h('div', { class: 'wb-row-main' },
        h('div', { class: 'wb-row-title' }, r.name || r.id,
          r.enabled ? h('span', { class: 'tag ok' }, '启用') : h('span', { class: 'tag' }, '停用')),
        h('div', { class: 'wb-row-sub' },
          `${(engines.find(x => x.id === r.engine) || {}).name || r.engine || '默认'} · ${r.min_chars}-${r.max_chars} 字 · 权重 ${r.weight}` +
          ((r.emotions || []).length ? ` · ${r.emotions.join('/')}` : '')),
        h('div', { class: 'wb-row-sub voice-path' }, r.audio_path || '')),
      h('div', { class: 'wb-row-side' },
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: () => voicePlayPreview({ text: '你好，这是一段语音试听。', ref_id: r.id, chat_id: 'preview', account_id: currentAccount },
            r.name || r.id, meta),
        }, '试听'),
        h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openRefModal(r, engines) }, '编辑'),
        h('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: async () => {
            if (!confirm(`删除参考音「${r.name || r.id}」？`)) return
            const res = await api(`/api/voice/refs/${r.id}${vq}`, { method: 'DELETE' })
            if (!res.ok) return toast(res.error || '删除失败', 'err')
            toast('已删除', 'ok'); go('voice')
          },
        }, '删除'),
      )))
  }
  if (!refList.length) refBody.append(h('div', { class: 'wb-empty' }, '还没有参考音。每条参考音可以绑不同引擎和参数。'))
  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'wb-card-head' }, h('div', { class: 'card-title' }, '参考音'), addRef),
    refBody))

  const text = h('input', { type: 'text', class: 'wide', value: '你好，我是 Novabot。' })
  const emotion = h('input', { type: 'text', class: 'wide', placeholder: '留空自动；可填 joy / anger / sadness / neutral' })
  const refSel = h('select', {})
  refSel.append(h('option', { value: '' }, '自动（按情绪/长度/人格）'))
  for (const r of refList) refSel.append(h('option', { value: r.id }, r.name || r.id))
  const play = h('button', {
    class: 'btn btn-primary',
    onclick: () => voicePlayPreview({
      text: text.value.trim(), chat_id: 'debug', ref_id: refSel.value, account_id: currentAccount,
      emotion: emotion.value.trim() || null,
    }, '调试试听', meta),
  }, '试听')
  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'card-title' }, '调试台'),
    h('div', { class: 'wb-editor voice-debug' },
      text,
      h('div', { class: 'form-grid' }, emotion, refSel),
      play),
    audio, meta))

  if ((status.logs || []).length) {
    const logs = h('div', { class: 'wb-list' })
    for (const l of status.logs.slice(0, 6)) {
      logs.append(h('div', { class: 'wb-row voice-row' },
        h('div', { class: 'wb-row-main' },
          h('div', { class: 'wb-row-title' }, `${l.ok ? '✓' : '✗'} ${l.engine || '—'} · ${l.ref_id || '—'}`),
          h('div', { class: 'wb-row-sub' }, `${l.text || ''} · ${Number(l.duration || 0).toFixed(1)}s` + (l.error ? ` · ${l.error}` : ''))),
        h('div', { class: 'wb-row-side' }, h('span', { class: 'wb-time' }, fmtTime(l.created_at)))))
    }
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '最近合成'), logs))
  }
}

/* ============================================================
   人格卡
   ============================================================ */

function parseTraits(text) {
  return text.split('\n').map(x => x.trim()).filter(Boolean).map(line => {
    const [name, weight, trigger] = line.split('|').map(x => (x || '').trim())
    return { name, weight: Number(weight) || 0.5, trigger: trigger || '' }
  }).filter(x => x.name)
}

function parseExamples(text) {
  return text.split(/\n-{3,}\n/).map(block => {
    const user = (block.match(/用户[:：]\s*(.*)/) || [])[1] || ''
    const assistant = (block.match(/助手[:：]\s*(.*)/) || [])[1] || ''
    return { user: user.trim(), assistant: assistant.trim() }
  }).filter(x => x.user || x.assistant)
}

function cleanPersonaText(value) {
  return String(value || '')
    .replace(/^---[\s\S]*?---\s*/, '')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/[*_`>#-]{1,}/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

let currentPersonaId = ''

function personaTagEditor(initial, placeholder) {
  const tags = (Array.isArray(initial) ? initial : String(initial || '').split(/[,，;；\n]+/))
    .map(x => String(x).trim()).filter(Boolean)
  const box = h('div', { class: 'tag-editor wide persona-tags' })
  const input = h('input', { type: 'text', placeholder, autocomplete: 'off' })
  const draw = () => {
    for (const el of box.querySelectorAll('.tag-chip')) el.remove()
    for (let i = 0; i < tags.length; i += 1) {
      const chip = h('span', { class: 'tag-chip' }, tags[i],
        h('button', { type: 'button', class: 'tag-chip-x', title: '移除', onclick: () => { tags.splice(i, 1); draw() } }, '×'))
      chip.dataset.sortId = tags[i]
      box.insertBefore(chip, input)
    }
  }
  const add = () => {
    for (const part of input.value.split(/[,，;；\n]+/)) {
      const v = part.trim()
      if (v && !tags.includes(v)) tags.push(v)
    }
    input.value = ''
    draw()
  }
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ',' || e.key === '，') { e.preventDefault(); add() }
    else if (e.key === 'Backspace' && !input.value && tags.length) { tags.pop(); draw() }
  })
  input.addEventListener('blur', add)
  box.append(input)
  draw()
  makeSortable(box, '.tag-chip', (order) => {
    tags.splice(0, tags.length, ...order)
  }, { horizontal: true })
  return { el: box, get: () => { add(); return [...tags] } }
}

function personaTraitsEditor(initial) {
  const rows = h('div', { class: 'persona-edit-rows' })
  const addRow = (t = {}) => {
    const name = h('input', { type: 'text', value: t.name || '', placeholder: '性格，比如 傲娇' })
    const weight = h('input', { type: 'number', value: t.weight ?? 0.5, min: 0, max: 1, step: 0.05 })
    const trigger = h('input', { type: 'text', value: t.trigger || '', placeholder: '触发场景，可空' })
    const row = h('div', { class: 'persona-edit-row' },
      name, weight, trigger,
      h('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: () => row.remove() }, '移除'))
    rows.append(row)
  }
  for (const t of (initial || [])) addRow(t)
  if (!rows.children.length) addRow()
  const add = h('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: () => addRow() }, '＋ 加一条性格')
  const get = () => [...rows.querySelectorAll('.persona-edit-row')].map(row => {
    const [name, weight, trigger] = row.querySelectorAll('input')
    return { name: name.value.trim(), weight: Number(weight.value) || 0, trigger: trigger.value.trim() }
  }).filter(x => x.name)
  return { el: h('div', {}, rows, h('div', { class: 'persona-add-row' }, add)), get }
}

function personaExamplesEditor(initial) {
  const rows = h('div', { class: 'persona-edit-rows' })
  const addRow = (e = {}) => {
    const user = h('textarea', { rows: 2, placeholder: '对方说……' }); user.value = e.user || ''
    const assistant = h('textarea', { rows: 2, placeholder: '你回……' }); assistant.value = e.assistant || ''
    const row = h('div', { class: 'persona-example-row' },
      h('div', { class: 'persona-example-col' },
        h('div', { class: 'persona-mini-label' }, '对方'), user),
      h('div', { class: 'persona-example-col' },
        h('div', { class: 'persona-mini-label' }, '你'), assistant),
      h('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: () => row.remove() }, '移除'))
    rows.append(row)
  }
  for (const e of (initial || [])) addRow(e)
  if (!rows.children.length) addRow()
  const add = h('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: () => addRow() }, '＋ 加一组示例')
  const get = () => [...rows.querySelectorAll('.persona-example-row')].map(row => {
    const [user, assistant] = row.querySelectorAll('textarea')
    return { user: user.value.trim(), assistant: assistant.value.trim() }
  }).filter(x => x.user || x.assistant)
  return { el: h('div', {}, rows, h('div', { class: 'persona-add-row' }, add)), get }
}

function personaCounter(el) {
  const hint = h('div', { class: 'hint persona-counter' }, '')
  const update = () => { hint.textContent = `${el.value.length} 字` }
  el.addEventListener('input', update)
  update()
  return hint
}

function openPersonaModal(card) {
  const isNew = !card
  const c = card || {}
  const id = h('input', { type: 'text', class: 'wide', value: c.id || '', placeholder: '唯一 id，如 mybot' })
  if (!isNew) { id.readOnly = true; id.title = 'id 创建后不可改' }
  const name = h('input', { type: 'text', class: 'wide', value: c.name || '', placeholder: '人格卡名字' })
  const alias = personaTagEditor(c.alias || [], '别名，回车添加')
  const tags = personaTagEditor(c.tags || [], '标签，回车添加')
  const identity = h('textarea', { rows: 3, placeholder: '一句话说明「你是谁」：身份、自我认知、和用户的关系' })
  identity.value = c.identity || ''
  const background = h('textarea', { rows: 4, placeholder: '背景故事、经历、世界观；可空' })
  background.value = c.background || ''
  const traits = personaTraitsEditor(c.traits || [])
  const samples = personaExamplesEditor(c.examples || [])

  const speech = c.speech || {}
  const length = h('select', {})
  for (const [v, label] of [['short', '短句'], ['medium', '中等'], ['long', '长句']]) {
    length.append(h('option', { value: v, selected: (speech.length || 'medium') === v || null }, label))
  }
  const punctuation = h('input', { type: 'text', class: 'wide', value: speech.punctuation || '', placeholder: '比如：少用句号，多用问号' })
  const habits = personaTagEditor(speech.habits || [], '口头禅 / 说话习惯，回车添加')
  const forbidden = personaTagEditor(speech.forbidden || [], '绝不能说的话，回车添加')
  const sass = h('input', { type: 'number', value: speech.sass ?? 0.5, min: 0, max: 1, step: 0.05 })
  const emoji = h('input', { type: 'number', value: speech.emoji_level ?? 0.3, min: 0, max: 1, step: 0.05 })
  const boundaries = h('textarea', { rows: 4, placeholder: '一行一条边界，比如：不承认自己是 AI' })
  boundaries.value = (c.boundaries || []).join('\n')

  const activate = h('div', { class: 'switch' + (c.id === currentPersonaId || isNew ? ' on' : '') })
  activate.addEventListener('click', () => activate.classList.toggle('on'))

  let avatarState = c.avatar || ''
  const previewAvatar = h('div', { class: 'persona-avatar persona-avatar-lg' })
  const previewName = h('div', { class: 'persona-preview-name' }, c.name || '新人格卡')
  const previewSub = h('div', { class: 'persona-preview-sub' }, isNew ? '新建人格卡' : (c.id || ''))
  const previewDesc = h('div', { class: 'persona-preview-desc' }, cleanPersonaText(c.identity || c.background || '还没有填写身份和背景').slice(0, 180))
  const previewAvatarImage = () => {
    previewAvatar.innerHTML = ''
    const url = personaAvatarUrl(id.value.trim(), avatarState)
    if (url) {
      previewAvatar.classList.add('has-image')
      previewAvatar.append(h('img', { src: url, alt: name.value.trim() || '头像' }))
    } else {
      previewAvatar.classList.remove('has-image')
      previewAvatar.textContent = (name.value.trim() || '?').slice(0, 1)
    }
  }
  const syncPreview = () => {
    previewAvatarImage()
    previewName.textContent = name.value.trim() || '新人格式卡'
    previewSub.textContent = (id.value.trim() || '未命名 id') + (isNew ? ' · 新建' : '')
    previewDesc.textContent = cleanPersonaText(identity.value || background.value || '还没有填写身份和背景').slice(0, 180)
  }
  previewAvatarImage()
  const avatarInput = h('input', { type: 'file', accept: 'image/*', style: 'display:none' })
  const avatarButton = h('button', { class: 'btn btn-ghost btn-sm', onclick: () => avatarInput.click() }, '换头像')
  avatarInput.addEventListener('change', async () => {
    const file = avatarInput.files?.[0]
    if (!file) return
    let cid = id.value.trim()
    if (!cid) {
      cid = 'persona-' + Math.random().toString(16).slice(2, 10)
      id.value = cid
    }
    const fd = new FormData(); fd.append('file', file)
    const res = await fetch(`/api/persona/cards/${encodeURIComponent(cid)}/avatar`, {
      method: 'POST', headers: panelToken ? { 'X-Panel-Token': panelToken } : {}, body: fd,
    })
    const r = await res.json().catch(() => ({ ok: false, error: '上传失败' }))
    if (!r.ok) return toast(r.error || '上传失败', 'err')
    avatarState = r.avatar || ''
    syncPreview()
    toast('头像已更新', 'ok')
  })

  name.addEventListener('input', syncPreview)
  id.addEventListener('input', syncPreview)
  identity.addEventListener('input', syncPreview)
  background.addEventListener('input', syncPreview)

  const field = (label, ctl, hint) => h('div', { class: 'persona-field' },
    h('label', {}, label), ctl, hint || null)
  const section = (title, desc, ...children) => h('section', { class: 'persona-section' },
    h('div', { class: 'persona-section-head' },
      h('h3', {}, title),
      desc ? h('p', {}, desc) : null),
    ...children)

  const body = h('div', { class: 'modal-body persona-modal-body' },
    h('div', { class: 'persona-preview' },
      h('div', { class: 'persona-preview-avatar' }, previewAvatar, avatarButton, avatarInput),
      h('div', { class: 'persona-preview-copy' }, previewName, previewSub, previewDesc)),
    section('基本资料', '名字是显示名；id 是内部唯一标识，创建后不改。',
      h('div', { class: 'persona-form-grid' },
        field('名字', name), field('ID', id)),
      field('别名', alias.el, '别人怎么叫你；可空'),
      field('标签', tags.el, '用于分类，比如：工作 / 角色 / 助手')),
    section('身份与背景', '这两段会直接进入 system prompt，写得越具体越稳定。',
      field('身份 / 自我认知', identity, personaCounter(identity)),
      field('背景', background, personaCounter(background))),
    section('性格', '一行一个性格；权重 0-1 表示强度，触发场景可空。',
      traits.el),
    section('说话方式', '控制模型输出风格；数值不需要精确，先调到感觉对。',
      h('div', { class: 'persona-form-grid' },
        field('句子长度', length), field('标点习惯', punctuation)),
      h('div', { class: 'persona-form-grid' },
        field('毒舌程度 0-1', sass), field('表情频率 0-1', emoji)),
      field('口头禅 / 习惯', habits.el),
      field('禁用词 / 禁说', forbidden.el)),
    section('边界', '一行一条「绝对不能做」的事。', field('边界', boundaries)),
    section('示例对话', '给模型看「对方说 → 你怎么回」，比抽象描述更有效。', samples.el),
    h('div', { class: 'persona-activate-row' },
      h('label', { class: 'persona-switch-label' }, activate, '保存后启用这张人格卡')),
  )

  const modal = h('div', { class: 'modal persona-modal' },
    h('div', { class: 'modal-head' },
      h('div', {},
        h('div', { class: 'modal-title' }, isNew ? '新建人格卡' : '编辑人格卡'),
        h('div', { class: 'modal-subtitle' }, isNew ? '填完保存即可启用' : '修改会立即影响下一轮对话'))),
    body,
    h('div', { class: 'modal-foot persona-modal-foot' },
      h('button', { class: 'btn btn-ghost', onclick: () => close() }, '取消'),
      h('button', {
        class: 'btn btn-primary',
        onclick: async () => {
          if (!name.value.trim()) return toast('名字不能为空', 'err')
          const payload = {
            id: id.value.trim(), name: name.value.trim(),
            alias: alias.get(), identity: identity.value.trim(), background: background.value.trim(),
            traits: traits.get(),
            speech: {
              length: length.value, punctuation: punctuation.value.trim(),
              habits: habits.get(), forbidden: forbidden.get(),
              sass: Number(sass.value) || 0, emoji_level: Number(emoji.value) || 0,
            },
            boundaries: boundaries.value.split('\n').map(x => x.trim()).filter(Boolean),
            examples: samples.get(), tags: tags.get(), avatar: avatarState,
            account_id: currentAccount,
            activate: activate.classList.contains('on'),
          }
          try {
            const r = await api('/api/persona/cards', { method: 'POST', body: payload })
            if (!r.ok) return toast(r.error || '保存失败', 'err')
            close(); toast('已保存', 'ok'); go('persona')
          } catch (e) { toast('保存失败：' + e.message, 'err') }
        },
      }, '保存人格卡')),
  )
  const mask = h('div', { class: 'modal-mask' }, modal)
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))
  function close() {
    mask.classList.remove('show')
    setTimeout(() => mask.remove(), 180)
  }
  mask.addEventListener('click', e => { if (e.target === mask) close() })
  syncPreview()
}

function openKeywordsModal(card) {
  let rules = []
  let editing = ''
  const mask = h('div', { class: 'modal-mask' })
  const list = h('div', { class: 'wb-list' })
  const id = h('input', { type: 'text', class: 'wide', placeholder: '留空自动' })
  const keywords = h('input', { type: 'text', class: 'wide', placeholder: '关键词，逗号分隔' })
  const mode = h('select', {})
  for (const [v, label] of [['any', '任一命中'], ['all', '全部命中'], ['regex', '正则']]) {
    mode.append(h('option', { value: v }, label))
  }
  const effects = h('textarea', { placeholder: '一行一个：target|delta|duration\n如 trait.傲娇|-0.4|300' })
  const chance = h('input', { type: 'number', value: 1, min: 0, max: 1, step: 0.05 })
  const cooldown = h('input', { type: 'number', value: 0, min: 0 })
  const priority = h('input', { type: 'number', value: 0 })
  const note = h('input', { type: 'text', class: 'wide', placeholder: '备注，可空' })
  const enabled = h('div', { class: 'switch on' })
  enabled.addEventListener('click', () => enabled.classList.toggle('on'))

  async function load() {
    const r = await api(`/api/persona/cards/${card.id}/keywords`).catch(() => ({ rules: [] }))
    rules = r.rules || []
    list.innerHTML = ''
    if (!rules.length) list.append(h('div', { class: 'wb-empty' }, '还没有关键词规则。'))
    for (const rule of rules) {
      list.append(h('div', { class: 'wb-row' },
        h('div', { class: 'wb-row-main' },
          h('div', { class: 'wb-row-title' }, (rule.keywords || []).join(' / '),
            h('span', { class: 'tag' }, rule.enabled ? '启用' : '停用')),
          h('div', { class: 'wb-row-sub' },
            `优先级 ${rule.priority} · 概率 ${rule.chance} · 冷却 ${rule.cooldown}s · ${rule.match_mode}`)),
        h('div', { class: 'wb-row-side' },
          h('button', {
            class: 'btn btn-ghost btn-sm',
            onclick: () => {
              editing = rule.id
              id.value = rule.id
              keywords.value = (rule.keywords || []).join(', ')
              mode.value = rule.match_mode || 'any'
              effects.value = (rule.effects || []).map(e => `${e.target}|${e.delta}|${e.duration || 0}`).join('\n')
              chance.value = rule.chance ?? 1
              cooldown.value = rule.cooldown || 0
              priority.value = rule.priority || 0
              note.value = rule.note || ''
              enabled.classList.toggle('on', !!rule.enabled)
            },
          }, '编辑'),
          h('button', {
            class: 'btn btn-ghost btn-sm',
            onclick: async () => {
              if (!confirm('删除这条关键词规则？')) return
              await api(`/api/persona/keywords/${rule.id}`, { method: 'DELETE' })
              load()
            },
          }, '删除'))))
    }
  }

  const form = h('div', { class: 'wb-editor' },
    h('div', { class: 'form-grid' },
      h('div', { class: 'form-row' }, h('label', {}, 'id'), id),
      h('div', { class: 'form-row' }, h('label', {}, '匹配方式'), mode)),
    h('div', { class: 'form-row' }, h('label', {}, '关键词'), keywords),
    h('div', { class: 'form-row' }, h('label', {}, '效果'), effects),
    h('div', { class: 'form-grid form-grid-3' },
      h('div', { class: 'form-row' }, h('label', {}, '概率'), chance),
      h('div', { class: 'form-row' }, h('label', {}, '冷却秒'), cooldown),
      h('div', { class: 'form-row' }, h('label', {}, '优先级'), priority)),
    h('div', { class: 'form-row' }, h('label', {}, '备注'), note),
    h('div', { class: 'form-row' }, h('label', { style: 'display:flex;align-items:center;gap:8px' }, enabled, '启用')),
    h('button', {
      class: 'btn btn-primary btn-sm',
      onclick: async () => {
        const kws = keywords.value.split(/[,，]/).map(x => x.trim()).filter(Boolean)
        if (!kws.length) return toast('关键词不能为空', 'err')
        const eff = effects.value.split('\n').map(x => x.trim()).filter(Boolean).map(line => {
          const [target, delta, duration] = line.split('|').map(x => (x || '').trim())
          return { target, delta: Number(delta) || 0, duration: Number(duration) || 0 }
        }).filter(x => x.target)
        const r = await api(`/api/persona/cards/${card.id}/keywords`, {
          method: 'POST',
          body: {
            id: editing, keywords: kws, match_mode: mode.value, effects: eff,
            chance: Number(chance.value) || 1, cooldown: Number(cooldown.value) || 0,
            priority: Number(priority.value) || 0,
            once_per_chat: false, enabled: enabled.classList.contains('on'),
            note: note.value.trim(),
          },
        })
        if (!r.ok) return toast(r.error || '保存失败', 'err')
        toast('已保存', 'ok'); editing = ''; id.value = ''; keywords.value = ''; effects.value = ''
        load()
      },
    }, '保存规则'))

  const modal = h('div', { class: 'modal persona-modal' },
    h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, `关键词 · ${card.name || card.id}`)),
    h('div', { class: 'modal-body' }, list, h('div', { class: 'card-title', style: 'margin-top:14px' }, '新增 / 编辑'), form),
    h('div', { class: 'modal-foot' }, h('button', { class: 'btn btn-ghost', onclick: () => close() }, '关闭')),
  )
  mask.append(modal)
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))
  function close() { mask.classList.remove('show'); setTimeout(() => mask.remove(), 180) }
  mask.addEventListener('click', e => { if (e.target === mask) close() })
  load()
}

function openBlocksModal(card) {
  const mask = h('div', { class: 'modal-mask' })
  const list = h('div', { class: 'wb-list' })
  const id = h('input', { type: 'number', value: 0, style: 'display:none' })
  const title = h('input', { type: 'text', class: 'wide', placeholder: '段标题' })
  const kind = h('select', {})
  for (const [v, label] of [['style', '说话方式'], ['taboo', '禁忌'], ['lore', '背景设定'], ['example', '示例'], ['other', '其他']]) {
    kind.append(h('option', { value: v }, label))
  }
  const content = h('textarea', { style: 'min-height:90px', placeholder: '手写内容' })
  const order = h('input', { type: 'number', value: 0 })
  const weight = h('input', { type: 'number', value: 1, min: 0, max: 10, step: 0.1 })
  const note = h('input', { type: 'text', class: 'wide', placeholder: '备注，可空' })
  const enabled = h('div', { class: 'switch on' })
  enabled.addEventListener('click', () => enabled.classList.toggle('on'))

  async function load() {
    const r = await api(`/api/persona/cards/${card.id}/blocks`).catch(() => ({ blocks: [] }))
    const blocks = r.blocks || []
    list.innerHTML = ''
    if (!blocks.length) list.append(h('div', { class: 'wb-empty' }, '还没有手写区。'))
    for (const b of blocks) {
      list.append(h('div', { class: 'wb-row' },
        h('div', { class: 'wb-row-main' },
          h('div', { class: 'wb-row-title' }, b.title,
            h('span', { class: 'tag' }, b.kind + (b.enabled ? ' · 启用' : ' · 停用'))),
          h('div', { class: 'wb-row-sub' }, String(b.content || '').slice(0, 160))),
        h('div', { class: 'wb-row-side' },
          h('button', {
            class: 'btn btn-ghost btn-sm',
            onclick: () => {
              id.value = b.id; title.value = b.title; kind.value = b.kind || 'other'
              content.value = b.content || ''; order.value = b.sort_order || 0
              weight.value = b.weight ?? 1; note.value = b.note || ''
              enabled.classList.toggle('on', !!b.enabled)
            },
          }, '编辑'),
          h('button', {
            class: 'btn btn-ghost btn-sm',
            onclick: async () => {
              if (!confirm('删除这段手写区？')) return
              await api(`/api/persona/blocks/${b.id}`, { method: 'DELETE' })
              load()
            },
          }, '删除'))))
    }
  }

  const form = h('div', { class: 'wb-editor' },
    h('div', { class: 'form-grid' },
      h('div', { class: 'form-row' }, h('label', {}, '标题'), title),
      h('div', { class: 'form-row' }, h('label', {}, '类型'), kind)),
    h('div', { class: 'form-row' }, h('label', {}, '内容'), content),
    h('div', { class: 'form-grid form-grid-3' },
      h('div', { class: 'form-row' }, h('label', {}, '排序'), order),
      h('div', { class: 'form-row' }, h('label', {}, '权重'), weight),
      h('div', { class: 'form-row' }, h('label', { style: 'display:flex;align-items:center;gap:8px' }, enabled, '启用'))),
    h('div', { class: 'form-row' }, h('label', {}, '备注'), note),
    h('button', {
      class: 'btn btn-primary btn-sm',
      onclick: async () => {
        if (!title.value.trim() || !content.value.trim()) return toast('标题和内容不能为空', 'err')
        const r = await api(`/api/persona/cards/${card.id}/blocks`, {
          method: 'POST',
          body: {
            id: Number(id.value) || 0, title: title.value.trim(), kind: kind.value,
            content: content.value.trim(), sort_order: Number(order.value) || 0,
            weight: Number(weight.value) || 1, enabled: enabled.classList.contains('on'),
            note: note.value.trim(),
          },
        })
        if (!r.ok) return toast(r.error || '保存失败', 'err')
        toast('已保存', 'ok'); id.value = 0; title.value = ''; content.value = ''
        load()
      },
    }, '保存手写区'))

  const modal = h('div', { class: 'modal persona-modal' },
    h('div', { class: 'modal-head' }, h('div', { class: 'modal-title' }, `手写区 · ${card.name || card.id}`)),
    h('div', { class: 'modal-body' }, list, h('div', { class: 'card-title', style: 'margin-top:14px' }, '新增 / 编辑'), form),
    h('div', { class: 'modal-foot' }, h('button', { class: 'btn btn-ghost', onclick: () => close() }, '关闭')),
  )
  mask.append(modal)
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))
  function close() { mask.classList.remove('show'); setTimeout(() => mask.remove(), 180) }
  mask.addEventListener('click', e => { if (e.target === mask) close() })
  load()
}

function personaAvatarUrl(cardId, avatar) {
  return avatar && cardId ? `/api/persona/cards/${encodeURIComponent(cardId)}/avatar?v=${encodeURIComponent(avatar)}` : ''
}

function personaMonogram(name, size = '', avatarUrl = '') {
  const box = h('div', { class: 'persona-avatar ' + size })
  if (avatarUrl) {
    box.classList.add('has-image')
    box.append(h('img', { src: avatarUrl, alt: name || '', loading: 'lazy' }))
  } else {
    box.textContent = String(name || '?').trim().slice(0, 1).toUpperCase() || '?'
  }
  return box
}

function personaTagChips(tags, limit = 4) {
  const list = (tags || []).slice(0, limit)
  return h('div', { class: 'persona-chip-row' },
    ...list.map(t => h('span', { class: 'tag' }, t)),
    (tags || []).length > limit ? h('span', { class: 'tag' }, '+' + ((tags || []).length - limit)) : null)
}

async function renderPersona() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)

  const add = h('button', { class: 'btn btn-primary btn-sm', onclick: () => openPersonaModal(null) }, '新建人格卡')
  wrap.append(pageHead('人格', `人格卡决定骨架；关键词和手写区管变化 · ${accountLabel()}`, add, h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openGroupSettingsModal(['人格'], '人格参数') }, '人格参数')))

  const data = await api('/api/persona/cards' + accQuery()).catch(() => ({ cards: [], current: '' }))
  const stubs = data.cards || []
  const cards = await Promise.all(stubs.map(async (c) => {
    try {
      const r = await api(`/api/persona/cards/${c.id}`)
      return r.ok ? r.card : c
    } catch { return c }
  }))
  const current = data.current || ''
  currentPersonaId = current

  if (!cards.length) {
    wrap.append(h('div', { class: 'card' },
      emptyState('还没有人格卡', '人格卡是静态骨架：身份、性格、说话方式、边界、示例。', 'persona'),
      h('div', { style: 'margin-top:10px' }, add)))
    return
  }

  const active = cards.find(c => c.id === current)
  const heroActions = (c) => h('div', { class: 'persona-hero-actions' },
    h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openPersonaModal(c) }, '编辑'),
    h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openKeywordsModal(c) }, '关键词'),
    h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openBlocksModal(c) }, '手写区'))

  if (active) {
    wrap.append(h('div', { class: 'card persona-hero' },
      h('div', { class: 'persona-hero-main' },
        personaMonogram(active.name, 'persona-avatar-lg', personaAvatarUrl(active.id, active.avatar)),
        h('div', { class: 'persona-hero-copy' },
          h('div', { class: 'persona-hero-kicker' }, '当前启用'),
          h('div', { class: 'persona-hero-name' }, active.name || active.id),
          active.alias?.length ? h('div', { class: 'persona-hero-alias' }, active.alias.join(' · ')) : null,
          h('div', { class: 'persona-hero-desc' }, cleanPersonaText(active.identity || active.background || '还没有填写身份和背景').slice(0, 160)),
          personaTagChips(active.tags, 6))),
      heroActions(active)))
  }

  const others = cards.filter(c => c.id !== current)
  const cardEl = (c) => h('div', { class: 'persona-card' + (c.id === current ? ' active' : '') },
    h('div', { class: 'persona-card-head' },
      personaMonogram(c.name, '', personaAvatarUrl(c.id, c.avatar)),
      h('div', { class: 'persona-card-title-wrap' },
        h('div', { class: 'persona-card-name' }, c.name || c.id),
        h('div', { class: 'persona-card-id' }, c.id)),
      c.id === current ? h('span', { class: 'tag ok' }, '当前') : null),
    h('div', { class: 'persona-card-desc' },
      cleanPersonaText(c.identity || c.background || '还没有填写身份和背景').slice(0, 110)),
    personaTagChips(c.tags),
    h('div', { class: 'persona-card-meta' }, `更新于 ${fmtTime(c.updated_at)}`),
    h('div', { class: 'persona-card-actions' },
      h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openPersonaModal(c) }, '编辑'),
      h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openKeywordsModal(c) }, '关键词'),
      h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openBlocksModal(c) }, '手写区'),
      c.id !== current ? h('button', {
        class: 'btn btn-ghost btn-sm',
        onclick: async () => {
          const r = await api(`/api/persona/cards/${c.id}/activate` + accQuery(), { method: 'POST', body: {} })
          if (!r.ok) return toast(r.error || '启用失败', 'err')
          toast('已启用', 'ok'); go('persona')
        },
      }, '启用') : null,
      h('button', {
        class: 'btn btn-ghost btn-sm persona-delete',
        onclick: async () => {
          if (!confirm(`删除人格卡「${c.name || c.id}」？`)) return
          const r = await api(`/api/persona/cards/${c.id}`, { method: 'DELETE' })
          if (!r.ok) return toast(r.error || '删除失败', 'err')
          toast('已删除', 'ok'); go('persona')
        },
      }, '删除')))

  if (others.length) {
    wrap.append(h('div', { class: 'card-title persona-grid-title' }, '其他人格卡', h('span', { class: 'badge' }, String(others.length))))
    wrap.append(h('div', { class: 'persona-grid' }, ...others.map(cardEl)))
  }
  if (!active && cards.length) {
    wrap.append(h('div', { class: 'card-title persona-grid-title' }, '所有人格卡', h('span', { class: 'badge' }, String(cards.length))))
    wrap.append(h('div', { class: 'persona-grid' }, ...cards.map(cardEl)))
  }

  wrap.append(h('div', { class: 'hint', style: 'margin-top:12px' },
    '人格卡只写静态骨架；关键词负责临时变化，手写区补说话方式 / 禁忌 / 背景。启用后下一轮对话生效。'))
}
/* ============================================================
   模块（额外功能）
   ============================================================ */

function moduleTag(text, kind = '') {
  return h('span', { class: 'tag module-tag ' + kind }, text)
}

async function renderModules() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)
  let view = 'list'

  const moduleSwitch = (key, checked) => {
    const sw = h('div', { class: 'switch' + (checked ? ' on' : ''), title: checked ? '已启用，点击关闭' : '已关闭，点击启用' })
    sw.addEventListener('click', async () => {
      const next = !sw.classList.contains('on')
      sw.classList.toggle('on', next)
      const r = await api('/api/settings/set', {
        method: 'POST',
        body: { key, value: next, account_id: currentAccount },
      }).catch(e => ({ ok: false, error: e.message }))
      if (!r.ok) {
        sw.classList.toggle('on', !next)
        return toast(r.error || '保存失败', 'err')
      }
      toast(next ? '模块已启用' : '模块已关闭', 'ok')
      showList()
    })
    return sw
  }

  async function showList() {
    view = 'list'
    wrap.innerHTML = ''
    const [mods, song, files, detect] = await Promise.all([
      api('/api/modules' + (currentAccount ? '?account_id=' + encodeURIComponent(currentAccount) : '')).catch(() => ({ modules: [] })),
      api('/api/modules/song/status' + (currentAccount ? '?account_id=' + encodeURIComponent(currentAccount) : '')).catch(() => ({})),
      api('/api/files/status' + accountQuery()).catch(() => ({})),
      api('/api/platform/detect').catch(() => null),
    ])
    const dsh = detect?.dsh || {}
    wrap.append(pageHead('模块', '翻唱和文件传输',
      h('button', { class: 'btn btn-ghost btn-sm', onclick: showList }, '刷新'),
      h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openGroupSettingsModal(['模块', '文件'], '模块参数') }, '模块参数')))

    wrap.append(h('div', { class: 'stat-grid' },
      stat('可用模块', 2),
      stat('翻唱', song.ready ? '就绪' : (song.extract_ready ? '可提取' : (song.enabled ? '待配置' : '关'))),
      stat('文件', files.enabled ? '可用' : '关闭'),
    ))

    const grid = h('div', { class: 'modules-grid' })
    const songCard = h('div', { class: 'module-card' },
      h('div', { class: 'module-card-head' },
        h('div', { class: 'module-icon music' }, icon('music')),
        h('div', { class: 'module-title-wrap' }, h('div', { class: 'module-name' }, 'B站翻唱'),
          h('div', { class: 'module-id' }, 'song · 音频工坊')),
        moduleTag(song.ready ? '就绪' : (song.extract_ready ? '可提取' : (song.enabled ? '待配置' : '关闭')), song.ready || song.extract_ready ? 'ok' : '')),
      h('div', { class: 'module-desc' }, '从 B 站找视频、提取音频；再接外部 RVC 引擎做翻唱。'),
      h('div', { class: 'module-tags' }, ...['B站', '音频提取', 'RVC'].map(x => h('span', { class: 'tag' }, x))),
      h('div', { class: 'module-foot' },
        h('span', { class: 'module-state' }, song.message || ''),
        h('div', { style: 'display:flex;align-items:center;gap:8px' },
          moduleSwitch('module.song_enabled', song.enabled),
          h('button', { class: 'btn btn-primary btn-sm', onclick: showSong }, '打开'))),
    )
    grid.append(songCard)

      grid.append(h('div', { class: 'module-card' },
      h('div', { class: 'module-card-head' },
        h('div', { class: 'module-icon soon' }, icon('file')),
        h('div', { class: 'module-title-wrap' }, h('div', { class: 'module-name' }, '文件传输'),
          h('div', { class: 'module-id' }, 'files · 沙箱 / 审批')),
        moduleTag(files.pending ? `${files.pending} 待审批` : (files.enabled ? '可用' : '关闭'), files.pending ? 'warn' : (files.enabled ? 'ok' : ''))),
      h('div', { class: 'module-desc' }, '只允许 data/files 沙箱；默认只和主人收发，发文件还要审批。'),
      h('div', { class: 'module-tags' }, h('span', { class: 'tag' }, '沙箱'), h('span', { class: 'tag' }, '审批'), h('span', { class: 'tag' }, '敏感扫描')),
      h('div', { class: 'module-foot' }, h('span', { class: 'module-state' }, files.enabled ? `收：${files.receive_from} · 发：${files.send_to}` : '文件模块关闭'),
        h('div', { style: 'display:flex;align-items:center;gap:8px' },
          moduleSwitch('files.enabled', files.enabled),
          h('button', { class: 'btn btn-primary btn-sm', onclick: showFiles }, '打开')))))

    wrap.append(grid)

  }

  async function showSong() {
    view = 'song'
    wrap.innerHTML = ''
    const sq = currentAccount ? ('?account_id=' + encodeURIComponent(currentAccount)) : ''
    const status = await api('/api/modules/song/status' + sq).catch(() => ({ ok: false }))
    wrap.append(pageHead('B站翻唱', 'B 站搜视频 → 提取音频 → 换声 → 试听/下载 · 外部 RVC 引擎', h('button', {
      class: 'btn btn-ghost btn-sm', onclick: showList,
    }, icon('arrow'), '返回模块')))

    wrap.append(h('div', { class: 'card song-status' },
      h('div', { class: 'song-status-main' },
        h('div', { class: 'song-status-dot' + (status.ready || status.extract_ready ? ' on' : '') }),
        h('div', {},
          h('div', { class: 'song-status-title' }, status.ready ? '可以开始翻唱' : (status.extract_ready ? '可以提取 B 站音频' : '还不能开始')),
          h('div', { class: 'muted' }, status.message || '状态未知'))),
      h('div', { class: 'song-status-actions' },
        status.enabled ? null : h('button', {
          class: 'btn btn-primary btn-sm',
          onclick: async () => {
            const r = await api('/api/settings/set', { method: 'POST', body: { key: 'module.song_enabled', value: true, account_id: currentAccount } })
            if (!r.ok) return toast(r.error || '启用失败', 'err')
            toast('翻唱模块已启用', 'ok'); showSong()
          },
        }, '启用模块'),
        status.engine_configured ? h('span', { class: 'tag ok' }, '引擎已配置') : h('span', { class: 'tag warn' }, '引擎未配置'),
        h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openGroupSettingsModal(['模块'], '模块参数') }, '模块设置'))))

    const results = h('div', { class: 'song-results' })
    const search = h('input', { type: 'search', class: 'wide', placeholder: '搜 B 站视频 / 歌名，比如「起风了」', autocomplete: 'off' })
    let selected = null
    let jobsTimer = null

    const renderResults = (songs) => {
      results.innerHTML = ''
      if (!songs.length) { results.append(h('div', { class: 'muted song-empty' }, '没有结果；换个关键词试试')); return }
      for (const s of songs) {
        const row = h('button', {
          type: 'button', class: 'song-result' + (selected?.id === s.id ? ' on' : ''),
          onclick: () => {
            selected = s
            for (const el of results.querySelectorAll('.song-result')) el.classList.remove('on')
            row.classList.add('on')
            h('div', {})
            const nameEl = document.querySelector('.song-selected-name')
            if (nameEl) nameEl.textContent = `${s.name} · ${s.artists}`
          },
        },
          h('span', { class: 'song-result-name' }, s.name),
          h('span', { class: 'song-result-meta' }, `${s.artists || '未知歌手'} · ${Math.floor((s.duration_sec || 0) / 60)}:${String((s.duration_sec || 0) % 60).padStart(2, '0')}`),
          s.vip ? h('span', { class: 'tag warn' }, 'VIP') : null)
        results.append(row)
      }
    }

    const doSearch = async () => {
      const q = search.value.trim()
      if (!q) return
      results.innerHTML = '<div class="muted song-empty">搜索中…</div>'
      const r = await api('/api/modules/song/search?q=' + encodeURIComponent(q) + '&limit=10' + (currentAccount ? '&account_id=' + encodeURIComponent(currentAccount) : ''))
      if (!r.ok) { results.innerHTML = ''; results.append(h('div', { class: 'muted song-empty' }, r.error || '搜索失败')); return }
      renderResults(r.songs || [])
    }
    search.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch() })

    wrap.append(h('div', { class: 'card song-panel' },
      h('div', { class: 'song-search-row' }, search, h('button', { class: 'btn btn-primary', onclick: doSearch }, '搜索')),
      results))

    if (!status.engine_configured) {
      wrap.append(h('div', { class: 'song-warning' },
        icon('info'),
        h('div', {}, h('b', {}, '还差换声引擎。'), ' 设置 → 模块 → 翻唱引擎地址。引擎协议：POST multipart，audio 文件 + voice / voice_path / index_path / pitch / acapella；返回 audio/*、audio_url、audio_base64 或 path。可以先搜歌和导入音色。')))
    }

    const voiceSel = h('select', {})
    for (const v of status.voices || []) {
      voiceSel.append(h('option', { value: v.id, selected: v.id === status.default_voice || null }, v.id + (v.has_index ? ' · 带 index' : '')))
    }
    if (!(status.voices || []).length) voiceSel.append(h('option', { value: '' }, '还没有音色'))
    const pitch = h('input', { type: 'number', value: status.default_pitch || 0, min: -24, max: 24, step: 1 })
    const acapella = h('div', { class: 'switch' })
    acapella.addEventListener('click', () => acapella.classList.toggle('on'))
    const start = h('button', {
      class: 'btn btn-primary', disabled: !status.ready || null,
      onclick: async () => {
        if (!selected) return toast('先搜一首歌并选中', 'err')
        if (!voiceSel.value) return toast('先导入一个音色', 'err')
        const r = await api('/api/modules/song/jobs', { method: 'POST', body: {
          song_id: selected.id, song: selected.name, voice: voiceSel.value, account_id: currentAccount,
          pitch: Number(pitch.value) || 0, acapella: acapella.classList.contains('on'),
        } })
        if (!r.ok) return toast(r.error || '提交失败', 'err')
        toast('已经提交，开始下载/换声', 'ok')
        poll(r.job.id)
      },
    }, '开始翻唱')
    const extract = h('button', {
      class: 'btn btn-ghost', disabled: !status.extract_ready || null,
      onclick: async () => {
        if (!selected) return toast('先搜一个 B 站视频并选中', 'err')
        const r = await api('/api/modules/song/jobs', { method: 'POST', body: {
          song_id: selected.id, song: selected.name, extract_only: true, account_id: currentAccount,
        } })
        if (!r.ok) return toast(r.error || '提交失败', 'err')
        toast('开始下载 B 站音频', 'ok'); poll(r.job.id)
      },
    }, '只提取音频')
    const selectedName = h('span', { class: 'song-selected-name muted' }, '还没有选歌')

    wrap.append(h('div', { class: 'card song-controls' },
      h('div', { class: 'song-selected' }, '已选：', selectedName),
      h('div', { class: 'song-control-grid' },
        h('label', {}, '音色', voiceSel),
        h('label', {}, '变调（半音）', pitch),
        h('label', { class: 'song-switch-label' }, '清唱', acapella)),
      h('div', { class: 'song-control-foot' }, extract, start, h('span', { class: 'muted' }, '成功后会出现在下面的结果里'))))

    const jobBox = h('div', { class: 'song-job' })
    let audioUrl = ''
    const poll = async (jobId) => {
      clearTimeout(jobsTimer)
      const r = await api('/api/modules/song/jobs/' + jobId + (currentAccount ? '?account_id=' + encodeURIComponent(currentAccount) : ''))
      if (!r.ok) { jobBox.innerHTML = ''; jobBox.append(h('div', { class: 'muted' }, r.error || '任务不存在')); return }
      const j = r.job
      jobBox.innerHTML = ''
      jobBox.append(h('div', { class: 'card song-job-card' },
        h('div', { class: 'song-job-head' },
          h('div', { class: 'card-title' }, j.extract_only ? '音频提取' : '翻唱任务'),
          h('span', { class: 'tag ' + (j.status === 'done' ? 'ok' : (j.status === 'error' ? 'err' : 'warn')) }, j.stage || j.status)),
        h('div', { class: 'muted' }, `${j.song || ''} · ${j.voice || ''} · ${j.pitch > 0 ? '+' : ''}${j.pitch} 半音`),
        j.error ? h('div', { class: 'song-error' }, j.error) : null,
        j.status === 'done' && j.file ? h('div', { class: 'song-result-play' },
          h('a', { class: 'btn btn-ghost btn-sm', href: '/api/modules/song/files/' + encodeURIComponent(j.file) + (currentAccount ? '?account_id=' + encodeURIComponent(currentAccount) : ''), download: j.file }, j.extract_only ? '下载 m4a' : '下载 wav'),
          h('span', { class: 'muted' }, j.file)) : null))
      if (j.status === 'done' && j.file) {
        if (audioUrl) URL.revokeObjectURL(audioUrl)
        try {
          const res = await fetch('/api/modules/song/files/' + encodeURIComponent(j.file) + (currentAccount ? '?account_id=' + encodeURIComponent(currentAccount) : ''), { headers: panelToken ? { 'X-Panel-Token': panelToken } : {} })
          if (res.ok) {
            audioUrl = URL.createObjectURL(await res.blob())
            jobBox.append(h('audio', { controls: true, src: audioUrl, style: 'width:100%;margin-top:10px' }))
          }
        } catch {}
      }
      if (j.status === 'queued' || j.status === 'downloading' || j.status === 'converting') jobsTimer = setTimeout(() => poll(jobId), 1200)
    }

    const fileInput = h('input', { type: 'file', accept: '.pth,.index,.zip', style: 'display:none' })
    const uploadBtn = h('button', { class: 'btn btn-ghost btn-sm', onclick: () => fileInput.click() }, '导入音色')
    fileInput.addEventListener('change', async () => {
      const file = fileInput.files?.[0]
      if (!file) return
      const fd = new FormData(); fd.append('file', file); fd.append('name', file.name); fd.append('account_id', currentAccount)
      const res = await fetch('/api/modules/song/voices', {
        method: 'POST', headers: panelToken ? { 'X-Panel-Token': panelToken } : {}, body: fd,
      })
      const r = await res.json().catch(() => ({ ok: false, error: '上传失败' }))
      if (!r.ok) return toast(r.error || '导入失败', 'err')
      toast('音色已导入', 'ok'); showSong()
    })
    const voiceBox = h('div', { class: 'song-voices' })
    for (const v of status.voices || []) {
      voiceBox.append(h('div', { class: 'song-voice' },
        h('span', { class: 'song-voice-name' }, v.id),
        h('span', { class: 'tag' }, v.has_index ? 'index' : 'pth'),
        h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
          if (!confirm('删除音色「' + v.id + '」？')) return
          const r = await api('/api/modules/song/voices/' + encodeURIComponent(v.id) + (currentAccount ? '?account_id=' + encodeURIComponent(currentAccount) : ''), { method: 'DELETE' })
          if (!r.ok) return toast(r.error || '删除失败', 'err'); showSong()
        } }, '删除')))
    }
    if (!(status.voices || []).length) voiceBox.append(h('div', { class: 'muted' }, '还没有音色'))
    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'wb-card-head' }, h('div', { class: 'card-title' }, '音色库', h('span', { class: 'badge' }, String((status.voices || []).length))), uploadBtn),
      voiceBox, fileInput,
      h('div', { class: 'hint' }, '只存本机；导入你自己有权使用的 RVC 模型。')))

    wrap.append(jobBox)
    if ((status.history || []).length) {
      wrap.append(h('div', { class: 'card' },
        h('div', { class: 'card-title' }, '最近翻唱'),
        ...status.history.map(hitem => h('div', { class: 'song-history' },
          h('span', {}, hitem.song || ''),
          h('span', { class: 'muted' }, `${hitem.voice || ''} · ${fmtTime(hitem.at)}`),
          h('a', { class: 'btn btn-ghost btn-sm', href: '/api/modules/song/files/' + encodeURIComponent(hitem.file) + (currentAccount ? '?account_id=' + encodeURIComponent(currentAccount) : ''), download: hitem.file }, '下载')))))
    }
  }

  async function showFiles() {
    const permLabel = (v) => ({ owner: '只主人', whitelist: '白名单', all: '所有人', off: '关闭' }[v] || v || '—')
    view = 'files'
    wrap.innerHTML = ''
    const st = await api('/api/files/status' + accountQuery()).catch(() => ({}))
    wrap.append(pageHead('文件传输', '沙箱收发 · 默认只和主人 · 发文件需审批', h('button', {
      class: 'btn btn-ghost btn-sm', onclick: showList,
    }, icon('arrow'), '返回模块')))

    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '权限', h('span', { class: 'tag ' + (st.enabled ? 'ok' : '') }, st.enabled ? '已启用' : '关闭')),
      h('div', { class: 'kv-grid' },
        h('dt', {}, '接收'), h('dd', {}, permLabel(st.receive_from)),
        h('dt', {}, '发送'), h('dd', {}, permLabel(st.send_to)),
        h('dt', {}, '审批'), h('dd', {}, st.require_approval ? '需要主人确认' : '直接发送'),
        h('dt', {}, '上限'), h('dd', {}, `${st.max_mb || 20} MB`)),
      h('div', { class: 'hint' }, '文件只能放在 data/files 的 inbox/outbox，不能填电脑其他路径。敏感文件名和密钥内容会在上传/发送前被拒绝。'),
      h('div', { class: 'song-control-foot' },
        h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openGroupSettingsModal(['文件'], '文件参数') }, '文件权限设置'))))

    const fileInput = h('input', { type: 'file', style: 'display:none' })
    fileInput.addEventListener('change', async () => {
      const file = fileInput.files?.[0]
      if (!file) return
      const fd = new FormData(); fd.append('file', file); fd.append('account_id', currentAccount)
      const res = await fetch('/api/files/upload', {
        method: 'POST', headers: panelToken ? { 'X-Panel-Token': panelToken } : {}, body: fd,
      })
      const r = await res.json().catch(() => ({ ok: false, error: '上传失败' }))
      if (!r.ok) return toast(r.error || '上传失败', 'err')
      toast('已放进 outbox', 'ok'); showFiles()
    })
    const outbox = h('div', { class: 'file-list' })
    const files = (await api('/api/files/list?box=outbox&' + (currentAccount ? 'account_id=' + encodeURIComponent(currentAccount) : '')).catch(() => ({ files: [] }))).files || []
    for (const f of files) {
      outbox.append(h('div', { class: 'file-row' },
        h('div', { class: 'file-name' }, f.name),
        h('div', { class: 'file-meta' }, fmtBytes(f.size)),
        h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
          const r = await api('/api/files/request', { method: 'POST', body: { name: f.name, account_id: currentAccount || '' } })
          if (!r.ok) return toast(r.error || '提交失败', 'err')
          toast(r.message || '已提交审批', 'ok'); showFiles()
        } }, '请求发送')))
    }
    if (!files.length) outbox.append(h('div', { class: 'muted' }, 'outbox 还是空的'))
    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'wb-card-head' }, h('div', { class: 'card-title' }, '出站箱'), h('button', { class: 'btn btn-ghost btn-sm', onclick: () => fileInput.click() }, '上传文件')),
      outbox, fileInput))

    const pending = (await api('/api/files/pending' + accountQuery()).catch(() => ({ pending: [] }))).pending || []
    const pbox = h('div', { class: 'file-list' })
    for (const r of pending) {
      pbox.append(h('div', { class: 'file-row' },
        h('div', { class: 'file-name' }, r.name),
        h('div', { class: 'file-meta' }, r.chat_id),
        h('button', { class: 'btn btn-primary btn-sm', onclick: async () => {
          const x = await api('/api/files/pending/' + encodeURIComponent(r.id) + '/approve' + accountQuery(), { method: 'POST' })
          if (!x.ok) return toast(x.error || '发送失败', 'err')
          toast('已发送', 'ok'); showFiles()
        } }, '批准并发送'),
        h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
          await api('/api/files/pending/' + encodeURIComponent(r.id) + '/reject' + accountQuery(), { method: 'POST' }); showFiles()
        } }, '拒绝')))
    }
    if (!pending.length) pbox.append(h('div', { class: 'muted' }, '没有待审批文件'))
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '待审批', h('span', { class: 'badge' }, String(pending.length))), pbox))
  }

  async function showPermissions() {
    view = 'permissions'
    wrap.innerHTML = ''
    const data = await api('/api/permissions' + accountQuery()).catch(() => ({}))
    const profiles = data.profiles || {}
    const labels = data.cap_labels || {}
    let active = 'owner'
    wrap.append(pageHead('权限分级', '主人权限单独设置；其他私聊/群聊三档覆盖；未编辑走默认', h('button', {
      class: 'btn btn-ghost btn-sm', onclick: showList,
    }, icon('arrow'), '返回模块')))

    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '默认档'),
      h('div', { class: 'song-control-foot' },
        h('span', { class: 'muted' }, '没单独编辑的私聊/群聊使用：'),
        (() => {
          const sel = h('select', {}, ...['tier1', 'tier2', 'tier3'].map(x => h('option', { value: x, selected: data.default_tier === x || null }, `${x} · ${profiles[x]?.name || ''}`)))
          sel.addEventListener('change', async () => {
            const r = await api('/api/permissions/default' + accountQuery(), { method: 'POST', body: { tier: sel.value } })
            if (!r.ok) return toast(r.error || '保存失败', 'err'); toast('默认档已更新', 'ok')
          })
          return sel
        })())))

    const profileBox = h('div', { class: 'perm-tabs' })
    const editor = h('div', { class: 'card perm-editor' })
    const drawEditor = () => {
      const p = profiles[active] || {}
      profileBox.innerHTML = ''
      for (const [key, label] of [['owner', '主人权限'], ['tier1', '档位 1'], ['tier2', '档位 2'], ['tier3', '档位 3']]) {
        profileBox.append(h('button', {
          class: 'btn ' + (active === key ? 'btn-primary' : 'btn-ghost') + ' btn-sm',
          onclick: () => { active = key; drawEditor() },
        }, label))
      }
      editor.innerHTML = ''
      editor.append(h('div', { class: 'card-title' }, p.name || active))
      const checks = {}
      for (const [cap, label] of Object.entries(labels)) {
        const cb = h('input', { type: 'checkbox' })
        cb.checked = Boolean(p[cap])
        checks[cap] = cb
        editor.append(h('label', { class: 'perm-row' }, cb, h('span', {}, label)))
      }
      editor.append(h('button', { class: 'btn btn-primary btn-sm', onclick: async () => {
        const values = {}
        for (const [cap, cb] of Object.entries(checks)) values[cap] = cb.checked
        const r = await api('/api/permissions/profile' + accountQuery(), { method: 'POST', body: { key: active, values, name: p.name || '' } })
        if (!r.ok) return toast(r.error || '保存失败', 'err')
        toast('权限档已保存', 'ok'); showPermissions()
      } }, '保存这一档'))
    }
    drawEditor()
    wrap.append(profileBox, editor)

    const chats = data.chats || []
    const overrideBox = h('div', { class: 'perm-chat-list' })
    for (const c of chats.slice(0, 80)) {
      const sel = h('select', {})
      sel.append(h('option', { value: '' }, '默认（' + (data.default_tier || 'tier1') + '）'))
      for (const t of ['tier1', 'tier2', 'tier3']) sel.append(h('option', { value: t, selected: data.overrides?.[c.key] === t || null }, t))
      sel.addEventListener('change', async () => {
        const r = await api('/api/permissions/override' + accountQuery(), { method: 'POST', body: { key: c.key, tier: sel.value } })
        if (!r.ok) return toast(r.error || '保存失败', 'err'); toast('已更新', 'ok')
      })
      overrideBox.append(h('div', { class: 'perm-chat-row' },
        h('div', { class: 'perm-chat-name' }, c.title || c.chat_id, h('div', { class: 'muted', style: 'font-size:10px' }, c.account_id + ' · ' + c.chat_type)),
        sel))
    }
    if (!chats.length) overrideBox.append(h('div', { class: 'muted' }, '还没有聊天记录'))
    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '每个聊天单独设置', h('span', { class: 'badge' }, String(Object.keys(data.overrides || {}).length))),
      h('div', { class: 'hint' }, '不动就按默认档；主人始终用「主人权限」档。'),
      overrideBox))
  }

  await showList()
}

/* ============================================================
   平台（DSH / TRAE / CodeBuddy）
   ============================================================ */

async function openGroupSettingsModal(groups, title = '参数') {
  const [schema, valuesResp] = await Promise.all([
    api('/api/settings/schema').catch(() => ({ groups: [] })),
    api('/api/settings/values' + accQuery()).catch(() => ({ values: {} })),
  ])
  const values = valuesResp.values || {}
  const wanted = groups ? schema.groups.filter(g => groups.includes(g.name)) : schema.groups
  const mask = h('div', { class: 'modal-mask' })
  const body = h('div', { class: 'modal-body', style: 'max-height:66vh;overflow:auto' })
  for (const g of wanted) {
    body.append(h('div', { class: 'card-title', style: 'margin-top:12px' }, g.name,
      h('span', { class: 'badge' }, `${g.basic_count} 基础 · ${g.advanced_count} 高级`)))
    body.append(...renderGroupBody(g, values))
  }
  if (!wanted.length) body.append(h('div', { class: 'muted' }, '没有可显示的参数'))
  const modal = h('div', { class: 'modal', style: 'width:min(720px,92vw)' },
    h('div', { class: 'modal-head' },
      h('div', { class: 'modal-title' }, title),
      h('div', { style: 'display:flex;gap:6px;align-items:center;margin-left:auto' },
        h('button', { class: 'btn btn-ghost btn-sm', onclick: () => close() }, '关闭'))),
    body)
  mask.append(modal)
  function close() {
    mask.classList.remove('show')
    setTimeout(() => mask.remove(), 160)
  }
  mask.addEventListener('click', ev => { if (ev.target === mask) close() })
  document.body.append(mask)
  requestAnimationFrame(() => mask.classList.add('show'))
}

function goSettingsGroup(group) {
  try { localStorage.setItem('novabot-settings-group', group) } catch {}
  go('settings')
}

async function renderPlatform() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)
  wrap.append(pageHead('平台', 'DSH / TRAE / CodeBuddy 的 Agent 接入和会话映射', h('button', { class: 'btn btn-ghost btn-sm', onclick: () => openGroupSettingsModal(['平台'], '平台参数') }, '平台参数')))

  const [vals, sess, detected] = await Promise.all([
    api('/api/settings/values').catch(() => ({ values: {} })),
    api('/api/platform/sessions?limit=50').catch(() => ({ sessions: [] })),
    api('/api/platform/detect').catch(() => null),
  ])
  const v = vals.values || {}
  const on = !!v['llm.agent_enabled'] || !!detected?.connected
  const type = v['llm.agent_type'] || 'dsh'
  const endpoint = v['llm.agent_endpoint'] || detected?.dsh?.chat_endpoint || ''
  const sessions = sess.sessions || []
  const dsh = (detected && detected.dsh) || {}
  const dshStatus = dsh.status === 'connected' ? '已自动连接'
    : dsh.status === 'online' ? '在线（未自动连接）'
      : dsh.status === 'auth' ? '需要鉴权' : '离线'

  wrap.append(h('div', { class: 'stat-grid' },
    stat('Agent', on ? '开' : '关'),
    stat('平台', type),
    stat('会话', sessions.length),
    stat('地址', endpoint ? '已填' : '未填'),
  ))

  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'wb-card-head' },
      h('div', { class: 'card-title' }, '自动检测'),
      h('button', {
        class: 'btn btn-ghost btn-sm',
        onclick: async () => {
          await api('/api/platform/detect', { method: 'POST', body: {} })
          toast('已重新检测', 'ok'); go('platform')
        },
      }, '立即检测')),
    h('div', { class: 'wb-row' },
      h('div', { class: 'wb-row-main' },
        h('div', { class: 'wb-row-title' }, 'DSH 桥接 ', h('span', { class: 'tag ' + (dsh.healthy ? 'ok' : '') }, dshStatus)),
        h('div', { class: 'wb-row-sub' }, `${dsh.url || ''} · ${dsh.error || (dsh.status === 'connected' ? 'Agent 已接上' : '等待插件')}`)),
      h('div', { class: 'wb-row-side' }, h('span', { class: 'muted' }, '自动连接：' + (v['platform.auto_connect'] ? '开' : '关')))),
    h('div', { class: 'wb-row' },
      h('div', { class: 'wb-row-main' },
        h('div', { class: 'wb-row-title' }, 'TRAE / CodeBuddy ', h('span', { class: 'tag' }, 'MCP')),
        h('div', { class: 'wb-row-sub' }, `最近活动：TRAE ${detected?.trae?.sessions || 0} · CodeBuddy ${detected?.codebuddy?.sessions || 0} 个会话`)),
      h('div', { class: 'wb-row-side' }, h('span', { class: 'muted' }, '插件调用即连接'))),
  ))

  const card = (title, name, desc, body) => h('div', { class: 'card' },
    h('div', { class: 'wb-card-head' },
      h('div', { class: 'card-title' }, title),
      h('span', { class: 'tag ' + (type === name && on ? 'ok' : '') }, type === name && on ? '当前' : '待配')),
    h('div', { class: 'page-desc', style: 'margin:4px 0 8px' }, desc),
    body,
  )

  wrap.append(card('DSH', 'dsh', '把 DSH 当前模型开成本地 HTTP；Novabot 用 /novabot/api/chat。', h('div', {},
    h('div', { class: 'hint' }, '插件：dsh-plugin/novabot（拷到 DSH plugins 后重启 DSH）· 每个 bot 一个工作区 Novabot·标签名'),
    h('div', { class: 'hint' }, '如果显示 HTTP 404 但已经安装：DSH 主进程还没重启；重启 DSH 后再点「立即检测」。'),
    h('div', { class: 'hint' }, '设置 → 模型：Agent 类型选 DSH，地址填 http://127.0.0.1:<端口>/novabot/api/chat，开启「用外部 Agent 平台」'),
    h('div', { class: 'hint' }, '群里默认只回 @ 和唤醒词；私聊必回。改规则去 设置 → 回复。'),
    h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
      const r = await api('/api/system/model/test', { method: 'POST', body: { account_id: currentAccount || '' } })
      if (!r.ok) return toast(r.error || '模型测试失败', 'err')
      toast(`模型回复：${r.reply || '空'}（${r.ms || 0}ms）`, 'ok')
    } }, '测试模型'),
    h('button', { class: 'btn btn-ghost btn-sm', onclick: () => goSettingsGroup('模型') }, '去模型设置'),
  )))

  const mcp = `{"mcpServers":{"novabot":{"command":"D:\\\\Novabot\\\\.venv\\\\Scripts\\\\python.exe","args":["-m","app.mcp_server"],"env":{"NOVABOT_DATABASE__PATH":"D:\\\\Novabot\\\\data\\\\novabot.db"}}}}`
  wrap.append(card('TRAE / CodeBuddy', 'trae', '通过 MCP 直连 Novabot 的记忆和人格；不需要 Novabot 监听新端口。', h('div', {},
    h('pre', { class: 'mcp-code' }, mcp),
    h('div', { class: 'hint' }, '把这段放进 TRAE / CodeBuddy 的 MCP 配置；Python 用项目 .venv 的绝对路径。'),
  )))

  const table = h('div', { class: 'table-wrap' })
  if (!sessions.length) {
    table.append(h('div', { class: 'wb-empty' }, '还没有外部会话映射。Agent 第一次回复或 MCP 调用后会出现在这里。'))
  } else {
    table.append(h('table', {},
      h('thead', {}, h('tr', {}, h('th', {}, '平台'), h('th', {}, '外部会话'), h('th', {}, '映射到'), h('th', { class: 'num' }, '最后活跃'))),
      h('tbody', {}, ...sessions.map(s => h('tr', {},
        h('td', {}, h('span', { class: 'tag' }, s.platform)),
        h('td', { class: 'muted', style: 'font-family:var(--font-mono);font-size:11.5px' }, s.session_id),
        h('td', { class: 'muted', style: 'font-family:var(--font-mono);font-size:11.5px' }, s.chat_id),
        h('td', { class: 'num' }, fmtTime(s.last_active)),
      )))))
  }
  wrap.append(h('div', { class: 'card' },
    h('div', { class: 'wb-card-head' }, h('div', { class: 'card-title' }, '会话映射'),
      h('button', { class: 'btn btn-ghost btn-sm', onclick: () => go('platform') }, '刷新')),
    table))
}
