import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TabBar } from './TabBar'
import { eggState } from '../__fixtures__'

const strings = eggState.strings

describe('TabBar', () => {
  it('labels every tab from the engine catalogue, Settings included', () => {
    render(<TabBar tab="home" strings={strings} onSelect={vi.fn()} />)
    // Settings used to be a hardcoded English literal here; the engine now
    // carries the string, so a ko/ja/es user sees five translated tabs.
    expect(strings).toHaveProperty('settings')
    for (const label of [strings.home, strings.shop, strings.bag, strings.collection, strings.settings]) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument()
    }
  })

  it('marks the current tab selected', () => {
    render(<TabBar tab="bag" strings={strings} onSelect={vi.fn()} />)
    expect(screen.getByRole('tab', { name: strings.bag })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: strings.home })).toHaveAttribute('aria-selected', 'false')
  })

  it('reports the selected tab id', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<TabBar tab="home" strings={strings} onSelect={onSelect} />)
    await user.click(screen.getByRole('tab', { name: strings.collection }))
    expect(onSelect).toHaveBeenCalledWith('collection')
  })
})
