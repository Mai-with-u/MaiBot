import { describe, expect, it } from 'vitest'

import { hslToHex } from '../types'

describe('hslToHex', () => {
  it('空串、不足三段或仅空白时回退为黑色', () => {
    expect(hslToHex('')).toBe('#000000')
    expect(hslToHex('120 50%')).toBe('#000000')
    expect(hslToHex('   ')).toBe('#000000')
  })

  it.each([
    { hsl: '0 100% 50%', hex: '#ff0000' },
    { hsl: '30 100% 50%', hex: '#ff8000' },
    { hsl: '90 100% 50%', hex: '#80ff00' },
    { hsl: '150 100% 50%', hex: '#00ff80' },
    { hsl: '210 100% 50%', hex: '#0080ff' },
    { hsl: '270 100% 50%', hex: '#8000ff' },
    { hsl: '330 100% 50%', hex: '#ff0080' },
  ])('色相区间 $hsl 转换为 $hex', ({ hsl, hex }) => {
    expect(hslToHex(hsl)).toBe(hex)
  })

  it('色相边界落入对应区间', () => {
    expect(hslToHex('60 100% 50%')).toBe('#ffff00')
    expect(hslToHex('120 100% 50%')).toBe('#00ff00')
    expect(hslToHex('180 100% 50%')).toBe('#00ffff')
    expect(hslToHex('240 100% 50%')).toBe('#0000ff')
    expect(hslToHex('300 100% 50%')).toBe('#ff00ff')
  })

  it('色相越界时不着色，按明度回退灰度', () => {
    expect(hslToHex('360 100% 50%')).toBe('#000000')
    expect(hslToHex('-10 100% 50%')).toBe('#000000')
  })

  it('过滤多余空格后仍能解析', () => {
    expect(hslToHex('  30  100%  50%  ')).toBe('#ff8000')
  })

  it('单数字 hex 会补零', () => {
    expect(hslToHex('0 0% 0.2%')).toBe('#010101')
  })

  it('无饱和度时按明度输出灰阶', () => {
    expect(hslToHex('0 0% 0%')).toBe('#000000')
    expect(hslToHex('0 0% 100%')).toBe('#ffffff')
    expect(hslToHex('0 0% 50%')).toBe('#808080')
  })
})
