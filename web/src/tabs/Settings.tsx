import { useEffect, useState } from 'react'
import type { AppConfig } from '../types'

interface SettingsProps {
  /** The EFFECTIVE settings from GET /api/config — never hardcoded defaults. */
  config: AppConfig
  strings: Record<string, string>
  onSave: (key: keyof AppConfig, value: string | number) => Promise<void>
}

/** Mirrors poketokenweb.api.CONFIG_RANGES so the browser can hint the bounds. */
const RANGES: Record<string, { min: number; max: number }> = {
  refresh_interval: { min: 30, max: 3600 },
  warn_threshold: { min: 1, max: 100 },
  crit_threshold: { min: 1, max: 100 },
}

const LANGUAGES: { value: AppConfig['language']; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'ko', label: '한국어' },
  { value: 'ja', label: '日本語' },
  { value: 'es', label: 'Español' },
]

const MODES: AppConfig['limit_display_mode'][] = ['session', 'weekly', 'both']

type NumericKey = 'refresh_interval' | 'warn_threshold' | 'crit_threshold'

export function Settings({ config, onSave }: SettingsProps) {
  // Drafts exist only so typing does not fire a request per keystroke; the
  // config prop is the source of truth and re-seeds them whenever it changes.
  const [drafts, setDrafts] = useState<Record<NumericKey, string>>({
    refresh_interval: String(config.refresh_interval),
    warn_threshold: String(config.warn_threshold),
    crit_threshold: String(config.crit_threshold),
  })
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    setDrafts({
      refresh_interval: String(config.refresh_interval),
      warn_threshold: String(config.warn_threshold),
      crit_threshold: String(config.crit_threshold),
    })
  }, [config.refresh_interval, config.warn_threshold, config.crit_threshold])

  const submit = async (key: keyof AppConfig, value: string | number) => {
    setError(null)
    setSaved(null)
    try {
      await onSave(key, value)
      setSaved(key)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      // Never leave a rejected value on screen pretending to be in effect.
      if (key in RANGES) {
        const numericKey = key as NumericKey
        setDrafts((previous) => ({ ...previous, [numericKey]: String(config[numericKey]) }))
      }
    }
  }

  /**
   * Commit a numeric field. Fires ONLY on a real change: a focus-then-leave
   * with no edit, or a re-typed identical value, must not POST — that is how
   * the previous build silently reverted live settings.
   */
  const commit = (key: NumericKey) => {
    const raw = drafts[key].trim()
    if (raw === '') {
      setDrafts((previous) => ({ ...previous, [key]: String(config[key]) }))
      return
    }
    const parsed = Number(raw)
    if (!Number.isInteger(parsed) || parsed === config[key]) {
      setDrafts((previous) => ({ ...previous, [key]: String(config[key]) }))
      return
    }
    void submit(key, parsed)
  }

  const numberField = (key: NumericKey, label: string, hint: string) => (
    <label className="field" htmlFor={key}>
      <span className="field-label">{label}</span>
      <input
        id={key}
        name={key}
        type="number"
        inputMode="numeric"
        className="input"
        min={RANGES[key].min}
        max={RANGES[key].max}
        value={drafts[key]}
        onChange={(event) => setDrafts((previous) => ({ ...previous, [key]: event.target.value }))}
        onBlur={() => commit(key)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            commit(key)
          }
        }}
      />
      <span className="field-hint">{hint}</span>
      {saved === key ? (
        <span className="field-ok" data-testid="save-ok">
          Saved
        </span>
      ) : null}
    </label>
  )

  return (
    <div className="tab-panel" data-testid="tab-settings">
      {/* strings has no "settings" key (44 keys, none of them settings), so
          this screen's own labels are local English text. */}
      <h1 className="tab-title">Settings</h1>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}

      <section className="card">
        {numberField('refresh_interval', 'Refresh interval (seconds)', '30–3600')}
        {numberField('warn_threshold', 'Warn threshold (%)', '1–100')}
        {numberField('crit_threshold', 'Critical threshold (%)', '1–100')}

        <label className="field" htmlFor="limit_display_mode">
          <span className="field-label">Limit display</span>
          <select
            id="limit_display_mode"
            name="limit_display_mode"
            className="input"
            value={config.limit_display_mode}
            onChange={(event) => void submit('limit_display_mode', event.target.value)}
          >
            {MODES.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
          {saved === 'limit_display_mode' ? (
            <span className="field-ok" data-testid="save-ok">
              Saved
            </span>
          ) : null}
        </label>

        <label className="field" htmlFor="language">
          <span className="field-label">Language</span>
          <select
            id="language"
            name="language"
            className="input"
            value={config.language}
            onChange={(event) => void submit('language', event.target.value)}
          >
            {LANGUAGES.map((language) => (
              <option key={language.value} value={language.value}>
                {language.label}
              </option>
            ))}
          </select>
          {saved === 'language' ? (
            <span className="field-ok" data-testid="save-ok">
              Saved
            </span>
          ) : null}
        </label>
      </section>
    </div>
  )
}
