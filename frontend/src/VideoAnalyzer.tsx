import { useEffect, useState, type FormEvent } from 'react'
import { AlertCircle, AudioLines, CheckCircle2, Clapperboard, ExternalLink, Eye, FileText, Images, Link2, Loader2, Network, RotateCcw, ShieldCheck, Sparkles, Trash2 } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from './api'
import type { Provider, VideoJob, VideoProxyStatus, VideoProxyTest } from './types'

type Props = {
  providerId: string
  model: string
  provider?: Provider
  onOpenSettings: () => void
}

const platformName: Record<string, string> = {
  youtube: 'YouTube', tiktok: 'TikTok', facebook: 'Facebook',
}

function formatDuration(value?: number | null) {
  if (value == null) return ''
  const hours = Math.floor(value / 3600)
  const minutes = Math.floor((value % 3600) / 60)
  const seconds = Math.floor(value % 60)
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}` : `${minutes}:${String(seconds).padStart(2, '0')}`
}

function transcriptDescription(source?: string | null) {
  if (!source) return 'Chưa xác định nguồn transcript.'
  if (source.startsWith('whisper')) return 'Âm thanh được speech-to-text cục bộ bằng Whisper.'
  if (source.startsWith('metadata')) return 'Video không có transcript đủ dùng; hệ thống phân tích caption/metadata của bài đăng.'
  if (source.startsWith('subtitle') || source.startsWith('automatic')) return 'Transcript được lấy từ phụ đề của nền tảng.'
  return `Nguồn nội dung: ${source}.`
}

function visionDescription(job: VideoJob) {
  if (job.vision_status === 'completed') return `AI đã xem ${job.keyframes?.length || 0} keyframe bằng ${job.vision_model || 'model Vision'}.`
  if (job.vision_status === 'unsupported') return 'Model hiện tại không nhận ảnh và không có fallback Vision phù hợp.'
  if (job.vision_status === 'failed') return 'Bước Vision gặp lỗi; báo cáo vẫn hoàn tất từ transcript/caption khi có thể.'
  if (job.vision_status === 'processing') return 'Hệ thống đang lấy keyframe và gửi cho model Vision.'
  return 'Vision chưa được xử lý.'
}

export default function VideoAnalyzer({ providerId, model, provider, onOpenSettings }: Props) {
  const [url, setUrl] = useState('')
  const [job, setJob] = useState<VideoJob | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [proxyStatus, setProxyStatus] = useState<VideoProxyStatus>({ configured: false })
  const [proxyInput, setProxyInput] = useState('')
  const [proxyBusy, setProxyBusy] = useState(false)
  const [proxyTest, setProxyTest] = useState<VideoProxyTest | null>(null)

  useEffect(() => {
    api.videoProxy().then(setProxyStatus).catch(() => undefined)
  }, [])

  const jobId = job?.id || ''
  const jobStatus = job?.status || ''

  useEffect(() => {
    if (!jobId || jobStatus === 'completed' || jobStatus === 'failed') return
    const timer = window.setInterval(async () => {
      try {
        const next = await api.videoJob(jobId)
        setJob(next)
        if (next.status === 'failed') setError(next.error || 'Phân tích video thất bại')
      } catch (e) {
        setError((e as Error).message)
      }
    }, 1400)
    return () => window.clearInterval(timer)
  }, [jobId, jobStatus])

  const analyze = async (event: FormEvent) => {
    event.preventDefault()
    const target = url.trim()
    if (!target || !model || submitting) return
    if (!provider?.configured) {
      setError(`Hãy thêm API key cho ${provider?.name || 'nhà cung cấp AI'} trước.`)
      onOpenSettings()
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const created = await api.createVideoJob(target, providerId, model)
      setJob(created)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const saveProxy = async () => {
    if (!proxyInput.trim() || proxyBusy) return
    setProxyBusy(true)
    setProxyTest(null)
    try {
      const status = await api.saveVideoProxy(proxyInput.trim())
      setProxyStatus(status)
      setProxyInput('')
      setError('')
    } catch (e) { setError((e as Error).message) }
    finally { setProxyBusy(false) }
  }

  const testProxy = async () => {
    setProxyBusy(true)
    setProxyTest(null)
    try { setProxyTest(await api.testVideoProxy()) }
    catch (e) { setError((e as Error).message) }
    finally { setProxyBusy(false) }
  }

  const removeProxy = async () => {
    setProxyBusy(true)
    try {
      await api.removeVideoProxy()
      setProxyStatus({ configured: false })
      setProxyTest(null)
    } catch (e) { setError((e as Error).message) }
    finally { setProxyBusy(false) }
  }

  const reset = () => {
    setJob(null)
    setUrl('')
    setError('')
  }

  return (
    <section className="video-workspace">
      <div className="video-content">
        {!job ? (
          <div className="video-hero">
            <div className="video-hero-icon"><Clapperboard size={29} /></div>
            <div className="video-kicker">AI VIDEO INTELLIGENCE</div>
            <h1>Phân tích video<br /><span>cả hình ảnh và nội dung.</span></h1>
            <p>Dán link YouTube, TikTok hoặc Facebook. Hệ thống lấy transcript/caption, trích keyframe theo timeline, cho AI xem hình và hợp nhất tất cả thành một báo cáo đa phương thức.</p>

            <form className="video-url-box" onSubmit={analyze}>
              <Link2 size={19} />
              <input value={url} onChange={e => setUrl(e.target.value)} placeholder="Dán link video YouTube / TikTok / Facebook..." autoFocus />
              <button disabled={!url.trim() || !model || submitting}>
                {submitting ? <Loader2 className="spin" size={17} /> : <Sparkles size={17} />}
                Phân tích
              </button>
            </form>

            <div className="platform-row">
              <span>Hỗ trợ</span>
              <i>YouTube</i><i>TikTok</i><i>Facebook</i>
            </div>

            <details className="proxy-config">
              <summary>
                <span className={`proxy-icon ${proxyStatus.configured ? 'ready' : ''}`}><Network size={16} /></span>
                <div><strong>Proxy SOCKS5 cho Video</strong><em>{proxyStatus.configured ? proxyStatus.masked_proxy : 'Tùy chọn · hữu ích khi YouTube chặn IP cloud'}</em></div>
                <b>{proxyStatus.configured ? 'Đã bật' : 'Chưa cấu hình'}</b>
              </summary>
              <div className="proxy-config-body">
                <label>
                  <span>Dán proxy dạng IP:PORT:USER:PASSWORD</span>
                  <div className="proxy-input-row">
                    <input type="password" autoComplete="off" spellCheck={false} value={proxyInput} onChange={e => setProxyInput(e.target.value)} placeholder="1.2.3.4:1080:user:password" />
                    <button type="button" onClick={saveProxy} disabled={!proxyInput.trim() || proxyBusy}>{proxyBusy ? <Loader2 className="spin" size={15} /> : <ShieldCheck size={15} />} Lưu proxy</button>
                  </div>
                </label>
                <p>Chỉ cần dán nguyên chuỗi proxy theo dạng <code>IP:PORT:USER:PASSWORD</code>. Ứng dụng sẽ tự chuyển sang SOCKS5H. Vẫn hỗ trợ <code>socks5://</code> và <code>socks5h://</code> nếu bạn muốn nhập dạng URL.</p>
                {proxyStatus.configured && (
                  <div className="proxy-actions">
                    <button type="button" onClick={testProxy} disabled={proxyBusy}><Network size={14} /> Kiểm tra proxy + YouTube</button>
                    <button type="button" className="proxy-remove" onClick={removeProxy} disabled={proxyBusy}><Trash2 size={14} /> Gỡ proxy</button>
                  </div>
                )}
                {proxyTest && (
                  <div className={`proxy-test ${proxyTest.proxy_ok ? 'ok' : 'bad'}`}>
                    <strong>{proxyTest.proxy_ok ? `SOCKS5 hoạt động · IP thoát ${proxyTest.exit_ip || 'không xác định'}` : 'Không kết nối được SOCKS5'}</strong>
                    <span className={proxyTest.youtube_ok ? 'youtube-ok' : 'youtube-bad'}>{proxyTest.youtube_ok ? `YouTube extractor: OK${proxyTest.youtube_title ? ` · ${proxyTest.youtube_title}` : ''}` : `YouTube extractor: chưa vượt chặn · ${proxyTest.youtube_error || 'Không rõ lỗi'}`}</span>
                  </div>
                )}
              </div>
            </details>

            <div className="pipeline-grid vision-pipeline">
              <div><span>01</span><Link2 size={19} /><strong>Đọc video</strong><p>Metadata, phụ đề, caption và luồng media.</p></div>
              <div><span>02</span><AudioLines size={19} /><strong>Transcript</strong><p>Ưu tiên subtitle, fallback speech-to-text.</p></div>
              <div><span>03</span><Images size={19} /><strong>Keyframe</strong><p>Lấy các frame đại diện trải đều theo timeline.</p></div>
              <div><span>04</span><Eye size={19} /><strong>Vision AI</strong><p>Nhân vật, cảnh, vật thể, chữ và visual hook.</p></div>
              <div><span>05</span><FileText size={19} /><strong>Báo cáo</strong><p>Hợp nhất hình + tiếng + caption thành kết luận.</p></div>
            </div>
          </div>
        ) : (
          <div className="video-result-page">
            <div className="video-result-top">
              <div>
                <div className="video-kicker">PHÂN TÍCH VIDEO ĐA PHƯƠNG THỨC</div>
                <h2>{job.title || 'Đang đọc thông tin video...'}</h2>
                <div className="video-meta-line">
                  <span>{platformName[job.platform] || job.platform}</span>
                  {job.channel && <span>{job.channel}</span>}
                  {job.duration != null && <span>{formatDuration(job.duration)}</span>}
                  <a href={job.url} target="_blank" rel="noreferrer">Mở video <ExternalLink size={12} /></a>
                </div>
              </div>
              <button className="analyze-another" onClick={reset}><RotateCcw size={15} /> Phân tích video khác</button>
            </div>

            <div className="video-source-card">
              {job.thumbnail ? <img src={job.thumbnail} alt="Thumbnail video" /> : <div className="video-thumb-empty"><Clapperboard size={26} /></div>}
              <div className="video-job-state">
                <div className="job-state-row">
                  <div className={`job-state-icon ${job.status}`}>
                    {job.status === 'completed' ? <CheckCircle2 size={17} /> : job.status === 'failed' ? <AlertCircle size={17} /> : <Loader2 className="spin" size={17} />}
                  </div>
                  <div><strong>{job.stage}</strong><span>{job.status === 'completed' ? 'Báo cáo đa phương thức đã sẵn sàng' : job.status === 'failed' ? 'Có lỗi trong pipeline' : `${job.progress}% hoàn tất`}</span></div>
                </div>
                <div className="progress-track"><div style={{ width: `${job.progress}%` }} /></div>
                <div className="job-tech-line">
                  <span>{job.provider}</span><span>{job.model}</span>
                  {job.transcript_source && <span>{job.transcript_source}</span>}
                  {job.vision_status && <span>Vision: {job.vision_status}</span>}
                  {job.vision_model && <span>{job.vision_model}</span>}
                </div>
              </div>
            </div>

            {!!job.keyframes?.length && (
              <section className="keyframe-section">
                <div className="keyframe-head"><Images size={16} /><div><strong>Keyframe AI sử dụng</strong><span>{job.keyframes.length} frame đại diện theo timeline</span></div></div>
                <div className="keyframe-grid">
                  {job.keyframes.map(frame => (
                    <figure key={frame.filename}>
                      <img src={frame.url} alt={`Keyframe ${frame.index} tại ${formatDuration(frame.timestamp)}`} loading="lazy" />
                      <figcaption><span>#{frame.index}</span><time>{formatDuration(frame.timestamp)}</time></figcaption>
                    </figure>
                  ))}
                </div>
              </section>
            )}

            {error && <div className="video-error"><AlertCircle size={16} /><span>{error}</span></div>}

            {job.status !== 'completed' && job.status !== 'failed' && (
              <div className="processing-panel">
                <Sparkles size={22} />
                <h3>Đang xử lý video đa phương thức</h3>
                <p>Hệ thống đang lần lượt tạo transcript, lấy keyframe, chạy Vision và tổng hợp báo cáo. Tiến trình được cập nhật tự động.</p>
              </div>
            )}

            {job.report && (
              <div className="report-layout">
                <article className="video-report">
                  <div className="report-title"><Sparkles size={18} /><div><span>BÁO CÁO AI</span><h3>Phân tích hình ảnh + âm thanh + nội dung</h3></div></div>
                  <div className="report-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{job.report}</ReactMarkdown></div>
                </article>
                <aside className="report-side">
                  <div className="side-card"><strong>Nguồn nội dung</strong><p>{transcriptDescription(job.transcript_source)}</p></div>
                  <div className={`side-card vision-card ${job.vision_status || ''}`}><strong>Vision Analysis</strong><p>{visionDescription(job)}</p>{job.vision_note && <small>{job.vision_note}</small>}</div>
                  <details className="transcript-card">
                    <summary><FileText size={14} /> Xem transcript</summary>
                    <pre>{job.transcript || 'Không có transcript.'}</pre>
                  </details>
                  {job.visual_summary && (
                    <details className="transcript-card vision-notes">
                      <summary><Eye size={14} /> Xem ghi chú Vision</summary>
                      <div className="vision-summary"><ReactMarkdown remarkPlugins={[remarkGfm]}>{job.visual_summary}</ReactMarkdown></div>
                    </details>
                  )}
                </aside>
              </div>
            )}
          </div>
        )}

        {error && !job && <div className="video-error hero-error"><AlertCircle size={16} /><span>{error}</span></div>}
      </div>
    </section>
  )
}
