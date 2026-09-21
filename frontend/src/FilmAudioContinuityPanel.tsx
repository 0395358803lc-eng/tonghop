import type { FilmAudioRequirements, FilmVoiceProfile } from './types'

type Props = {
  requirements?: FilmAudioRequirements | null
  profiles?: FilmVoiceProfile[]
}

export default function FilmAudioContinuityPanel({ requirements, profiles = [] }: Props) {
  const speakers = requirements?.speakers || []
  const matched = profiles.filter(item => speakers.includes(item.character_id))
  return (
    <div className="film-audio-panel">
      <div className="film-ccc-qc-head">
        <strong>Audio continuity</strong>
        <span>{requirements?.speech_status === 'required' ? 'Có thoại' : 'Không thoại'}</span>
      </div>
      <dl className="film-ccc-meta">
        <div><dt>Ambient</dt><dd>{requirements?.ambient?.length ? requirements.ambient.map(item => item.continuity_group || item.type).join(', ') : '—'}</dd></div>
        <div><dt>SFX</dt><dd>{requirements?.sfx?.length || 0}</dd></div>
        <div><dt>Music</dt><dd>{requirements?.music?.length || 0}</dd></div>
        <div><dt>Voice</dt><dd>{matched.length ? matched.map(item => `${item.character_id} · ${(item.profile?.gender as string) || '—'}`).join(' / ') : 'Chưa gán voice profile'}</dd></div>
      </dl>
    </div>
  )
}
