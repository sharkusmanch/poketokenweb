import { useState } from 'react'
import type { MonthDay } from '../types'
import { compact } from '../lib/format'

interface MonthTrendProps {
  series: MonthDay[]
  strings: Record<string, string>
  /** Injectable so the caption's "today" fallback is testable. */
  today?: string
}

/** Day-of-month from a "YYYY-MM-DD" key, without constructing a Date. */
function dayNumber(date: string): number {
  return Number.parseInt(date.slice(8, 10), 10)
}

/**
 * Whether this local date falls on a weekend.
 *
 * Parsed WITHOUT the trailing Z: "2026-09-13" alone is treated as UTC by
 * Date.parse, which shifts the weekday by one for anyone west of Greenwich.
 * The engine already bucketed these by local date, so they must be read back
 * as local dates too.
 */
function isWeekend(date: string): boolean {
  const weekday = new Date(`${date}T00:00:00`).getDay()
  return weekday === 0 || weekday === 6
}

/** Day 1, every seventh day, and always the last one. */
function isLabelled(date: string, index: number, length: number): boolean {
  const day = dayNumber(date)
  return day === 1 || day % 7 === 0 || index === length - 1
}

export function MonthTrend({ series, strings, today }: MonthTrendProps) {
  const [selected, setSelected] = useState<string | null>(null)

  const peak = series.reduce((highest, row) => Math.max(highest, row.tokens), 0)
  // Gated on the PEAK, not on emptiness. Producers fill unused days with
  // explicit zeros, so an isEmpty gate would render a flat empty chart for
  // someone who has not used the tool this month.
  if (peak <= 0) return null

  const last = series[series.length - 1]
  const caption = series.find((row) => row.date === (selected ?? today)) ?? last

  return (
    <section className="card trend-card" aria-label={strings.month_trend}>
      <h2 className="card-title">{strings.month_trend}</h2>
      <ol className="trend" data-testid="month-trend">
        {series.map((row, index) => {
          const height = Math.round((row.tokens / peak) * 100)
          return (
            <li
              key={row.date}
              className={`trend-col${isWeekend(row.date) ? ' trend-weekend' : ''}`}
              data-testid={`trend-${row.date}`}
            >
              <button
                type="button"
                className="trend-bar-hit"
                aria-label={`${row.date}: ${compact(row.tokens)}`}
                aria-pressed={caption.date === row.date}
                onClick={() => setSelected(row.date)}
                onMouseEnter={() => setSelected(row.date)}
              >
                <span
                  className="trend-bar"
                  style={{ height: `${height}%` }}
                  data-height={height}
                />
              </button>
              <span className="trend-tick" aria-hidden="true">
                {isLabelled(row.date, index, series.length) ? dayNumber(row.date) : ''}
              </span>
            </li>
          )
        })}
      </ol>
      <p className="trend-caption" data-testid="trend-caption">
        {caption.date} · {compact(caption.tokens)}
      </p>
    </section>
  )
}
