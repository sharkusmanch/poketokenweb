import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Shop } from './Shop'
import { clone, eggState, monState } from '../__fixtures__'
import type { ShopEntry } from '../types'

const strings = eggState.strings

function affordableShop(): ShopEntry[] {
  // Affordable AND buyable: the egg fixture is captured mid-incubation, where
  // eggs are gated, and these tests are about the confirmation flow that only
  // a purchasable egg reaches.
  return clone(eggState.shop).map((entry) => ({
    ...entry,
    affordable: true,
    purchasable: true,
    blocked_reason: '',
  }))
}

function renderShop(entries: ShopEntry[] = affordableShop(), onBuy = vi.fn()) {
  render(<Shop entries={entries} strings={strings} onBuy={onBuy} pending={null} />)
  return onBuy
}

function row(key: string): HTMLElement {
  return screen.getByTestId(`shop-${key}`)
}

describe('Shop rows', () => {
  it('lists every entry from the real payload, including the colon egg tiers', () => {
    renderShop()
    expect(screen.getAllByTestId(/^shop-/)).toHaveLength(6)
    expect(row('egg:rare')).toBeInTheDocument()
    expect(row('egg:uncommon')).toBeInTheDocument()
    expect(within(row('rareCandy')).getByText('Rare Candy')).toBeInTheDocument()
  })

  it('shows the preformatted price and the tier badge', () => {
    renderShop()
    expect(row('egg:rare')).toHaveTextContent('4B')
    expect(within(row('egg:rare')).getByTestId('badge')).toHaveTextContent('RARE')
  })

  it('disables the button and explains when the entry is unaffordable', async () => {
    // The real payload has spendable 0: nothing is affordable.
    renderShop(clone(eggState.shop))
    const button = within(row('mint')).getByRole('button')
    expect(button).toBeDisabled()
    expect(row('mint')).toHaveTextContent(strings.not_enough_tokens)
  })

  it('marks an owned entry as owned', () => {
    const entries = affordableShop()
    entries[4] = { ...entries[4], owned: true, owned_count: 1, affordable: false }
    renderShop(entries)
    expect(row('shinyCharm')).toHaveTextContent(strings.owned)
    expect(within(row('shinyCharm')).getByRole('button')).toBeDisabled()
  })

  it('falls back to the row emoji when an item sprite is missing', () => {
    renderShop()
    // mint has sprite_path "" in the real payload.
    expect(within(row('mint')).getByTestId('sprite-emoji')).toHaveTextContent('🌿')
  })
})

