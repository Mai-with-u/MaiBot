function hslToHex(hsl: string): string {
  if (!hsl) return '#000000'
  
  // 解析 "221.2 83.2% 53.3%" 格式
  const parts = hsl.split(' ').filter(Boolean)
  if (parts.length < 3) return '#000000'
  
  const h = parseFloat(parts[0])
  const s = parseFloat(parts[1].replace('%', ''))
  const l = parseFloat(parts[2].replace('%', ''))
  
  const sDecimal = s / 100
  const lDecimal = l / 100
  
  const c = (1 - Math.abs(2 * lDecimal - 1)) * sDecimal
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = lDecimal - c / 2
  
  let r = 0, g = 0, b = 0
  
  if (h >= 0 && h < 60) { r = c; g = x; b = 0 }
  else if (h >= 60 && h < 120) { r = x; g = c; b = 0 }
  else if (h >= 120 && h < 180) { r = 0; g = c; b = x }
  else if (h >= 180 && h < 240) { r = 0; g = x; b = c }
  else if (h >= 240 && h < 300) { r = x; g = 0; b = c }
  else if (h >= 300 && h < 360) { r = c; g = 0; b = x }
  
  const toHex = (n: number) => {
    const hex = Math.round((n + m) * 255).toString(16)
    return hex.length === 1 ? '0' + hex : hex
  }
  
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`
}

type LibraryItemProps = {
  name: string
  description: string
  license: string
}

type ThemeOptionProps = {
  value: 'light' | 'dark' | 'system'
  current: 'light' | 'dark' | 'system'
  onChange: (theme: 'light' | 'dark' | 'system') => void
  label: string
  description: string
}

export { hslToHex }
export type { LibraryItemProps, ThemeOptionProps }
