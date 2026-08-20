import { useEffect, useState } from 'react'

interface SpriteProps {
  /** "/sprites/<file>" from the payload, or "" when the engine has none. */
  src: string
  alt: string
  /**
   * The row's own emoji. Shop and bag rows carry one (🍬 🌿 ✨ 🥚); species do
   * NOT. Falling back to 🥚 for a species is the bug this prop exists to
   * prevent — with PokeAPI unreachable, every Pokémon looked like an egg.
   */
  emoji?: string
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

/** Neutral, species-agnostic stand-in. Deliberately not an egg. */
const PLACEHOLDER = '◍'

export function Sprite({ src, alt, emoji, size = 'md', className = '' }: SpriteProps) {
  const [failed, setFailed] = useState(false)

  // A new URL deserves a fresh attempt; otherwise one 404 poisons the slot for
  // the rest of the session (the sprite cache fills in asynchronously).
  useEffect(() => {
    setFailed(false)
  }, [src])

  const classes = `sprite sprite-${size}${className ? ` ${className}` : ''}`

  if (src && !failed) {
    return (
      <img
        className={classes}
        src={src}
        alt={alt}
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
      />
    )
  }

  if (emoji) {
    return (
      <span className={`${classes} sprite-fallback`} role="img" aria-label={alt} data-testid="sprite-emoji">
        {emoji}
      </span>
    )
  }

  return (
    <span
      className={`${classes} sprite-fallback sprite-placeholder`}
      role="img"
      aria-label={alt}
      data-testid="sprite-placeholder"
    >
      {PLACEHOLDER}
    </span>
  )
}
