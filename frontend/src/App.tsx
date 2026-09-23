import { lazy, Suspense, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Bot, CheckCircle2, ChevronDown, Clapperboard, Film, KeyRound, Loader2, LogIn, MessageSquare, Plus, Save, Send, Settings, Sparkles, Trash2, UserPlus, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from './api'
import { getRuntimeConfig } from './runtime'
import type { Chat, FlowMetrics, FlowSavedSession, FlowSessionList, FlowStatus, Message, Provider } from './types'
import DesktopStatusCenter from './DesktopStatusCenter'
import './App.css'

const VideoAnalyzer = lazy(() => import('./VideoAnalyzer'))
const FilmStudio = lazy(() => import('./FilmStudio'))

type DraftKeys = Record<string, { key: string; base: string }>

const DEFAULT_FLOW_BRIDGE_URL = getRuntimeConfig().backendBaseUrl
  ? ''
  : 'http://127.0.0.1:8765'

function App() {
  const [providers, setProviders] = useState<Provider[]>([])
  const [chats, setChats] = useState<Chat[]>([])
  const [active, setActive] = useState<Chat | null>(null)
  const [providerId, setProviderId] = useState('openai')
  const [models, setModels] = useState<string[]>([])
  const [model, setModel] = useState('')
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [draftKeys, setDraftKeys] = useState<DraftKeys>({})
  const [flowStatus, setFlowStatus] = useState<FlowStatus | null>(null)
  const [flowMetrics, setFlowMetrics] = useState<FlowMetrics | null>(null)
  const [flowAuthenticated, setFlowAuthenticated] = useState<boolean | null>(null)
  const [flowBridgeUrl, setFlowBridgeUrl] = useState(DEFAULT_FLOW_BRIDGE_URL)
  const [flowBridgeKey, setFlowBridgeKey] = useState('')
  const [flowTesting, setFlowTesting] = useState(false)
  const [flowMessage, setFlowMessage] = useState('')
  const [flowSessions, setFlowSessions] = useState<FlowSavedSession[]>([])
  const [flowActiveSessionId, setFlowActiveSessionId] = useState<string>('')
  const [flowAccount, setFlowAccount] = useState<string>('')
  const [error, setError] = useState('')
  const [appMode, setAppMode] = useState<'chat' | 'video' | 'film'>('chat')
  const [appMenuOpen, setAppMenuOpen] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const flowLoginPollRef = useRef<number | null>(null)

  const currentProvider = useMemo(() => providers.find(p => p.id === providerId), [providers, providerId])

  const refreshProviders = async () => setProviders(await api.providers())
  const refreshChats = async () => setChats(await api.chats())

  const clearFlowLoginTimer = () => {
    if (flowLoginPollRef.current !== null) {
      window.clearInterval(flowLoginPollRef.current)
      flowLoginPollRef.current = null
    }
  }

  const applySessionList = (data: FlowSessionList) => {
    setFlowSessions(data.sessions || [])
    setFlowActiveSessionId(data.active_id || '')
    setFlowAccount(data.active_account || '')
    if (typeof data.authenticated === 'boolean') setFlowAuthenticated(data.authenticated)
    if (data.message) setFlowMessage(data.message)
  }

  useEffect(() => {
    Promise.all([api.providers(), api.chats()]).then(([p, c]) => {
      setProviders(p)
      setChats(c)
      const first = p.find(x => x.configured) || p[0]
      if (first) setProviderId(first.id)
    }).catch(e => setError(e.message))
  }, [])

  useEffect(() => {
    api.flowStatus().then(status => {
      setFlowStatus(status)
      if (status.bridge_url) setFlowBridgeUrl(status.bridge_url)
      if (status.configured) {
        api.flowMetrics().then(setFlowMetrics).catch(() => undefined)
        api.testFlow().then(result => setFlowAuthenticated(result.authenticated)).catch(() => setFlowAuthenticated(false))
        api.flowSessions().then(applySessionList).catch(() => undefined)
      } else {
        setFlowAuthenticated(null)
      }
    }).catch(() => undefined)
  }, [])

  useEffect(() => {
    return () => clearFlowLoginTimer()
  }, [])

  useEffect(() => {
    if (!providerId) return
    api.models(providerId).then(({ models: list }) => {
      setModels(list)
      setModel(prev => list.includes(prev) ? prev : (list[0] || ''))
    }).catch(e => setError(e.message))
  }, [providerId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [active?.messages, loading])

  const openChat = async (chat: Chat) => {
    try {
      const detail = await api.chat(chat.id)
      setActive(detail)
      setProviderId(detail.provider)
      setModel(detail.model)
    } catch (e) { setError((e as Error).message) }
  }

  const newChat = () => {
    setActive(null)
    setInput('')
    setError('')
  }

  const changeProvider = async (next: string) => {
    setProviderId(next)
    const data = await api.models(next)
    const nextModel = data.models[0] || ''
    setModels(data.models)
    setModel(nextModel)
    if (appMode === 'chat' && active && nextModel) {
      const updated = await api.updateChat(active.id, { provider: next, model: nextModel })
      setActive(updated)
      await refreshChats()
    }
  }

  const changeModel = async (next: string) => {
    setModel(next)
    if (appMode === 'chat' && active) {
      try {
        const updated = await api.updateChat(active.id, { model: next })
        setActive(updated)
        await refreshChats()
      } catch (e) { setError((e as Error).message) }
    }
  }

  const removeChat = async (event: React.MouseEvent, id: string) => {
    event.stopPropagation()
    await api.deleteChat(id)
    if (active?.id === id) setActive(null)
    await refreshChats()
  }

  const sendMessage = async (event?: FormEvent) => {
    event?.preventDefault()
    const text = input.trim()
    if (!text || loading || !model) return
    if (!currentProvider?.configured) {
      setSettingsOpen(true)
      setError(`Hãy thêm API key cho ${currentProvider?.name || 'provider'} trước.`)
      return
    }
    setLoading(true)
    setError('')
    setInput('')
    try {
      let chat = active
      if (!chat) {
        chat = await api.createChat(providerId, model)
        setActive(chat)
      }
      const optimistic: Message = {
        id: `temp-${Date.now()}`, chat_id: chat.id, role: 'user', content: text, created_at: new Date().toISOString(),
      }
      setActive({ ...chat, messages: [...(chat.messages || []), optimistic] })
      const updated = await api.send(chat.id, text)
      setActive(updated)
      await refreshChats()
    } catch (e) {
      setError((e as Error).message)
      if (active) setActive(await api.chat(active.id).catch(() => active))
    } finally {
      setLoading(false)
    }
  }

  const saveProvider = async (provider: Provider) => {
    const draft = draftKeys[provider.id] || { key: '', base: '' }
    if (!draft.key.trim()) {
      setError(`Nhập API key mới cho ${provider.name}.`)
      return
    }
    try {
      await api.saveProvider(provider.id, draft.key.trim(), draft.base.trim() || undefined)
      setDraftKeys(prev => ({ ...prev, [provider.id]: { key: '', base: '' } }))
      await refreshProviders()
      setError('')
    } catch (e) { setError((e as Error).message) }
  }

  const disconnectProvider = async (provider: Provider) => {
    await api.removeProvider(provider.id)
    await refreshProviders()
  }

  const updateDraft = (id: string, patch: Partial<{ key: string; base: string }>) => {
    setDraftKeys(prev => ({ ...prev, [id]: { key: prev[id]?.key || '', base: prev[id]?.base || '', ...patch } }))
  }

  const saveFlowConnection = async () => {
    if (!flowBridgeKey.trim()) {
      setError('Nhập Flow Bridge API key nội bộ trước khi lưu.')
      return
    }
    try {
      const status = await api.saveFlow(flowBridgeUrl.trim(), flowBridgeKey.trim(), true)
      setFlowStatus(status)
      setFlowBridgeKey('')
      if (status.configured) {
        setFlowMetrics(await api.flowMetrics())
        const result = await api.testFlow()
        setFlowAuthenticated(result.authenticated)
      }
      setFlowMessage('Đã lưu Flow Bridge. Phiên Google được giữ trực tiếp trong Chrome profile riêng của Flow.')
      setError('')
    } catch (e) { setError((e as Error).message) }
  }

  const openFlowLogin = async () => {
    clearFlowLoginTimer()
    setFlowTesting(true)
    setFlowMessage('')
    try {
      const result = await api.openFlowLogin()
      setFlowMessage(result.message || 'Đã mở lại Chrome Flow profile. Hoàn tất đăng nhập; ứng dụng sẽ tự phát hiện trạng thái thành công.')
      setFlowAuthenticated(false)
      setError('')

      let attempts = 0
      flowLoginPollRef.current = window.setInterval(async () => {
        attempts += 1
        try {
          const check = await api.testFlow()
          if (check.authenticated) {
            clearFlowLoginTimer()
            setFlowAuthenticated(true)
            const [status, metrics, sessions] = await Promise.all([
              api.flowStatus(),
              api.flowMetrics(),
              api.flowSessions(),
            ])
            setFlowStatus(status)
            setFlowMetrics(metrics)
            applySessionList(sessions)
            setFlowMessage('Đăng nhập Google Flow thành công. Chrome profile hiện tại đã được giữ nguyên và tab Flow đang chạy ẩn trong nền.')
          } else if (attempts >= 150) {
            clearFlowLoginTimer()
            setFlowMessage('Chưa phát hiện đăng nhập Flow sau 5 phút. Bấm Đăng nhập để mở lại đúng tab hiện tại và tiếp tục.')
          }
        } catch {
          if (attempts >= 150) clearFlowLoginTimer()
        }
      }, 2000)
    } catch (e) { setError((e as Error).message) }
    finally { setFlowTesting(false) }
  }

  const testFlowConnection = async () => {
    setFlowTesting(true)
    setFlowMessage('')
    try {
      const result = await api.testFlow()
      setFlowAuthenticated(result.authenticated)
      setFlowMessage(result.authenticated ? 'Flow Bridge hoạt động và Chrome profile đang giữ phiên Flow.' : 'Flow cần đăng nhập lại. Bấm Đăng nhập, đăng nhập trong Chrome rồi bấm Kiểm tra.')
      setFlowStatus(await api.flowStatus())
      setFlowMetrics(await api.flowMetrics())
      applySessionList(await api.flowSessions())
    } catch (e) { setError((e as Error).message) }
    finally { setFlowTesting(false) }
  }

  const saveFlowSession = async () => {
    setFlowTesting(true)
    setFlowMessage('')
    try {
      const data = await api.saveFlowSession()
      applySessionList(data)
      setError('')
    } catch (e) { setError((e as Error).message) }
    finally { setFlowTesting(false) }
  }

  const startNewFlowSession = async () => {
    setFlowTesting(true)
    setFlowMessage('')
    try {
      const data = await api.newFlowSession(true)
      applySessionList(data)
      setFlowAuthenticated(false)
      setError('')
    } catch (e) { setError((e as Error).message) }
    finally { setFlowTesting(false) }
  }

  const switchFlowSession = async (sessionId: string) => {
    if (!sessionId || sessionId === flowActiveSessionId) return
    setFlowTesting(true)
    setFlowMessage('')
    try {
      const data = await api.restoreFlowSession(sessionId)
      applySessionList(data)
      setError('')
    } catch (e) { setError((e as Error).message) }
    finally { setFlowTesting(false) }
  }

  const disconnectFlow = async () => {
    await api.removeFlow()
    setFlowStatus(await api.flowStatus())
    setFlowMetrics(null)
    setFlowAuthenticated(null)
    setFlowMessage('Đã gỡ cấu hình bridge khỏi TH Media; browser profile không bị xóa.')
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-wrap">
          <button className="brand brand-button" onClick={() => setAppMenuOpen(value => !value)}>
            <div className="brand-mark"><Sparkles size={18} /></div>
            <div className="brand-copy"><strong>TH Media</strong><span>{appMode === 'chat' ? 'Chat AI' : appMode === 'video' ? 'Phân tích Video' : 'Tạo phim điện ảnh'}</span></div>
            <ChevronDown className={`brand-chevron ${appMenuOpen ? 'open' : ''}`} size={16} />
          </button>
          {appMenuOpen && (
            <div className="app-switcher">
              <div className="app-switcher-label">TH MEDIA · ỨNG DỤNG</div>
              <button className={appMode === 'chat' ? 'active' : ''} onClick={() => { setAppMode('chat'); setAppMenuOpen(false) }}>
                <span className="switcher-icon purple"><MessageSquare size={17} /></span><div><strong>Chat AI</strong><em>Trò chuyện với nhiều model</em></div>
              </button>
              <button className={appMode === 'video' ? 'active' : ''} onClick={() => { setAppMode('video'); setAppMenuOpen(false) }}>
                <span className="switcher-icon blue"><Clapperboard size={17} /></span><div><strong>Phân tích Video</strong><em>YouTube · TikTok · Facebook</em></div>
              </button>
              <button className={appMode === 'film' ? 'active' : ''} onClick={() => { setAppMode('film'); setAppMenuOpen(false) }}>
                <span className="switcher-icon amber"><Film size={17} /></span><div><strong>Tạo phim điện ảnh</strong><em>Story Bible · Storyboard · Continuity</em></div>
              </button>
            </div>
          )}
        </div>
        {appMode === 'chat' ? (<>
          <button className="new-chat" onClick={newChat}><Plus size={18} /> Cuộc trò chuyện mới</button>
          <div className="sidebar-label">Gần đây</div>
          <div className="chat-list">
            {chats.map(chat => (
              <button key={chat.id} className={`chat-item ${active?.id === chat.id ? 'active' : ''}`} onClick={() => openChat(chat)}>
                <MessageSquare size={16} /><span>{chat.title}</span>
                <Trash2 className="trash" size={15} onClick={e => removeChat(e, chat.id)} />
              </button>
            ))}
          </div>
        </>) : appMode === 'video' ? (<>
          <div className="video-side-intro"><Clapperboard size={20} /><strong>TH Media · Phân tích Video</strong><p>Dán link video và nhận báo cáo AI từ hình ảnh, lời nói, phụ đề và caption.</p></div>
          <div className="sidebar-label">Nền tảng hỗ trợ</div>
          <div className="platform-side-list"><span>YouTube</span><span>TikTok</span><span>Facebook</span></div>
          <div className="chat-list" />
        </>) : (<>
          <div className="video-side-intro film-side-intro"><Film size={20} /><strong>TH Media · Tạo phim điện ảnh</strong><p>Story Bible → Storyboard → Continuity → Flow Prompt → Render adapter.</p></div>
          <div className="sidebar-label">Pipeline</div>
          <div className="platform-side-list"><span>Story Bible</span><span>Scenes</span><span>Flow Prompt</span></div>
          <div className="chat-list" />
        </>)}
        <DesktopStatusCenter provider={currentProvider} model={model} />
        <div className="sidebar-footer">
          <button onClick={() => setSettingsOpen(true)}><Settings size={18} /><span>Cài đặt API</span></button>
          <div className="secure-note"><KeyRound size={14} /> Khóa được mã hóa phía server</div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div className="model-controls">
            <label>
              <span>Nhà cung cấp</span>
              <select value={providerId} onChange={e => changeProvider(e.target.value)}>
                {providers.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
              </select>
            </label>
            <label className="model-select">
              <span>Model</span>
              <select value={model} onChange={e => changeModel(e.target.value)} disabled={!models.length}>
                {models.map(item => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
          </div>
          <div className={`flow-account ${flowAuthenticated ? 'ready' : flowStatus?.configured ? 'need-login' : ''}`}>
            <span>Google Flow</span>
            <div className="flow-account-row">
              <b title={flowMessage || undefined}>
                {!flowStatus?.configured
                  ? 'Chưa cấu hình bridge'
                  : flowAuthenticated === true
                    ? (flowAccount || 'Đã đăng nhập')
                    : flowAuthenticated === false
                      ? (flowAccount ? `Chưa mở phiên · ${flowAccount}` : 'Cần đăng nhập')
                      : (flowAccount || 'Đang kiểm tra phiên')}
              </b>
              {flowSessions.length > 0 && (
                <select
                  value={flowActiveSessionId}
                  disabled={flowTesting || !flowStatus?.configured}
                  onChange={e => switchFlowSession(e.target.value)}
                  title="Chuyển phiên đã lưu"
                >
                  <option value="">Phiên hiện tại</option>
                  {flowSessions.map(session => (
                    <option key={session.id} value={session.id}>{session.name}{session.account_hint && session.account_hint !== session.name ? ` · ${session.account_hint}` : ''}</option>
                  ))}
                </select>
              )}
              <button disabled={!flowStatus?.configured || flowTesting} onClick={saveFlowSession} title="Lưu phiên Google hiện tại">
                {flowTesting ? <Loader2 className="spin" size={12} /> : <Save size={12} />}
                Lưu phiên
              </button>
              <button disabled={!flowStatus?.configured || flowTesting} onClick={startNewFlowSession} title="Mở phiên trống để đăng nhập tài khoản khác">
                <UserPlus size={12} />
                Phiên mới
              </button>
              <button
                disabled={flowTesting}
                onClick={() => {
                  if (!flowStatus?.configured) {
                    setSettingsOpen(true)
                    return
                  }
                  openFlowLogin()
                }}
              >
                <LogIn size={12} />
                Đăng nhập
              </button>
              <button disabled={!flowStatus?.configured || flowTesting} onClick={testFlowConnection}>
                Kiểm tra
              </button>
            </div>
          </div>
          <div className={`provider-status ${currentProvider?.configured ? 'ready' : ''}`}>
            {currentProvider?.configured ? <CheckCircle2 size={15} /> : <KeyRound size={15} />}
            {currentProvider?.configured ? 'API sẵn sàng' : 'Chưa có API key'}
          </div>
        </header>

        {appMode === 'chat' ? (<>
        <section className="conversation">
          {!active?.messages?.length ? (
            <div className="welcome">
              <div className="hero-icon"><Bot size={30} /></div>
              <h1>Chat với mọi model AI<br /><span>trong một không gian.</span></h1>
              <p>Chọn nhà cung cấp, chọn model và bắt đầu hội thoại. Lịch sử được lưu riêng trên server.</p>
              <div className="quick-grid">
                <button onClick={() => setInput('Giúp tôi phân tích một ý tưởng kinh doanh và chỉ ra các rủi ro chính.')}>Phân tích ý tưởng<div>Chiến lược & rủi ro</div></button>
                <button onClick={() => setInput('Viết giúp tôi một đoạn code Python sạch và giải thích từng phần.')}>Hỗ trợ lập trình<div>Code & giải thích</div></button>
                <button onClick={() => setInput('Hãy viết lại nội dung của tôi theo phong cách chuyên nghiệp và súc tích.')}>Viết nội dung<div>Soạn thảo & chỉnh sửa</div></button>
              </div>
            </div>
          ) : (
            <div className="message-stream">
              {active.messages.map(message => (
                <article key={message.id} className={`message ${message.role}`}>
                  <div className="avatar">{message.role === 'assistant' ? <Sparkles size={16} /> : 'B'}</div>
                  <div className="message-body">
                    <div className="message-meta">{message.role === 'assistant' ? `${currentProvider?.name || 'AI'} · ${active.model}` : 'Bạn'}</div>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                  </div>
                </article>
              ))}
              {loading && (
                <article className="message assistant typing">
                  <div className="avatar"><Sparkles size={16} /></div>
                  <div className="message-body"><div className="message-meta">AI đang trả lời</div><div className="typing-dots"><i></i><i></i><i></i></div></div>
                </article>
              )}
              <div ref={bottomRef} />
            </div>
          )}
        </section>

        <div className="composer-wrap">
          {error && <div className="error-banner"><span>{error}</span><button onClick={() => setError('')}><X size={15} /></button></div>}
          <form className="composer" onSubmit={sendMessage}>
            <textarea value={input} onChange={e => setInput(e.target.value)} placeholder={`Nhắn tin với ${model || 'AI'}...`} rows={1}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() } }} />
            <button className="send-button" disabled={!input.trim() || loading || !model}>{loading ? <Loader2 className="spin" size={19} /> : <Send size={19} />}</button>
          </form>
          <div className="composer-note">AI có thể mắc lỗi. Hãy kiểm tra thông tin quan trọng.</div>
        </div>
        </>) : appMode === 'video' ? (
          <Suspense fallback={<div className="feature-loading"><Loader2 className="spin" size={20} /><span>Đang tải Phân tích video...</span></div>}>
            <VideoAnalyzer providerId={providerId} model={model} provider={currentProvider} onOpenSettings={() => setSettingsOpen(true)} />
          </Suspense>
        ) : (
          <Suspense fallback={<div className="feature-loading"><Loader2 className="spin" size={20} /><span>Đang tải Xưởng phim AI...</span></div>}>
            <FilmStudio providerId={providerId} model={model} provider={currentProvider} onOpenSettings={() => setSettingsOpen(true)} />
          </Suspense>
        )}
      </main>

      {settingsOpen && (
        <div className="modal-backdrop" onMouseDown={() => setSettingsOpen(false)}>
          <div className="settings-modal" onMouseDown={e => e.stopPropagation()}>
            <div className="settings-head">
              <div>
                <span className="eyebrow">KẾT NỐI</span>
                <h2>Nhà cung cấp AI</h2>
                <p>Khóa API được mã hóa trên server. Đăng nhập Google Flow ở thanh trên.</p>
              </div>
              <div className="settings-head-meta">
                <span>{providers.filter(p => p.configured).length}/{providers.length} model</span>
                <span className={flowStatus?.configured ? 'on' : ''}>{flowStatus?.configured ? 'Flow sẵn sàng' : 'Flow chưa gắn'}</span>
                <button className="icon-button" onClick={() => setSettingsOpen(false)}><X size={18} /></button>
              </div>
            </div>
            <div className="settings-body">
              <section className="settings-section">
                <div className="settings-section-title">
                  <strong>Model AI</strong>
                  <em>Dùng cho chat, phân tích video và kiểm tra kịch bản</em>
                </div>
                <div className="provider-list">
                  {providers.map(provider => {
                    const draft = draftKeys[provider.id] || { key: '', base: '' }
                    return (
                      <article className={`provider-row ${provider.configured ? 'connected' : ''}`} key={provider.id} style={{ '--accent': provider.accent } as React.CSSProperties}>
                        <div className="provider-row-head">
                          <div className="provider-logo">{provider.name.slice(0, 1)}</div>
                          <div className="provider-row-copy">
                            <strong>{provider.name}</strong>
                            <span>{provider.configured ? provider.masked_key : 'Chưa kết nối'}</span>
                          </div>
                          <b className={`status-pill ${provider.configured ? 'on' : ''}`}>{provider.configured ? 'Đã kết nối' : 'Trống'}</b>
                        </div>
                        <div className="provider-row-fields">
                          <label>
                            API key
                            <input type="password" value={draft.key} onChange={e => updateDraft(provider.id, { key: e.target.value })} placeholder={provider.configured ? 'Nhập khóa mới để thay thế' : 'Nhập API key'} />
                          </label>
                          <div className="provider-row-actions">
                            <button className="save-key" onClick={() => saveProvider(provider)}>Lưu</button>
                            {provider.configured && <button className="disconnect" onClick={() => disconnectProvider(provider)}>Gỡ</button>}
                          </div>
                        </div>
                        <details className="provider-advanced">
                          <summary>Base URL tùy chọn</summary>
                          <input value={draft.base} onChange={e => updateDraft(provider.id, { base: e.target.value })} placeholder={provider.custom_base_url || provider.base_url} />
                        </details>
                      </article>
                    )
                  })}
                </div>
              </section>
              <section className="settings-section">
                <div className="settings-section-title">
                  <strong>Google Flow</strong>
                  <em>Cầu nối kỹ thuật. Đăng nhập tài khoản ở thanh trên, cạnh ô model.</em>
                </div>
                <article className={`provider-row flow-row ${flowStatus?.configured ? 'connected' : ''}`} style={{ '--accent': '#4285f4' } as React.CSSProperties}>
                  <div className="provider-row-head">
                    <div className="provider-logo">F</div>
                    <div className="provider-row-copy">
                      <strong>Flow Session Bridge</strong>
                      <span>{flowStatus?.configured ? flowStatus.masked_key : 'Chưa cấu hình bridge'}</span>
                    </div>
                    <b className={`status-pill ${flowStatus?.configured ? 'on' : ''}`}>{flowStatus?.configured ? 'Sẵn sàng' : 'Trống'}</b>
                  </div>
                  <div className="provider-row-fields two">
                    <label>Bridge URL<input value={flowBridgeUrl} onChange={e => setFlowBridgeUrl(e.target.value)} placeholder="http://127.0.0.1:8765" /></label>
                    <label>Bridge API key<input type="password" value={flowBridgeKey} onChange={e => setFlowBridgeKey(e.target.value)} placeholder={flowStatus?.configured ? 'Nhập khóa mới để thay thế' : 'thflow_...'} /></label>
                  </div>
                  {flowMetrics && (
                    <div className="flow-metric-chips">
                      <span>{flowMetrics.jobs_total} jobs</span>
                      <span>{flowMetrics.active_jobs} đang chạy</span>
                      <span>{flowMetrics.video_files} video · {Math.round(flowMetrics.video_bytes / 1024 / 1024 * 10) / 10} MB</span>
                    </div>
                  )}
                  <div className="provider-row-actions">
                    <button className="save-key" onClick={saveFlowConnection}>Lưu bridge</button>
                    {flowStatus?.configured && <button className="disconnect" onClick={disconnectFlow}>Gỡ bridge</button>}
                  </div>
                </article>
              </section>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
