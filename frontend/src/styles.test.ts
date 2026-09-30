/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// Vite's ?raw import returns an empty string for .css files under this
// project's vitest config (css imports are stubbed in tests), so read the
// files from disk instead. tsconfig.app.json (src's tsconfig) doesn't list
// "node" in its types, hence the explicit reference above.
const here = dirname(fileURLToPath(import.meta.url))
const indexCss = readFileSync(join(here, 'index.css'), 'utf-8')
const mainTsx = readFileSync(join(here, 'main.tsx'), 'utf-8')

// Plan 026 appended styles to frontend/src/App.css, which nothing imports —
// every rule the branch added there was dead. This test guards that the
// classes those components depend on live in the stylesheet main.tsx
// actually loads.
describe('plan 026 styles live in the loaded stylesheet', () => {
  it.each([
    '.leaderboard-entry.model',
    '.leaderboard-caption',
    '.leaderboard-raw',
    '.verdict-above',
    '.verdict-below',
    '.spend-confirm',
    '.spend-confirm-text',
  ])('index.css defines %s', (selector) => {
    expect(indexCss).toContain(selector)
  })

  it('main.tsx imports ./index.css', () => {
    expect(mainTsx).toContain("import './index.css'")
  })
})

describe('plan 027 styles live in the loaded stylesheet', () => {
  it.each([
    '.quote-sides',
    '.quote-side',
    '.quote-side.selected',
    '.quote-reason',
    '.quote-age',
    '.prop-quote-row',
    '.no-pin-badge',
    '.set-pin',
  ])('index.css defines %s', (selector) => {
    expect(indexCss).toContain(selector)
  })
})