describe('buying', () => {
  it('buys a non-egg item immediately', async () => {
    const user = userEvent.setup()
    const onBuy = renderShop()
    await user.click(within(row('rareCandy')).getByRole('button', { name: strings.buy }))
    expect(onBuy).toHaveBeenCalledWith('rareCandy')
  })

  /**
   * Requirement 6: buying an egg makes the engine clear state.active WITHOUT
   * graduating it — the current Pokémon is destroyed, not retired. One mis-tap
   * from wiping a legendary, so it takes a second, explicit action.
   */
  it('never buys an egg on the first tap', async () => {
    const user = userEvent.setup()
    const onBuy = renderShop()
    await user.click(within(row('egg')).getByRole('button', { name: strings.buy }))
    expect(onBuy).not.toHaveBeenCalled()
    expect(screen.getByTestId('confirm-egg')).toBeInTheDocument()
  })

  it('warns that the individual is released, without claiming the species is lost', async () => {
    const user = userEvent.setup()
    renderShop()
    await user.click(within(row('egg:rare')).getByRole('button', { name: strings.buy }))
    const confirm = screen.getByTestId('confirm-egg:rare')
    expect(confirm.textContent?.toLowerCase()).toContain('pokédex')
    expect(confirm.textContent).toMatch(/released/i)
    // The old copy said the Pokémon was "not added to your Pokédex — lost for
    // good". Since #242 the species IS kept, so that warning is now false and
    // must not come back.
    expect(confirm.textContent).not.toMatch(/lost for good|not added/i)
  })

  it('buys the egg only after the confirmation is accepted', async () => {
    const user = userEvent.setup()
    const onBuy = renderShop()
    await user.click(within(row('egg:uncommon')).getByRole('button', { name: strings.buy }))
    await user.click(within(screen.getByTestId('confirm-egg:uncommon')).getByRole('button', { name: /confirm/i }))
    expect(onBuy).toHaveBeenCalledTimes(1)
    expect(onBuy).toHaveBeenCalledWith('egg:uncommon')
  })

  it('cancelling buys nothing and closes the prompt', async () => {
    const user = userEvent.setup()
    const onBuy = renderShop()
    await user.click(within(row('egg')).getByRole('button', { name: strings.buy }))
    await user.click(within(screen.getByTestId('confirm-egg')).getByRole('button', { name: /cancel/i }))
    expect(onBuy).not.toHaveBeenCalled()
    expect(screen.queryByTestId('confirm-egg')).toBeNull()
  })

  it('only ever prompts for one egg at a time', async () => {
    const user = userEvent.setup()
    renderShop()
    await user.click(within(row('egg')).getByRole('button', { name: strings.buy }))
    await user.click(within(row('egg:rare')).getByRole('button', { name: strings.buy }))
    expect(screen.queryByTestId('confirm-egg')).toBeNull()
    expect(screen.getByTestId('confirm-egg:rare')).toBeInTheDocument()
  })

  it('surfaces a server error next to the shop', () => {
    render(
      <Shop
        entries={affordableShop()}
        strings={strings}
        onBuy={vi.fn()}
        pending={null}
        error="not enough tokens"
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('not enough tokens')
  })

  it('disables the row that is in flight', () => {
    render(<Shop entries={affordableShop()} strings={strings} onBuy={vi.fn()} pending="mint" />)
    expect(within(row('mint')).getByRole('button')).toBeDisabled()
    expect(within(row('rareCandy')).getByRole('button')).toBeEnabled()
  })
})

describe('the egg-stage gate', () => {
  it('still lists all three egg tiers while an egg is incubating', () => {
    render(<Shop entries={eggState.shop} strings={strings} onBuy={vi.fn()} pending={null} />)
    expect(screen.getByTestId('shop-egg')).toBeInTheDocument()
    expect(screen.getByTestId('shop-egg:uncommon')).toBeInTheDocument()
    expect(screen.getByTestId('shop-egg:rare')).toBeInTheDocument()
  })

  it('disables the buy button and says why', () => {
    render(<Shop entries={eggState.shop} strings={strings} onBuy={vi.fn()} pending={null} />)
    const row = screen.getByTestId('shop-egg')
    expect(within(row).getByRole('button')).toBeDisabled()
    expect(within(row).getByTestId('blocked-egg')).toHaveTextContent(
      strings.egg_needs_companion,
    )
  })

  it('prefers the state reason over the balance reason', () => {
    // Both apply in the egg fixture; only one should be shown.
    render(<Shop entries={eggState.shop} strings={strings} onBuy={vi.fn()} pending={null} />)
    const row = screen.getByTestId('shop-egg')
    expect(within(row).queryByText(strings.not_enough_tokens)).not.toBeInTheDocument()
  })

  it('cannot be confirmed into a purchase', async () => {
    const user = userEvent.setup()
    const onBuy = vi.fn()
    render(<Shop entries={eggState.shop} strings={strings} onBuy={onBuy} pending={null} />)
    const row = screen.getByTestId('shop-egg')
    await user.click(within(row).getByRole('button'))
    expect(screen.queryByTestId('confirm-egg')).not.toBeInTheDocument()
    expect(onBuy).not.toHaveBeenCalled()
  })

  it('leaves eggs buyable once a companion has hatched', () => {
    render(<Shop entries={monState.shop} strings={strings} onBuy={vi.fn()} pending={null} />)
    expect(screen.queryByTestId('blocked-egg')).not.toBeInTheDocument()
  })
})
