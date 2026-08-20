import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TabBar } from './TabBar'
import { eggState } from '../__fixtures__'

const strings = eggState.strings

describe('TabBar', () => {
  it('labels the four tabs the engine has strings for, plus a local Settings label', () => {
    render(<TabBar tab="home" strings={strings} onSelect={vi.fn()} />)
    // strings has 44 keys and NO "settings" key.
    expect(strings).not.toHaveProperty('settings')
    for (const label of [strings.home, strings.shop, strings.bag, strings.collection, 'Settings']) {
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
