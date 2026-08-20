import type { AppConfig, LimitWindow, Limits as LimitsPayload } from '../types'
import { limitLevel, percent, resetsIn } from '../lib/format'

interface LimitsProps {
  limits: LimitsPayload
  strings: Record<string, string>
  config: AppConfig
  /** Injectable clock so the reset countdown is testable. */
  now?: number
}

interface RowProps {
  id: 'session' | 'weekly'
  label: string
  window: LimitWindow
  strings: Record<string, string>
  config: AppConfig
  now: number
}

function LimitRow({ id, label, window: limit, strings, config, now }: RowProps) {
  // Derived here — NEVER limit.severity, which is Anthropic's vendor string
  // and reads "normal" at 51% and at 97% alike.
  const level = limitLevel(limit.utilization, config.warn_threshold, config.crit_threshold)
  const countdown = resetsIn(limit.resets_at, now, strings.resetting_now ?? '')
  const width = Math.max(0, Math.min(100, limit.utilization))

  return (
    <div className={`limit limit-${level}`} data-level={level} data-testid={`limit-${id}`}>
      <div className="limit-head">
        <span className="limit-label">{label}</span>
        <span className="limit-value">{percent(limit.utilization)}</span>
      </div>
      <div
        className="limit-track"
        role="progressbar"
        aria-label={label}
        aria-valuenow={Math.round(limit.utilization)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={`limit-fill limit-fill-${level}`} style={{ width: `${width}%` }} />
      </div>
      {countdown ? (
        <div className="limit-reset" data-testid={`limit-${id}-reset`}>
          {countdown}
        </div>
      ) : null}
    </div>
  )
}

export function Limits({ limits, strings, config, now = Date.now() }: LimitsProps) {
  const mode = config.limit_display_mode
  const rows: { id: 'session' | 'weekly'; label: string; window: LimitWindow }[] = []
  if (mode !== 'weekly' && limits.session) {
    rows.push({ id: 'session', label: strings.five_hour_session ?? '5-hour session', window: limits.session })
  }
  if (mode !== 'session' && limits.weekly) {
    rows.push({ id: 'weekly', label: strings.weekly ?? 'Weekly', window: limits.weekly })
  }

  return (
    <section className="card limits-card" aria-label={strings.limits_official ?? 'Limits'}>
      <h2 className="card-title">{strings.limits_official}</h2>
      {rows.length === 0 ? (
        <p className="muted">{strings.limits_unavailable}</p>
      ) : (
        rows.map((row) => (
          <LimitRow
            key={row.id}
            id={row.id}
            label={row.label}
            window={row.window}
            strings={strings}
            config={config}
            now={now}
          />
        ))
      )}
      {limits.plan ? <p className="limit-plan muted">{limits.plan}</p> : null}
    </section>
  )
}
