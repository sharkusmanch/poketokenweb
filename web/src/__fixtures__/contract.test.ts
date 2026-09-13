import { describe, it, expect } from 'vitest'
import { eggState, monState } from './index'

/**
 * Guards the fixtures against drifting from the engine. If the payload shape
 * changes, this fails here rather than silently making every component test
 * assert against a shape production never produces.
 */
describe('fixture ↔ engine contract', () => {
  it('the egg payload is the real probe output', () => {
    expect(eggState.schema_version).toBe(1)
    expect(typeof eggState.updated_at).toBe('number')
    // unix SECONDS, not milliseconds: ~1.78e9, not ~1.78e12.
    expect(eggState.updated_at).toBeLessThan(2e10)
    expect(Object.keys(eggState.strings)).toHaveLength(78)
    // Settings used to be the one screen the engine had no string for; it now
    // carries one, so no label in the app is hardcoded English any more.
    expect(eggState.strings).toHaveProperty('settings')
    expect(eggState.strings.active).toBe('Active')
    expect(eggState.burn).toEqual({})
    expect(eggState.bag).toEqual([])
    expect(eggState.dex).toEqual([])
  })

  it('the egg companion carries no mon fields', () => {
    const companion = eggState.companion
    expect(companion.stage).toBe('egg')
    expect(companion).not.toHaveProperty('species_id')
    expect(companion).not.toHaveProperty('evo_line')
    expect(companion).toHaveProperty('egg_progress')
  })

  it('the mon companion uses evo_line (never "line")', () => {
    const companion = monState.companion
    expect(companion.stage).toBe('mon')
    if (companion.stage !== 'mon') throw new Error('unreachable')
    expect(companion).not.toHaveProperty('line')
    expect(companion.evo_line).toHaveLength(3)
    expect(companion.is_shiny).toBe(true)
    expect(companion.rarity).toBe('rare')
  })

  it('shop rows use the COLON form for egg tiers and always carry an emoji', () => {
    const keys = eggState.shop.map((entry) => entry.key)
    expect(keys).toContain('egg:uncommon')
    expect(keys).toContain('egg:rare')
    expect(keys).not.toContain('egg_rare')
    expect(eggState.shop.every((entry) => entry.emoji.length > 0)).toBe(true)
  })

  it('a catch_log entry really can have caught_at: null', () => {
    expect(monState.catch_log.some((entry) => entry.caught_at === null)).toBe(true)
  })

  it('limits severity is useless for colouring', () => {
    // 97% and 82.5% both report "normal".
    expect(monState.limits.session?.severity).toBe('normal')
    expect(monState.limits.weekly?.severity).toBe('normal')
    expect(monState.limits.session?.utilization).toBe(97)
  })

  it('burn, when populated, has exactly rate_per_minute/minutes_to_full/eta_text', () => {
    expect(Object.keys(monState.burn.session ?? {}).sort()).toEqual([
      'eta_text',
      'minutes_to_full',
      'rate_per_minute',
    ])
    expect(monState.burn.weekly?.minutes_to_full).toBeNull()
  })

  it('sprite paths arrive as /sprites/<file> URLs or ""', () => {
    expect(monState.companion.sprite_path).toMatch(/^\/sprites\//)
    expect(eggState.companion.sprite_path).toBe('')
  })
})
