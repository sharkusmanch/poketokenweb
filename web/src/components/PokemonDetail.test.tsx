import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { PokemonDetail, titleCase } from './PokemonDetail'
import { eggState } from '../__fixtures__'
import type { PokemonDetail as Detail } from '../types'

const strings = eggState.strings

const DETAIL: Detail = {
  species_id: 1,
  name: 'Bulbasaur',
  sprite_path: '/sprites/1-s.png',
  types: ['grass', 'poison'],
  base_stats: {
    hp: 45,
    attack: 49,
    defense: 49,
    special_attack: 65,
    special_defense: 65,
    speed: 45,
  },
  stats: {
    hp: 120,
    attack: 92,
    defense: 92,
    special_attack: 121,
    special_defense: 121,
    speed: 84,
  },
  ivs: { hp: 31, attack: 12, defense: 0, special_attack: 31, special_defense: 7, speed: 20 },
  moves: [
    { name: 'tackle', level: 1 },
    { name: 'vine-whip', level: 13 },
  ],
  version_group: 'black-white',
  is_shiny: false,
  nature: 'modest',
  level: 42,
  gender: 'female',
  ability: 'overgrow',
  has_individual: true,
}

function renderDetail(detail: Partial<Detail> = {}, load?: () => Promise<Detail>) {
  const onClose = vi.fn()
  render(
    <PokemonDetail
      speciesId={1}
      strings={strings}
      onClose={onClose}
      load={load ?? (() => Promise.resolve({ ...DETAIL, ...detail }))}
    />,
  )
  return onClose
}

describe('titleCase', () => {
  it('turns a PokéAPI slug into a readable name', () => {
    expect(titleCase('vine-whip')).toBe('Vine Whip')
    expect(titleCase('overgrow')).toBe('Overgrow')
    expect(titleCase('')).toBe('')
  })
})

describe('PokemonDetail', () => {
  it('shows a placeholder before the species data arrives', () => {
    renderDetail({}, () => new Promise<Detail>(() => {}))
    expect(screen.getByTestId('detail-loading')).toBeInTheDocument()
  })

  it('renders the species identity', async () => {
    renderDetail()
    expect(await screen.findByTestId('detail-name')).toHaveTextContent('Bulbasaur')
    expect(within(screen.getByTestId('detail-types')).getByText('Grass')).toBeInTheDocument()
    expect(within(screen.getByTestId('detail-types')).getByText('Poison')).toBeInTheDocument()
  })

  it('renders the individual, not just the species', async () => {
    renderDetail()
    expect(await screen.findByTestId('detail-level')).toHaveTextContent('42')
    expect(screen.getByTestId('detail-gender')).toHaveTextContent(strings.female)
    expect(screen.getByTestId('detail-ability')).toHaveTextContent('Overgrow')
  })

  it('shows the computed stat and the individual value behind it', async () => {
    renderDetail()
    const hp = await screen.findByTestId('stat-hp')
    expect(hp).toHaveTextContent('120')
    expect(hp).toHaveTextContent('31')
  })

  it('lists the level-up learnset in order', async () => {
    renderDetail()
    const moves = await screen.findByTestId('detail-moves')
    const rows = within(moves).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('Tackle')
    expect(rows[1]).toHaveTextContent('Vine Whip')
    expect(rows[1]).toHaveTextContent('13')
  })

  it('says so when a species has no recorded moves', async () => {
    renderDetail({ moves: [] })
    expect(await screen.findByText(strings.no_moves)).toBeInTheDocument()
  })

  it('hides the individual facts for a record written before profiles existed', async () => {
    // Better than inventing a perfect creature out of missing data.
    renderDetail({ has_individual: false, ivs: {}, stats: {}, level: null })
    await screen.findByTestId('detail-stats')
    expect(screen.queryByTestId('detail-facts')).not.toBeInTheDocument()
    // Species data still renders.
    expect(screen.getByTestId('detail-types')).toBeInTheDocument()
  })

  it('falls back to the base stat when there is no computed one', async () => {
    renderDetail({ has_individual: false, ivs: {}, stats: {} })
    expect(await screen.findByTestId('stat-hp')).toHaveTextContent('45')
  })

  it('says details are unavailable rather than rendering an empty creature', async () => {
    renderDetail({}, () => Promise.reject(new Error('offline')))
    expect(await screen.findByTestId('detail-unavailable')).toHaveTextContent(
      strings.detail_unavailable,
    )
  })

  it('closes back to the grid', async () => {
    const user = userEvent.setup()
    const onClose = renderDetail()
    await screen.findByTestId('detail-name')
    await user.click(screen.getByRole('button', { name: new RegExp(strings.close, 'i') }))
    expect(onClose).toHaveBeenCalled()
  })

  it('marks a shiny individual', async () => {
    renderDetail({ is_shiny: true })
    await waitFor(() =>
      expect(screen.getByTestId('detail-name')).toHaveTextContent(strings.shiny),
    )
  })
})
