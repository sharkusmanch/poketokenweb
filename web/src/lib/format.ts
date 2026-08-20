/**
 * Pure formatting helpers.
 *
 * `compact`, `cost` and `percent` are ports of poketokenbar/format.py so a
 * value this UI derives (periods.week.tokens has no preformatted twin) reads
 * identically to one the engine preformatted.
 */

export type LimitLevel = 'ok' | 'warn' | 'crit'

/** Age tolerated before the staleness banner appears, as a multiple of the
 *  configured refresh interval. */
export const STALE_FACTOR = 2.5

function trim(value: number, decimals: number): string {
  let s = value.toFixed(decimals)
  if (s.includes('.')) s = s.replace(/0+$/, '').replace(/\.$/, '')
  return s
}

export function compact(value: number): string {
  const v = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (v < 1_000) return String(value)
  if (v < 1_000_000) return sign + trim(v / 1_000, 1) + 'K'
  if (v < 1_000_000_000) return sign + trim(v / 1_000_000, 1) + 'M'
  return sign + trim(v / 1_000_000_000, 2) + 'B'
}

export function cost(usd: number): string {
  return `$${usd.toFixed(2)}`
}

export function percent(value: number): string {
  return value === Math.round(value) ? `${value.toFixed(0)}%` : `${value.toFixed(1)}%`
}

/**
 * Colour band for a utilization percentage — ports limits.level().
 *
 * NOT limits.*.severity: that is Anthropic's own string and was observed as
 * "normal" at 51% AND at 97%, so a UI keyed on it never warns.
 * A boundary value belongs to the more severe band.
 */
export function limitLevel(utilization: number, warn: number, crit: number): LimitLevel {
  if (utilization >= crit) return 'crit'
  if (utilization >= warn) return 'warn'
  return 'ok'
}

/** Whole minutes until an ISO-8601 instant; null when absent or unparsable. */
export function minutesUntil(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return null
  return Math.floor((at - now) / 60_000)
}

export function formatDuration(minutes: number): string {
  const total = Math.max(0, Math.floor(minutes))
  const days = Math.floor(total / 1440)
  const hours = Math.floor((total % 1440) / 60)
  const mins = total % 60
  if (days > 0) return hours > 0 ? `${days}d ${hours}h` : `${days}d`
  if (hours > 0) return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`
  return `${mins}m`
}

/** "4h 59m", the caller's localized now-label at/past the reset, "" when absent. */
export function resetsIn(
  iso: string | null | undefined,
  now: number,
  nowLabel: string,
): string {
  const minutes = minutesUntil(iso, now)
  if (minutes === null) return ''
  if (minutes <= 0) return nowLabel
  return formatDuration(minutes)
}

/**
 * `updated_at` is unix SECONDS (float) — not milliseconds.
 * The tolerance comes from /api/config's refresh_interval; hardcoding 120
 * mislabels every deployment that polls on any other cadence.
 */
export function isStale(
  updatedAtSeconds: number | null | undefined,
  refreshInterval: number,
  now: number = Date.now(),
): boolean {
  if (!updatedAtSeconds) return false
  const ageSeconds = now / 1000 - updatedAtSeconds
  if (ageSeconds < 0) return false // clock skew, not staleness
  return ageSeconds > refreshInterval * STALE_FACTOR
}

/**
 * `caught_at` is `float | None` in companion.py (entries written before the
 * field existed). `new Date(null * 1000)` renders 12/31/1969, so null must
 * short-circuit before it ever reaches the Date constructor.
 */
export function formatCaughtAt(
  seconds: number | null | undefined,
  locale?: string,
  timeZone?: string,
): string {
  if (!seconds) return ''
  const options: Intl.DateTimeFormatOptions = {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }
  if (timeZone) options.timeZone = timeZone
  return new Date(seconds * 1000).toLocaleDateString(locale, options)
}
