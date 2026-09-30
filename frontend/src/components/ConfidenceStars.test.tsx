import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import ConfidenceStars from './ConfidenceStars'

describe('ConfidenceStars', () => {
  it('renders nothing while SHOW_STARS is false (owner decision 2026-09-28)', () => {
    const { container } = render(<ConfidenceStars rating={5} />)
    expect(container.firstChild).toBeNull()
    expect(container.textContent).toBe('')
  })
})
