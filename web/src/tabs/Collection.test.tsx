import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Collection } from './Collection'
import { clone, eggState, monState } from '../__fixtures__'
import type { StatePayload } from '../types'

const strings = eggState.strings

function renderCollection(state: StatePayload = monState) {
  return render(<Collection state={state} />)
}

describe('Pokédex', () => {
  it('shows the empty string for the real (empty) collection', () => {
    renderCollection(eggState)
    expect(eggState.dex).toEqual([])
    expect(screen.getByText(strings.no_pokemon_yet)).toBeInTheDocument()
  })

  it('lists every collected species', () => {
    renderCollection()
    expect(screen.getAllByTestId(/^dex-/)).toHaveLength(7)
    expect(screen.getByTestId('dex-134')).toHaveTextContent('Vaporeon')
  })

  it('shows the rarity tallies from rarity_counts', () => {
    renderCollection()
    const counts = screen.getByTestId('rarity-counts')
    expect(within(counts).getByTestId('count-legendary')).toHaveTextContent('3')
    expect(within(counts).getByTestId('count-common')).toHaveTextContent('0')
    expect(within(counts).getByText(strings.legendary)).toBeInTheDocument()
  })

  it('marks a shiny and flags a species that is only being raised', () => {
    renderCollection()
    const pikachu = screen.getByTestId('dex-25')
    expect(within(pikachu).getByTestId('shiny-mark')).toBeInTheDocument()
    // is_raising: an egg purchase would erase it, so it is not permanent yet.
    expect(pikachu).toHaveTextContent(strings.raising)
    expect(screen.getByTestId('dex-134')).not.toHaveTextContent(strings.raising)
  })

  it('renders a species with no sprite as a neutral placeholder, never an egg', () => {
    const state = clone(monState)
    state.dex = state.dex.map((entry) => ({ ...entry, sprite_path: '' }))
    renderCollection(state)
    expect(screen.getAllByTestId('sprite-placeholder').length).toBeGreaterThan(0)
    expect(screen.queryByTestId('sprite-emoji')).toBeNull()
  })
})

describe('catch log', () => {
  it('switches to the catch log', async () => {
    const user = userEvent.setup()
    renderCollection()
    await user.click(screen.getByRole('tab', { name: strings.catch_log }))
    expect(screen.getAllByTestId(/^catch-/)).toHaveLength(3)
    expect(screen.queryByTestId('dex-25')).toBeNull()
  })

  it('leads with the companion still being raised', async () => {
    const user = userEvent.setup()
    renderCollection()
    await user.click(screen.getByRole('tab', { name: strings.catch_log }))
    const first = screen.getAllByTestId(/^catch-/)[0]
    expect(first).toHaveTextContent(strings.raising)
    expect(first).toHaveTextContent('Pikachu')
  })

  /** caught_at is float | None; `new Date(null * 1000)` renders 12/31/1969. */
  it('renders no date at all for a null caught_at — never 1969', () => {
    const state = clone(monState)
    expect(state.catch_log.some((entry) => entry.caught_at === null)).toBe(true)
    render(<Collection state={state} initialView="catch_log" />)
    const rows = screen.getAllByTestId(/^catch-/)
    const undated = rows[rows.length - 1]
    expect(undated.textContent).not.toContain('1969')
    expect(within(undated).queryByTestId('caught-at')).toBeNull()
  })

  it('dates the entries that do have a timestamp', () => {
    render(<Collection state={monState} initialView="catch_log" />)
    const dated = screen.getAllByTestId('caught-at')
    expect(dated.length).toBe(2)
    dated.forEach((node) => expect(node.textContent).not.toContain('1969'))
  })

  it('shows the whole evolution chain of a catch', () => {
    render(<Collection state={monState} initialView="catch_log" />)
    const rows = screen.getAllByTestId(/^catch-/)
    expect(within(rows[0]).getAllByRole('img')).toHaveLength(2) // Pichu -> Pikachu
  })

  it('shows nature, rarity and how long it was raised', () => {
    render(<Collection state={monState} initialView="catch_log" />)
    const rows = screen.getAllByTestId(/^catch-/)
    expect(rows[1]).toHaveTextContent('Calm')
    expect(rows[1]).toHaveTextContent(strings.uncommon)
    expect(rows[1]).toHaveTextContent('1 days, 2 hr')
  })

  it('shows the empty string when nothing has been caught', () => {
    render(<Collection state={eggState} initialView="catch_log" />)
    expect(screen.getByText(strings.no_pokemon_yet)).toBeInTheDocument()
  })
})

describe('releases', () => {
  it('marks a released individual in the catch log', () => {
    render(<Collection state={monState} initialView="catch_log" />)
    // Exactly one fixture entry carries released: true.
    expect(screen.getAllByTestId('released-badge')).toHaveLength(1)
  })

  it('does not badge a graduated individual as released', () => {
    render(<Collection state={monState} initialView="catch_log" />)
    const rows = screen.getAllByTestId(/^catch-/)
    const badged = rows.filter((row) => within(row).queryByTestId('released-badge'))
    const graduated = rows.filter((row) => !within(row).queryByTestId('released-badge'))
    expect(badged.length).toBeGreaterThan(0)
    expect(graduated.length).toBeGreaterThan(0)
  })

  it('keeps a released species in the Pokédex', () => {
    // The species Vaporeon is released in the fixture, yet still listed.
    render(<Collection state={monState} />)
    expect(screen.getByTestId('dex-134')).toBeInTheDocument()
  })
})

describe('opening a Pokédex entry', () => {
  const DETAIL = {
    species_id: 134,
    name: 'Vaporeon',
    sprite_path: '',
    types: ['water'],
    base_stats: { hp: 130, attack: 65, defense: 60, special_attack: 110, special_defense: 95, speed: 65 },
    stats: { hp: 200, attack: 100, defense: 95, special_attack: 150, special_defense: 130, speed: 100 },
    ivs: { hp: 31, attack: 5, defense: 9, special_attack: 28, special_defense: 14, speed: 3 },
    moves: [{ name: 'water-gun', level: 1 }],
    version_group: 'black-white',
    is_shiny: false,
    nature: 'calm',
    level: 100,
    gender: 'male',
    ability: 'water-absorb',
    has_individual: true,
  }

  it('opens the detail page for the species that was tapped', async () => {
    const user = userEvent.setup()
    const load = vi.fn().mockResolvedValue(DETAIL)
    render(<Collection state={monState} loadDetail={load} />)
    await user.click(within(screen.getByTestId('dex-134')).getByRole('button'))
    expect(load).toHaveBeenCalledWith(134)
    expect(await screen.findByTestId('detail-name')).toHaveTextContent('Vaporeon')
  })

  it('goes back to the grid on close', async () => {
    const user = userEvent.setup()
    render(<Collection state={monState} loadDetail={vi.fn().mockResolvedValue(DETAIL)} />)
    await user.click(within(screen.getByTestId('dex-134')).getByRole('button'))
    await screen.findByTestId('detail-name')
    await user.click(screen.getByRole('button', { name: new RegExp(strings.close, 'i') }))
    expect(screen.getByTestId('dex-134')).toBeInTheDocument()
  })

  it('names the species in the cell accessible label', () => {
    render(<Collection state={monState} loadDetail={vi.fn()} />)
    const cell = within(screen.getByTestId('dex-134')).getByRole('button')
    expect(cell).toHaveAccessibleName(new RegExp('Vaporeon'))
  })
})
