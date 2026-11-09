import { describe, expect, it } from 'vitest'
import { inlineText, parseInline, parseMarkdown, splitRow, type MdBlock } from './markdown'

const kinds = (blocks: MdBlock[]) => blocks.map((b) => b.kind)

describe('Copilot Markdown blocks', () => {
  it('reads a table with alignment, inline formatting and <br> in cells', () => {
    const [table] = parseMarkdown(
      [
        '| Aircraft | Role | Height (m) | Note |',
        '|:--|:-:|--:|---|',
        '| sim-1 | **Gunner** | 1.20 | `hunter_easy`<br>team B |',
        '| sim-2 | Rammer | 0.80 |',
      ].join('\n'),
    )
    expect(table).toMatchObject({
      kind: 'table',
      align: ['left', 'center', 'right', null],
      head: ['Aircraft', 'Role', 'Height (m)', 'Note'],
      rows: [
        ['sim-1', '**Gunner**', '1.20', '`hunter_easy`<br>team B'],
        ['sim-2', 'Rammer', '0.80', ''],
      ],
    })
  })

  it('keeps pipes inside code spans and escaped pipes in one cell', () => {
    expect(splitRow('| `a|b` | c \\| d |')).toEqual(['`a|b`', 'c | d'])
  })

  it('reads a table without outer pipes, right after a paragraph', () => {
    const blocks = parseMarkdown('Three aircraft are up:\nName | Team\n--- | ---\nA | 1\nB | 2')
    expect(kinds(blocks)).toEqual(['paragraph', 'table'])
    expect((blocks[1] as Extract<MdBlock, { kind: 'table' }>).rows).toEqual([
      ['A', '1'],
      ['B', '2'],
    ])
  })

  it('does not take a dash list for a table', () => {
    expect(kinds(parseMarkdown('- a | b\n- c'))).toEqual(['list'])
  })

  it('reads headings, rules, quotes and fenced code (also unclosed while streaming)', () => {
    const blocks = parseMarkdown('## Result\n\n---\n> checked\n> twice\n\n```python\ndef step(obs, ctx):\n    return Sticks()\n```\n\n```json\n{"a": 1')
    expect(kinds(blocks)).toEqual(['heading', 'hr', 'quote', 'code', 'code'])
    expect(blocks[0]).toMatchObject({ level: 2, text: 'Result' })
    expect(blocks[3]).toMatchObject({ lang: 'python', text: 'def step(obs, ctx):\n    return Sticks()' })
    expect(blocks[4]).toMatchObject({ lang: 'json', text: '{"a": 1' })
  })

  it('nests lists by indentation and keeps numbering, tasks and continuation lines', () => {
    const [list] = parseMarkdown(['3. Take off', '   to 2 m', '4. Hover', '   - check the height', '   - check the battery', '5. Land'].join('\n'))
    expect(list).toMatchObject({ kind: 'list', ordered: true, start: 3 })
    const items = (list as Extract<MdBlock, { kind: 'list' }>).items
    expect(items).toHaveLength(3)
    expect(items[0].blocks).toEqual([{ kind: 'paragraph', text: 'Take off\nto 2 m' }])
    expect(kinds(items[1].blocks)).toEqual(['paragraph', 'list'])
    const tasks = parseMarkdown('- [x] armed\n- [ ] airborne')[0] as Extract<MdBlock, { kind: 'list' }>
    expect(tasks.items.map((i) => i.task)).toEqual([true, false])
  })

  it('keeps a list going over blank lines between items', () => {
    const blocks = parseMarkdown('1. one\n\n2. two\n\nAfter.')
    expect(kinds(blocks)).toEqual(['list', 'paragraph'])
    expect((blocks[0] as Extract<MdBlock, { kind: 'list' }>).items).toHaveLength(2)
  })
})

describe('Copilot Markdown inline', () => {
  it('reads bold, italic, strikethrough and code, also around Chinese text', () => {
    expect(parseInline('**结论：**油门不足，*建议*提高到 `0.62`，~~0.5~~')).toEqual([
      { kind: 'strong', children: [{ kind: 'text', text: '结论：' }] },
      { kind: 'text', text: '油门不足，' },
      { kind: 'em', children: [{ kind: 'text', text: '建议' }] },
      { kind: 'text', text: '提高到 ' },
      { kind: 'code', text: '0.62' },
      { kind: 'text', text: '，' },
      { kind: 'del', children: [{ kind: 'text', text: '0.5' }] },
    ])
  })

  it('leaves arithmetic and snake_case names alone', () => {
    expect(inlineText(parseInline('2 * 3 * 4 and target_alt_m'))).toBe('2 * 3 * 4 and target_alt_m')
    expect(parseInline('target_alt_m')).toEqual([{ kind: 'text', text: 'target_alt_m' }])
  })

  it('links only http(s) and mailto, and finds bare URLs', () => {
    expect(parseInline('[docs](https://example.com/a)')).toEqual([{ kind: 'link', href: 'https://example.com/a', children: [{ kind: 'text', text: 'docs' }] }])
    expect(parseInline('[x](javascript:alert(1))')[0]).toEqual({ kind: 'text', text: 'x' })
    expect(parseInline('see https://example.com/p.')).toEqual([
      { kind: 'text', text: 'see ' },
      { kind: 'link', href: 'https://example.com/p', children: [{ kind: 'text', text: 'https://example.com/p' }] },
      { kind: 'text', text: '.' },
    ])
  })

  it('treats HTML other than <br> as text and honours escapes', () => {
    expect(inlineText(parseInline('<b>x</b> \\*y\\*'))).toBe('<b>x</b> *y*')
  })
})
