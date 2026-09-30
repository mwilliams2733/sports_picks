/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const config = readFileSync(join(here, '..', 'vite.config.ts'), 'utf-8')

describe('vite dev proxy', () => {
  it('proxies the /paper API without swallowing the /paper-trading page', () => {
    expect(config).toContain("'^/paper/'")
    expect(config).not.toMatch(/'\/paper':/)
  })
})
