/**
 * theme/sanitizer 单元测试：验证 CSS 危险模式过滤与安全检查。
 *
 * 注意：过滤规则的正则均带 g 标志，lastIndex 会在调用之间残留，
 * 因此每个用例通过 vi.resetModules + 动态 import 获取干净的模块实例。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

type SanitizerModule = typeof import('../sanitizer')

let sanitizeCSS: SanitizerModule['sanitizeCSS']
let isCSSSafe: SanitizerModule['isCSSSafe']

beforeEach(async () => {
  vi.resetModules()
  const mod = await import('../sanitizer')
  sanitizeCSS = mod.sanitizeCSS
  isCSSSafe = mod.isCSSSafe
})

describe('sanitizeCSS', () => {
  it('安全 CSS 原样保留且不产生警告', () => {
    const raw = '.card { color: red; }'
    const result = sanitizeCSS(raw)

    expect(result.css).toBe(raw)
    expect(result.warnings).toEqual([])
  })

  it('移除 @import 语句并给出对应警告', () => {
    const result = sanitizeCSS('@import url("https://evil.com/x.css");')

    expect(result.css).toBe('')
    expect(result.warnings).toEqual(['Line 1: 移除 @import 语句（禁止加载外部资源）'])
  })

  it('移除加载外部资源的 url() 调用', () => {
    const result = sanitizeCSS('.bg { background: url(https://evil.com/a.png); }')

    expect(result.css).not.toContain('evil.com')
    expect(result.warnings).toEqual(['Line 1: 移除 url() 调用（禁止外部请求）'])
  })

  it('移除 data: 协议的 url() 调用', () => {
    const result = sanitizeCSS('.icon { background: url(data:image/png;base64,AAAA); }')

    expect(result.css).not.toContain('data:')
    expect(result.warnings).toEqual(['Line 1: 移除 url() 调用（禁止外部请求）'])
  })

  it('站内相对路径的 url() 不受影响', () => {
    const raw = '.bg { background: url(/assets/a.png); }'
    const result = sanitizeCSS(raw)

    expect(result.css).toBe(raw)
    expect(result.warnings).toEqual([])
  })

  it('移除 javascript: 协议片段', () => {
    const result = sanitizeCSS('.a { content: "javascript:alert(1)"; }')

    expect(result.css).not.toContain('javascript:')
    expect(result.css).toContain('alert(1)')
    expect(result.warnings).toEqual(['Line 1: 移除 javascript: 协议（XSS 防护）'])
  })

  it('移除 expression() 函数', () => {
    const result = sanitizeCSS('.a { width: expression(alert(1)); }')

    expect(result.css).not.toContain('expression')
    expect(result.warnings).toEqual(['Line 1: 移除 expression() 函数（IE 遗留 XSS 向量）'])
  })

  it('移除 -moz-binding 属性', () => {
    const result = sanitizeCSS('.a { -moz-binding: url("http://evil/x.xml"); }')

    expect(result.css).not.toContain('-moz-binding')
    expect(result.warnings).toEqual(['Line 1: 移除 -moz-binding 属性（Firefox XSS 向量）'])
  })

  it('移除 behavior: 属性', () => {
    const result = sanitizeCSS('.a { behavior: url(#default#time2); }')

    expect(result.css).not.toContain('behavior')
    expect(result.warnings).toEqual(['Line 1: 移除 behavior: 属性（IE HTC）'])
  })

  it('多行输入时警告标注正确行号并清理产生的空行', () => {
    const raw = [
      '.safe { color: red; }',
      '',
      '@import "https://evil.com/theme.css";',
      '.after { margin: 0; }',
    ].join('\n')

    const result = sanitizeCSS(raw)

    expect(result.warnings).toEqual(['Line 3: 移除 @import 语句（禁止加载外部资源）'])
    // 被移除的行与原有空行会被压缩，只留下安全内容
    expect(result.css).toBe('.safe { color: red; }\n.after { margin: 0; }')
  })
})

describe('isCSSSafe', () => {
  it('安全 CSS 返回 true', () => {
    expect(isCSSSafe('.a { color: red; background: url(/img/a.png); }')).toBe(true)
  })

  it('包含 @import 时返回 false', () => {
    expect(isCSSSafe('@import "https://evil.com/x.css";')).toBe(false)
  })

  it('包含外部 url() 时返回 false', () => {
    expect(isCSSSafe('.a { background: url(https://evil.com/a.png); }')).toBe(false)
  })

  it('包含 expression() 时返回 false', () => {
    expect(isCSSSafe('.a { width: expression(alert(1)); }')).toBe(false)
  })
})
