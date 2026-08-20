import type { BagEntry } from '../types'
import { Sprite } from '../components/Sprite'

interface BagProps {
  entries: BagEntry[]
  strings: Record<string, string>
  onUse: (key: string) => void
  pending: string | null
  error?: string | null
}

export function Bag({ entries, strings, onUse, pending, error }: BagProps) {
  return (
    <div className="tab-panel" data-testid="tab-bag">
      <h1 className="tab-title">{strings.bag}</h1>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {entries.length === 0 ? (
        <p className="empty">{strings.bag_empty}</p>
      ) : (
        <ul className="rows">
          {entries.map((entry) => (
            <li className="row" key={entry.key} data-testid={`bag-${entry.key}`}>
              <div className="row-main">
                <Sprite src={entry.sprite_path} alt={entry.label} emoji={entry.emoji} />
                <div className="row-text">
                  <span className="row-title">
                    {entry.label} <span className="count">×{entry.count}</span>
                  </span>
                  <span className="row-desc">{entry.description}</span>
                  <span className="row-effect">{entry.effect}</span>
                </div>
                {/* Only rareCandy and mint are consumable; use_item() rejects
                    shinyCharm, so no button is offered for it at all. */}
                {entry.usable ? (
                  <button
                    type="button"
                    className="btn"
                    disabled={pending === entry.key}
                    onClick={() => onUse(entry.key)}
                  >
                    {strings.use}
                  </button>
                ) : entry.passive ? (
                  <span className="badge badge-active">{strings.active}</span>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
