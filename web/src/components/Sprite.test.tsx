import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Sprite } from './Sprite'

/**
 * Requirement 7: with PokeAPI unreachable every sprite_path is "", and a
 * previous Sprite fell back to 🥚 for everything — a shiny Raichu and an
 * unhatched egg looked identical.
 */
describe('Sprite fallbacks', () => {
  it('renders the image when a URL is present', () => {
    render(<Sprite src="/sprites/25-s-a.png" alt="Pikachu" />)
    const img = screen.getByRole('img', { name: 'Pikachu' })
    expect(img).toHaveAttribute('src', '/sprites/25-s-a.png')
  })

  it('uses the row emoji when the row has one (shop/bag items and eggs)', () => {
    render(<Sprite src="" alt="Pokemon Egg" emoji="🥚" />)
    expect(screen.getByTestId('sprite-emoji')).toHaveTextContent('🥚')
    expect(screen.queryByTestId('sprite-placeholder')).toBeNull()
  })

  it('uses a NEUTRAL placeholder for a species, never an egg emoji', () => {
    render(<Sprite src="" alt="Raichu" />)
    const placeholder = screen.getByTestId('sprite-placeholder')
    expect(placeholder).toBeInTheDocument()
    expect(placeholder.textContent).not.toContain('🥚')
    expect(screen.getByLabelText('Raichu')).toBeInTheDocument()
  })

  it('falls back the same way when the image 404s at runtime', () => {
    render(<Sprite src="/sprites/gone.png" alt="Raichu" />)
    fireEvent.error(screen.getByRole('img', { name: 'Raichu' }))
    // the placeholder keeps role="img" for a11y, so assert on the tag itself
    expect(screen.getByTestId('sprite-placeholder').tagName).toBe('SPAN')
    expect(document.querySelector('img')).toBeNull()
  })

  it('falls back to the emoji when an item image 404s', () => {
    render(<Sprite src="/sprites/item-mint.png" alt="Mint" emoji="🌿" />)
    fireEvent.error(screen.getByRole('img', { name: 'Mint' }))
    expect(screen.getByTestId('sprite-emoji')).toHaveTextContent('🌿')
  })

  it('retries when the src changes after an error', () => {
    const { rerender } = render(<Sprite src="/sprites/a.png" alt="Eevee" />)
    fireEvent.error(screen.getByRole('img', { name: 'Eevee' }))
    expect(screen.getByTestId('sprite-placeholder')).toBeInTheDocument()
    rerender(<Sprite src="/sprites/b.png" alt="Eevee" />)
    expect(screen.getByRole('img', { name: 'Eevee' })).toHaveAttribute('src', '/sprites/b.png')
  })
})
