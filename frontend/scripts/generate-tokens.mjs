/**
 * Generates design token artifacts from docs/design/design-tokens.json.
 *
 * The JSON file is the single source of truth for visual values. This script
 * produces:
 *   1. src/styles/tokens.css          -- CSS custom properties for layout/type
 *   2. src/styles/element-theme.css   -- Element Plus variable mapping
 *
 * Run via `pnpm tokens` (also part of `pnpm build`). A test asserts the
 * generated files are current, so editing the JSON without regenerating fails
 * CI rather than silently drifting.
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(here, '../..')
// Inside the Docker build the repository layout differs, so an explicit path
// can be supplied instead of relying on relative traversal.
const tokenPath =
  process.env.DESIGN_TOKENS_PATH ??
  resolve(repoRoot, 'docs/design/design-tokens.json')
const tokens = JSON.parse(readFileSync(tokenPath, 'utf8'))

const {
  color,
  spacing,
  font,
  radius,
  control,
  layout,
  motion,
  focus,
  zIndex,
  shadow,
} = tokens

const px = (value) => `${value}px`

const colorVars = Object.entries(color)
  .map(([name, value]) => `  --acos-color-${kebab(name)}: ${value};`)
  .join('\n')

const fontVars = Object.entries(font)
  .filter(([, value]) => typeof value === 'object')
  .map(
    ([name, value]) =>
      `  --acos-font-${kebab(name)}-size: ${px(value.size)};\n` +
      `  --acos-font-${kebab(name)}-line: ${px(value.line)};\n` +
      `  --acos-font-${kebab(name)}-weight: ${value.weight};`,
  )
  .join('\n')

const radiusVars = Object.entries(radius)
  .map(([name, value]) => `  --acos-radius-${kebab(name)}: ${px(value)};`)
  .join('\n')

const controlVars = Object.entries(control)
  .map(([name, value]) => {
    if (typeof value === 'object') {
      const inner = Object.entries(value)
        .map(
          ([k, v]) => `  --acos-control-${kebab(name)}-${kebab(k)}: ${px(v)};`,
        )
        .join('\n')
      return inner
    }
    return `  --acos-control-${kebab(name)}: ${px(value)};`
  })
  .join('\n')

const layoutVars = Object.entries(layout)
  .map(
    ([name, value]) =>
      `  --acos-layout-${kebab(name)}: ${name === 'dialogMaxHeightVh' ? `${value}vh` : px(value)};`,
  )
  .join('\n')

const spacingVars = spacing
  .map((value) => `  --acos-space-${value}: ${px(value)};`)
  .join('\n')

const motionVars = Object.entries(motion)
  .map(([name, value]) => `  --acos-motion-${kebab(name)}: ${value}ms;`)
  .join('\n')

const focusVars =
  `  --acos-focus-width: ${px(focus.width)};\n` +
  `  --acos-focus-offset: ${px(focus.offset)};`

const zIndexVars = Object.entries(zIndex)
  .map(([name, value]) => `  --acos-z-${kebab(name)}: ${value};`)
  .join('\n')

const shadowVars = Object.entries(shadow)
  .map(([name, value]) => `  --acos-shadow-${kebab(name)}: ${value};`)
  .join('\n')

const tokensCss = `/*
 * GENERATED FILE -- do not edit by hand.
 * Source: docs/design/design-tokens.json (v${tokens.version})
 * Regenerate with: pnpm tokens
 */

:root {
  color-scheme: light;

${colorVars}

${spacingVars}

  --acos-font-family: ${font.family};
  --acos-font-mono: ${font.mono};

${fontVars}

${radiusVars}

${controlVars}

${layoutVars}

${motionVars}

${focusVars}

${zIndexVars}

${shadowVars}
}
`

const elementThemeCss = `/*
 * GENERATED FILE -- do not edit by hand.
 * Element Plus variable mapping. Source: docs/design/design-tokens.json
 * Regenerate with: pnpm tokens
 *
 * Only semantic tokens are mapped here. Element Plus component internals keep
 * their own defaults; project controls use AppButton/AppInput wrappers so the
 * 32/40/48px sizes come from the tokens, not from Element defaults.
 */

