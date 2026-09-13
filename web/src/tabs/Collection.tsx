import { useState } from 'react'
import type { CatchLogEntry, DexEntry, RarityCounts, StatePayload } from '../types'
import { PokemonDetail } from '../components/PokemonDetail'
import { Sprite } from '../components/Sprite'
import { formatCaughtAt } from '../lib/format'
import type { PokemonDetail as Detail } from '../types'

type View = 'dex' | 'catch_log'

interface CollectionProps {
  state: StatePayload
  /** Injectable so a test can land straight on the catch log. */
  initialView?: View
  /** Injectable so a test does not go through the network layer. */
  loadDetail?: (speciesId: number) => Promise<Detail>
}

const RARITIES = ['legendary', 'rare', 'uncommon', 'common'] as const

function Counts({ counts, strings }: { counts: RarityCounts; strings: Record<string, string> }) {
  return (
    <ul className="rarity-counts" data-testid="rarity-counts">
      {RARITIES.map((rarity) => (
        <li key={rarity} className={`chip rarity-${rarity}`}>
          <span className="chip-label">{strings[rarity] ?? rarity}</span>
          <span className="chip-count" data-testid={`count-${rarity}`}>
            {counts[rarity] ?? 0}
          </span>
        </li>
      ))}
    </ul>
  )
}

function DexGrid({
  entries,
  strings,
  onOpen,
}: {
  entries: DexEntry[]
  strings: Record<string, string>
  onOpen: (speciesId: number) => void
}) {
  if (entries.length === 0) return <p className="empty">{strings.no_pokemon_yet}</p>
  return (
    <ul className="dex-grid">
      {entries.map((entry) => (
        <li
          key={entry.species_id}
          className={`dex-cell rarity-${entry.rarity}${entry.is_raising ? ' dex-raising' : ''}`}
          data-testid={`dex-${entry.species_id}`}
        >
          {/* The whole cell opens the detail page: a 44px sprite is already at
              the floor of a comfortable touch target, so a smaller hit area
              inside it would be unusable on a phone. */}
          <button
            type="button"
            className="dex-cell-hit"
            onClick={() => onOpen(entry.species_id)}
            aria-label={`${entry.name || `#${entry.species_id}`} — ${strings.pokedex_detail}`}
          >
            {/* A species never falls back to an egg emoji. */}
            <Sprite src={entry.sprite_path} alt={entry.name || `#${entry.species_id}`} />
            <span className="dex-name">
              {entry.name || `#${entry.species_id}`}
              {entry.is_shiny ? (
                <span className="shiny-mark" data-testid="shiny-mark" title={strings.shiny}>
                  ★
                </span>
              ) : null}
            </span>
          </button>
          {entry.is_raising ? <span className="badge badge-raising">{strings.raising}</span> : null}
        </li>
      ))}
    </ul>
  )
}

function CatchLog({ entries, strings }: { entries: CatchLogEntry[]; strings: Record<string, string> }) {
  if (entries.length === 0) return <p className="empty">{strings.no_pokemon_yet}</p>
  return (
    <ul className="rows catch-log">
      {entries.map((entry, index) => {
        // caught_at is float | None — formatCaughtAt returns "" for null so a
        // missing timestamp renders nothing instead of 12/31/1969.
        const caught = formatCaughtAt(entry.caught_at)
        const last = entry.chain[entry.chain.length - 1]
        return (
          <li className="row" key={`${index}-${last?.species_id ?? 'x'}`} data-testid={`catch-${index}`}>
            <div className="chain">
              {entry.chain.map((link) => (
                <Sprite
                  key={link.species_id}
                  src={link.sprite_path}
                  alt={link.name || `#${link.species_id}`}
                  size="sm"
                />
              ))}
            </div>
            <div className="row-text">
              <span className="row-title">
                {last?.name || `#${last?.species_id ?? ''}`}
                {entry.is_shiny ? (
                  <span className="shiny-mark" title={strings.shiny}>
                    ★
                  </span>
                ) : null}
                {entry.raising ? <span className="badge badge-raising">{strings.raising}</span> : null}
                {entry.released ? (
                  /* Neutral, not a warning colour: a release is a different
                     kind of record, not a failure. */
                  <span className="badge badge-released" data-testid="released-badge">
                    {strings.released}
                  </span>
                ) : null}
              </span>
              <span className="row-desc">
                <span className={`rarity rarity-${entry.rarity}`}>
                  {strings[String(entry.rarity)] ?? entry.rarity}
                </span>
                {entry.nature ? ` · ${entry.nature}` : ''}
              </span>
              {caught ? (
                <span className="muted" data-testid="caught-at">
                  {caught}
                </span>
              ) : null}
              {entry.raised_text ? <span className="muted">{entry.raised_text}</span> : null}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

export function Collection({ state, initialView = 'dex', loadDetail }: CollectionProps) {
  const [view, setView] = useState<View>(initialView)
  const [detailOf, setDetailOf] = useState<number | null>(null)
  const strings = state.strings
  const counts = view === 'dex' ? state.rarity_counts : state.catch_counts

  if (detailOf !== null) {
    return (
      <PokemonDetail
        speciesId={detailOf}
        strings={strings}
        onClose={() => setDetailOf(null)}
        load={loadDetail}
      />
    )
  }

  return (
    <div className="tab-panel" data-testid="tab-collection">
      <h1 className="tab-title">{strings.collection}</h1>
      <div className="segmented" role="tablist" aria-label={strings.collection}>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'dex'}
          className={view === 'dex' ? 'seg seg-on' : 'seg'}
          onClick={() => setView('dex')}
        >
          {strings.pokedex}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'catch_log'}
          className={view === 'catch_log' ? 'seg seg-on' : 'seg'}
          onClick={() => setView('catch_log')}
        >
          {strings.catch_log}
        </button>
      </div>
      <Counts counts={counts} strings={strings} />
      {view === 'dex' ? (
        <DexGrid entries={state.dex} strings={strings} onOpen={setDetailOf} />
      ) : (
        <CatchLog entries={state.catch_log} strings={strings} />
      )}
    </div>
  )
}
