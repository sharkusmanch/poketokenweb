import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MonthTrend } from './MonthTrend'
import { eggState, monState } from '../__fixtures__'
import type { MonthDay } from '../types'

const strings = eggState.strings
const series = monState.periods.month_daily as MonthDay[]

describe('MonthTrend', () => {
  it('draws one bar per day of the month so far', () => {
    render(<MonthTrend series={series} strings={strings} />)
    expect(within(screen.getByTestId('month-trend')).getAllByRole('listitem')).toHaveLength(
      series.length,
    )
  })

  it('keeps a day with no usage as an explicit zero-height bar', () => {
    // A bar's POSITION is its date; dropping empty days slides every later bar
    // onto the wrong one.
    render(<MonthTrend series={series} strings={strings} />)
    const empty = screen.getByTestId('trend-2026-09-01')
    expect(empty).toBeInTheDocument()
    expect(within(empty).getByRole('button')).toHaveAttribute('aria-label', '2026-09-01: 0')
  })

  it('scales bar heights against the busiest day', () => {
    render(<MonthTrend series={series} strings={strings} />)
    const peak = within(screen.getByTestId('trend-2026-09-11')).getByRole('button')
    expect(peak.querySelector('.trend-bar')).toHaveAttribute('data-height', '100')
  })

  it('hides itself entirely when nothing was used this month', () => {
    // Gated on the peak, not on emptiness: the engine fills unused days with
    // explicit zeros, so an isEmpty gate would render a flat empty chart.
    const empty = eggState.periods.month_daily as MonthDay[]
    expect(empty.length).toBeGreaterThan(0)
    render(<MonthTrend series={empty} strings={strings} />)
    expect(screen.queryByTestId('month-trend')).not.toBeInTheDocument()
  })

  it('hides itself when the series is missing entirely', () => {
    render(<MonthTrend series={[]} strings={strings} />)
    expect(screen.queryByTestId('month-trend')).not.toBeInTheDocument()
  })

  it('captions the last day by default', () => {
    render(<MonthTrend series={series} strings={strings} />)
    expect(screen.getByTestId('trend-caption')).toHaveTextContent('2026-09-13')
  })

  it('captions today when the engine says which day that is', () => {
    render(<MonthTrend series={series} strings={strings} today="2026-09-04" />)
    expect(screen.getByTestId('trend-caption')).toHaveTextContent('2026-09-04')
  })

  it('captions the day you tap', async () => {
    const user = userEvent.setup()
    render(<MonthTrend series={series} strings={strings} />)
    await user.click(within(screen.getByTestId('trend-2026-09-03')).getByRole('button'))
    expect(screen.getByTestId('trend-caption')).toHaveTextContent('2026-09-03')
    expect(screen.getByTestId('trend-caption')).toHaveTextContent('41M')
  })

  it('labels day 1, every seventh day, and the last one', () => {
    render(<MonthTrend series={series} strings={strings} />)
    const ticks = Array.from(
      screen.getByTestId('month-trend').querySelectorAll('.trend-tick'),
    ).map((node) => node.textContent)
    expect(ticks).toEqual(['1', '', '', '', '', '', '7', '', '', '', '', '', '13'])
  })

  it('reads weekend dates as LOCAL dates, not UTC', () => {
    // "2026-09-05" parsed as UTC shifts the weekday for anyone west of
    // Greenwich, mis-shading the weekend columns.
    render(<MonthTrend series={series} strings={strings} />)
    expect(screen.getByTestId('trend-2026-09-05')).toHaveClass('trend-weekend')
    expect(screen.getByTestId('trend-2026-09-06')).toHaveClass('trend-weekend')
    expect(screen.getByTestId('trend-2026-09-07')).not.toHaveClass('trend-weekend')
  })
})
