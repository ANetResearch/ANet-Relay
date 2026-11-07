// Drift gate (03 §10.3, §13 ci.yml): types.ts must equal what `npm run types` generates from the backend schema.json.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const dir = resolve(import.meta.dirname)

describe('snapshot types', () => {
  it('types.ts matches the backend schema.json', async () => {
    const { generate } = await import('../../scripts/gen-types.mjs')
    const expected = await generate(JSON.parse(readFileSync(resolve(dir, 'schema.json'), 'utf8')))
    expect(readFileSync(resolve(dir, 'types.ts'), 'utf8')).toBe(expected)
  })
})
