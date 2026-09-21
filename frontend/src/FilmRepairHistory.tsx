import { qcDimensionVi } from './filmVi'
import type { FilmRepairAttempt } from './types'

type Props = {
  repairs?: FilmRepairAttempt[] | unknown[] | null
}

function asAttempt(value: unknown, index: number): FilmRepairAttempt {
  if (!value || typeof value !== 'object') return { attempt: index }
  return value as FilmRepairAttempt
}

export default function FilmRepairHistory({ repairs }: Props) {
  const items = (repairs || []).map((item, index) => asAttempt(item, index))
  if (!items.length) return <div className="film-ccc-empty">Scene chưa có lần sửa QC.</div>
  return (
    <ol className="film-ccc-repair">
      {items.map((item, index) => {
        const failed = item.failed_dimensions || item.failed || []
        const lines = item.lines || (item.instruction ? [item.instruction] : [])
        return (
          <li key={`${item.attempt ?? index}`}>
            <strong>Attempt {item.attempt ?? index}</strong>
            <span>{failed.length ? failed.map(qcDimensionVi).join(', ') : 'Không ghi dimension'}</span>
            {lines.length ? <p>{lines.slice(0, 4).join(' · ')}</p> : null}
            {item.qc_status ? <em>{item.qc_status}</em> : null}
          </li>
        )
      })}
    </ol>
  )
}
