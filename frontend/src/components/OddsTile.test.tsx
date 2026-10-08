import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import OddsTile from './OddsTile'

describe('OddsTile', () => {
  it('shows the line and price and calls onSelect', () => {
    const onSelect = vi.fn()
    render(<OddsTile label="Cowboys +3" top="+3" price={-110} line={3} onSelect={onSelect} />)
    const tile = screen.getByRole('button', { name: /Cowboys \+3 -110/ })
    expect(tile).toHaveTextContent('+3')
    fireEvent.click(tile)
    expect(onSelect).toHaveBeenCalledOnce()
  })
  it('is locked, disabled and explains why when the side is refused', () => {
    render(<OddsTile label="Under" top={null} price={null} line={null} lockedReason="The price is stale" onSelect={() => {}} />)
    const tile = screen.getByRole('button', { name: /Under locked/ })
    expect(tile).toBeDisabled()
    expect(tile).toHaveAttribute('title', 'The price is stale')
    expect(tile).toHaveTextContent('🔒')
  })
  it('locks a priced side when the board is offline', () => {
    render(<OddsTile label="X" top={null} price={-110} line={null} offline onSelect={() => {}} />)
    expect(screen.getByRole('button')).toBeDisabled()
    expect(screen.getByRole('button')).toHaveAttribute('title', 'Board offline — prices unavailable')
  })
  it('flashes green when the price improves and red when it worsens', () => {
    const { rerender } = render(<OddsTile label="X" top={null} price={-110} line={null} onSelect={() => {}} />)
    rerender(<OddsTile label="X" top={null} price={-105} line={null} onSelect={() => {}} />)
    expect(screen.getByRole('button')).toHaveClass('sb-flash-better')
    rerender(<OddsTile label="X" top={null} price={-120} line={null} onSelect={() => {}} />)
    expect(screen.getByRole('button')).toHaveClass('sb-flash-worse')
  })
  it('shows a tile on the slip as selected and pressed', () => {
    render(<OddsTile label="X" top={null} price={-110} line={null} selected onSelect={() => {}} />)
    expect(screen.getByRole('button')).toHaveClass('sb-tile-selected')
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true')
  })
})
