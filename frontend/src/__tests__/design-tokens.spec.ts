/**
 * Guard against design-token drift.
 *
 * docs/design/design-tokens.json is the only source of visual values. These
 * tests fail if:
 *  - the generated CSS no longer matches the JSON,
 *  - a component hardcodes a hex colour or a raw px control height,
 *  - a semantic token referenced by CSS is missing.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const repoRoot = resolve(__dirname, '../../..')
const tokenPath = join(repoRoot, 'docs/design/design-tokens.json')
const tokens = JSON.parse(readFileSync(tokenPath, 'utf8')) as {
  color: Record<string, string>
  control: Record<string, unknown>
  layout: Record<string, number>
}

const srcRoot = resolve(__dirname, '..')

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      if (entry === 'styles' || entry === '__tests__') continue
      walk(full, out)
    } else if (/\.(vue|ts|css)$/.test(entry)) {
      out.push(full)
    }
  }
  return out
}

const sourceFiles = walk(srcRoot)

describe('design tokens', () => {
  it('keeps generated CSS in sync with design-tokens.json', () => {
    const css = readFileSync(join(srcRoot, 'styles/tokens.css'), 'utf8')
    for (const [name, value] of Object.entries(tokens.color)) {
      const kebab = name.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase()
      expect(css).toContain(`--acos-color-${kebab}: ${value};`)
    }
    expect(css).toContain(`--acos-layout-dialog-max-height-vh: ${tokens.layout.dialogMaxHeightVh}vh;`)
  })

  it('exposes the control heights used by the 32/40/48 contract', () => {
    const css = readFileSync(join(srcRoot, 'styles/tokens.css'), 'utf8')
    const small = tokens.control.small as { height: number }
    const medium = tokens.control.medium as { height: number }
    const large = tokens.control.large as { height: number }
    expect(small.height).toBe(32)
    expect(medium.height).toBe(40)
    expect(large.height).toBe(48)
    expect(css).toContain(`--acos-control-small-height: ${small.height}px;`)
    expect(css).toContain(`--acos-control-medium-height: ${medium.height}px;`)
    expect(css).toContain(`--acos-control-large-height: ${large.height}px;`)
  })

  it('has no hardcoded hex colours outside generated styles', () => {
    const offenders: string[] = []
    for (const file of sourceFiles) {
      const content = readFileSync(file, 'utf8')
      const matches = content.match(/#[0-9a-fA-F]{3,8}\b/g)
      if (matches) offenders.push(`${file}: ${matches.join(', ')}`)
    }
    expect(offenders).toEqual([])
  })

  it('has no hardcoded control heights in component styles', () => {
    const offenders: string[] = []
    for (const file of sourceFiles.filter((f) => f.endsWith('.vue'))) {
      const content = readFileSync(file, 'utf8')
      // height: 40px / min-height: 44px style literals
      const matches = content.match(/(?:min-)?height:\s*\d+px/g)
      if (matches) offenders.push(`${file}: ${matches.join(', ')}`)
    }
    expect(offenders).toEqual([])
  })

  it('every --acos-* custom property used in components is defined', () => {
    const defined = new Set<string>()
    const tokensCss = readFileSync(join(srcRoot, 'styles/tokens.css'), 'utf8')
    for (const match of tokensCss.matchAll(/(--acos-[a-z0-9-]+):/g)) {
      defined.add(match[1] as string)
    }
    // A few aliases are intentionally declared per component scope.
    const local = new Set<string>()
    const missing = new Set<string>()

    for (const file of sourceFiles) {
      const content = readFileSync(file, 'utf8')
      for (const match of content.matchAll(/var\((--acos-[a-z0-9-]+)/g)) {
        const name = match[1] as string
        if (!defined.has(name) && !local.has(name)) missing.add(`${name} (${file})`)
      }
    }
    expect([...missing]).toEqual([])
  })
})
