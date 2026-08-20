import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  ApiError,
  MAX_SESSION_RELOADS,
  SESSION_RELOAD_KEY,
  fetchConfig,
  fetchEvents,
  fetchState,
  isSessionExpiry,
  noteSuccessfulResponse,
  postCommand,
  postConfig,
  requestSessionReload,
} from './api'

type FetchFn = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  })
}

function htmlResponse(status = 200): Response {
  return new Response('<!doctype html><title>Sign in</title>', {
    status,
    headers: { 'Content-Type': 'text/html; charset=utf-8' },
  })
}

function installFetch(fn: FetchFn) {
  const spy = vi.fn(fn)
  vi.stubGlobal('fetch', spy)
  return spy
}

beforeEach(() => {
  window.sessionStorage.clear()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('reads', () => {
  it('GETs /api/state and returns the payload', async () => {
    const spy = installFetch(async () => jsonResponse({ schema_version: 1 }))
    await expect(fetchState()).resolves.toEqual({ schema_version: 1 })
    expect(spy.mock.calls[0][0]).toBe('/api/state')
  })

  it('unwraps the {events: [...]} envelope the server actually returns', async () => {
    // NOT a bare array: server._events() answers {"events": [...]}.
    installFetch(async () =>
      jsonResponse({ events: [{ kind: 'hatched', title: 'It hatched!', detail: '', published_at: 1 }] }),
    )
    const events = await fetchEvents()
    expect(events).toHaveLength(1)
    expect(events[0].kind).toBe('hatched')
  })

  it('tolerates a bare array from /api/events', async () => {
    installFetch(async () => jsonResponse([{ kind: 'shiny', title: 'x', detail: '', published_at: 2 }]))
    await expect(fetchEvents()).resolves.toHaveLength(1)
  })

  it('returns [] when the events body is neither', async () => {
    installFetch(async () => jsonResponse({ nope: true }))
    await expect(fetchEvents()).resolves.toEqual([])
  })

  it('reads the five exposed settings', async () => {
    installFetch(async () =>
      jsonResponse({
        refresh_interval: 120,
        warn_threshold: 80,
        crit_threshold: 95,
        limit_display_mode: 'both',
        language: 'en',
      }),
    )
    await expect(fetchConfig()).resolves.toMatchObject({ refresh_interval: 120, language: 'en' })
  })
})

describe('writes', () => {
  it('POSTs a buy command with the colon-form egg key', async () => {
    const spy = installFetch(async () => jsonResponse({ status: 'accepted' }, 202))
    await postCommand('buy', { key: 'egg:rare' })
    const [url, init] = [spy.mock.calls[0][0], spy.mock.calls[0][1]]
    expect(url).toBe('/api/command')
    expect(init?.method).toBe('POST')
    expect(init?.headers).toMatchObject({ 'Content-Type': 'application/json' })
    expect(JSON.parse(String(init?.body))).toEqual({ name: 'buy', args: { key: 'egg:rare' } })
  })

  it('POSTs a config change as {key, value}', async () => {
    const spy = installFetch(async () => jsonResponse({ status: 'accepted' }, 202))
    await postConfig('refresh_interval', 300)
    expect(JSON.parse(String(spy.mock.calls[0][1]?.body))).toEqual({
      key: 'refresh_interval',
      value: 300,
    })
  })

  it("surfaces the server's own 400 message", async () => {
    installFetch(async () =>
      jsonResponse({ error: 'refresh_interval must be between 30 and 3600' }, 400),
    )
    await expect(postConfig('refresh_interval', 5)).rejects.toThrow(
      'refresh_interval must be between 30 and 3600',
    )
  })

  it('carries the status code on ApiError', async () => {
    installFetch(async () => jsonResponse({ error: 'state not published yet' }, 503))
    const error = await fetchState().catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(503)
  })
})

describe('session expiry — must never become a reload storm', () => {
  it('is an expiry only when the response is OK and HTML', () => {
    expect(isSessionExpiry(200, 'text/html; charset=utf-8')).toBe(true)
    expect(isSessionExpiry(200, 'application/json')).toBe(false)
    // An nginx 502 error page is HTML but is a backend failure, not an expiry.
    expect(isSessionExpiry(502, 'text/html')).toBe(false)
    expect(isSessionExpiry(500, 'text/html')).toBe(false)
    expect(isSessionExpiry(401, 'text/html')).toBe(false)
    expect(isSessionExpiry(200, null)).toBe(false)
  })

  it('caps reloads with a sessionStorage counter', () => {
    const reload = vi.fn()
    for (let i = 0; i < MAX_SESSION_RELOADS; i += 1) {
      expect(requestSessionReload(reload)).toBe(true)
    }
    expect(reload).toHaveBeenCalledTimes(MAX_SESSION_RELOADS)
    expect(requestSessionReload(reload)).toBe(false)
    expect(reload).toHaveBeenCalledTimes(MAX_SESSION_RELOADS)
    expect(window.sessionStorage.getItem(SESSION_RELOAD_KEY)).toBe(String(MAX_SESSION_RELOADS + 1))
  })

  it('clears the budget once a real JSON response arrives', () => {
    const reload = vi.fn()
    requestSessionReload(reload)
    noteSuccessfulResponse()
    expect(window.sessionStorage.getItem(SESSION_RELOAD_KEY)).toBeNull()
  })

  it('reloads at most once for an OK HTML body, then throws instead', async () => {
    const reload = vi.fn()
    installFetch(async () => htmlResponse(200))
    for (let i = 0; i < MAX_SESSION_RELOADS; i += 1) {
      await expect(fetchState(reload)).rejects.toThrow(ApiError)
    }
    expect(reload).toHaveBeenCalledTimes(MAX_SESSION_RELOADS)
    await expect(fetchState(reload)).rejects.toThrow(ApiError)
    expect(reload).toHaveBeenCalledTimes(MAX_SESSION_RELOADS) // capped, no storm
  })

  it('never reloads for a 502 HTML error page', async () => {
    const reload = vi.fn()
    installFetch(async () => htmlResponse(502))
    await expect(fetchState(reload)).rejects.toThrow(ApiError)
    expect(reload).not.toHaveBeenCalled()
    expect(window.sessionStorage.getItem(SESSION_RELOAD_KEY)).toBeNull()
  })
})
