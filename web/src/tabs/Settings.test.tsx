import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Settings } from './Settings'
import { defaultConfig, eggState } from '../__fixtures__'
import type { AppConfig } from '../types'

const strings = eggState.strings

function renderSettings(config: AppConfig = defaultConfig, onSave = vi.fn().mockResolvedValue(undefined)) {
  render(<Settings config={config} strings={strings} onSave={onSave} />)
  return onSave
}

/**
 * Requirement 4. The previous Settings used uncontrolled inputs with
 * hardcoded defaults, so it lied about the current values after any change,
 * and its onBlur fired on focus-then-leave with NO edit — silently POSTing the
 * hardcoded default over a real setting.
 */
describe('Settings shows the server values', () => {
  it('renders the REAL current config, not defaults', () => {
    renderSettings({
      refresh_interval: 300,
      warn_threshold: 55,
      crit_threshold: 90,
      limit_display_mode: 'weekly',
      language: 'ja',
      growth_difficulty: 0.5,
      shop_difficulty: 1.5,
    })
    expect(screen.getByLabelText(/refresh interval/i)).toHaveValue(300)
    expect(screen.getByLabelText(/warn threshold/i)).toHaveValue(55)
    expect(screen.getByLabelText(/critical threshold/i)).toHaveValue(90)
    expect(screen.getByLabelText(/limit display/i)).toHaveValue('weekly')
    expect(screen.getByLabelText(/language/i)).toHaveValue('ja')
  })

  it('is controlled: a new config prop replaces what is displayed', () => {
    const { rerender } = render(
      <Settings config={defaultConfig} strings={strings} onSave={vi.fn()} />,
    )
    expect(screen.getByLabelText(/refresh interval/i)).toHaveValue(120)
    rerender(
      <Settings config={{ ...defaultConfig, refresh_interval: 900 }} strings={strings} onSave={vi.fn()} />,
    )
    expect(screen.getByLabelText(/refresh interval/i)).toHaveValue(900)
  })
})

