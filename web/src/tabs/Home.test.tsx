import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { Home } from './Home'
import { clone, defaultConfig, eggState, monState } from '../__fixtures__'
import type { StatePayload } from '../types'

const now = Date.parse('2026-08-20T00:00:00Z')

function renderHome(state: StatePayload = eggState) {
  return render(<Home state={state} config={defaultConfig} now={now} />)
}

describe('Home — egg stage (the real probe payload)', () => {
  it("shows today's usage from the preformatted engine strings", () => {
    renderHome()
    expect(screen.getByText(eggState.strings.todays_tokens)).toBeInTheDocument()
    expect(screen.getByText('529,546,967')).toBeInTheDocument()
    expect(screen.getByText('$411.19')).toBeInTheDocument()
  })

  it('renders the egg companion with its progress and status message', () => {
    renderHome()
    expect(screen.getByText('An egg is warming up.')).toBeInTheDocument()
    expect(screen.getByTestId('egg-progress')).toHaveAttribute('aria-valuenow', '0')
    expect(screen.getByText(eggState.strings.egg)).toBeInTheDocument()
  })

  it('uses the egg emoji for an egg with no sprite (and the payload has none)', () => {
    renderHome()
    expect(eggState.companion.sprite_path).toBe('')
    expect(screen.getByTestId('sprite-emoji')).toHaveTextContent('🥚')
  })

  it('shows no mon-only sections for an egg', () => {
    renderHome()
    expect(screen.queryByTestId('evo-line')).toBeNull()
    expect(screen.queryByTestId('mon-name')).toBeNull()
  })

  it('shows spendable tokens', () => {
    renderHome()
    expect(screen.getByText(eggState.strings.spendable_tokens)).toBeInTheDocument()
    expect(screen.getByTestId('spendable')).toHaveTextContent('0')
  })

  it('renders week and month totals it has to format itself', () => {
    renderHome()
    // periods carries raw numbers only — no preformatted twin exists.
    expect(screen.getByTestId('period-week')).toHaveTextContent('907.6M')
    expect(screen.getByTestId('period-month')).toHaveTextContent('2.67B')
  })

  it('omits the periods card entirely when periods is {}', () => {
    const state = clone(eggState)
    state.periods = {}
    renderHome(state)
    expect(screen.queryByTestId('period-week')).toBeNull()
    expect(screen.queryByText(eggState.strings.this_week)).toBeNull()
  })

  it('shows nothing about burn while burn is {} (fewer than 3 samples)', () => {
    renderHome()
    expect(eggState.burn).toEqual({})
    expect(screen.queryByTestId('burn')).toBeNull()
  })

  it('renders limits at the real 51% / 36%', () => {
    renderHome()
    expect(screen.getByTestId('limit-session')).toHaveTextContent('51%')
    expect(screen.getByTestId('limit-session')).toHaveAttribute('data-level', 'ok')
  })

  it('handles a payload with no limits at all', () => {
    const state = clone(eggState)
    state.limits = {}
    renderHome(state)
    expect(screen.getByText(eggState.strings.limits_unavailable)).toBeInTheDocument()
  })

  it('lists engine errors', () => {
    const state = clone(eggState)
    state.errors = ['cannot read <path>']
    renderHome(state)
    expect(screen.getByTestId('errors')).toHaveTextContent('cannot read <path>')
  })
})

describe('Home — mon stage', () => {
  it('names the companion and marks it shiny', () => {
    renderHome(monState)
    expect(screen.getByTestId('mon-name')).toHaveTextContent('Pikachu')
    expect(screen.getByText(monState.strings.shiny)).toBeInTheDocument()
  })

  it('shows nature and rarity', () => {
    renderHome(monState)
    expect(screen.getByTestId('mon-meta')).toHaveTextContent('Jolly')
    expect(screen.getByTestId('mon-meta')).toHaveTextContent(monState.strings.rare)
  })

  it('renders evo_line — never a field called "line"', () => {
    renderHome(monState)
    const line = screen.getByTestId('evo-line')
    expect(within(line).getAllByRole('img')).toHaveLength(3)
    expect(within(line).getByTestId('evo-25')).toHaveAttribute('data-current', 'true')
    expect(within(line).getByTestId('evo-172')).toHaveAttribute('data-reached', 'true')
    expect(within(line).getByTestId('evo-26')).toHaveAttribute('data-reached', 'false')
  })

  it('translates the engine goal token into the localized label', () => {
    renderHome(monState)
    expect(screen.getByTestId('mon-goal')).toHaveTextContent(monState.strings.next_evolution)
    expect(screen.getByTestId('mon-goal')).toHaveTextContent('600M')
  })

  it('uses the graduation label on a final form', () => {
    const state = clone(monState)
    if (state.companion.stage !== 'mon') throw new Error('fixture is not a mon')
    state.companion.is_final_form = true
    state.companion.goal = 'graduation'
    renderHome(state)
    expect(screen.getByTestId('mon-goal')).toHaveTextContent(monState.strings.graduation)
  })

  it('shows the burn forecast when the tracker has samples', () => {
    renderHome(monState)
    // strings.at_this_rate is "at this rate, full at %1"
    expect(screen.getByTestId('burn')).toHaveTextContent('at this rate, full at 18:42')
  })

  it('omits a burn window whose ETA is empty (flat slope)', () => {
    renderHome(monState)
    expect(screen.getByTestId('burn').textContent).not.toContain('undefined')
    expect(screen.queryByTestId('burn-weekly')).toBeNull()
  })

  it('renders a species with a missing sprite as a neutral placeholder, not an egg', () => {
    const state = clone(monState)
    if (state.companion.stage !== 'mon') throw new Error('fixture is not a mon')
    state.companion.sprite_path = ''
    state.companion.evo_line = []
    renderHome(state)
    expect(screen.getByTestId('sprite-placeholder')).toBeInTheDocument()
    expect(screen.queryByTestId('sprite-emoji')).toBeNull()
  })
})
