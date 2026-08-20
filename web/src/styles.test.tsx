import { describe, it, expect, beforeAll, vi } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { App } from './App'
import * as api from './lib/api'
import { ApiError } from './lib/api'
import { Home } from './tabs/Home'
import { Shop } from './tabs/Shop'
import { Bag } from './tabs/Bag'
import { Collection } from './tabs/Collection'
import { Settings } from './tabs/Settings'
import { Events } from './components/Events'
import { Limits } from './components/Limits'
import { Sprite } from './components/Sprite'
import { clone, defaultConfig, eggState, monState, sampleEvents } from './__fixtures__'

vi.mock('./lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./lib/api')>()
  return {
    ...actual,
    fetchState: vi.fn(),
    fetchConfig: vi.fn(),
    fetchEvents: vi.fn(),
    postCommand: vi.fn(),
    postConfig: vi.fn(),
  }
})

/**
 * Requirement 2: styles.css must be a REAL file and every class the app emits
 * must have a rule. The previous attempt described this stylesheet in prose,
 * so `vite build` died on the missing import while tests stayed green.
 *
 * The class list is harvested from the RENDERED DOM rather than by reading the
 * source: that way it can only ever contain classes the app really emits, and
 * a new unstyled element fails this test the moment it renders anywhere.
 */
const css = readFileSync(join(process.cwd(), 'src', 'styles.css'), 'utf8')
const emitted = new Set<string>()

function harvest() {
  document.querySelectorAll<HTMLElement>('[class]').forEach((node) => {
    node.classList.forEach((name) => emitted.add(name))
  })
}

function hasRule(className: string): boolean {
  return new RegExp(`\\.${className.replace(/-/g, '\\-')}(?![\\w-])`).test(css)
}

beforeAll(async () => {
  const user = userEvent.setup()
  const strings = eggState.strings
  const noop = vi.fn()
  const affordable = clone(eggState.shop).map((entry) => ({ ...entry, affordable: true }))

  // --- plain component renders, both companion stages --------------------
  render(<Home state={eggState} config={defaultConfig} />)
  harvest()
  cleanup()

  render(<Home state={monState} config={defaultConfig} />)
  harvest()
  cleanup()

  render(<Limits limits={{}} strings={strings} config={defaultConfig} />)
  harvest()
  cleanup()

  render(<Shop entries={affordable} strings={strings} onBuy={noop} pending="mint" error="nope" />)
  // open the egg confirmation so its classes render too
  await user.click(within(screen.getByTestId('shop-egg')).getByRole('button', { name: strings.buy }))
  harvest()
  cleanup()

  render(<Bag entries={monState.bag} strings={strings} onUse={noop} pending={null} error="nope" />)
  harvest()
  cleanup()

  render(<Bag entries={[]} strings={strings} onUse={noop} pending={null} />)
  harvest()
  cleanup()

  render(<Collection state={monState} />)
  harvest()
  cleanup()

  render(<Collection state={monState} initialView="catch_log" />)
  harvest()
  cleanup()

  render(<Events events={sampleEvents} strings={strings} onClose={noop} loading={false} />)
  harvest()
  cleanup()

  render(<Sprite src="" alt="Raichu" />)
  render(<Sprite src="" alt="Egg" emoji="🥚" />)
  harvest()
  cleanup()

  // Settings, including the saved marker and the rejection alert.
  const onSave = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('boom'))
  render(<Settings config={defaultConfig} strings={strings} onSave={onSave} />)
  const input = screen.getByLabelText(/refresh interval/i)
  await user.clear(input)
  await user.type(input, '300{Enter}')
  await screen.findByTestId('save-ok')
  harvest()
  await user.clear(input)
  await user.type(input, '31{Enter}')
  await screen.findByRole('alert')
  harvest()
  cleanup()

  // --- the App shell, fresh / stale / failed ------------------------------
  vi.mocked(api.fetchConfig).mockResolvedValue(defaultConfig)
  vi.mocked(api.fetchEvents).mockResolvedValue(sampleEvents)
  vi.mocked(api.fetchState).mockResolvedValue(monState)
  vi.useFakeTimers({ shouldAdvanceTime: true })

  vi.setSystemTime(new Date((monState.updated_at + 30) * 1000))
  render(<App />)
  await screen.findByTestId('tab-home')
  harvest()
  cleanup()

  vi.setSystemTime(new Date((monState.updated_at + 9000) * 1000))
  render(<App />)
  await screen.findByTestId('stale')
  harvest()
  cleanup()

  vi.mocked(api.fetchState).mockRejectedValue(new ApiError('state not published yet', 503))
  render(<App />)
  await screen.findByRole('alert')
  harvest()
  cleanup()
  vi.useRealTimers()
})

describe('styles.css', () => {
  it('exists as a real file with the required dark base', () => {
    expect(css.length).toBeGreaterThan(2000)
    expect(css).toContain('--bg: #0d1117')
  })

  it('harvested a realistic number of classes from real renders', () => {
    expect(emitted.size).toBeGreaterThan(50)
  })

  it('has a rule for every class the app actually renders', () => {
    const missing = [...emitted].filter((name) => !hasRule(name)).sort()
    expect(missing).toEqual([])
  })

  it('covers the interpolated class families', () => {
    for (const level of ['ok', 'warn', 'crit']) {
      expect(hasRule(`limit-${level}`)).toBe(true)
      expect(hasRule(`limit-fill-${level}`)).toBe(true)
    }
    for (const rarity of ['legendary', 'rare', 'uncommon', 'common']) {
      expect(hasRule(`rarity-${rarity}`)).toBe(true)
    }
    for (const size of ['sm', 'md', 'lg']) {
      expect(hasRule(`sprite-${size}`)).toBe(true)
    }
  })

  it('renders sprites pixelated', () => {
    expect(css).toMatch(/\.sprite\s*\{[^}]*image-rendering:\s*pixelated/)
  })

  it('keeps the fixed tab bar clear of the home indicator', () => {
    expect(css).toMatch(/\.tabbar\s*\{[^}]*position:\s*fixed/)
    expect(css).toMatch(/\.tabbar\s*\{[^}]*env\(safe-area-inset-bottom\)/)
  })

  it('is imported by the app so a missing file breaks the build loudly', () => {
    expect(readFileSync(join(process.cwd(), 'src', 'App.tsx'), 'utf8')).toContain(
      "import './styles.css'",
    )
  })
})
