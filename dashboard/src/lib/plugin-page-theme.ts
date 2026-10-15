/** Only trusted host component appearance crosses the sandbox, never host DOM or code. */
const APPEARANCE = [
  'background-color',
  'background-image',
  'background-size',
  'color',
  'border',
  'border-radius',
  'box-shadow',
  'font-family',
  'font-weight',
  'font-size',
  'line-height',
] as const

export interface PluginPageContext {
  theme: string
  language: string
  dashboardStyle: string
  styles: string
}

export interface PluginPageFont {
  id: string
  css: string
}
const fonts = new Map<string, Promise<PluginPageFont>>()
const FONT_FILES = [
  ['Departure Mono', 'DepartureMono-Regular.woff2', ''],
  ['Ark Pixel 12px SC', 'ArkPixel12-ZhHans.woff2', 'unicode-range:U+3000-9FFF,U+FF00-FFEF;'],
  [
    'Jersey 20',
    'Jersey20-Regular.ttf',
    'size-adjust:118%;unicode-range:U+0000-024F,U+2000-206F,U+20A0-20CF,U+2190-21FF;',
  ],
  ['JetBrains Mono', 'JetBrainsMono-Medium.ttf', 'font-weight:500;'],
] as const

/** Fixed host assets only; cache across pages and bound payloads without relaxing font-src. */
export async function pluginPageFonts(family: string): Promise<PluginPageFont[]> {
  const selected = FONT_FILES.filter(([name]) => family.includes(name))
  return Promise.all(
    selected.map(([name, file, extra]) => {
      if (!fonts.has(file))
        fonts.set(
          file,
          fetch(`/fonts/${file}`).then(async (response) => {
            if (!response.ok) throw new Error(`Theme font unavailable: ${file}`)
            const blob = await response.blob()
            if (blob.size > 2 * 1024 * 1024) throw new Error('Theme font exceeds 2 MiB')
            return new Promise<PluginPageFont>((resolve, reject) => {
              const reader = new FileReader()
              reader.onload = () =>
                resolve({
                  id: file,
                  css: `@font-face{font-family:"${name}";src:url("${reader.result}");font-display:swap;${extra}}`,
                })
              reader.onerror = () => reject(reader.error)
              reader.readAsDataURL(blob)
            })
          })
        )
      return fonts.get(file)!
    })
  )
}

export function readPluginPageTheme(probes: HTMLElement, language: string): PluginPageContext {
  // Sampling animated colors would freeze an intermediate frame in the child.
  const samples: Record<string, CSSStyleDeclaration> = {}
  for (const probe of probes.querySelectorAll<HTMLElement>('[data-theme-probe]')) {
    if (probe.style.transition !== 'none')
      probe.style.setProperty('transition', 'none', 'important')
    if (probe.style.animation !== 'none') probe.style.setProperty('animation', 'none', 'important')
    samples[probe.dataset.themeProbe!] = getComputedStyle(probe)
  }
  const root = document.documentElement
  const style = getComputedStyle(root)
  const { card, input, primary, muted } = samples
  const dashboardStyle = root.dataset.dashboardStyle ?? 'modern'
  const variable = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback
  const background =
    dashboardStyle === 'millennium'
      ? variable('--mil-shell', card.backgroundColor)
      : dashboardStyle === 'future-retro'
        ? variable('--retro-paper', card.backgroundColor)
        : samples.background.backgroundColor
  const texture =
    dashboardStyle === 'millennium'
      ? variable('--mil-speck', 'none')
      : dashboardStyle === 'future-retro'
        ? variable('--retro-paper-texture', 'none')
        : 'none'
  const tokens = {
    background,
    surface: card.backgroundColor,
    foreground: card.color,
    muted: muted.color,
    soft:
      input.backgroundColor === 'rgba(0, 0, 0, 0)'
        ? samples.soft.backgroundColor
        : input.backgroundColor,
    border: card.borderTopColor,
    primary: primary.backgroundColor,
    'primary-foreground': primary.color,
    danger: samples.danger.backgroundColor,
    radius: card.borderRadius,
    'font-body': card.fontFamily,
    'font-display': variable(
      dashboardStyle === 'millennium' ? '--mil-font-pixel' : '--retro-font-heading',
      card.fontFamily
    ),
    'font-mono': variable('--typography-font-family-code', 'ui-monospace, monospace'),
  }
  let css = `:root{${Object.entries(tokens)
    .map(([key, value]) => `--maibot-${key}:${value};`)
    .join('')}}`
  // Selectors are fixed by the host. Styles are sampled after the active theme has resolved.
  const rules: Record<string, string> = {
    button: '.maibot-button',
    primary: '.maibot-button.maibot-button-primary,.maibot-button[aria-pressed="true"]',
    danger: '.maibot-button.maibot-button-danger',
    card: '.maibot-card',
    input: '.maibot-input,.maibot-select,.maibot-inset',
  }
  for (const [name, selector] of Object.entries(rules)) {
    const sampled = samples[name]
    css += `${selector}{${APPEARANCE.map((property) => `${property}:${sampled.getPropertyValue(property)};`).join('')}}`
  }
  css += `
  .maibot-page{margin:0;color:var(--maibot-foreground);background-color:var(--maibot-background);background-image:${texture};background-size:${dashboardStyle === 'millennium' ? '6px 6px' : variable('--retro-paper-texture-size', 'auto')};font-family:var(--maibot-font-body)}
  .maibot-button{padding:8px 16px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:8px}
  .maibot-button:disabled{opacity:.5;cursor:not-allowed}
  .maibot-button:hover:not(:disabled){filter:brightness(.96)}
  :is(.maibot-button,.maibot-input,.maibot-select):focus-visible{outline:2px solid var(--maibot-primary);outline-offset:3px}
  .maibot-card{padding:20px}.maibot-input,.maibot-select{padding:8px 12px;font:inherit}
  .maibot-card-header{padding-bottom:12px;margin-bottom:16px;border-bottom:1px solid var(--maibot-border);font-weight:600}
  .maibot-badge{display:inline-flex;padding:3px 8px;border:1px solid var(--maibot-border);border-radius:var(--maibot-radius);background:var(--maibot-soft);color:var(--maibot-foreground)}
  .maibot-table{width:100%;border-collapse:collapse;text-align:left}.maibot-table :is(th,td){padding:10px 12px;border-bottom:1px solid var(--maibot-border)}.maibot-table th{color:var(--maibot-muted)}
  .maibot-muted{color:var(--maibot-muted)}.maibot-number{font-family:var(--maibot-font-display);font-synthesis:none}
  @font-face{font-family:MaiRetroText;src:local('Bahnschrift'),local('Microsoft YaHei UI');font-display:swap}
  `
  return {
    theme: root.classList.contains('dark') ? 'dark' : 'light',
    language,
    dashboardStyle,
    styles: css,
  }
}
