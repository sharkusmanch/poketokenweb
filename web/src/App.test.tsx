import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { App } from './App'
import * as api from './lib/api'
import { ApiError } from './lib/api'
import { clone, defaultConfig, eggState, monState, sampleEvents } from './__fixtures__'
import type { AppConfig, StatePayload } from './types'

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

function arrange(state: StatePayload = monState, config: AppConfig = defaultConfig) {
  vi.mocked(api.fetchState).mockResolvedValue(state)
  vi.mocked(api.fetchConfig).mockResolvedValue(config)
  vi.mocked(api.fetchEvents).mockResolvedValue(sampleEvents)
  vi.mocked(api.postCommand).mockResolvedValue({ status: 'accepted' })
  vi.mocked(api.postConfig).mockResolvedValue({ status: 'accepted' })
}

async function renderApp(state?: StatePayload, config?: AppConfig) {
  arrange(state, config)
  const user = userEvent.setup()
  render(<App />)
  await screen.findByTestId('tab-home')
  return user
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  // Fresh enough not to be stale at the default interval.
  vi.setSystemTime(new Date((monState.updated_at + 60) * 1000))
  window.sessionStorage.clear()
})

afterEach(() => {
  vi.useRealTimers()
})

/**
 * Requirement 1. The previous App.tsx rendered only
 * `{tab === 'home' && <Home/>}`; the other four components were written and
 * never mounted, so the bundler tree-shook them out entirely.
 */
describe('every tab is wired', () => {
  it('opens on Home', async () => {
    await renderApp()
    expect(screen.getByTestId('tab-home')).toBeInTheDocument()
    expect(screen.getByTestId('mon-name')).toHaveTextContent('Pikachu')
  })

  it('renders the Shop tab with its own content', async () => {
    const user = await renderApp()
    await user.click(screen.getByRole('tab', { name: eggState.strings.shop }))
    expect(screen.getByTestId('tab-shop')).toBeInTheDocument()
    expect(screen.getByTestId('shop-egg:rare')).toBeInTheDocument()
    expect(screen.queryByTestId('tab-home')).toBeNull()
  })

  it('renders the Bag tab with its own content', async () => {
    const user = await renderApp()
    await user.click(screen.getByRole('tab', { name: eggState.strings.bag }))
    expect(screen.getByTestId('tab-bag')).toBeInTheDocument()
    expect(screen.getByTestId('bag-rareCandy')).toBeInTheDocument()
  })

  it('renders the Collection tab with its own content', async () => {
    const user = await renderApp()
    await user.click(screen.getByRole('tab', { name: eggState.strings.collection }))
    expect(screen.getByTestId('tab-collection')).toBeInTheDocument()
    expect(screen.getByTestId('dex-25')).toBeInTheDocument()
  })

  it('renders the Settings tab with its own content', async () => {
    const user = await renderApp()
    await user.click(screen.getByRole('tab', { name: 'Settings' }))
    expect(screen.getByTestId('tab-settings')).toBeInTheDocument()
    expect(screen.getByLabelText(/refresh interval/i)).toHaveValue(120)
  })

  it('renders the recent-events view and comes back', async () => {
    const user = await renderApp()
    await user.click(screen.getByRole('button', { name: /recent activity/i }))
    expect(await screen.findByTestId('events-view')).toBeInTheDocument()
    expect(screen.getByTestId('event-0')).toHaveTextContent('A shiny hatched!')
    await user.click(screen.getByRole('button', { name: /back/i }))
    expect(screen.getByTestId('tab-home')).toBeInTheDocument()
  })

  it('renders the egg payload end to end too', async () => {
    const user = await renderApp(eggState)
    expect(screen.getByText('An egg is warming up.')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: eggState.strings.bag }))
    expect(screen.getByText(eggState.strings.bag_empty)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: eggState.strings.collection }))
    expect(screen.getByText(eggState.strings.no_pokemon_yet)).toBeInTheDocument()
  })
})

describe('staleness banner', () => {
  it('stays hidden while the data is fresh', async () => {
    await renderApp()
    expect(screen.queryByTestId('stale')).toBeNull()
  })

  it('appears once the payload is older than the CONFIGURED interval', async () => {
    vi.setSystemTime(new Date((monState.updated_at + 700) * 1000))
    await renderApp()
    expect(screen.getByTestId('stale')).toHaveTextContent(eggState.strings.stale_warning)
  })

  it('uses refresh_interval from /api/config, not a hardcoded 120', async () => {
    // Same 700s age: stale at 120 (above), fresh at 600.
    vi.setSystemTime(new Date((monState.updated_at + 700) * 1000))
    await renderApp(monState, { ...defaultConfig, refresh_interval: 600 })
    expect(screen.queryByTestId('stale')).toBeNull()
  })
})