:root {
  --el-color-primary: ${color.primary};
  --el-color-primary-light-3: ${color.primaryHover};
  --el-color-primary-light-5: ${color.primaryHover};
  --el-color-primary-light-7: ${color.primarySubtle};
  --el-color-primary-light-8: ${color.primarySubtle};
  --el-color-primary-light-9: ${color.primarySubtle};
  --el-color-primary-dark-2: ${color.primaryActive};

  --el-color-success: ${color.success};
  --el-color-success-light-9: ${color.successSubtle};
  --el-color-warning: ${color.warning};
  --el-color-warning-light-9: ${color.warningSubtle};
  --el-color-danger: ${color.danger};
  --el-color-danger-light-9: ${color.dangerSubtle};
  --el-color-error: ${color.danger};
  --el-color-info: ${color.neutral};
  --el-color-info-light-9: ${color.neutralSubtle};

  --el-text-color-primary: ${color.text};
  --el-text-color-regular: ${color.textSecondary};
  --el-text-color-secondary: ${color.textMuted};
  --el-text-color-placeholder: ${color.textMuted};
  --el-text-color-disabled: ${color.disabledText};

  --el-bg-color: ${color.surface};
  --el-bg-color-page: ${color.bg};
  --el-bg-color-overlay: ${color.surface};

  --el-border-color: ${color.border};
  --el-border-color-light: ${color.border};
  --el-border-color-lighter: ${color.border};
  --el-border-color-extra-light: ${color.surfaceSubtle};
  --el-border-color-dark: ${color.controlBorder};
  --el-border-color-darker: ${color.controlBorder};

  --el-fill-color: ${color.surfaceSubtle};
  --el-fill-color-light: ${color.surfaceSubtle};
  --el-fill-color-blank: ${color.surface};
  --el-fill-color-lighter: ${color.surfaceSubtle};

  --el-border-radius-base: ${px(radius.control)};
  --el-border-radius-small: ${px(radius.control)};
  --el-border-radius-round: ${px(radius.pill)};

  --el-font-family: ${font.family};
  --el-font-size-base: ${px(font.body.size)};
  --el-text-color-disabled-fallback: ${color.disabledText};

  --el-box-shadow-light: ${shadow.card};
  --el-box-shadow: ${shadow.floating};
  --el-mask-color: ${color.overlay};

  --el-transition-duration: ${motion.normal}ms;
  --el-transition-duration-fast: ${motion.fast}ms;

  --el-component-size: ${px(control.medium.height)};
  --el-component-size-small: ${px(control.small.height)};
  --el-component-size-large: ${px(control.large.height)};
}
`

function kebab(value) {
  return value.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase()
}

mkdirSync(resolve(here, '../src/styles'), { recursive: true })
writeFileSync(resolve(here, '../src/styles/tokens.css'), tokensCss, 'utf8')
writeFileSync(
  resolve(here, '../src/styles/element-theme.css'),
  elementThemeCss,
  'utf8',
)

// A TypeScript mirror so component code can reference typed token groups.
const tokenTs = `/**
 * GENERATED FILE -- do not edit by hand.
 * Source: docs/design/design-tokens.json
 */

export const tokenVersion = ${JSON.stringify(tokens.version)}

export const controlSizes = {
  small: ${control.small.height},
  medium: ${control.medium.height},
  large: ${control.large.height},
  mobileTarget: ${control.mobileTarget},
  iconButton: ${control.iconButton},
  iconButtonSmall: ${control.iconButtonSmall},
} as const

export type ControlSize = 'small' | 'medium' | 'large'

export const layout = {
  sidebar: ${layout.sidebar},
  sidebarCollapsed: ${layout.sidebarCollapsed},
  topbar: ${layout.topbar},
  topbarMobile: ${layout.topbarMobile},
  desktopPadding: ${layout.desktopPadding},
  tabletPadding: ${layout.tabletPadding},
  mobilePadding: ${layout.mobilePadding},
  contentMax: ${layout.contentMax},
  formMax: ${layout.formMax},
  loginCard: ${layout.loginCard},
  loginPadding: ${layout.loginPadding},
  loginMobilePadding: ${layout.loginMobilePadding},
  breakpointMobile: ${layout.breakpointMobile},
  breakpointDesktop: ${layout.breakpointDesktop},
} as const

export const colorValues = ${JSON.stringify(tokens.color, null, 2)} as const

export const statusTone = ${JSON.stringify(tokens.status, null, 2)} as const

export type StatusTone = 'neutral' | 'info' | 'warning' | 'success' | 'danger'
`

writeFileSync(resolve(here, '../src/styles/tokens.ts'), tokenTs, 'utf8')

console.log('design tokens generated from docs/design/design-tokens.json')
