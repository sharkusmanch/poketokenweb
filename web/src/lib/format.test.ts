import { describe, it, expect } from 'vitest'
import {
  compact,
  cost,
  percent,
  limitLevel,
  minutesUntil,
  formatDuration,
  resetsIn,
  isStale,
  formatCaughtAt,
  STALE_FACTOR,
} from './format'

describe('compact — ports poketokenbar.format.compact verbatim', () => {
  it.each([
    [987, '987'],
    [12345, '12.3K'],
    [190612940, '190.6M'],
    [1240000000, '1.24B'],
    [529546967, '529.5M'],
    [0, '0'],
    [-12345, '-12.3K'],
    [1000, '1K'],
  ])('compact(%i) === %s', (input, expected) => {
    expect(compact(input)).toBe(expected)
  })
})

describe('cost / percent', () => {
  it('formats dollars to two decimals', () => {
    expect(cost(411.18772974999945)).toBe('$411.19')
    expect(cost(0)).toBe('$0.00')
  })
  it('drops the decimal on whole percentages, keeps one otherwise', () => {
    expect(percent(51)).toBe('51%')
    expect(percent(82.5)).toBe('82.5%')
  })
})

describe('limitLevel — derived from utilization, never from limits.*.severity', () => {
  // severity is Anthropic's vendor string ("normal" at both 51% and 97%),
  // so it can never drive the colour.
  it('treats a boundary value as the more severe band', () => {
    expect(limitLevel(79.9, 80, 95)).toBe('ok')
    expect(limitLevel(80, 80, 95)).toBe('warn')
    expect(limitLevel(94.9, 80, 95)).toBe('warn')
    expect(limitLevel(95, 80, 95)).toBe('crit')
    expect(limitLevel(100, 80, 95)).toBe('crit')
  })
  it('honours custom thresholds from /api/config', () => {
    expect(limitLevel(51, 50, 60)).toBe('warn')
    expect(limitLevel(51, 80, 95)).toBe('ok')
    expect(limitLevel(97, 96, 99)).toBe('warn')
  })
})

describe('minutesUntil / formatDuration / resetsIn', () => {
  const now = Date.parse('2026-08-20T00:00:00Z')
  it('returns null for a missing or unparsable timestamp', () => {
    expect(minutesUntil(null, now)).toBeNull()
    expect(minutesUntil(undefined, now)).toBeNull()
    expect(minutesUntil('not a date', now)).toBeNull()
  })
  it('measures whole minutes to an ISO reset', () => {
    expect(minutesUntil('2026-08-20T04:59:59.595435+00:00', now)).toBe(299)
    expect(minutesUntil('2026-08-19T23:00:00+00:00', now)).toBe(-60)
  })
  it('formats durations coarsely', () => {
    expect(formatDuration(0)).toBe('0m')
    expect(formatDuration(45)).toBe('45m')
    expect(formatDuration(299)).toBe('4h 59m')
    expect(formatDuration(120)).toBe('2h')
    expect(formatDuration(5820)).toBe('4d 1h')
  })
  it('falls back to the localized "resetting now" label at or past the reset', () => {
    expect(resetsIn('2026-08-19T23:00:00+00:00', now, 'resetting now')).toBe('resetting now')
    expect(resetsIn('2026-08-20T00:00:00Z', now, 'resetting now')).toBe('resetting now')
    expect(resetsIn(null, now, 'resetting now')).toBe('')
    expect(resetsIn('2026-08-20T04:59:59.595435+00:00', now, 'resetting now')).toBe('4h 59m')
  })
})

describe('isStale — driven by refresh_interval from /api/config, never a hardcoded 120', () => {
  const updated = 1787196320 // unix SECONDS, as the engine writes it
  it('is fresh inside the tolerance window', () => {
    expect(isStale(updated, 120, (updated + 120) * 1000)).toBe(false)
    expect(isStale(updated, 120, (updated + 120 * STALE_FACTOR) * 1000)).toBe(false)
  })
  it('is stale once past interval * STALE_FACTOR', () => {
    expect(isStale(updated, 120, (updated + 120 * STALE_FACTOR + 1) * 1000)).toBe(true)
  })
  it('scales with a configured interval rather than assuming 120', () => {
    const now = (updated + 700) * 1000
    expect(isStale(updated, 120, now)).toBe(true) // 700s > 120*2.5
    expect(isStale(updated, 600, now)).toBe(false) // 700s < 600*2.5
  })
  it('is not stale when updated_at is missing (nothing published yet)', () => {
    expect(isStale(0, 120, Date.now())).toBe(false)
    expect(isStale(undefined, 120, Date.now())).toBe(false)
  })
  it('does not treat a clock skew into the future as stale', () => {
    expect(isStale(updated, 120, (updated - 5000) * 1000)).toBe(false)
  })
})

describe('formatCaughtAt — caught_at is float | None in the engine', () => {
  it('returns an empty string for null rather than 12/31/1969', () => {
    expect(formatCaughtAt(null)).toBe('')
    expect(formatCaughtAt(undefined)).toBe('')
    expect(formatCaughtAt(0)).toBe('')
  })
  it('renders a real epoch-seconds timestamp', () => {
    const text = formatCaughtAt(1786000000, 'en-US', 'UTC')
    expect(text).not.toBe('')
    expect(text).not.toMatch(/1969/)
    expect(text).toMatch(/2026/)
  })
})
