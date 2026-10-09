import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FAQ from './FAQ'

describe('FAQ', () => {
  it('hides the confidence-stars entry and the "Conf" column note while SHOW_STARS is false', async () => {
    const user = userEvent.setup()
    render(<FAQ />)
    expect(screen.queryByText('What do the confidence stars mean?')).toBeNull()

    const columnsQuestion = screen.getByText('What do the columns on the Player Props page mean?')
    await user.click(columnsQuestion)
    expect(screen.queryByText(/Conf.*confidence rating/)).toBeNull()

    expect(document.body.textContent).not.toMatch(/[★☆]/)
  })
  it('says a moved price is only skipped past when "Accept any odds changes" is ticked', async () => {
    const user = userEvent.setup()
    render(<FAQ />)
    await user.click(screen.getByText('How do I place a pick?'))
    expect(document.body.textContent).toMatch(/that bet is not placed and shows the new price for you to accept, unless you ticked "Accept any odds changes"/)
    expect(document.body.textContent).not.toMatch(/before anything is placed/)
  })
})
