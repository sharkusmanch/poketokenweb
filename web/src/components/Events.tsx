import type { AppEvent } from '../types'
import { formatCaughtAt } from '../lib/format'

interface EventsProps {
  events: AppEvent[]
  strings: Record<string, string>
  onClose: () => void
  loading: boolean
}

const ICONS: Record<string, string> = {
  hatched: '🐣',
  evolved: '⬆️',
  graduated: '🎓',
  shiny: '✨',
  ditto: '🟪',
}

export function Events({ events, strings, onClose, loading }: EventsProps) {
  return (
    <div className="tab-panel" data-testid="events-view">
      <div className="events-head">
        <button type="button" className="btn btn-ghost" onClick={onClose}>
          ← Back
        </button>
        <h1 className="tab-title">Recent activity</h1>
      </div>
      {loading ? (
        <p className="empty" data-testid="events-loading">
          …
        </p>
      ) : events.length === 0 ? (
        <p className="empty" data-testid="events-empty">
          {strings.status_idle}
        </p>
      ) : (
        <ul className="rows">
          {events.map((event, index) => {
            // published_at is unix SECONDS and may be 0/absent on a malformed
            // entry; formatCaughtAt returns "" rather than a 1969 date.
            const when = formatCaughtAt(event.published_at)
            return (
              <li
                className="row"
                key={`${index}-${event.published_at}`}
                data-testid={`event-${index}`}
                data-kind={event.kind}
              >
                <span className="event-icon" aria-hidden="true">
                  {ICONS[event.kind] ?? '•'}
                </span>
                <div className="row-text">
                  <span className="row-title">{event.title}</span>
                  <span className="row-desc">{event.detail}</span>
                  {when ? <span className="muted">{when}</span> : null}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
