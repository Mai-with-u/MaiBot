import type { ColorTokens } from './tokens'

type HSL = {
  h: number
  s: number
  l: number
}

const clamp = (value: number, min: number, max: number): number => {
  if (value < min) return min
  if (value > max) return max
  return value
}

const roundToTenth = (value: number): number => Math.round(value * 10) / 10

const wrapHue = (value: number): number => ((value % 360) + 360) % 360

export const parseHSL = (hslStr: string): HSL => {
  const cleaned = hslStr
    .trim()
    .replace(/^hsl\(/i, '')
    .replace(/\)$/i, '')
    .replace(/,/g, ' ')
  const parts = cleaned.split(/\s+/).filter(Boolean)
  const rawH = parts[0] ?? '0'
  const rawS = parts[1] ?? '0%'
  const rawL = parts[2] ?? '0%'

  const h = Number.parseFloat(rawH)
  const s = Number.parseFloat(rawS.replace('%', ''))
  const l = Number.parseFloat(rawL.replace('%', ''))

  return {
    h: Number.isNaN(h) ? 0 : h,
    s: Number.isNaN(s) ? 0 : s,
    l: Number.isNaN(l) ? 0 : l,
  }
}

export const formatHSL = (h: number, s: number, l: number): string => {
  const safeH = roundToTenth(wrapHue(h))
  const safeS = roundToTenth(clamp(s, 0, 100))
  const safeL = roundToTenth(clamp(l, 0, 100))
  return `${safeH} ${safeS}% ${safeL}%`
}

export const hexToHSL = (hex: string): string => {
  let cleaned = hex.trim().replace('#', '')
  if (cleaned.length === 3) {
    cleaned = cleaned
      .split('')
      .map((char) => `${char}${char}`)
      .join('')
  }

  if (cleaned.length !== 6) {
    return formatHSL(0, 0, 0)
  }

  const r = Number.parseInt(cleaned.slice(0, 2), 16) / 255
  const g = Number.parseInt(cleaned.slice(2, 4), 16) / 255
  const b = Number.parseInt(cleaned.slice(4, 6), 16) / 255

  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const delta = max - min
  const l = (max + min) / 2

  let h = 0
  let s = 0

  if (delta !== 0) {
    s = l > 0.5 ? delta / (2 - max - min) : delta / (max + min)
    switch (max) {
      case r:
        h = (g - b) / delta + (g < b ? 6 : 0)
        break
      case g:
        h = (b - r) / delta + 2
        break
      case b:
        h = (r - g) / delta + 4
        break
      default:
        break
    }
    h *= 60
  }

  return formatHSL(h, s * 100, l * 100)
}

export const adjustLightness = (hsl: string, amount: number): string => {
  const { h, s, l } = parseHSL(hsl)
  return formatHSL(h, s, l + amount)
}

export const adjustSaturation = (hsl: string, amount: number): string => {
  const { h, s, l } = parseHSL(hsl)
  return formatHSL(h, s + amount, l)
}

export const rotateHue = (hsl: string, degrees: number): string => {
  const { h, s, l } = parseHSL(hsl)
  return formatHSL(h + degrees, s, l)
}

const setLightness = (hsl: string, lightness: number): string => {
  const { h, s } = parseHSL(hsl)
  return formatHSL(h, s, lightness)
}

const setSaturation = (hsl: string, saturation: number): string => {
  const { h, l } = parseHSL(hsl)
  return formatHSL(h, saturation, l)
}

const getReadableForeground = (hsl: string): string => {
  const { h, s, l } = parseHSL(hsl)
  const neutralSaturation = clamp(s * 0.15, 6, 20)
  return l > 60
    ? formatHSL(h, neutralSaturation, 10)
    : formatHSL(h, neutralSaturation, 96)
}

export const generatePalette = (accentHSL: string, isDark: boolean): ColorTokens => {
  const accent = parseHSL(accentHSL)
  const primary = formatHSL(accent.h, accent.s, accent.l)

  const background = isDark ? '222.2 84% 4.9%' : '0 0% 100%'
  const foreground = isDark ? '210 40% 98%' : '222.2 84% 4.9%'

  const secondary = formatHSL(
    accent.h,
    clamp(accent.s * 0.35, 8, 40),
    isDark ? 17.5 : 96,
  )

  const muted = formatHSL(
    accent.h,
    clamp(accent.s * 0.12, 2, 18),
    isDark ? 17.5 : 96,
  )

  const accentVariant = formatHSL(
    accent.h + 35,
    clamp(accent.s * 0.6, 20, 85),
    isDark ? clamp(accent.l * 0.6 + 8, 25, 60) : clamp(accent.l * 0.8 + 14, 40, 75),
  )

  const destructive = formatHSL(
    0,
    clamp(accent.s, 60, 90),
    isDark ? 30.6 : 60.2,
  )

  const border = formatHSL(
    accent.h,
    clamp(accent.s * 0.2, 5, 25),
    isDark ? 17.5 : 91.4,
  )

  const mutedForeground = setSaturation(
    setLightness(muted, isDark ? 65.1 : 46.9),
    clamp(accent.s * 0.2, 10, 30),
  )

  const chartBase = formatHSL(accent.h, accent.s, accent.l)
  const chartSteps = [0, 72, 144, 216, 288]
  const charts = chartSteps.map((step) => rotateHue(chartBase, step))

  const card = adjustLightness(background, isDark ? 2 : -1)
  const popover = adjustLightness(background, isDark ? 3 : -0.5)

  return {
    primary,
    'primary-foreground': getReadableForeground(primary),
    'primary-gradient': 'none',
    secondary,
    'secondary-foreground': getReadableForeground(secondary),
    muted,
    'muted-foreground': mutedForeground,
    accent: accentVariant,
    'accent-foreground': getReadableForeground(accentVariant),
    destructive,
    'destructive-foreground': getReadableForeground(destructive),
    background,
    foreground,
    card,
    'card-foreground': foreground,
    popover,
    'popover-foreground': foreground,
    border,
    input: border,
    ring: primary,
    'chart-1': charts[0],
    'chart-2': charts[1],
    'chart-3': charts[2],
    'chart-4': charts[3],
    'chart-5': charts[4],
  }
}
