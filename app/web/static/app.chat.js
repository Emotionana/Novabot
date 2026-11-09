/* 聊天中心：隐私 / 权限 / 表情包 / 白名单 / 真实性 / 回复 */
async function renderChat() {
  const main = $('#main')
  main.innerHTML = ''
  const wrap = h('div', { class: 'main-inner' })
  main.append(wrap)
  let tab = 'overview'
  const account = () => currentAccount || ''
  const q = (s) => encodeURIComponent(account())
  const settingsValues = async () => (await api('/api/settings/values?account_id=' + q()).catch(() => ({ values: {} }))).values || {}

  const tabs = (v = {}) => {
    const names = [['overview', '概览'], ['privacy', '隐私'], ['voice', '语音'], ['media', '媒体'], ['session', '会话'], ['permissions', '聊天权限'], ['stickers', '表情包'], ['whitelist', '白名单'], ['truth', '真实性'], ['reply', '回复']]
      .filter(([id]) => id === 'voice' ? v['voice.enabled'] !== false : id === 'media' ? v['media.enabled'] !== false : id === 'stickers' ? v['stickers.enabled'] !== false : true)
    return h('div', { class: 'chat-tabs' }, ...names.map(([id, label]) =>
      h('button', { class: 'btn ' + (tab === id ? 'btn-primary' : 'btn-ghost') + ' btn-sm', onclick: () => { tab = id; draw() } }, label)))
  }

  const drawOverview = async () => {
    const [st, perms, stickers, vals] = await Promise.all([
      api('/api/gateway/status').catch(() => ({ accounts: [] })),
      api('/api/permissions' + accountQuery()).catch(() => ({})),
      api('/api/stickers/status?account_id=' + q()).catch(() => ({})),
      settingsValues(),
    ])
    const row = (st.accounts || []).find(a => a.id === account()) || (st.accounts || [])[0] || {}
    wrap.append(h('div', { class: 'chat-hero' },
      h('div', {}, h('div', { class: 'chat-hero-title' }, '聊天中心'), h('div', { class: 'muted' }, '所有会改变“她怎么和你聊天”的设置，集中在这里。')),
      h('div', { class: 'chat-hero-state' }, h('span', { class: 'dot ' + (row.connected ? 'on' : 'off') }), row.connected ? '账号在线' : '账号离线')))
    wrap.append(h('div', { class: 'stat-grid' },
      stat('隐私隔离', vals['privacy.enabled'] ? '开' : '关'),
      stat('默认权限', perms.default_tier || 'tier1'),
      stat('表情库', String(stickers.count || 0)),
      stat('自动表情', stickers.auto_send ? '开' : '关')))
    const quick = h('div', { class: 'chat-quick' })
    for (const [id, title, desc] of [
      ['privacy', '隐私隔离', '私聊、群聊、资料、文档的边界'],
      ['permissions', '聊天权限', '主人权限 + 三档 + 每聊天覆盖'],
      ['stickers', '表情包', '收藏、上传、发送和自动配图'],
      ['whitelist', '白名单', '谁可以私聊、哪些群可以接入'],
      ['truth', '真实性', '不知道就说不知道，工作不谎报'],
      ['reply', '回复方式', '群里怎么回、拆条和生活主动'],
    ]) quick.append(h('button', { class: 'chat-quick-card', onclick: () => { tab = id; draw() } },
      h('div', { class: 'chat-quick-title' }, title), h('div', { class: 'muted' }, desc)))
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '常用'), quick))
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '模块开关'),
      toggleRow('voice.enabled', '语音', '关闭后隐藏语音设置页签', vals['voice.enabled']),
      toggleRow('stickers.enabled', '表情包', '关闭后隐藏表情包页签', vals['stickers.enabled']),
      toggleRow('media.enabled', '媒体识别', '关闭后隐藏媒体页签', vals['media.enabled'])))
    const adv = await advancedPanel(['聊天', '输出'], '聊天与输出高级参数')
    if (adv) wrap.append(adv)
  }

  const saveSetting = async (key, value) => {
    const r = await api('/api/settings/set', { method: 'POST', body: { key, value, account_id: account() } })
    if (!r.ok) { toast(r.error || '保存失败', 'err'); return false }
    toast('已保存', 'ok'); return true
  }
  const chatSyncCard = async () => {
    const meta = await api('/api/settings/syncable' + accountQuery()).catch(() => ({ targets: [] }))
    const targets = meta.targets || []
    const selected = new Set(targets.map(t => t.id))
    const checks = new Map()
    const all = h('input', { type: 'checkbox' })
    all.checked = targets.length > 0
    const box = h('div', { class: 'card', style: 'margin-top:10px' },
      h('div', { class: 'card-title' }, '同步到哪些 bot'),
      h('label', { class: 'perm-row' }, all, h('span', {}, '全选'),
        h('span', { class: 'muted', style: 'margin-left:auto' }, `${targets.length} 个其他 bot`)))
    const syncAll = () => { all.checked = targets.length > 0 && selected.size === targets.length }
    all.addEventListener('change', () => {
      selected.clear()
      if (all.checked) for (const t of targets) selected.add(t.id)
      for (const [id, cb] of checks) cb.checked = selected.has(id)
    })
    for (const target of targets) {
      const cb = h('input', { type: 'checkbox' })
      cb.checked = selected.has(target.id)
      cb.addEventListener('change', () => {
        if (cb.checked) selected.add(target.id)
        else selected.delete(target.id)
        syncAll()
      })
      checks.set(target.id, cb)
      box.append(h('label', { class: 'perm-row' }, cb,
        h('span', {}, target.name || target.id),
        h('span', { class: 'muted', style: 'margin-left:auto;font-family:var(--font-mono);font-size:10px' }, target.id)))
    }
    const btn = h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
      const ids = [...selected]
      if (!ids.length) return toast('先勾选要同步的 bot', 'err')
      const names = targets.filter(t => selected.has(t.id)).map(t => t.name || t.id).join('、')
      if (!confirm(`把当前 bot 的聊天设置同步到：${names}？\n只同步回复、表情、媒体、隐私、文件、聊天、输出和生活行为，不会同步接口、端口、凭据和 TTS。`)) return
      btn.disabled = true
      const r = await api('/api/settings/sync', { method: 'POST', body: {
        account_id: account(), targets: ids,
        groups: ['回复', '表情', '真实', '媒体', '文件', '隐私', '聊天', '输出', '生活'],
      } }).catch(e => ({ ok: false, error: e.message }))
      btn.disabled = false
      if (!r.ok) return toast(r.error || '同步失败', 'err')
      toast(`已同步 ${r.key_count || 0} 项到 ${(r.targets || []).length} 个 bot`, 'ok')
    } }, '同步到其他 bot')
    return h('div', { class: 'card chat-sync-card' },
      h('div', { class: 'card-title' }, '设置同步',
        h('span', { class: 'badge' }, `${targets.length} 个目标`)),
      h('div', { class: 'hint' }, '把当前账号的聊天行为复制到其他 bot；不会复制接口地址、端口、凭据、TTS 和人格卡。'),
      h('div', { class: 'plugin-actions' }, btn),
      box)
  }
  const toggleRow = (key, label, desc, checked) => {
    const cb = h('input', { type: 'checkbox' }); cb.checked = Boolean(checked)
    cb.addEventListener('change', async () => { if (await saveSetting(key, cb.checked)) draw() })
    return h('label', { class: 'perm-row' }, cb, h('span', {}, label),
      h('span', { class: 'muted', style: 'margin-left:auto;font-size:10px' }, desc))
  }
  const selectRow = (key, label, value, options) => {
    const sel = h('select', {})
    for (const [v, text] of options) sel.append(h('option', { value: v, selected: value === v || null }, text))
    sel.addEventListener('change', () => saveSetting(key, sel.value))
    return h('label', { class: 'perm-row' }, h('span', { style: 'min-width:110px' }, label), sel)
  }
  const numberRow = (key, label, desc, value, min, max, step = 1, unit = '秒') => {
    const input = h('input', { type: 'number', class: 'wide', min, max, step, value: value ?? '' , style: 'width:110px' })
    input.addEventListener('change', async () => {
      const n = Math.max(min, Math.min(max, Number(input.value) || 0))
      input.value = n
      if (await saveSetting(key, n)) draw()
    })
    return h('label', { class: 'perm-row' }, h('span', {}, label),
      h('span', { class: 'muted', style: 'margin-left:auto;font-size:10px' }, desc),
      input, h('span', { class: 'muted' }, unit))
  }
  const advancedPanel = async (groups, title = '高级参数', exclude = []) => {
    const [schema, values] = await Promise.all([
      api('/api/settings/schema').catch(() => ({ groups: [] })),
      settingsValues(),
    ])
    const skip = new Set(exclude)
    const blocks = []
    for (const g of (schema.groups || [])) {
      if (!groups.includes(g.name)) continue
      const adv = (g.params || []).filter(p => p.level === 'advanced' && !skip.has(p.key))
      if (!adv.length) continue
      blocks.push(h('div', { class: 'advanced-group-name' }, g.name), ...adv.map(p => renderField(p, values)))
    }
    if (!blocks.length) return null
    const head = h('div', { class: 'collapse-head' }, icon('chevron'), h('span', {}, title))
    const body = h('div', { class: 'collapse-body' }, blocks)
    head.addEventListener('click', () => {
      head.classList.toggle('open')
      body.classList.toggle('open')
    })
    return h('div', { class: 'advanced-panel' }, head, body)
  }

  const drawPrivacy = async () => {
    const v = await settingsValues()
    const box = h('div', { class: 'card' }, h('div', { class: 'card-title' }, '隐私隔离'),
      toggleRow('privacy.enabled', '启用隐私隔离', '群聊/别人私聊读不到主人私聊内容', v['privacy.enabled']),
      toggleRow('privacy.cross_private', '允许同一个人跨私聊回忆', '默认关；只限同一个人自己的私聊', v['privacy.cross_private']),
      toggleRow('privacy.profile_detail', '群聊带人物资料', '允许查昵称/别名/公开画像；不包含聊天原文', v['privacy.profile_detail']),
      toggleRow('privacy.documents', '带知识文档', '默认关；文档没有会话来源', v['privacy.documents']),
      toggleRow('privacy.block_contacts', '拦截联系方式', '手机号/邮箱等', v['privacy.block_contacts']),
      selectRow('privacy.leak_action', '检测到泄露', v['privacy.leak_action'] || 'refuse', [['refuse', '改成拒绝回答'], ['off', '只记录不拦']]),
      h('div', { class: 'hint' }, '这些开关直接作用于当前账号，保存即生效。'))
    const adv = await advancedPanel(['隐私'])
    if (adv) box.append(adv)
    wrap.append(box)
  }

  const drawVoice = async () => {
    const v = await settingsValues()
    const min = h('input', { type: 'number', min: '1', max: '100', value: String(Number(v['voice.min_chars'] || 1)) })
    min.addEventListener('change', () => saveSetting('voice.min_chars', Math.max(1, Number(min.value) || 1)))
    const max = h('input', { type: 'number', min: '10', max: '500', value: String(Number(v['voice.max_chars'] || 80)) })
    max.addEventListener('change', () => saveSetting('voice.max_chars', Math.max(10, Number(max.value) || 80)))
    const budget = h('input', { type: 'number', min: '0', max: '120', step: '0.5', value: String(Number(v['voice.reply_budget_sec'] || 10)) })
    budget.addEventListener('change', () => saveSetting('voice.reply_budget_sec', Math.max(0, Number(budget.value) || 0)))
    const card = h('div', { class: 'card' }, h('div', { class: 'card-title' }, '语音发送'),
      toggleRow('voice.enabled', '启用语音', '当前账号独立开关', v['voice.enabled']),
      selectRow('voice.reply_mode', '发送方式', v['voice.reply_mode'] || 'both', [
        ['both', '语音+文字一起发'], ['voice', '只发语音'], ['mixed', '多条里部分转语音']]),
      h('label', { class: 'perm-row' }, h('span', { style: 'min-width:110px' }, '转换概率'), h('b', { style: 'color:var(--accent)' }, String(Number(v['voice.convert_rate'] ?? 100)) + '%'), h('button', { class: 'btn btn-ghost btn-sm', onclick: () => go('voice') }, '去语音页调整')),
      selectRow('voice.trigger_mode', '什么时候允许语音', v['voice.trigger_mode'] || 'asked', [
        ['asked', '对方要求时'], ['always', '每条都考虑'], ['boss_only', '只对主人'], ['never', '从不']]),
      h('div', { class: 'form-grid' }, h('label', {}, '最少字数 ', min), h('label', {}, '最多字数 ', max), h('label', {}, '合成预算（秒）', budget)),
      h('div', { class: 'hint' }, '转换概率在「语音」页调整；对方明确要语音时优先发语音。当前引擎：' + (v['voice.engine_url'] || '按语音页配置')),
      h('button', { class: 'btn btn-primary btn-sm', onclick: () => go('voice') }, '打开语音库 / 参考音'))
    const adv = await advancedPanel(['语音'])
    if (adv) card.append(adv)
    wrap.append(card)
  }

  const drawMedia = async () => {
    const v = await settingsValues()
    const textRow = (key, label, hint = "", placeholder = "") => {
      const inp = h('input', { type: 'text', class: 'wide', value: String(v[key] ?? ''), placeholder })
      inp.addEventListener('change', () => saveSetting(key, inp.value.trim()))
      return h('div', { class: 'persona-field' }, h('label', {}, label), inp, hint ? h('div', { class: 'hint' }, hint) : null)
    }
    const secretRow = (key, label, hint = "") => {
      const masked = v[key] === '••••••'
      const inp = h('input', { type: 'password', class: 'wide', value: masked ? '' : String(v[key] ?? ''), placeholder: masked ? '已设置，留空不改' : '' })
      inp.addEventListener('change', () => { if (inp.value.trim() || !masked) saveSetting(key, inp.value.trim()) })
      return h('div', { class: 'persona-field' }, h('label', {}, label), inp, hint ? h('div', { class: 'hint' }, hint) : null)
    }
    const numberRow = (key, label, hint = "", min = 0, max = 999) => {
      const inp = h('input', { type: 'number', value: String(Number(v[key] ?? 0)), min, max })
      inp.addEventListener('change', () => saveSetting(key, Math.max(min, Math.min(max, Number(inp.value) || 0))))
      return h('label', { class: 'perm-row' }, h('span', { style: 'min-width:120px' }, label), inp, hint ? h('span', { class: 'muted' }, hint) : null)
    }

    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '媒体理解'),
      toggleRow('media.enabled', '总开关', '关掉后图片/视频/语音条都不读', v['media.enabled']),
      toggleRow('media.image_enabled', '读图片 / 表情包', 'DSH 模型直接分析，失败自动回退', v['media.image_enabled']),
      toggleRow('media.voice_enabled', '读语音条', '先转文字再交给聊天模型', v['media.voice_enabled']),
      toggleRow('media.video_enabled', '识别视频', '抽帧 + 音轨转写', v['media.video_enabled']),
      toggleRow('media.vision_auto', '自动检索视觉模型', '打开媒体页时自动检索本机 Ollama/LM Studio 模型', v['media.vision_auto']),
      h('div', { class: 'hint' }, '这些开关按当前账号保存。')))

    let detectedModels = []
    if (v['media.vision_auto'] !== false) {
      try {
        const found = await api('/api/system/media/models')
        detectedModels = found.models || []
      } catch {}
    }

    const saveModel = async (kind, value) => {
      let item = {}
      try { item = JSON.parse(value) } catch {}
      const mode = String(item.mode || 'dsh')
      const pairs = [[`media.${kind}_vision_mode`, mode]]
      if (mode === 'custom') {
        pairs.push([`media.${kind}_vision_base_url`, item.base_url || ''])
        pairs.push([`media.${kind}_vision_model`, item.model || ''])
      } else {
        pairs.push([`media.${kind}_vision_base_url`, ''])
        pairs.push([`media.${kind}_vision_model`, ''])
      }
      for (const [key, val] of pairs) {
        const r = await api('/api/settings/set', { method: 'POST', body: { key, value: val, account_id: account() } })
        if (!r.ok) return toast(r.error || '保存失败', 'err')
      }
      toast(kind === 'image' ? '图片模型已更新' : '视频模型已更新', 'ok')
      draw()
    }

    const modelSelect = (kind, label, mode, base, model) => {
      const options = [{ mode: 'dsh', label: 'DSH 当前模型（直接看图）' }, { mode: 'chat', label: '跟聊天模型一样' }]
      for (const m of detectedModels) {
        options.push({ mode: 'custom', base_url: m.base_url, model: m.model, label: m.label })
      }
      options.push({ mode: 'off', label: kind === 'image' ? '不分析图片' : '不分析视频画面' })
      const curMode = String(mode || 'dsh')
      const curBase = String(base || '')
      const curModel = String(model || '')
      let matched = options.find(o => o.mode === curMode && (o.mode !== 'custom' || (o.base_url === curBase && o.model === curModel)))
      if (!matched && curMode === 'custom' && curModel) {
        matched = { mode: 'custom', base_url: curBase, model: curModel, label: `当前：${curModel} @ ${curBase}` }
        options.push(matched)
      }
      if (!matched) matched = options[0]
      const sel = h('select', { style: 'min-width:260px' },
        ...options.map(o => h('option', { value: JSON.stringify(o), selected: o === matched || null }, o.label)))
      sel.value = JSON.stringify(matched)
      sel.addEventListener('change', () => saveModel(kind, sel.value))
      return h('label', { class: 'perm-row' },
        h('span', { style: 'min-width:120px' }, label), sel)
    }

    const detectBtn = h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
      detectBtn.disabled = true
      const r = await api('/api/system/media/models').catch(e => ({ ok: false, error: e.message }))
      detectBtn.disabled = false
      if (!r.ok) return toast(r.error || '检索失败', 'err')
      toast(`检索到 ${(r.models || []).length} 个本地视觉模型`, 'ok')
      draw()
    } }, '重新检索模型')

    wrap.append(h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '图片 / 视频分析模型'),
      modelSelect('image', '图片分析模型',
        v['media.image_vision_mode'] || v['media.vision_mode'] || 'dsh',
        v['media.image_vision_base_url'] || v['media.vision_base_url'] || '',
        v['media.image_vision_model'] || v['media.vision_model'] || ''),
      modelSelect('video', '视频画面模型',
        v['media.video_vision_mode'] || v['media.vision_mode'] || 'dsh',
        v['media.video_vision_base_url'] || v['media.vision_base_url'] || '',
        v['media.video_vision_model'] || v['media.vision_model'] || ''),
      h('div', { class: 'hint' }, detectedModels.length
        ? `已检索到 ${detectedModels.length} 个本地视觉模型；DSH 始终可用。`
        : '没有检索到本地视觉模型；仍可用 DSH。'),
      h('div', { class: 'plugin-actions' }, detectBtn),
      h('div', { class: 'hint' }, '图片和视频可以分别选模型；选 DSH 就直接交给当前 DSH 模型，失败才回退到本地接口。'),
      textRow('media.vision_base_url', '回退接口地址', 'DSH 模式不用填；图片/视频没有单独配置时使用这个回退接口', 'http://127.0.0.1:8000/v1'),
      textRow('media.vision_model', '回退模型名', '图片/视频没有单独配置时使用', '例如 qwen2.5-vl-7b-instruct'),
      secretRow('media.vision_api_key', '回退接口密钥', '留空表示不需要')))
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '语音条转文字（Whisper）'),
      textRow('media.asr_base_url', '语音识别接口', 'OpenAI Whisper 兼容 base_url；留空则用本机 faster-whisper', 'http://127.0.0.1:8000/v1'),
      textRow('media.asr_local_python', '本地识别 Python', '本机 faster-whisper 的 python.exe', 'D:\\...\\runtime\\python.exe'),
      textRow('media.asr_local_script', '本地识别脚本', '接收音频路径、输出文字的脚本', 'D:\\...\\asr_whisper.py'),
      textRow('media.asr_model', '语音识别模型', '', 'whisper-1'),
      textRow('media.asr_language', '识别语言', '中文一般填 zh', 'zh'),
      secretRow('media.asr_api_key', '语音识别密钥', '留空表示不需要'),
      h('div', { class: 'hint' }, '语音条先转成文字，再作为「语音转写」附在消息里。接口留空时使用上面的本机 faster-whisper。')))
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '视频 / 文件'),
      textRow('media.ffmpeg_path', 'ffmpeg 路径', '没装 ffmpeg 就填绝对路径', 'ffmpeg'),
      numberRow('media.video_frames', '视频抽帧数', '1–6 张', 1, 6),
      numberRow('media.max_mb', '单文件上限（MB）', '', 1, 200),
      h('div', { class: 'hint' }, '视频识别 = ffmpeg 抽帧 + 视觉模型看图 + 音轨 Whisper 转写；任一接口没配就会在回复里说明。')))
    const adv = await advancedPanel(['媒体', '文件'], '媒体与文件高级参数')
    if (adv) wrap.append(adv)
  }

  const drawSession = async () => {
    const v = await settingsValues()
    const numInput = (key, def, min, max) => {
      const inp = h('input', { type: 'number', min: String(min), max: String(max), value: String(Number(v[key] ?? def)) })
      inp.addEventListener('change', () => saveSetting(key, Math.max(min, Math.min(max, Number(inp.value) || 0))))
      return inp
    }
    const pc = numInput('llm.private_session_compact_turns', 30, 0, 500)
    const pr = numInput('llm.private_session_rotate_turns', 0, 0, 1000)
    const gc = numInput('llm.group_session_compact_turns', 0, 0, 500)
    const gr = numInput('llm.group_session_rotate_turns', 20, 0, 1000)
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, 'DSH 会话策略'),
      h('div', { class: 'muted' }, '私聊和群聊分开设置；每个私聊、每个群聊各用独立 DSH 会话。'),
      h('div', { class: 'card-subtitle', style: 'margin:12px 0 4px' }, '私聊'),
      h('label', { class: 'perm-row' }, h('span', { style: 'min-width:170px' }, '多少轮自动压缩'), pc, h('span', {}, '轮'),
        h('span', { class: 'muted' }, '0 = 关闭')),
      h('label', { class: 'perm-row' }, h('span', { style: 'min-width:170px' }, '多少轮自动换会话'), pr, h('span', {}, '轮'),
        h('span', { class: 'muted' }, '默认 0，不换会话；0 = 关闭')),
      h('div', { class: 'card-subtitle', style: 'margin:14px 0 4px' }, '群聊'),
      h('label', { class: 'perm-row' }, h('span', { style: 'min-width:170px' }, '多少轮自动压缩'), gc, h('span', {}, '轮'),
        h('span', { class: 'muted' }, '0 = 关闭')),
      h('label', { class: 'perm-row' }, h('span', { style: 'min-width:170px' }, '多少轮自动换会话'), gr, h('span', {}, '轮'),
        h('span', { class: 'muted' }, '0 = 关闭')),
      h('div', { class: 'hint' }, '长期记忆仍由 Novabot 保存，不依赖 DSH 会话。')))
  }

  const drawPermissions = async () => {
    const data = await api('/api/permissions' + accountQuery()).catch(() => ({}))
    const profiles = data.profiles || {}
    const customTiers = data.custom_tiers || []
    const tierKeys = ['tier1', ...customTiers]
    let active = 'owner'
    const box = h('div', { class: 'perm-tabs' })
    const editor = h('div', { class: 'card perm-editor' })
    const tierLabel = (key) => profiles[key]?.name || key
    const drawEditor = () => {
      box.innerHTML = ''
      for (const key of ['owner', 'admin', 'tier1', ...customTiers])
        box.append(h('button', { class: 'btn ' + (active === key ? 'btn-primary' : 'btn-ghost') + ' btn-sm', onclick: () => { active = key; drawEditor() } }, tierLabel(key)))
      const p = profiles[active] || {}
      const checks = {}
      editor.innerHTML = ''
      editor.append(h('div', { class: 'card-title' }, p.name || active))
      let nameInput = null
      if (customTiers.includes(active)) {
        nameInput = h('input', { type: 'text', value: String(p.name || ''), style: 'width:220px' })
        editor.append(h('label', { class: 'perm-row' }, h('span', { style: 'min-width:120px' }, '档位名称'), nameInput))
      }
      for (const [cap, label] of Object.entries(data.cap_labels || {})) {
        const cb = h('input', { type: 'checkbox' }); cb.checked = Boolean(p[cap]); checks[cap] = cb
        editor.append(h('label', { class: 'perm-row' }, cb, h('span', {}, label)))
      }
      const actions = h('div', { class: 'plugin-actions' },
        h('button', { class: 'btn btn-primary btn-sm', onclick: async () => {
          const values = {}; for (const [k, cb] of Object.entries(checks)) values[k] = cb.checked
          const r = await api('/api/permissions/profile' + accountQuery(), { method: 'POST', body: {
            key: active, values, name: nameInput ? nameInput.value.trim() : (p.name || ''),
          } })
          if (!r.ok) return toast(r.error || '保存失败', 'err'); toast('权限档已保存', 'ok'); draw()
        } }, '保存这一档'))
      if (customTiers.includes(active)) {
        actions.append(h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
          if (!confirm(`删除「${p.name || active}」这个档位？使用它的账号会回到默认。`)) return
          const r = await api('/api/permissions/tier/delete' + accountQuery(), { method: 'POST', body: { key: active } })
          if (!r.ok) return toast(r.error || '删除失败', 'err')
          toast('档位已删除', 'ok'); active = 'tier1'; draw()
        } }, '删除这个档位'))
      }
      editor.append(actions)
    }
    drawEditor()

    const defaultCard = h('div', { class: 'card' },
      h('div', { class: 'card-title' }, '默认档位'),
      h('label', { class: 'perm-row' }, h('span', {}, '没有单独设置的聊天使用：'), h('b', {}, '默认')))

    const customRows = h('div', { class: 'perm-chat-list' })
    for (const key of customTiers) {
      customRows.append(h('div', { class: 'perm-chat-row' },
        h('div', { class: 'perm-chat-name' }, tierLabel(key), h('div', { class: 'muted', style: 'font-size:10px' }, key)),
        h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
          if (!confirm(`删除「${tierLabel(key)}」？使用它的账号会回到默认。`)) return
          const r = await api('/api/permissions/tier/delete' + accountQuery(), { method: 'POST', body: { key } })
          if (!r.ok) return toast(r.error || '删除失败', 'err'); toast('档位已删除', 'ok'); draw()
        } }, '删除')))
    }
    if (!customTiers.length) customRows.append(h('div', { class: 'muted' }, '还没有自定义档位'))
    const tierName = h('input', { type: 'text', placeholder: '新档位名称', style: 'width:180px' })
    const addTier = h('button', { class: 'btn btn-primary btn-sm', onclick: async () => {
      const r = await api('/api/permissions/tier/add' + accountQuery(), { method: 'POST', body: { name: tierName.value.trim() } })
      if (!r.ok) return toast(r.error || '添加失败', 'err'); toast('档位已添加', 'ok'); draw()
    } }, '添加档位')
    const tierCard = h('div', { class: 'card' }, h('div', { class: 'card-title' }, '档位管理'),
      h('div', { class: 'hint' }, '主人权限、管理员权限、默认固定；其他档位可以自己添加和删除。'),
      customRows, h('div', { class: 'perm-chat-row' }, tierName, addTier))

    const accountRows = h('div', { class: 'perm-chat-list' })
    const ownerQq = String(data.owner_qq || '')
    accountRows.append(h('div', { class: 'perm-chat-row' },
      h('div', { class: 'perm-chat-name' }, '主人账号', h('div', { class: 'muted', style: 'font-size:10px' }, ownerQq || '未设置主人 QQ')),
      h('span', { class: 'tag ok' }, '主人权限（任何聊天）')))
    for (const [qq, tier] of Object.entries(data.users || {})) {
      const usel = h('select', {})
      for (const x of tierKeys) usel.append(h('option', { value: x, selected: tier === x || null }, tierLabel(x)))
      usel.addEventListener('change', async () => {
        const r = await api('/api/permissions/user' + accountQuery(), { method: 'POST', body: { qq, tier: usel.value } })
        if (!r.ok) return toast(r.error || '保存失败', 'err'); toast('账号权限已更新', 'ok')
      })
      const del = h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
        const r = await api('/api/permissions/user' + accountQuery(), { method: 'POST', body: { qq, tier: '' } })
        if (!r.ok) return toast(r.error || '删除失败', 'err'); toast('已删除账号权限', 'ok'); draw()
      } }, '删除')
      accountRows.append(h('div', { class: 'perm-chat-row' },
        h('div', { class: 'perm-chat-name' }, qq), h('div', { style: 'display:flex;gap:6px;align-items:center' }, usel, del)))
    }
    const newQq = h('input', { type: 'text', placeholder: 'QQ 号', style: 'width:130px' })
    const newTier = h('select', {})
    for (const x of tierKeys) newTier.append(h('option', { value: x }, tierLabel(x)))
    const addUser = h('button', { class: 'btn btn-primary btn-sm', onclick: async () => {
      const r = await api('/api/permissions/user' + accountQuery(), { method: 'POST', body: { qq: newQq.value.trim(), tier: newTier.value } })
      if (!r.ok) return toast(r.error || '保存失败', 'err'); toast('账号权限已保存', 'ok'); draw()
    } }, '添加账号权限')
    const accountCard = h('div', { class: 'card' }, h('div', { class: 'card-title' }, '按账号权限'),
      h('div', { class: 'hint' }, '权限跟账号走，不跟私聊/群聊走；主人在任何群都保持主人权限。'),
      accountRows, h('div', { class: 'perm-chat-row' }, newQq, newTier, addUser))

    const adminRows = h('div', { class: 'perm-chat-list' })
    for (const qq of (data.admins || [])) {
      adminRows.append(h('div', { class: 'perm-chat-row' },
        h('div', { class: 'perm-chat-name' }, qq, h('div', { class: 'muted', style: 'font-size:10px' }, '管理员')),
        h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
          const r = await api('/api/permissions/admin' + accountQuery(), { method: 'POST', body: { qq, enabled: false } })
          if (!r.ok) return toast(r.error || '删除失败', 'err'); toast('已移出管理员', 'ok'); draw()
        } }, '移除')))
    }
    if (!(data.admins || []).length) adminRows.append(h('div', { class: 'muted' }, '还没有管理员'))
    const adminQq = h('input', { type: 'text', placeholder: 'QQ 号', style: 'width:130px' })
    const addAdmin = h('button', { class: 'btn btn-primary btn-sm', onclick: async () => {
      const r = await api('/api/permissions/admin' + accountQuery(), { method: 'POST', body: { qq: adminQq.value.trim(), enabled: true } })
      if (!r.ok) return toast(r.error || '保存失败', 'err'); toast('已加管理员', 'ok'); draw()
    } }, '添加管理员')
    const adminCard = h('div', { class: 'card' }, h('div', { class: 'card-title' }, '管理员名单'),
      h('div', { class: 'hint' }, '管理员默认算聊天白名单，不需要再加私聊白名单；默认只读本地文件，不能改/删/跑命令，其他权限默认开。'),
      adminRows, h('div', { class: 'perm-chat-row' }, adminQq, addAdmin))

    const chats = data.chats || []
    const ov = h('div', { class: 'perm-chat-list' })
    for (const c of chats.slice(0, 80)) {
      const sel = h('select', {})
      sel.append(h('option', { value: '' }, '默认'))
      for (const x of tierKeys) sel.append(h('option', { value: x, selected: data.overrides?.[c.key] === x || null }, tierLabel(x)))
      sel.addEventListener('change', async () => {
        const r = await api('/api/permissions/override' + accountQuery(), { method: 'POST', body: { key: c.key, tier: sel.value } })
        if (!r.ok) return toast(r.error || '保存失败', 'err'); toast('已更新', 'ok')
      })
      ov.append(h('div', { class: 'perm-chat-row' }, h('div', { class: 'perm-chat-name' }, c.title || c.chat_id, h('div', { class: 'muted', style: 'font-size:10px' }, c.account_id + ' · ' + c.chat_type)), sel))
    }
    const chatCard = h('div', { class: 'card' }, h('div', { class: 'card-title' }, '每个聊天单独设置', h('span', { class: 'badge' }, String(Object.keys(data.overrides || {}).length))), h('div', { class: 'hint' }, '只列出当前账号白名单里的私聊/群；主人始终用主人权限，不在这里。'), ov)

    wrap.append(defaultCard, box, editor, tierCard, accountCard, adminCard, chatCard)
  }

  const drawStickers = async () => {
    const st = await api('/api/stickers/status?account_id=' + q()).catch(() => ({}))
    let qtext = '', scope = ''
    const grid = h('div', { class: 'sticker-grid' })
    const render = async () => {
      const r = await api('/api/stickers?account_id=' + q() + '&q=' + encodeURIComponent(qtext) + '&scope=' + encodeURIComponent(scope)).catch(() => ({ items: [] }))
      grid.innerHTML = ''
      for (const it of (r.items || [])) grid.append(h('div', { class: 'sticker-card' },
        h('img', { src: '/api/stickers/image/' + it.md5 + '?account_id=' + q(), alt: it.name || '', loading: 'lazy' }),
        h('div', { class: 'sticker-name' }, it.name || '未命名'),
        h('div', { class: 'muted' }, (it.tags || []).map(x => '#' + x).join(' ')),
        h('div', { class: 'sticker-meaning' }, it.meaning || it.description || '还没理解含义'),
        h('div', { class: 'muted', style: 'font-size:10px' }, it.analyzed ? ('情绪：' + (it.emotion || '已理解')) : '未理解'),
        h('div', { class: 'sticker-actions' },
          h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
            const name = prompt('名字（12 字内）', it.name || ''); if (name === null) return
            const tags = prompt('标签，用逗号分隔', (it.tags || []).join(',')); if (tags === null) return
            const emotion = prompt('主要情绪（如 开心/无语/生气/委屈）', it.emotion || '') ?? (it.emotion || '')
            const meaning = prompt('它表达什么意思/适合什么场景', it.meaning || it.use_when || '') ?? (it.meaning || '')
            const r = await api('/api/stickers/' + it.md5 + '/update', { method: 'POST', body: {
              account_id: account(), name, tags: tags.split(/[,，]/).filter(Boolean), scope: it.scope,
              enabled: it.enabled !== false, emotion, meaning,
            } })
            if (!r.ok) return toast(r.error || '保存失败', 'err'); render()
          } }, '编辑'),
          h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => { if (!confirm('删掉这张表情？')) return; await api('/api/stickers/' + it.md5 + '?account_id=' + q(), { method: 'DELETE' }); render() } }, '删除'))))
      if (!(r.items || []).length) grid.append(h('div', { class: 'muted' }, '还没有表情；先打开「偷表情包」，或在下面上传。'))
    }
    const search = h('input', { type: 'search', placeholder: '搜名字/标签', oninput: (e) => { qtext = e.target.value; render() } })
    const scopeSel = h('select', { onchange: (e) => { scope = e.target.value; render() } },
      h('option', { value: '' }, '全部范围'), h('option', { value: 'owner_only' }, '只主人私聊'), h('option', { value: 'private_safe' }, '私聊可用'), h('option', { value: 'group_safe' }, '群聊/私聊可用'), h('option', { value: 'shared' }, '全部可用'))
    const fileInput = h('input', { type: 'file', accept: 'image/*', style: 'display:none' })
    const upload = h('button', { class: 'btn btn-primary btn-sm', onclick: () => fileInput.click() }, '上传')
    const importQq = h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
      if (!confirm('读取当前 QQ 账号的收藏表情？会下载到本账号表情库并自动打标。')) return
      const r = await api('/api/stickers/import_qq', { method: 'POST', body: { account_id: account(), count: 120, scope: 'shared' } }).catch(e => ({ ok: false, error: e.message }))
      if (!r.ok) return toast(r.error || '读取失败', 'err')
      toast(`QQ 收藏：新增 ${r.added}，已有 ${r.skipped}，失败 ${r.failed}；正在自动打标`, 'ok')
      drawStickers()
    } }, '读取本账号表情')
    fileInput.addEventListener('change', async () => {
      const f = fileInput.files?.[0]; if (!f) return
      const fd = new FormData(); fd.append('file', f); fd.append('name', f.name.replace(/\.[^.]+$/, '').slice(0, 12)); fd.append('tags', ''); fd.append('scope', 'owner_only'); fd.append('account_id', account())
      const res = await fetch('/api/stickers/upload', { method: 'POST', headers: panelToken ? { 'X-Panel-Token': panelToken } : {}, body: fd })
      const r = await res.json().catch(() => ({ ok: false, error: '上传失败' }))
      if (!r.ok) return toast(r.error || '上传失败', 'err'); toast('已放进表情库', 'ok'); drawStickers()
    })
    const toggle = (key, label, desc, checked) => {
      const cb = h('input', { type: 'checkbox' }); cb.checked = Boolean(checked)
      cb.addEventListener('change', async () => {
        const r = await api('/api/settings/set', { method: 'POST', body: { key, value: cb.checked, account_id: account() } })
        if (!r.ok) return toast(r.error || '保存失败', 'err'); toast(label + '已更新', 'ok')
      })
      return h('label', { class: 'perm-row' }, cb, h('span', {}, label), h('span', { class: 'muted', style: 'margin-left:auto;font-size:10px' }, desc))
    }
    const config = h('div', { class: 'card' }, h('div', { class: 'card-title' }, '收集与自动配图'),
      toggle('stickers.collect', '偷表情包', '默认关；收到聊天表情时自动收进本账号库', st.collect),
      toggle('stickers.auto_send', '回复后自动配表情', '按下面的概率触发，不受冷却限制', st.auto_send),
      numberRow('stickers.auto_private', '私聊概率', '', st.auto_private, 0, 100, 1, '%'),
      numberRow('stickers.auto_group', '群聊概率', '', st.auto_group, 0, 100, 1, '%'),
      numberRow('stickers.cooldown_sec', '同一聊天冷却', '0 = 不冷却', st.cooldown_sec, 0, 3600, 1, '秒'))
    const adv = await advancedPanel(['表情'], '更多表情参数', [
      'stickers.collect', 'stickers.auto_send',
      'stickers.auto_private', 'stickers.auto_group', 'stickers.cooldown_sec',
    ])
    if (adv) config.append(adv)
    const analyzeStatus = h('div', { class: 'hint' }, '用图片和已有文字重新理解全部表情')
    const analyzeBtn = h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
      if (!st.count) return toast('表情库是空的', 'err')
      if (!confirm('重新理解全部表情？会覆盖现有含义、情绪和场景，耗时可能较长。')) return
      analyzeBtn.disabled = true
      analyzeStatus.textContent = '正在启动…'
      const r = await api('/api/stickers/analyze_all?account_id=' + q(), { method: 'POST' })
        .catch(e => ({ ok: false, error: e.message }))
      if (!r.ok) { analyzeBtn.disabled = false; return toast(r.error || '启动失败', 'err') }
      pollAnalyze()
    } }, '重新理解全部')
    const pollAnalyze = async () => {
      const r = await api('/api/stickers/analyze_all/status?account_id=' + q()).catch(() => ({}))
      const j = r.job
      if (!j) { analyzeBtn.disabled = false; return }
      analyzeStatus.textContent = j.status === 'running'
        ? `正在理解 ${j.done}/${j.total} · 失败 ${j.failed} · ${j.current || ''}`
        : j.status === 'done'
          ? `理解完成：${j.done}/${j.total}，失败 ${j.failed}`
          : `理解失败：${j.error || '未知错误'}`
      if (j.status === 'running') {
        setTimeout(pollAnalyze, 1200)
        return
      }
      analyzeBtn.disabled = false
      if (j.status === 'done') {
        toast(`表情理解完成：${j.done}/${j.total}`, 'ok')
        render()
      }
    }
    const loadAnalyzeStatus = async () => {
      const r = await api('/api/stickers/analyze_all/status?account_id=' + q()).catch(() => ({}))
      const j = r.job
      if (!j) return
      if (j.status === 'running') {
        analyzeBtn.disabled = true
        analyzeStatus.textContent = `正在理解 ${j.done}/${j.total} · 失败 ${j.failed} · ${j.current || ''}`
        setTimeout(pollAnalyze, 1200)
      } else if (j.status === 'done') {
        analyzeStatus.textContent = `上次理解完成：${j.done}/${j.total}，失败 ${j.failed}`
      }
    }
    wrap.append(config)
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '表情库', h('span', { class: 'badge' }, String(st.count || 0)), analyzeBtn, importQq, upload, fileInput),
      h('div', { class: 'sticker-tools' }, search, scopeSel), grid, analyzeStatus,
      h('div', { class: 'hint' }, '每个 QQ 号独立库；读取的是当前登录账号自己的收藏表情。导入后会在后台自动打标，回复时按语义挑图。')))
    await render()
    loadAnalyzeStatus()
  }

  const drawWhitelist = async () => {
    const v = await settingsValues()
    const editor = (key, label, values) => {
      let items = [...(values || [])]
      const list = h('div', { class: 'whitelist-list' })
      const count = h('span', { class: 'badge' }, String(items.length))
      const details = h('details', { class: 'whitelist-details' })
      const input = h('input', { type: 'text', class: 'wide', placeholder: 'QQ / 群号，可一次粘贴多个' })
      const addBtn = h('button', { class: 'btn btn-ghost btn-sm' }, '添加')
      const refresh = () => {
        list.innerHTML = ''
        count.textContent = String(items.length)
        if (!items.length) {
          list.append(h('div', { class: 'muted', style: 'padding:8px 0' }, '还没有，添加后会出现在这里。'))
        }
        for (const item of items) {
          const row = h('div', { class: 'whitelist-row' },
            h('span', { class: 'whitelist-value' }, item),
            h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
              const next = items.filter(x => x !== item)
              if (!await saveSetting(key, next)) return
              items = next; refresh()
            } }, '移除'))
          row.dataset.sortId = item
          list.append(row)
        }
      }
      const add = async () => {
        const nextItems = input.value.split(/[,，\s]+/).map(x => x.trim()).filter(Boolean)
        if (!nextItems.length) return
        const merged = [...items]
        for (const item of nextItems) if (!merged.includes(item)) merged.push(item)
        if (merged.length === items.length) return toast('已经在白名单里了', 'err')
        if (!await saveSetting(key, merged)) return
        items = merged; input.value = ''; refresh(); details.open = true
      }
      addBtn.addEventListener('click', add)
      input.addEventListener('keydown', e => { if (e.key === 'Enter') add() })
      const summary = h('summary', {},
        h('span', {}, label),
        count,
        h('span', { class: 'whitelist-toggle' }, '展开'))
      details.append(summary,
        h('div', { class: 'whitelist-body' },
          list,
          h('div', { class: 'whitelist-add' }, input, addBtn)))
      details.open = items.length === 0
      const initialToggle = details.querySelector('.whitelist-toggle')
      if (initialToggle) initialToggle.textContent = details.open ? '收起' : '展开'
      details.addEventListener('toggle', () => {
        const toggle = details.querySelector('.whitelist-toggle')
        if (toggle) toggle.textContent = details.open ? '收起' : '展开'
      })
      refresh()
      makeSortable(list, '.whitelist-row', async (order) => {
        if (!await saveSetting(key, order)) { refresh(); return }
        items = [...order]
        refresh()
      })
      return details
    }

    const privateList = v['security.private_whitelist'] || []
    const groupList = v['security.group_whitelist'] || []
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '接入白名单'),
      selectRow('chat.private_access', '私聊接入', v['chat.private_access'] || 'all', [
        ['all', '所有人都回'], ['whitelist', '仅白名单'], ['owner', '只回主人'], ['off', '关闭私聊']]),
      selectRow('chat.group_access', '群聊接入', v['chat.group_access'] || 'all', [
        ['all', '所有群'], ['whitelist', '仅白名单群'], ['off', '关闭群聊']]),
      editor('security.private_whitelist', '私聊白名单', privateList),
      editor('security.group_whitelist', '群白名单', groupList),
      h('div', { class: 'hint' }, '只有这里列出的私聊/群会出现在权限覆盖列表里；主人单独一档。')))
    const adv = await advancedPanel(['聊天'], '聊天高级参数')
    if (adv) wrap.append(adv)
  }

  const drawTruth = async () => {
    const v = await settingsValues()
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '真实性'),
      toggleRow('truth.enabled', '真实性优先', '不知道就说不知道', v['truth.enabled']),
      selectRow('truth.work_mode', '工作模式', v['truth.work_mode'] || 'auto', [
        ['auto', '主人私聊自动识别'], ['always', '始终严格'], ['off', '关闭工作模式']]),
      toggleRow('truth.strict_unknown', '不知道就直说', '没有依据时明确说不确定', v['truth.strict_unknown']),
      toggleRow('truth.allow_roleplay', '允许角色扮演和玩笑', '但不能用编造事实撑人设', v['truth.allow_roleplay']),
      toggleRow('truth.guard_actions', '拦截假称完成', '工作模式无工具证据时改成诚实说明', v['truth.guard_actions']),
      h('div', { class: 'hint' }, '工作话题只讲可验证结果；保存即生效。')))
    const adv = await advancedPanel(['真实'], '真实性高级参数')
    if (adv) wrap.append(adv)
  }

  const drawReply = async () => {
    const v = await settingsValues()
    const wake = await api('/api/persona/wake-words?account_id=' + q()).catch(() => ({ ok: false }))
    if (wake.ok) {
      let aliases = [...(wake.alias || [])]
      const nameInput = h('input', { type: 'text', class: 'wide', value: wake.name || '' })
      const aliasList = h('div', { class: 'whitelist-list' })
      const addInput = h('input', { type: 'text', class: 'wide', placeholder: '新增别名' })
      const saveWake = async () => {
        const r = await api('/api/persona/wake-words?account_id=' + q(), {
          method: 'POST', body: { name: nameInput.value.trim(), alias: aliases },
        }).catch(e => ({ ok: false, error: e.message }))
        if (!r.ok) { toast(r.error || '保存失败', 'err'); return false }
        aliases = [...(r.alias || [])]
        nameInput.value = r.name || nameInput.value
        renderAliases()
        toast('唤醒词已同步到人格卡', 'ok')
        return true
      }
      const renderAliases = () => {
        aliasList.innerHTML = ''
        if (!aliases.length) aliasList.append(h('div', { class: 'hint' }, '还没有别名，可以添加。'))
        aliases.forEach((a, i) => {
          const inp = h('input', { type: 'text', class: 'wide', value: a })
          inp.addEventListener('change', async () => { aliases[i] = inp.value.trim(); await saveWake() })
          aliasList.append(h('div', { class: 'perm-row' }, inp,
            h('button', { class: 'btn btn-ghost btn-sm', onclick: async () => {
              aliases.splice(i, 1); await saveWake()
            } }, '删除')))
        })
      }
      renderAliases()
      nameInput.addEventListener('change', async () => { if (!(await saveWake())) nameInput.value = wake.name || '' })
      const addBtn = h('button', { class: 'btn btn-primary btn-sm', onclick: async () => {
        const v2 = addInput.value.trim()
        if (!v2) return
        aliases.push(v2); addInput.value = ''; await saveWake()
      } }, '添加')
      addInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') addBtn.click() })
      wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '唤醒词'),
        h('div', { class: 'hint' }, '唤醒词与人格卡同步，切换人格卡时自动切换。'),
        h('label', { class: 'perm-row' }, h('span', { style: 'min-width:110px' }, '主名字'), nameInput,
          h('span', { class: 'muted' }, '不可删除')),
        h('div', { class: 'card-subtitle', style: 'margin:10px 0 6px' }, '别名'),
        aliasList,
        h('div', { class: 'perm-row' }, addInput, addBtn)))
    } else {
      wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '唤醒词'),
        h('div', { class: 'hint' }, '唤醒词与人格卡同步，切换人格卡时自动切换。'),
        h('div', { class: 'hint' }, wake.error || '当前没有激活的人格卡。')))
    }
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '回复方式'),
      toggleRow('reply.owner_always', '主人必回', '主人说话直接回，不吃群聊频率限制', v['reply.owner_always']),
      toggleRow('reply.private_always', '私聊必回', '私聊直接回，不吃群聊频率限制', v['reply.private_always']),
      numberRow('reply.cooldown_sec', '防抖', '收到消息后等 N 秒；期间又有要回的消息就只回最后一条，所有回复都生效', v['reply.cooldown_sec'] ?? 2, 0, 300, 0.5),
      selectRow('reply.long_task_mode', '长任务轮次', v['reply.long_task_mode'] || 'manual', [
        ['off', '不开启'], ['manual', '手动轮次'], ['auto', '自动轮次']]),
      numberRow('reply.long_task_max_rounds', '最多自动轮次', '自动轮次最多续跑几轮，防止无限循环', v['reply.long_task_max_rounds'] ?? 3, 1, 10, 1, '轮'),
      toggleRow('output.split_enabled', '长回复拆条', '按句子自然拆，不固定条数', v['output.split_enabled']),
      toggleRow('proactive.enabled', '生活主动', '默认关；开启前先预览', v['proactive.enabled']),
      h('div', { class: 'hint' }, '主人必回和私聊必回不吃群聊频率限制。')))
    wrap.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '群聊设置'),
      selectRow('reply.group_mode', '群里怎么回', v['reply.group_mode'] || 'mention', [
        ['mention', '只回 @ / 唤醒词'], ['smart', '判断相关性'], ['random', '按概率'], ['always', '每条都回'], ['never', '从不主动回']]),
      toggleRow('reply.ignore_other_bots', '忽略其他 bot', '关闭后两个 bot 也能接话，但仍受每人窗口和条数上限约束', v['reply.ignore_other_bots'] ?? false),
      numberRow('reply.group_rate_window_sec', '每人时间窗口', '群里同一个人在这段时间内最多被回多少条', v['reply.group_rate_window_sec'] ?? 60, 1, 3600, 1, '秒'),
      numberRow('reply.group_rate_max_msgs', '每人最多', '0 = 不限制；每个 bot 独立计数', v['reply.group_rate_max_msgs'] ?? 5, 0, 500, 1, '条'),
      numberRow('output.group_split_chars', '消息字数上限', '超过这个字数就拆；私聊不受影响', v['output.group_split_chars'] ?? 50, 10, 500, 1, '字'),
      numberRow('output.group_split_max', '拆分条数上限', '群聊最多拆几条，防止刷屏；私聊不受影响', v['output.group_split_max'] ?? 3, 1, 20, 1, '条'),
      numberRow('reply.followup_sec', '唤醒保持时间', '一次唤醒词/点名/@ 后的持续时间，普通消息不续期', v['reply.followup_sec'] ?? 8, 0, 120, 1),
      toggleRow('reply.followup_sender_only', '接话窗口只接唤醒者', '开启后其他人的消息只进入上下文，不单独回复', v['reply.followup_sender_only'] ?? true),
      h('div', { class: 'hint' }, '群聊频率按「bot + 群 + 发送者」独立计数；字数/条数上限只作用于群聊，私聊仍走「输出 → 超过多少字拆」。')))
    const adv = await advancedPanel(['回复'], '回复高级参数')
    if (adv) wrap.append(adv)
  }

  async function draw() {
    wrap.innerHTML = ''
    const dv = await settingsValues().catch(() => ({}))
    if ((tab === 'voice' && dv['voice.enabled'] === false) || (tab === 'media' && dv['media.enabled'] === false) || (tab === 'stickers' && dv['stickers.enabled'] === false)) tab = 'overview'
    wrap.append(pageHead('聊天', '隐私、聊天权限、表情包、白名单和回复，都在这里；高级项在各自面板底部折叠'), tabs(dv))
    if (tab === 'overview') await drawOverview()
    else if (tab === 'privacy') await drawPrivacy()
    else if (tab === 'voice') await drawVoice()
    else if (tab === 'media') await drawMedia()
    else if (tab === 'session') await drawSession()
    else if (tab === 'permissions') await drawPermissions()
    else if (tab === 'stickers') await drawStickers()
    else if (tab === 'whitelist') await drawWhitelist()
    else if (tab === 'truth') await drawTruth()
    else await drawReply()
    wrap.append(await chatSyncCard())
  }
  await draw()
}
