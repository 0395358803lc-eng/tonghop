import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, Mic2 } from 'lucide-react'
import { api } from './api'
import type { FilmSpeakerAcceptance, FilmSpeakerStatus } from './types'

type Props = {
  projectId: string
}

const statusVi = (value?: string | null) => {
  const key = String(value || '').toLowerCase()
  if (key === 'calibrated') return 'ĐÃ CALIBRATE'
  if (key === 'partial') return 'MỘT PHẦN'
  if (key === 'ambiguous') return 'MƠ HỒ / BỊ CHẶN'
  if (key === 'insufficient_samples') return 'CHƯA ĐỦ MẪU'
  if (key === 'not_present') return 'KHÔNG CÓ'
  if (key === 'not_calibrated') return 'CHƯA CALIBRATE'
  return value || 'CHƯA KIỂM TRA'
}

const percent = (value?: number | null) => (
  typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—'
)

export default function FilmSpeakerIdentityPanel({ projectId }: Props) {
  const [status, setStatus] = useState<FilmSpeakerStatus | null>(null)
  const [acceptance, setAcceptance] = useState<FilmSpeakerAcceptance | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    api.filmSpeakerStatus().then(next => {
      if (!cancelled) setStatus(next)
    }).catch(e => {
      if (!cancelled) setError((e as Error).message)
    })
    return () => { cancelled = true }
  }, [projectId])

  const rows = useMemo(
    () => Object.values(acceptance?.calibration?.speaker_calibrations || {}),
    [acceptance],
  )
  const blocked = acceptance?.items.filter(item =>
    ['calibration_ambiguous', 'calibration_pending', 'not_evaluated', 'error'].includes(item.status)
  ) || []
  const charactersReady = acceptance?.calibration?.character_status === 'calibrated'
  const narratorReady = ['calibrated', 'not_present'].includes(String(acceptance?.calibration?.narrator_status || ''))
  const ready = charactersReady && narratorReady && (acceptance?.failed_scenes || 0) === 0 && (acceptance?.blocked_scenes || 0) === 0

  const run = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      setAcceptance(await api.runFilmSpeakerAcceptance(projectId))
      setStatus(await api.filmSpeakerStatus())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className={`production-gate-card ${acceptance ? (ready ? 'pass' : 'fail') : ''}`}>
      <div className="production-gate-summary">
        {acceptance
          ? ready ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />
          : <Mic2 size={16} />}
        <div>
          <span>VOICE IDENTITY QC · {status?.backend || 'sherpa-onnx'}</span>
          <strong>
            {!status
              ? 'ĐANG ĐỌC SPEAKER VERIFIER'
              : !status.enabled || !status.model_exists
                ? 'SPEAKER VERIFIER CHƯA SẴN SÀNG'
                : !acceptance
                  ? 'SẴN SÀNG KIỂM TRA GIỌNG XUYÊN CẢNH'
                  : ready
                    ? 'VOICE IDENTITY ĐÃ ĐẠT'
                    : 'VOICE IDENTITY CÒN HẠNG MỤC BỊ CHẶN'}
          </strong>
          <em>
            Model: {acceptance?.calibration?.model || status?.model_path?.split(/[\\/]/).pop() || '—'}
            {' · '}Chiến lược: {acceptance?.calibration?.embedding_strategy || status?.embedding_strategy || '—'}
            {acceptance ? ` · ${acceptance.verified_scenes} scene verified · ${acceptance.blocked_scenes} blocked · ${acceptance.failed_scenes} failed` : ''}
          </em>
          {acceptance && (
            <p>
              Nhân vật: <b>{statusVi(acceptance.calibration?.character_status)}</b>
              {' · '}Narrator: <b>{statusVi(acceptance.calibration?.narrator_status)}</b>
            </p>
          )}
          {!!rows.length && (
            <dl className="film-ccc-meta">
              {rows.map(row => (
                <div key={row.speaker_id}>
                  <dt>{row.speaker_id}</dt>
                  <dd>
                    {statusVi(row.status)}
                    {' · '}threshold {percent(row.threshold)}
                    {typeof row.gap === 'number' ? ` · margin ${percent(row.gap)}` : ''}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {!!blocked.length && (
            <p className="gate-issue">
              Bị chặn: {blocked.map(item => `${item.scene_id} · ${item.speaker_character_id || 'speaker'} · ${statusVi(item.status)}`).join(' | ')}
            </p>
          )}
          {error && <p className="gate-issue">{error}</p>}
        </div>
      </div>
      <div className="production-gate-actions">
        <button
          type="button"
          disabled={busy || status?.enabled === false || status?.model_exists === false}
          onClick={run}
        >
          {busy ? <Loader2 className="spin" size={12} /> : <Mic2 size={12} />}
          {busy ? 'Đang phân tích voice identity…' : 'Kiểm tra voice identity'}
        </button>
      </div>
    </section>
  )
}
