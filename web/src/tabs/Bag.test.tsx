import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Bag } from './Bag'
import { eggState, monState } from '../__fixtures__'

const strings = eggState.strings

describe('Bag', () => {
  it('shows the empty-bag string for the real (empty) payload', () => {
    render(<Bag entries={eggState.bag} strings={strings} onUse={vi.fn()} pending={null} />)
    expect(eggState.bag).toEqual([])
    expect(screen.getByText(strings.bag_empty)).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('lists held items with their counts', () => {
    render(<Bag entries={monState.bag} strings={strings} onUse={vi.fn()} pending={null} />)
    expect(screen.getByTestId('bag-rareCandy')).toHaveTextContent('Rare Candy')
    expect(screen.getByTestId('bag-rareCandy')).toHaveTextContent('×2')
    expect(screen.getByTestId('bag-rareCandy')).toHaveTextContent('+100M XP')
  })

  it('uses a consumable item', async () => {
    const user = userEvent.setup()
    const onUse = vi.fn()
    render(<Bag entries={monState.bag} strings={strings} onUse={onUse} pending={null} />)
    await user.click(within(screen.getByTestId('bag-rareCandy')).getByRole('button', { name: strings.use }))
    expect(onUse).toHaveBeenCalledWith('rareCandy')
  })

  it('offers no Use button for a passive item — the engine refuses it', () => {
    render(<Bag entries={monState.bag} strings={strings} onUse={vi.fn()} pending={null} />)
    const charm = screen.getByTestId('bag-shinyCharm')
    expect(within(charm).queryByRole('button')).toBeNull()
    expect(charm).toHaveTextContent(strings.active)
  })

  it('falls back to the item emoji when the sprite is missing', () => {
    const entries = monState.bag.map((entry) => ({ ...entry, sprite_path: '' }))
    render(<Bag entries={entries} strings={strings} onUse={vi.fn()} pending={null} />)
    expect(within(screen.getByTestId('bag-rareCandy')).getByTestId('sprite-emoji')).toHaveTextContent('🍬')
  })

  it('disables the row in flight and surfaces an error', () => {
    render(
      <Bag entries={monState.bag} strings={strings} onUse={vi.fn()} pending="rareCandy" error="no companion" />,
    )
    expect(within(screen.getByTestId('bag-rareCandy')).getByRole('button')).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('no companion')
  })
})
