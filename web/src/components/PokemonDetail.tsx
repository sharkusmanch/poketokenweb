import { useEffect, useState } from 'react'
import type { PokemonDetail as Detail } from '../types'
import { fetchPokemon } from '../lib/api'
import { Sprite } from './Sprite'

interface PokemonDetailProps {
  speciesId: number
  strings: Record<string, string>
  onClose: () => void
  /** Injectable so tests do not go through the network layer. */
  load?: (speciesId: number) => Promise<Detail>
}

/** The six stats, in the order every Pokémon game lists them. */
const STATS = [
  ['hp', 'stat_hp'],
  ['attack', 'stat_attack'],
  ['defense', 'stat_defense'],
  ['special_attack', 'stat_special_attack'],
  ['special_defense', 'stat_special_defense'],
  ['speed', 'stat_speed'],
] as const

const GENDER_SYMBOL: Record<string, string> = {
  male: '♂',
  female: '♀',
  genderless: '—',
}

/** "thunder-shock" -> "Thunder Shock". PokéAPI names everything in slugs. */
export function titleCase(slug: string): string {
  return slug
    .split('-')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

/** The highest base stat any species has, so bars are comparable across them. */
const BASE_STAT_CEILING = 255

export function PokemonDetail({ speciesId, strings, onClose, load }: PokemonDetailProps) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let current = true
    setDetail(null)
    setFailed(false)
    const fetcher = load ?? fetchPokemon
    fetcher(speciesId)
      .then((result) => {
        if (current) setDetail(result)
      })
      .catch(() => {
        // Species data comes from PokéAPI, which an offline deployment simply
        // cannot reach. That is an expected state, not an error worth a stack.
        if (current) setFailed(true)
      })
    return () => {
      current = false
    }
  }, [speciesId, load])

  return (
    <section className="tab-panel detail" data-testid="pokemon-detail" aria-live="polite">
      <div className="detail-head">
        <button type="button" className="btn btn-ghost" onClick={onClose}>
          ← {strings.close}
        </button>
      </div>

      {failed ? (
        <p className="alert" role="alert" data-testid="detail-unavailable">
          {strings.detail_unavailable}
        </p>
      ) : !detail ? (
        <p className="loading" data-testid="detail-loading">
          …
        </p>
      ) : (
        <>
          <div className="card detail-hero">
            <Sprite src={detail.sprite_path} alt={detail.name} size="lg" />
            <h1 className="companion-title" data-testid="detail-name">
              {detail.name || `#${detail.species_id}`}
              {detail.is_shiny ? (
                <span className="badge badge-shiny">{strings.shiny}</span>
              ) : null}
            </h1>
            <p className="muted">#{detail.species_id}</p>
            {detail.types.length > 0 ? (
              <ul className="type-row" data-testid="detail-types">
                {detail.types.map((type) => (
                  <li key={type} className={`type type-${type}`}>
                    {titleCase(type)}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          {detail.has_individual ? (
            <section className="card" aria-label={strings.individual_values}>
              <dl className="detail-facts" data-testid="detail-facts">
                {detail.level !== null ? (
                  <>
                    <dt>{strings.level}</dt>
                    <dd data-testid="detail-level">{detail.level}</dd>
                  </>
                ) : null}
                {detail.nature ? (
                  <>
                    <dt>{strings.individual_values}</dt>
                    <dd>{titleCase(detail.nature)}</dd>
                  </>
                ) : null}
                {detail.gender ? (
                  <>
                    <dt>{strings.gender}</dt>
                    <dd data-testid="detail-gender">
                      <span aria-hidden="true">{GENDER_SYMBOL[detail.gender] ?? '—'}</span>{' '}
                      {strings[detail.gender] ?? detail.gender}
                    </dd>
                  </>
                ) : null}
                {detail.ability ? (
                  <>
                    <dt>{strings.ability}</dt>
                    <dd data-testid="detail-ability">{titleCase(detail.ability)}</dd>
                  </>
                ) : null}
              </dl>
            </section>
          ) : null}

          <section className="card" aria-label={strings.base_stats}>
            <h2 className="card-title">{strings.base_stats}</h2>
            <ul className="stat-list" data-testid="detail-stats">
              {STATS.map(([key, label]) => {
                const base = detail.base_stats[key] ?? 0
                const computed = detail.stats[key]
                const iv = detail.ivs[key]
                return (
                  <li key={key} className="stat-row" data-testid={`stat-${key}`}>
                    <span className="stat-label">{strings[label]}</span>
                    <span className="stat-track">
                      <span
                        className="stat-fill"
                        style={{ width: `${Math.min(100, (base / BASE_STAT_CEILING) * 100)}%` }}
                      />
                    </span>
                    <span className="stat-value">
                      {computed ?? base}
                      {iv === undefined ? null : (
                        <span className="stat-iv" title={strings.individual_values}>
                          {' '}
                          / {iv}
                        </span>
                      )}
                    </span>
                  </li>
                )
              })}
            </ul>
          </section>

          <section className="card" aria-label={strings.moves}>
            <h2 className="card-title">{strings.moves}</h2>
            {detail.moves.length === 0 ? (
              <p className="muted">{strings.no_moves}</p>
            ) : (
              <ol className="move-list" data-testid="detail-moves">
                {detail.moves.map((move) => (
                  <li key={`${move.level}-${move.name}`} className="move-row">
                    <span className="move-level">{move.level === 0 ? '—' : move.level}</span>
                    <span className="move-name">{titleCase(move.name)}</span>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </>
      )}
    </section>
  )
}
