import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, LockKeyhole, Mic2, WandSparkles } from 'lucide-react'
import { api } from './api'
import { uiErrorVi } from './filmVi'
import type { FilmNarratorUpgradePreview, FilmSpeakerAcceptance, FilmSpeakerCalibrationReport, FilmSpeakerStatus } from './types'

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
  const [savedCalibration, setSavedCalibration] = useState<FilmSpeakerCalibrationReport | null>(null)
  const [narratorProfilePresent, setNarratorProfilePresent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [narratorVoice, setNarratorVoice] = useState('confident-male-vietnamese')
  const [narratorSpeed, setNarratorSpeed] = useState(1)
  const [narratorPreview, setNarratorPreview] = useState<FilmNarratorUpgradePreview | null>(null)
  const [narratorBusy, setNarratorBusy] = useState<'preview' | 'apply' | null>(null)
  const [narratorMessage, setNarratorMessage] = useState('')

  useEffect(() => {
    let cancelled = false
    Promise.allSettled([
      api.filmSpeakerStatus(),
      api.filmVoiceProfiles(projectId),
      api.filmSpeakerCalibration(projectId),
    ]).then(results => {
      if (cancelled) return
      const [statusResult, profilesResult, calibrationResult] = results
      setAcceptance(null)
      setNarratorPreview(null)
      setNarratorMessage('')
      setStatus(statusResult.status === 'fulfilled' ? statusResult.value : null)
      if (profilesResult.status === 'fulfilled') {
        const narrator = profilesResult.value.profiles.find(item => item.character_id === 'NARRATOR')
        setNarratorProfilePresent(!!narrator)
        const voice = String(narrator?.provider_voice_id || narrator?.profile?.provider_voice_id || '').trim()
        if (voice) setNarratorVoice(voice)
      } else {
        setNarratorProfilePresent(false)
      }
      setSavedCalibration(calibrationResult.status === 'fulfilled' ? calibrationResult.value.calibration || null : null)
      const failures = results.filter(result => result.status === 'rejected')
      if (failures.length === results.length) {
        const reason = failures[0].status === 'rejected' ? failures[0].reason : 'Không đọc được voice identity'
        setError(uiErrorVi(reason instanceof Error ? reason.message : String(reason)))
      } else {
        setError('')
      }
    })
    return () => { cancelled = true }
  }, [projectId])

  const effectiveCalibration = acceptance?.calibration || savedCalibration || undefined
  const rows = useMemo(
    () => Object.values(effectiveCalibration?.speaker_calibrations || {}),
    [effectiveCalibration],
  )
  const blocked = acceptance?.items.filter(item =>
    ['calibration_ambiguous', 'calibration_pending', 'not_evaluated', 'error'].includes(item.status)
  ) || []
  const charactersReady = effectiveCalibration?.character_status === 'calibrated'
  const narratorStatus = String(
    effectiveCalibration?.narrator_status
      || (narratorProfilePresent ? 'not_calibrated' : 'not_present'),
  )
  const narratorReady = ['calibrated', 'not_present'].includes(narratorStatus)
  const acceptanceClean = !acceptance || ((acceptance.failed_scenes || 0) === 0 && (acceptance.blocked_scenes || 0) === 0)
  const ready = charactersReady && narratorReady && acceptanceClean
  const hasIdentityData = !!acceptance || !!savedCalibration
  const showNarratorLock = narratorProfilePresent || narratorStatus !== 'not_present'

  const run = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    setNarratorMessage('')
    try {
      const next = await api.runFilmSpeakerAcceptance(projectId)
      setAcceptance(next)
      setSavedCalibration(next.calibration || null)
      setStatus(await api.filmSpeakerStatus())
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
    } finally {
      setBusy(false)
    }
  }

  const previewNarrator = async () => {
    if (narratorBusy) return
    setNarratorBusy('preview')
    setError('')
    setNarratorMessage('')
    setNarratorPreview(null)
    try {
      const result = await api.previewFilmNarratorUpgrade(projectId, narratorVoice.trim(), narratorSpeed)
      setNarratorPreview(result)
      setNarratorMessage(
        result.ready
          ? 'Preview đạt. Có thể khóa narrator bằng đúng giọng này.'
          : 'Preview chưa đạt. Không thể Apply cho tới khi cùng một giọng narrator vượt ngưỡng kiểm tra.',
      )
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
    } finally {
      setNarratorBusy(null)
    }
  }

  const applyNarrator = async () => {
    if (narratorBusy || narratorPreview?.ready !== true) return
    setNarratorBusy('apply')
    setError('')
    setNarratorMessage('')
    try {
      const result = await api.applyFilmNarratorUpgrade(projectId, narratorVoice.trim(), narratorSpeed)
      setAcceptance(result.speaker_acceptance)
      setSavedCalibration(result.speaker_acceptance.calibration || null)
      setNarratorMessage(
        `Đã khóa narrator: ${result.upgraded_scenes.length} scene được cập nhật, speaker acceptance đã chạy lại.`,
      )
      setStatus(await api.filmSpeakerStatus())
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
    } finally {
      setNarratorBusy(null)
    }
  }

  return (
    <section className={`production-gate-card ${hasIdentityData ? (ready ? 'pass' : 'fail') : ''}`}>
      <div className="production-gate-summary">
        {hasIdentityData
          ? ready ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />
          : <Mic2 size={16} />}
        <div>
          <span>VOICE IDENTITY QC · {status?.backend || 'sherpa-onnx'}</span>
          <strong>
            {!status
              ? 'ĐANG ĐỌC SPEAKER VERIFIER'
              : !status.enabled || !status.model_exists
                ? 'SPEAKER VERIFIER CHƯA SẴN SÀNG'
                : !hasIdentityData
                  ? 'SẴN SÀNG KIỂM TRA GIỌNG XUYÊN CẢNH'
                  : ready
                    ? 'VOICE IDENTITY ĐÃ ĐẠT'
                    : 'VOICE IDENTITY CÒN HẠNG MỤC BỊ CHẶN'}
          </strong>
          <em>
            Model: {effectiveCalibration?.model || status?.model_path?.split(/[\\/]/).pop() || '—'}
            {' · '}Chiến lược: {effectiveCalibration?.embedding_strategy || status?.embedding_strategy || '—'}
            {acceptance ? ` · ${acceptance.verified_scenes} scene verified · ${acceptance.blocked_scenes} blocked · ${acceptance.failed_scenes} failed` : ''}
          </em>
          {hasIdentityData && (
            <p>
              Nhân vật: <b>{statusVi(effectiveCalibration?.character_status)}</b>
              {' · '}Narrator: <b>{statusVi(narratorStatus)}</b>
              {!acceptance && savedCalibration?.updated_at ? ` · Calibration lưu: ${new Date(savedCalibration.updated_at).toLocaleString('vi-VN')}` : ''}
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

          {showNarratorLock && narratorStatus !== 'calibrated' && (
            <div className={`film-narrator-lock ${narratorPreview?.ready ? 'ready' : ''}`}>
              <div className="film-narrator-lock-head">
                <div>
                  <span>CANONICAL NARRATOR VOICE</span>
                  <strong><LockKeyhole size={12} /> Khóa một giọng narrator cố định cho toàn bộ phim</strong>
                </div>
                {narratorPreview && (
                  <b>{narratorPreview.ready ? 'PREVIEW PASS' : 'PREVIEW BLOCKED'}</b>
                )}
              </div>
              <div className="film-narrator-lock-controls">
                <label>
                  Voice ID
                  <input
                    value={narratorVoice}
                    onChange={e => { setNarratorVoice(e.target.value); setNarratorPreview(null) }}
                    disabled={!!narratorBusy}
                  />
                </label>
                <label>
                  Tốc độ
                  <input
                    type="number"
                    min={0.5}
                    max={2}
                    step={0.05}
                    value={narratorSpeed}
                    onChange={e => { setNarratorSpeed(Number(e.target.value) || 1); setNarratorPreview(null) }}
                    disabled={!!narratorBusy}
                  />
                </label>
                <button type="button" disabled={!!narratorBusy || !narratorVoice.trim()} onClick={previewNarrator}>
                  {narratorBusy === 'preview' ? <Loader2 className="spin" size={12} /> : <WandSparkles size={12} />}
                  {narratorBusy === 'preview' ? 'Đang tạo preview…' : 'Preview narrator'}
                </button>
                <button type="button" disabled={!!narratorBusy || narratorPreview?.ready !== true} onClick={applyNarrator}>
                  {narratorBusy === 'apply' ? <Loader2 className="spin" size={12} /> : <LockKeyhole size={12} />}
                  {narratorBusy === 'apply' ? 'Đang khóa…' : 'Apply & khóa giọng'}
                </button>
              </div>
              <p className="gate-meta">
                Preview chỉ gọi TTS khi bạn bấm nút; audio TTS đã tạo được cache theo voice + nội dung + tốc độ và Apply sẽ tái sử dụng cache đó.
              </p>
              {narratorPreview && (
                <dl className="film-narrator-preview-meta">
                  <div><dt>Scene narrator</dt><dd>{narratorPreview.scene_count}</dd></div>
                  <div><dt>Độ giống thấp nhất</dt><dd>{percent(narratorPreview.min_same_voice_similarity)}</dd></div>
                  <div><dt>Ngưỡng yêu cầu</dt><dd>{percent(narratorPreview.required_similarity)}</dd></div>
                  <div><dt>Provider / model</dt><dd>{narratorPreview.provider} · {narratorPreview.model}</dd></div>
                </dl>
              )}
              {narratorMessage && <p className={narratorPreview?.ready ? 'gate-note' : 'gate-issue'}>{narratorMessage}</p>}
            </div>
          )}

          {hasIdentityData && narratorStatus === 'calibrated' && (
            <p className="gate-note">Narrator đã được calibrate và khóa theo speaker identity đã lưu của dự án.</p>
          )}
          {error && <p className="gate-issue">{error}</p>}
        </div>
      </div>
      <div className="production-gate-actions">
        <button
          type="button"
          disabled={busy || !!narratorBusy || status?.enabled === false || status?.model_exists === false}
          onClick={run}
        >
          {busy ? <Loader2 className="spin" size={12} /> : <Mic2 size={12} />}
          {busy ? 'Đang phân tích voice identity…' : 'Kiểm tra voice identity'}
        </button>
      </div>
    </section>
  )
}
