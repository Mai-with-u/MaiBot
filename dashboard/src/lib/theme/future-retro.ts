import type { FutureRetroTextureStyle } from './tokens'

function svgDataUrl(svg: string) {
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`
}

/**
 * 生成未来复古纸面纹理。纹理通过 CSS 变量应用到外壳、顶栏和浮层，
 * 因而切换配置时无需重建组件树。
 */
export function buildFutureRetroTexture(
  style: FutureRetroTextureStyle,
  intensity: number,
  dark: boolean
) {
  if (style === 'none' || intensity <= 0) {
    return 'none'
  }

  const strength = Math.max(0, Math.min(100, intensity)) / 100
  const ink = dark ? '#d2c0a9' : '#6b3b1c'
  const accent = dark ? '#c04d27' : '#c24d24'

  if (style === 'dot-grid') {
    return svgDataUrl(
      `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><circle cx="2" cy="2" r="0.8" fill="${ink}" opacity="${(0.34 * strength).toFixed(3)}"/></svg>`
    )
  }

  if (style === 'ruled') {
    return svgDataUrl(
      `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="28"><path d="M0 27.5H40" stroke="${ink}" stroke-width="0.7" opacity="${(0.28 * strength).toFixed(3)}"/></svg>`
    )
  }

  const coarse = style === 'coarse'
  const size = coarse ? 260 : 220
  const frequency = coarse ? '0.32' : '0.6'
  const octaves = 3
  const noiseOpacity = (strength * (coarse ? 0.28 : 0.29)).toFixed(3)
  const fleckOpacity = (strength * (coarse ? 0.2 : 0.18)).toFixed(3)

  return svgDataUrl(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"><filter id="n"><feTurbulence type="fractalNoise" baseFrequency="${frequency}" numOctaves="${octaves}" seed="11"/></filter><rect width="100%" height="100%" filter="url(#n)" opacity="${noiseOpacity}"/><g fill="${accent}" opacity="${fleckOpacity}"><circle cx="12" cy="21" r="${coarse ? 1.5 : 1.1}"/><circle cx="67" cy="43" r="${coarse ? 1.1 : 0.8}"/><circle cx="139" cy="16" r="${coarse ? 1.35 : 1}"/></g></svg>`
  )
}
