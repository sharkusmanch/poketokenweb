import { useState } from 'react'
import type { ShopEntry } from '../types'
import { Sprite } from '../components/Sprite'

interface ShopProps {
  entries: ShopEntry[]
  strings: Record<string, string>
  onBuy: (key: string) => void
  /** Key currently in flight, if any. */
  pending: string | null
  error?: string | null
}

export function Shop({ entries, strings, onBuy, pending, error }: ShopProps) {
  // Which egg is awaiting confirmation. At most one at a time.
  const [confirming, setConfirming] = useState<string | null>(null)

  const request = (entry: ShopEntry) => {
    // Requirement 6: shop.buy() for an egg clears state.active outright — the
    // companion is discarded, NOT graduated into the Pokédex — so an egg needs
    // a deliberate second tap. Items are harmless and buy straight away.
    if (entry.kind === 'egg') {
      setConfirming(entry.key)
      return
    }
    onBuy(entry.key)
  }

  return (
    <div className="tab-panel" data-testid="tab-shop">
      <h1 className="tab-title">{strings.shop}</h1>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      <ul className="rows">
        {entries.map((entry) => {
          const busy = pending === entry.key
          const disabled = entry.owned || !entry.affordable || busy
          const isConfirming = confirming === entry.key
          return (
            <li className="row" key={entry.key} data-testid={`shop-${entry.key}`}>
              <div className="row-main">
                <Sprite src={entry.sprite_path} alt={entry.label} emoji={entry.emoji} />
                <div className="row-text">
                  <span className="row-title">
                    {entry.label}
                    {entry.badge ? (
                      <span className="badge" data-testid="badge">
                        {entry.badge}
                      </span>
                    ) : null}
                  </span>
                  <span className="row-desc">{entry.description}</span>
                  <span className="row-price">
                    {strings.price}: {entry.price_text}
                  </span>
                  {entry.owned ? <span className="row-note">{strings.owned}</span> : null}
                  {!entry.owned && !entry.affordable ? (
                    <span className="row-note">{strings.not_enough_tokens}</span>
                  ) : null}
                </div>
                <button
                  type="button"
                  className="btn btn-buy"
                  disabled={disabled}
                  onClick={() => request(entry)}
                >
                  {strings.buy}
                </button>
              </div>
              {isConfirming ? (
                <div className="confirm" data-testid={`confirm-${entry.key}`} role="group">
                  <p className="confirm-text">
                    Your current Pokémon is sent away and is <strong>not added</strong> to your
                    Pokédex — it is lost for good. Hatch {entry.label}?
                  </p>
                  <div className="confirm-actions">
                    <button
                      type="button"
                      className="btn btn-danger"
                      onClick={() => {
                        setConfirming(null)
                        onBuy(entry.key)
                      }}
                    >
                      Confirm
                    </button>
                    <button type="button" className="btn btn-ghost" onClick={() => setConfirming(null)}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : null}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
