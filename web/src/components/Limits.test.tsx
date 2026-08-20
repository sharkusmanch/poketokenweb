import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Limits } from './Limits'
import { clone, defaultConfig, eggState, monState } from '../__fixtures__'
import type { AppConfig } from '../types'

const strings = eggState.strings
const now = Date.parse('2026-08-20T00:00:00Z')

function renderLimits(limits = monState.limits, config: AppConfig = defaultConfig) {
  return render(<Limits limits={limits} strings={strings} config={config} now={now} />)
}

/**
 * Requirement 3. limits.*.severity is Anthropic's vendor string and reads
 * "normal" at 51% AND at 97%; a previous build rendered class="limit normal"
 * at both and never warned. The level is derived here from utilization
 * against warn_threshold/crit_threshold out of /api/config.
 */
describe('limit colour levels', () => {
  it('never uses the vendor severity string as the CSS state', () => {
    renderLimits()
    const session = screen.getByTestId('limit-session')
    expect(session.className).not.toContain('normal')
    expect(session).toHaveAttribute('data-level', 'crit') // 97% >= crit 95
  })

  it('colours each window independently', () => {
    renderLimits() // session 97 -> crit, weekly 82.5 -> warn
    expect(screen.getByTestId('limit-session')).toHaveAttribute('data-level', 'crit')
    expect(screen.getByTestId('limit-weekly')).toHaveAttribute('data-level', 'warn')
  })

  it('is ok below the warn threshold — the real 51%/36% payload', () => {
    renderLimits(eggState.limits)
    expect(screen.getByTestId('limit-session')).toHaveAttribute('data-level', 'ok')
    expect(screen.getByTestId('limit-weekly')).toHaveAttribute('data-level', 'ok')
  })

  it.each([
    [79.9, 'ok'],
    [80, 'warn'],
    [94.9, 'warn'],
    [95, 'crit'],
    [100, 'crit'],
  ])('%f%% renders data-level=%s at the default 80/95', (utilization, level) => {
    const limits = clone(monState.limits)
    limits.session = { utilization, resets_at: null, severity: 'normal' }
    renderLimits(limits)
    expect(screen.getByTestId('limit-session')).toHaveAttribute('data-level', level)
  })

  it('honours thresholds coming from /api/config, not hardcoded ones', () => {
    renderLimits(eggState.limits, { ...defaultConfig, warn_threshold: 50, crit_threshold: 60 })
    // 51% is fine at 80/95 but a warning at 50/60.
    expect(screen.getByTestId('limit-session')).toHaveAttribute('data-level', 'warn')
  })

  it('formats percentages like the engine and shows the reset countdown', () => {
    renderLimits()
    expect(screen.getByTestId('limit-session')).toHaveTextContent('97%')
    expect(screen.getByTestId('limit-weekly')).toHaveTextContent('82.5%')
    expect(screen.getByTestId('limit-session')).toHaveTextContent('4h 59m')
    expect(screen.getByText(strings.five_hour_session)).toBeInTheDocument()
  })
})

describe('missing limits', () => {
  it('says limits are unavailable for the {} payload (no credentials)', () => {
    renderLimits({})
    expect(screen.getByText(strings.limits_unavailable)).toBeInTheDocument()
    expect(screen.queryByTestId('limit-session')).toBeNull()
  })

  it('renders only the window that exists when the other is null', () => {
    renderLimits({ session: null, weekly: monState.limits.weekly, plan: 'max' })
    expect(screen.queryByTestId('limit-session')).toBeNull()
    expect(screen.getByTestId('limit-weekly')).toBeInTheDocument()
  })

  it('drops the countdown when resets_at is absent', () => {
    renderLimits({ session: { utilization: 12, resets_at: null, severity: 'normal' } })
    expect(screen.getByTestId('limit-session')).toHaveTextContent('12%')
    expect(screen.queryByTestId('limit-session-reset')).toBeNull()
  })

  it('says "resetting now" once the window is past its reset', () => {
    renderLimits({
      session: { utilization: 12, resets_at: '2026-08-19T23:00:00+00:00', severity: 'normal' },
    })
    expect(screen.getByTestId('limit-session-reset')).toHaveTextContent(strings.resetting_now)
  })
})
