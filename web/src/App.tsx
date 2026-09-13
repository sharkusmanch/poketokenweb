import { useCallback, useEffect, useRef, useState } from 'react'
import type { AppConfig, AppEvent, StatePayload, TabId } from './types'
import { fetchConfig, fetchEvents, fetchState, postCommand, postConfig } from './lib/api'
import { isStale } from './lib/format'
import { TabBar } from './components/TabBar'
import { Events } from './components/Events'
import { Home } from './tabs/Home'
import { Shop } from './tabs/Shop'
import { Bag } from './tabs/Bag'
import { Collection } from './tabs/Collection'
import { Settings } from './tabs/Settings'
import './styles.css'

/** Used only until GET /api/config answers; never shown as a settings value. */
const FALLBACK_INTERVAL = 120

/**
 * Shortest gap between two reconnect-triggered reads.
 *
 * A flapping network fires `online` repeatedly, and tab switching fires
 * `visibilitychange` on every switch. Without this, putting the phone down and
 * picking it up a few times would hammer the daemon with full re-reads, and
 * each one walks the scan cache.
 */
export const RECONNECT_DEBOUNCE_MS = 5_000

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function App() {
  const [state, setState] = useState<StatePayload | null>(null)
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [commandError, setCommandError] = useState<string | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [tab, setTab] = useState<TabId>('home')
  const [showEvents, setShowEvents] = useState(false)
  const [events, setEvents] = useState<AppEvent[]>([])
  const [eventsLoading, setEventsLoading] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  const loadState = useCallback(async () => {
    try {
      const payload = await fetchState()
      setState(payload)
      setLoadError(null)
    } catch (error) {
      setLoadError(message(error))
    } finally {
      setNow(Date.now())
    }
  }, [])

  const loadConfig = useCallback(async () => {
    try {
      setConfig(await fetchConfig())
    } catch (error) {
      // A settings read failing must not blank the whole app; the state view
      // still works with the fallback interval.
      setLoadError((previous) => previous ?? message(error))
    }
  }, [])

  useEffect(() => {
    void loadState()
    void loadConfig()
  }, [loadState, loadConfig])

  // Poll on the CONFIGURED interval — re-armed whenever it changes.
  const interval = config?.refresh_interval ?? FALLBACK_INTERVAL
  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadState()
    }, interval * 1000)
    return () => window.clearInterval(timer)
  }, [interval, loadState])

  /**
   * Read again the moment the app can actually reach the server.
   *
   * On a phone the interval timer is the wrong instrument: the tab is frozen
   * while backgrounded, so coming back to it showed whatever was on screen
   * when it was put down — with the staleness banner up — until the next tick,
   * which could be minutes away.
   */
  const lastReconnect = useRef(0)
  useEffect(() => {
    const refreshIfDue = () => {
      const now = Date.now()
      if (now - lastReconnect.current < RECONNECT_DEBOUNCE_MS) return
      lastReconnect.current = now
      void loadState()
    }
    const onVisible = () => {
      if (document.visibilityState === 'visible') refreshIfDue()
    }
    window.addEventListener('online', refreshIfDue)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.removeEventListener('online', refreshIfDue)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [loadState])

  const runCommand = useCallback(
    async (name: 'buy' | 'use', key: string) => {
      setPending(key)
      setCommandError(null)
      try {
        await postCommand(name, { key })
        // The daemon applies the command on its next drain; re-read so the
        // bag/shop stop showing pre-command counts.
        await loadState()
      } catch (error) {
        setCommandError(message(error))
      } finally {
        setPending(null)
      }
    },
    [loadState],
  )

  const refresh = useCallback(async () => {
    setCommandError(null)
    try {
      await postCommand('refresh', {})
    } catch (error) {
      setCommandError(message(error))
    }
    await loadState()
  }, [loadState])

  const saveSetting = useCallback(
    async (key: keyof AppConfig, value: string | number) => {
      await postConfig(key, value)
      // Re-read rather than trusting the local value: the server is the only
      // thing that knows what was actually persisted.
      await loadConfig()
    },
    [loadConfig],
  )

  const openEvents = useCallback(async () => {
    setShowEvents(true)
    setEventsLoading(true)
    try {
      setEvents(await fetchEvents())
    } catch (error) {
      setCommandError(message(error))
    } finally {
      setEventsLoading(false)
    }
  }, [])

  if (!state) {
    return (
      <div className="app app-centred">
        {loadError ? (
          <p className="alert" role="alert">
            {loadError}
          </p>
        ) : (
          <p className="loading" data-testid="loading">
            …
          </p>
        )}
      </div>
    )
  }

  const strings = state.strings
  const effectiveConfig: AppConfig = config ?? {
    refresh_interval: FALLBACK_INTERVAL,
    warn_threshold: 80,
    crit_threshold: 95,
    limit_display_mode: 'both',
    language: 'en',
    growth_difficulty: 1,
    shop_difficulty: 1,
  }
  const stale = isStale(state.updated_at, effectiveConfig.refresh_interval, now)

  return (
    <div className="app">
      <header className="appbar">
        <span className="appbar-title">PokeToken</span>
        <div className="appbar-actions">
          <button type="button" className="btn btn-ghost" onClick={() => void openEvents()}>
            Recent activity
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => void refresh()}>
            {strings.refresh}
          </button>
        </div>
      </header>

      {stale ? (
        <p className="banner banner-warn" data-testid="stale">
          {strings.stale_warning}
        </p>
      ) : null}
      {loadError ? (
        <p className="banner banner-error" role="alert">
          {loadError}
        </p>
      ) : null}

      <main className="content">
        {showEvents ? (
          <Events
            events={events}
            strings={strings}
            loading={eventsLoading}
            onClose={() => setShowEvents(false)}
          />
        ) : (
          <>
            {tab === 'home' && <Home state={state} config={effectiveConfig} now={now} />}
            {tab === 'shop' && (
              <Shop
                entries={state.shop}
                strings={strings}
                pending={pending}
                error={commandError}
                onBuy={(key) => void runCommand('buy', key)}
              />
            )}
            {tab === 'bag' && (
              <Bag
                entries={state.bag}
                strings={strings}
                pending={pending}
                error={commandError}
                onUse={(key) => void runCommand('use', key)}
              />
            )}
            {tab === 'collection' && <Collection state={state} />}
            {tab === 'settings' && (
              <Settings config={effectiveConfig} strings={strings} onSave={saveSetting} />
            )}
          </>
        )}
      </main>

      <TabBar
        tab={tab}
        strings={strings}
        onSelect={(next) => {
          setShowEvents(false)
          setTab(next)
        }}
      />
    </div>
  )
}

export default App
