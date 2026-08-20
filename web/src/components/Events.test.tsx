import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Events } from './Events'
import { eggState, sampleEvents } from '../__fixtures__'

const strings = eggState.strings

describe('recent events', () => {
  it('lists events newest first with their kind', () => {
    render(<Events events={sampleEvents} strings={strings} onClose={vi.fn()} loading={false} />)
    const rows = screen.getAllByTestId(/^event-/)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('A shiny hatched!')
    expect(rows[0]).toHaveAttribute('data-kind', 'shiny')
    expect(rows[0]).toHaveTextContent('A shiny Pikachu — 1 in 512!')
  })

  it('renders every kind the engine publishes', () => {
    const kinds = ['hatched', 'evolved', 'graduated', 'shiny', 'ditto'] as const
    render(
      <Events
        events={kinds.map((kind, index) => ({ kind, title: kind, detail: '', published_at: index }))}
        strings={strings}
        onClose={vi.fn()}
        loading={false}
      />,
    )
    expect(screen.getAllByTestId(/^event-/).map((row) => row.getAttribute('data-kind'))).toEqual([
      ...kinds,
    ])
  })

  it('shows an empty state', () => {
    render(<Events events={[]} strings={strings} onClose={vi.fn()} loading={false} />)
    expect(screen.getByTestId('events-empty')).toBeInTheDocument()
  })

  it('shows a loading state', () => {
    render(<Events events={[]} strings={strings} onClose={vi.fn()} loading />)
    expect(screen.getByTestId('events-loading')).toBeInTheDocument()
  })

  it('closes', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<Events events={sampleEvents} strings={strings} onClose={onClose} loading={false} />)
    await user.click(screen.getByRole('button', { name: /back|close/i }))
    expect(onClose).toHaveBeenCalled()
  })

  it('does not render a 1969 date for a missing timestamp', () => {
    render(
      <Events
        events={[{ kind: 'hatched', title: 'It hatched!', detail: '', published_at: 0 }]}
        strings={strings}
        onClose={vi.fn()}
        loading={false}
      />,
    )
    expect(screen.getByTestId('event-0').textContent).not.toContain('1969')
  })
})
