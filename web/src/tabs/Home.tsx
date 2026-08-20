import type { AppConfig, MonCompanion, StatePayload } from '../types'
import { Limits } from '../components/Limits'
import { Sprite } from '../components/Sprite'
import { compact, cost } from '../lib/format'

interface HomeProps {
  state: StatePayload
  config: AppConfig
  now?: number
}

/** The engine emits the untranslated tokens "graduation"/"next evolution". */
function goalLabel(companion: MonCompanion, strings: Record<string, string>): string {
  const key = companion.goal === 'graduation' ? 'graduation' : 'next_evolution'
  return strings[key] ?? companion.goal
}

function Companion({ state }: { state: StatePayload }) {
  const companion = state.companion
  const strings = state.strings

  if (companion.stage === 'egg') {
    const pct = Math.round((companion.egg_progress ?? 0) * 100)
    return (
      <section className="card companion-card" aria-label={strings.egg}>
        {/* An egg legitimately falls back to 🥚 — a species never may. */}
        <Sprite src={companion.sprite_path} alt={strings.egg ?? 'Egg'} emoji="🥚" size="lg" />
        <h2 className="companion-title">{strings.egg}</h2>
        {companion.egg_tier ? (
          <span className="badge badge-tier">{companion.egg_tier.toUpperCase()}</span>
        ) : null}
        <p className="status-message">{companion.status_message}</p>
        <div
          className="track"
          role="progressbar"
          data-testid="egg-progress"
          aria-label={strings.egg}
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="track-fill" style={{ width: `${pct}%` }} />
        </div>
        <p className="muted">{companion.label}</p>
      </section>
    )
  }

  const pct = Math.round((companion.stage_progress ?? 0) * 100)
  return (
    <section className="card companion-card" aria-label={companion.name || 'Companion'}>
      {/* No emoji prop: a species with no sprite must NOT render as an egg. */}
      <Sprite src={companion.sprite_path} alt={companion.name || 'Companion'} size="lg" />
      <h2 className="companion-title" data-testid="mon-name">
        {companion.name || `#${companion.species_id}`}
        {companion.is_shiny ? <span className="badge badge-shiny">{strings.shiny}</span> : null}
      </h2>
      <p className="status-message">{companion.status_message}</p>
      <p className="muted" data-testid="mon-meta">
        <span className={`rarity rarity-${companion.rarity}`}>
          {strings[String(companion.rarity)] ?? companion.rarity}
        </span>
        {companion.nature ? <span className="nature"> · {companion.nature}</span> : null}
        <span className="forms">
          {' '}
          · {companion.stage_index + 1}/{companion.total_forms}
        </span>
      </p>
      <div
        className="track"
        role="progressbar"
        data-testid="mon-progress"
        aria-label={goalLabel(companion, strings)}
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="track-fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="goal" data-testid="mon-goal">
        {companion.remaining_text} → {goalLabel(companion, strings)}
      </p>
      {companion.evo_line.length > 0 ? (
        <ol className="evo-line" data-testid="evo-line">
          {companion.evo_line.map((form) => (
            <li
              key={form.species_id}
              className={`evo-form${form.current ? ' evo-current' : ''}${form.reached ? '' : ' evo-locked'}`}
              data-testid={`evo-${form.species_id}`}
              data-current={String(form.current)}
              data-reached={String(form.reached)}
            >
              <Sprite src={form.sprite_path} alt={form.name || `#${form.species_id}`} size="sm" />
              <span className="evo-name">{form.name || `#${form.species_id}`}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  )
}

export function Home({ state, config, now = Date.now() }: HomeProps) {
  const strings = state.strings
  const { week, month } = state.periods
  const burnSession = state.burn.session
  const rateTemplate = strings.at_this_rate ?? ''

  return (
    <div className="tab-panel" data-testid="tab-home">
      <Companion state={state} />

      <section className="card" aria-label={strings.todays_tokens}>
        <h2 className="card-title">{strings.todays_tokens}</h2>
        <p className="metric">{state.today.tokens_grouped}</p>
        <p className="muted">{state.today.cost_text}</p>
        {burnSession && burnSession.eta_text ? (
          <p className="burn" data-testid="burn">
            {rateTemplate.replace('%1', burnSession.eta_text)}
          </p>
        ) : null}
      </section>

      {week || month ? (
        <section className="card periods-card" aria-label={strings.this_week}>
          {week ? (
            <div className="period" data-testid="period-week">
              <span className="period-label">{strings.this_week}</span>
              <span className="period-value">{compact(week.tokens)}</span>
              <span className="muted">{cost(week.cost)}</span>
            </div>
          ) : null}
          {month ? (
            <div className="period" data-testid="period-month">
              <span className="period-label">{strings.this_month}</span>
              <span className="period-value">{compact(month.tokens)}</span>
              <span className="muted">{cost(month.cost)}</span>
            </div>
          ) : null}
        </section>
      ) : null}

      <Limits limits={state.limits} strings={strings} config={config} now={now} />

      <section className="card" aria-label={strings.spendable_tokens}>
        <h2 className="card-title">{strings.spendable_tokens}</h2>
        <p className="metric" data-testid="spendable">
          {state.companion.spendable_text}
        </p>
        <p className="muted">{strings.spend_hint}</p>
      </section>

      {state.errors.length > 0 ? (
        <section className="card card-error" data-testid="errors">
          <ul>
            {state.errors.map((message, index) => (
              <li key={`${index}-${message}`}>{message}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}
