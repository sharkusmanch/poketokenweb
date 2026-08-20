/**
 * The HTTP client. Every route here was read off poketokenweb/server.py.
 *
 * Session expiry (requirement 8): behind an auth proxy an expired session is
 * answered with the login page — HTTP 200 and text/html where JSON was asked
 * for. That, and only that, is an expiry. An nginx 502 error page is also HTML
 * but means the backend is down; reloading on it produces an infinite reload
 * loop that never recovers. Reloads are additionally capped by a
 * sessionStorage counter so even a mislabelled 200/HTML cannot storm.
 */
import type { AppConfig, AppEvent, CommandName, StatePayload } from '../types'

export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export const SESSION_RELOAD_KEY = 'ptw:session-reloads'
export const MAX_SESSION_RELOADS = 2

type Reload = () => void

const defaultReload: Reload = () => {
  window.location.reload()
}

/** OK + HTML only. A 4xx/5xx HTML error page is a failure, not an expiry. */
export function isSessionExpiry(status: number, contentType: string | null): boolean {
  if (status < 200 || status >= 300) return false
  return (contentType ?? '').toLowerCase().includes('text/html')
}

function readCounter(): number {
  const raw = window.sessionStorage.getItem(SESSION_RELOAD_KEY)
  const parsed = raw === null ? 0 : Number.parseInt(raw, 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}

/**
 * Reload once for an expired session, up to MAX_SESSION_RELOADS times per tab.
 * Returns whether a reload was actually triggered.
 */
export function requestSessionReload(reload: Reload = defaultReload): boolean {
  const count = readCounter() + 1
  window.sessionStorage.setItem(SESSION_RELOAD_KEY, String(count))
  if (count > MAX_SESSION_RELOADS) return false
  reload()
  return true
}

/** A real JSON answer proves the session works; give the budget back. */
export function noteSuccessfulResponse(): void {
  if (window.sessionStorage.getItem(SESSION_RELOAD_KEY) !== null) {
    window.sessionStorage.removeItem(SESSION_RELOAD_KEY)
  }
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'error' in body) {
      const value = (body as { error: unknown }).error
      if (typeof value === 'string' && value) return value
    }
  } catch {
    /* not JSON — fall through to a generic message */
  }
  return `request failed (${response.status})`
}

async function request<T>(
  path: string,
  init: RequestInit | undefined,
  reload: Reload,
): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch (cause) {
    throw new ApiError(cause instanceof Error ? cause.message : 'network error', 0)
  }

  const contentType = response.headers.get('content-type')
  if (isSessionExpiry(response.status, contentType)) {
    const reloaded = requestSessionReload(reload)
    throw new ApiError(
      reloaded ? 'session expired — reloading' : 'session expired — reload it manually',
      response.status,
    )
  }

  if (!response.ok) {
    throw new ApiError(await errorMessage(response), response.status)
  }

  noteSuccessfulResponse()
  return (await response.json()) as T
}

export function fetchState(reload: Reload = defaultReload): Promise<StatePayload> {
  return request<StatePayload>('/api/state', { headers: { Accept: 'application/json' } }, reload)
}

export function fetchConfig(reload: Reload = defaultReload): Promise<AppConfig> {
  return request<AppConfig>('/api/config', { headers: { Accept: 'application/json' } }, reload)
}

/** The server answers {"events": [...]}, newest first — not a bare array. */
export async function fetchEvents(reload: Reload = defaultReload): Promise<AppEvent[]> {
  const body = await request<unknown>(
    '/api/events',
    { headers: { Accept: 'application/json' } },
    reload,
  )
  if (Array.isArray(body)) return body as AppEvent[]
  if (body && typeof body === 'object' && Array.isArray((body as { events?: unknown }).events)) {
    return (body as { events: AppEvent[] }).events
  }
  return []
}

function post<T>(path: string, payload: unknown, reload: Reload): Promise<T> {
  return request<T>(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(payload),
    },
    reload,
  )
}

export interface CommandAck {
  status: string
  name?: string
  args?: Record<string, string>
}

export function postCommand(
  name: CommandName,
  args: { key: string } | Record<string, never> = {},
  reload: Reload = defaultReload,
): Promise<CommandAck> {
  return post<CommandAck>('/api/command', { name, args }, reload)
}

export interface ConfigAck {
  status: string
  key?: string
  value?: string
}

export function postConfig(
  key: keyof AppConfig,
  value: string | number,
  reload: Reload = defaultReload,
): Promise<ConfigAck> {
  return post<ConfigAck>('/api/config', { key, value }, reload)
}