describe('commands', () => {
  it('asks for confirmation before buying an egg, then posts the colon key', async () => {
    const state = clone(monState)
    state.shop = state.shop.map((entry) => ({ ...entry, affordable: true }))
    const user = await renderApp(state)
    await user.click(screen.getByRole('tab', { name: eggState.strings.shop }))
    await user.click(
      within(screen.getByTestId('shop-egg:rare')).getByRole('button', { name: eggState.strings.buy }),
    )
    expect(api.postCommand).not.toHaveBeenCalled()
    await user.click(
      within(screen.getByTestId('confirm-egg:rare')).getByRole('button', { name: /confirm/i }),
    )
    expect(api.postCommand).toHaveBeenCalledWith('buy', { key: 'egg:rare' })
  })

  it("surfaces a 400's message from the server", async () => {
    const state = clone(monState)
    state.shop = state.shop.map((entry) => ({ ...entry, affordable: true }))
    const user = await renderApp(state)
    vi.mocked(api.postCommand).mockRejectedValueOnce(new ApiError('not enough tokens', 400))
    await user.click(screen.getByRole('tab', { name: eggState.strings.shop }))
    await user.click(
      within(screen.getByTestId('shop-rareCandy')).getByRole('button', { name: eggState.strings.buy }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('not enough tokens')
  })

  it('uses a bag item and refreshes the state afterwards', async () => {
    const user = await renderApp()
    const before = vi.mocked(api.fetchState).mock.calls.length
    await user.click(screen.getByRole('tab', { name: eggState.strings.bag }))
    await user.click(
      within(screen.getByTestId('bag-rareCandy')).getByRole('button', { name: eggState.strings.use }),
    )
    expect(api.postCommand).toHaveBeenCalledWith('use', { key: 'rareCandy' })
    await waitFor(() =>
      expect(vi.mocked(api.fetchState).mock.calls.length).toBeGreaterThan(before),
    )
  })

  it('asks the daemon to refresh', async () => {
    const user = await renderApp()
    await user.click(screen.getByRole('button', { name: eggState.strings.refresh }))
    expect(api.postCommand).toHaveBeenCalledWith('refresh', {})
  })
})

describe('settings round-trip through the API', () => {
  it('POSTs a changed value and shows the reloaded config', async () => {
    const user = await renderApp()
    await user.click(screen.getByRole('tab', { name: 'Settings' }))
    const input = screen.getByLabelText(/refresh interval/i)
    vi.mocked(api.fetchConfig).mockResolvedValue({ ...defaultConfig, refresh_interval: 300 })
    await user.clear(input)
    await user.type(input, '300')
    await user.tab()
    expect(api.postConfig).toHaveBeenCalledWith('refresh_interval', 300)
    await waitFor(() => expect(screen.getByLabelText(/refresh interval/i)).toHaveValue(300))
  })

  it('shows the rejection message and keeps the old value', async () => {
    const user = await renderApp()
    await user.click(screen.getByRole('tab', { name: 'Settings' }))
    vi.mocked(api.postConfig).mockRejectedValueOnce(
      new ApiError('refresh_interval must be between 30 and 3600', 400),
    )
    const input = screen.getByLabelText(/refresh interval/i)
    await user.clear(input)
    await user.type(input, '5')
    await user.tab()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'refresh_interval must be between 30 and 3600',
    )
    expect(screen.getByLabelText(/refresh interval/i)).toHaveValue(120)
  })
})

describe('loading and failure', () => {
  it('shows a loading state before the first payload', async () => {
    arrange()
    vi.mocked(api.fetchState).mockImplementation(() => new Promise(() => {}))
    render(<App />)
    expect(screen.getByTestId('loading')).toBeInTheDocument()
  })

  it('reports a 503 from a daemon that has not published yet', async () => {
    arrange()
    vi.mocked(api.fetchState).mockRejectedValue(new ApiError('state not published yet', 503))
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('state not published yet')
  })

  it('polls on the configured interval', async () => {
    await renderApp(monState, { ...defaultConfig, refresh_interval: 30 })
    const before = vi.mocked(api.fetchState).mock.calls.length
    await vi.advanceTimersByTimeAsync(30_000)
    expect(vi.mocked(api.fetchState).mock.calls.length).toBeGreaterThan(before)
  })
})

describe('refreshing on reconnect', () => {
  /** jsdom's visibilityState is a getter, so it has to be redefined. */
  function setVisibility(value: DocumentVisibilityState) {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => value,
    })
  }

  function reads(): number {
    return vi.mocked(api.fetchState).mock.calls.length
  }

  it('re-reads the moment the network comes back', async () => {
    await renderApp()
    const before = reads()
    window.dispatchEvent(new Event('online'))
    await waitFor(() => expect(reads()).toBe(before + 1))
  })

  it('re-reads when the tab becomes visible again', async () => {
    // On a phone the interval timer is the wrong instrument: a backgrounded
    // tab is frozen, so coming back showed a stale screen until the next tick.
    await renderApp()
    const before = reads()
    setVisibility('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    await waitFor(() => expect(reads()).toBe(before + 1))
  })

  it('does nothing when the tab is being hidden', async () => {
    await renderApp()
    const before = reads()
    setVisibility('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(reads()).toBe(before)
    setVisibility('visible')
  })

  it('debounces a flapping connection into a single read', async () => {
    await renderApp()
    const before = reads()
    for (let i = 0; i < 5; i += 1) window.dispatchEvent(new Event('online'))
    await waitFor(() => expect(reads()).toBe(before + 1))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(reads()).toBe(before + 1)
  })
})
