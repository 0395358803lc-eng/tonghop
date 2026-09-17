import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Bot, CheckCircle2, ChevronDown, Clapperboard, Film, KeyRound, Loader2, MessageSquare, Plus, Send, Settings, Sparkles, Trash2, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from './api'
import VideoAnalyzer from './VideoAnalyzer'
import FilmStudio from './FilmStudio'
import type { Chat, Message, Provider } from './types'
import './App.css'

type DraftKeys = Record<string, { key: string; base: string }>

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
  const [error, setError] = useState('')
  const [appMode, setAppMode] = useState<'chat' | 'video' | 'film'>('chat')
  const [appMenuOpen, setAppMenuOpen] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const currentProvider = useMemo(() => providers.find(p => p.id === providerId), [providers, providerId])

  const refreshProviders = async () => setProviders(await api.providers())
  const refreshChats = async () => setChats(await api.chats())

  useEffect(() => {
    Promise.all([api.providers(), api.chats()]).then(([p, c]) => {
      setProviders(p)
      setChats(c)
      const first = p.find(x => x.configured) || p[0]
      if (first) setProviderId(first.id)
    }).catch(e => setError(e.message))
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
          <VideoAnalyzer providerId={providerId} model={model} provider={currentProvider} onOpenSettings={() => setSettingsOpen(true)} />
        ) : (
          <FilmStudio providerId={providerId} model={model} provider={currentProvider} onOpenSettings={() => setSettingsOpen(true)} />
        )}
      </main>

      {settingsOpen && (
        <div className="modal-backdrop" onMouseDown={() => setSettingsOpen(false)}>
          <div className="settings-modal" onMouseDown={e => e.stopPropagation()}>
            <div className="settings-head">
              <div><span className="eyebrow">KẾT NỐI MODEL</span><h2>Nhà cung cấp AI</h2><p>Khóa API chỉ được gửi đến backend và mã hóa trước khi lưu.</p></div>
              <button className="icon-button" onClick={() => setSettingsOpen(false)}><X size={20} /></button>
            </div>
            <div className="provider-grid">
              {providers.map(provider => {
                const draft = draftKeys[provider.id] || { key: '', base: '' }
                return (
                  <div className="provider-card" key={provider.id} style={{ '--accent': provider.accent } as React.CSSProperties}>
                    <div className="provider-card-head">
                      <div className="provider-logo">{provider.name.slice(0, 1)}</div>
                      <div><strong>{provider.name}</strong><span>{provider.configured ? provider.masked_key : 'Chưa kết nối'}</span></div>
                      <div className={`dot ${provider.configured ? 'online' : ''}`} />
                    </div>
                    <label>API key<input type="password" value={draft.key} onChange={e => updateDraft(provider.id, { key: e.target.value })} placeholder={provider.configured ? 'Nhập khóa mới để thay thế' : 'Nhập API key'} /></label>
                    <label>Base URL tùy chỉnh <em>không bắt buộc</em><input value={draft.base} onChange={e => updateDraft(provider.id, { base: e.target.value })} placeholder={provider.custom_base_url || provider.base_url} /></label>
                    <div className="provider-actions">
                      <button className="save-key" onClick={() => saveProvider(provider)}>Lưu kết nối</button>
                      {provider.configured && <button className="disconnect" onClick={() => disconnectProvider(provider)}>Gỡ khóa</button>}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
