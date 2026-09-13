/**
 * The data contract, measured against a real payload and the engine source
 * (poketokenbar/state.py:build, companion_store.py, poketokenweb/api.py).
 *
 * Rules that bit a previous attempt:
 *   - `companion` is a DISCRIMINATED UNION on `stage`. An egg carries none of
 *     the mon fields; reading `companion.name` on one is a type error here.
 *   - the evolution line is `evo_line`, not `line`.
 *   - `caught_at` is `float | None`.
 *   - `burn`, `periods`, `limits`, `celebration`, `provider_status` are all
 *     `{}` in the real world; every window inside `limits` is nullable too.
 *   - `updated_at` is unix SECONDS.
 *   - sprite paths reach the browser already rewritten to `/sprites/<file>`
 *     by poketokenweb.api.public_state, and are `""` when there is none.
 */

export type Rarity = 'legendary' | 'rare' | 'uncommon' | 'common'
export type DisplayState = string // status_<kind> keys the engine localizes for us

/** Fields both companion stages share. */
export interface CompanionCommon {
  label: string
  sprite_path: string
  dex_count: number
  spendable_tokens: number
  spendable_text: string
  display_state: DisplayState
  status_message: string
}

export interface EggCompanion extends CompanionCommon {
  stage: 'egg'
  egg_usage: number
  egg_progress: number
  egg_tier: string | null
}

export interface EvoForm {
  species_id: number
  name: string
  sprite_path: string
  current: boolean
  reached: boolean
}

export interface MonCompanion extends CompanionCommon {
  stage: 'mon'
  species_id: number
  name: string
  is_final_form: boolean
  remaining_tokens: number
  remaining_text: string
  /** "graduation" | "next evolution" — matches strings.graduation/next_evolution. */
  goal: string
  evo_line: EvoForm[]
  is_shiny: boolean
  nature: string | null
  rarity: Rarity | string
  stage_index: number
  total_forms: number
  used_at_stage: number
  stage_threshold: number
  stage_progress: number
}

export type Companion = EggCompanion | MonCompanion

export interface ShopEntry {
  key: string
  kind: 'item' | 'egg'
  price: number
  price_text: string
  label: string
  description: string
  badge: string
  sprite_path: string
  emoji: string
  owned: boolean
  owned_count: number
  affordable: boolean
}

export interface BagEntry {
  key: string
  label: string
  description: string
  effect: string
  sprite_path: string
  emoji: string
  count: number
  usable: boolean
  passive: boolean
}

export interface DexEntry {
  final_id: number
  species_id: number
  name: string
  rarity: Rarity | string
  is_shiny: boolean
  /** Backed only by the CURRENT companion — an egg purchase erases it. */
  is_raising: boolean
  sprite_path: string
}

export interface ChainLink {
  species_id: number
  name: string
  sprite_path: string
}

export interface CatchLogEntry {
  rarity: Rarity | string
  nature: string | null
  is_shiny: boolean
  chain: ChainLink[]
  /** float | None — null on entries written before the field existed. */
  caught_at: number | null
  raised_text: string
  raising: boolean
  /** Let go to buy a fresh egg. The species stays in the Pokedex either way;
   *  only this log distinguishes a release from a graduation. */
  released?: boolean
}

export interface LimitWindow {
  utilization: number
  resets_at: string | null
  /** Anthropic's vendor string ("normal" at 51% AND 97%). Display only —
   *  never derive a colour from it; see lib/format.limitLevel. */
  severity: string
}

/** `{}` when there are no credentials; each window may independently be null. */
export interface Limits {
  session?: LimitWindow | null
  weekly?: LimitWindow | null
  plan?: string | null
  account?: {
    uuid?: string
    email?: string
    display_name?: string
    organization?: string
  }
}

export interface Period {
  tokens: number
  cost: number
}

/** `{}` until the burn tracker has MIN_SAMPLES=3 samples. */
export interface BurnForecast {
  rate_per_minute: number
  /** null when the slope is flat or falling. */
  minutes_to_full: number | null
  eta_text: string
}

export interface ProviderUsage {
  total_tokens: number
  total_tokens_text: string
  total_tokens_compact: string
  total_cost: number
  input_tokens: number
  output_tokens: number
  cache_creation_tokens: number
  cache_read_tokens: number
}

export interface Celebration {
  kind?: string
  title?: string
  detail?: string
}

export type RarityCounts = Partial<Record<Rarity, number>>

export interface StatePayload {
  schema_version: number
  /** unix SECONDS (float). */
  updated_at: number
  scanning: boolean
  errors: string[]
  today: {
    total_tokens: number
    total_cost: number
    tokens_grouped: string
    tokens_compact: string
    cost_text: string
  }
  providers: Record<string, ProviderUsage>
  limits: Limits
  companion: Companion
  shop: ShopEntry[]
  bag: BagEntry[]
  dex: DexEntry[]
  catch_log: CatchLogEntry[]
  rarity_counts: RarityCounts
  catch_counts: RarityCounts
  periods: { week?: Period; month?: Period }
  strings: Record<string, string>
  celebration: Celebration
  burn: { session?: BurnForecast; weekly?: BurnForecast }
  provider_status: Record<string, unknown>
  panel: {
    tokens_text: string
    cost_text: string
    limit_text: string
    limit_windows: { value: number; text: string; level: string }[]
    sprite_path: string
  }
}

/** Exactly the five keys poketokenweb.api.WEB_CONFIG_KEYS exposes. */
export interface AppConfig {
  refresh_interval: number
  warn_threshold: number
  crit_threshold: number
  limit_display_mode: 'session' | 'weekly' | 'both'
  language: 'en' | 'ko' | 'ja' | 'es'
}

export type EventKind = 'hatched' | 'evolved' | 'graduated' | 'shiny' | 'ditto'

export interface AppEvent {
  kind: EventKind | string
  title: string
  detail: string
  /** unix SECONDS. */
  published_at: number
}

export type CommandName = 'buy' | 'use' | 'refresh'
export type TabId = 'home' | 'shop' | 'bag' | 'collection' | 'settings'
