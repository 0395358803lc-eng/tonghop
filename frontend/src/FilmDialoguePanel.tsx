import type { FilmAudioRequirements, FilmDialogueLine } from './types'

type Props = {
  requirements?: FilmAudioRequirements | null
}

function lineText(item: FilmDialogueLine) {
  return item.text || '—'
}

export default function FilmDialoguePanel({ requirements }: Props) {
  if (!requirements) return <div className="film-ccc-empty">Chưa có yêu cầu thoại cho scene này.</div>
  const dialogue = requirements.dialogue || []
  const voiceover = requirements.voiceover || []
  return (
    <div className="film-audio-panel">
      <div className="film-ccc-qc-head">
        <strong>Dialogue</strong>
        <span>{requirements.speech_required ? 'Speech = hard gate' : 'Speech không bắt buộc'}</span>
      </div>
      {dialogue.length ? (
        <ul className="film-dialogue-list">
          {dialogue.map((item, index) => (
            <li key={`${item.speaker_character_id || 'na'}-${index}`}>
              <b>{item.speaker_character_id || 'Chưa map speaker'}</b>
              <span>{lineText(item)}</span>
              {item.error ? <em>{item.error}</em> : null}
            </li>
          ))}
        </ul>
      ) : <p className="film-ccc-empty">Không có thoại nhân vật.</p>}
      {voiceover.length ? <p>Voiceover: {voiceover.map(item => item.text).join(' / ')}</p> : null}
      {(requirements.errors || []).length ? (
        <ul className="film-pipeline-errors">
          {requirements.errors!.map((item, index) => <li key={`${item.code}-${index}`}>{item.code}: {item.detail}</li>)}
        </ul>
      ) : null}
    </div>
  )
}