describe('Settings only submits real changes', () => {
  it('does NOT submit on focus-then-blur with no edit', async () => {
    const user = userEvent.setup()
    const onSave = renderSettings()
    const input = screen.getByLabelText(/refresh interval/i)
    await user.click(input)
    await user.tab()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('does not submit when the typed value equals the current one', async () => {
    const user = userEvent.setup()
    const onSave = renderSettings()
    const input = screen.getByLabelText(/refresh interval/i)
    await user.clear(input)
    await user.type(input, '120')
    await user.tab()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('submits an actual change on blur', async () => {
    const user = userEvent.setup()
    const onSave = renderSettings()
    const input = screen.getByLabelText(/refresh interval/i)
    await user.clear(input)
    await user.type(input, '300')
    await user.tab()
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith('refresh_interval', 300)
  })

  it('submits on Enter without waiting for a blur', async () => {
    const user = userEvent.setup()
    const onSave = renderSettings()
    const input = screen.getByLabelText(/warn threshold/i)
    await user.clear(input)
    await user.type(input, '70{Enter}')
    expect(onSave).toHaveBeenCalledWith('warn_threshold', 70)
  })

  it('submits a select change immediately', async () => {
    const user = userEvent.setup()
    const onSave = renderSettings()
    await user.selectOptions(screen.getByLabelText(/limit display/i), 'session')
    expect(onSave).toHaveBeenCalledWith('limit_display_mode', 'session')
    await user.selectOptions(screen.getByLabelText(/language/i), 'ko')
    expect(onSave).toHaveBeenCalledWith('language', 'ko')
  })

  it('never submits an empty field', async () => {
    const user = userEvent.setup()
    const onSave = renderSettings()
    await user.clear(screen.getByLabelText(/refresh interval/i))
    await user.tab()
    expect(onSave).not.toHaveBeenCalled()
    expect(screen.getByLabelText(/refresh interval/i)).toHaveValue(120)
  })
})

describe('Settings round-trip', () => {
  it("shows the server's 400 message and restores the real value", async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockRejectedValue(new Error('refresh_interval must be between 30 and 3600'))
    renderSettings(defaultConfig, onSave)
    const input = screen.getByLabelText(/refresh interval/i)
    await user.clear(input)
    await user.type(input, '5')
    await user.tab()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'refresh_interval must be between 30 and 3600',
    )
    // The UI must not keep showing a value the server rejected.
    expect(input).toHaveValue(120)
  })

  it('confirms a successful save and clears the error', async () => {
    const user = userEvent.setup()
    const onSave = vi
      .fn()
      .mockRejectedValueOnce(new Error('warn_threshold must be between 1 and 100'))
      .mockResolvedValueOnce(undefined)
    renderSettings(defaultConfig, onSave)
    const input = screen.getByLabelText(/warn threshold/i)
    await user.clear(input)
    await user.type(input, '200{Enter}')
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    await user.clear(input)
    await user.type(input, '70{Enter}')
    expect(await screen.findByTestId('save-ok')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(onSave).toHaveBeenLastCalledWith('warn_threshold', 70)
  })

  it('offers only the languages the engine has catalogues for', () => {
    renderSettings()
    const options = Array.from(screen.getByLabelText(/language/i).querySelectorAll('option')).map(
      (option) => option.value,
    )
    expect(options).toEqual(['en', 'ko', 'ja', 'es'])
  })

  it('offers exactly the three limit display modes', () => {
    renderSettings()
    const options = Array.from(screen.getByLabelText(/limit display/i).querySelectorAll('option')).map(
      (option) => option.value,
    )
    expect(options).toEqual(['session', 'weekly', 'both'])
  })
})

describe('difficulty multipliers', () => {
  it('renders each multiplier as a percentage of the original balance', () => {
    renderSettings({ ...defaultConfig, growth_difficulty: 0.5, shop_difficulty: 1.5 })
    const card = screen.getByTestId('difficulty-card')
    expect(within(card).getByText('50%')).toBeInTheDocument()
    expect(within(card).getByText('150%')).toBeInTheDocument()
  })

  it('shows 100% for the untouched default', () => {
    renderSettings()
    const card = screen.getByTestId('difficulty-card')
    expect(within(card).getAllByText('100%')).toHaveLength(2)
  })

  it('does not render a float artefact like 65.00000000000001%', () => {
    renderSettings({ ...defaultConfig, growth_difficulty: 0.65 })
    expect(screen.getByTestId('difficulty-card')).toHaveTextContent('65%')
  })

  it('commits growth difficulty on release, not on every drag frame', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<Settings config={defaultConfig} strings={strings} onSave={onSave} />)
    const slider = screen.getByLabelText(/growth/i)
    fireEvent.change(slider, { target: { value: '0.5' } })
    // Dragging alone must not POST: the engine rescales banked progress on
    // every change, so a drag would fire a rescale and a disk write per frame.
    expect(onSave).not.toHaveBeenCalled()
    fireEvent.mouseUp(slider)
    await waitFor(() => expect(onSave).toHaveBeenCalledWith('growth_difficulty', 0.5))
  })

  it('commits shop difficulty independently', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<Settings config={defaultConfig} strings={strings} onSave={onSave} />)
    const slider = screen.getByLabelText(/shop prices/i)
    fireEvent.change(slider, { target: { value: '1.5' } })
    fireEvent.mouseUp(slider)
    await waitFor(() => expect(onSave).toHaveBeenCalledWith('shop_difficulty', 1.5))
    expect(onSave).toHaveBeenCalledTimes(1)
  })

  it('offers the same bounds the server validates', () => {
    renderSettings()
    const slider = screen.getByLabelText(/growth/i)
    expect(slider).toHaveAttribute('min', '0.1')
    expect(slider).toHaveAttribute('max', '2')
  })
})
