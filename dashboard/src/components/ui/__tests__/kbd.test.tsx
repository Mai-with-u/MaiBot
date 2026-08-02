import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Kbd, ShortcutKbd } from '../kbd'

/**
 * 覆盖 navigator.platform，使 getShortcutKeyLabel 的平台分支可控。
 * jsdom 的 platform 定义在 Navigator 原型上，这里在实例上定义可配置的
 * 自有属性进行遮蔽，用完后删除即可恢复原型行为。
 */
function mockPlatform(value: string) {
  Object.defineProperty(navigator, 'platform', { value, configurable: true })
}

afterEach(() => {
  Reflect.deleteProperty(navigator, 'platform')
})

describe('Kbd', () => {
  it('渲染为 kbd 元素并使用默认尺寸', () => {
    render(<Kbd data-testid="kbd">K</Kbd>)

    const kbd = screen.getByTestId('kbd')
    expect(kbd.tagName).toBe('KBD')
    expect(kbd).toHaveTextContent('K')
    // 默认 size=default 对应 h-6 text-xs
    expect(kbd).toHaveClass('h-6', 'text-xs')
  })

  it('支持 sm 与 lg 尺寸变体', () => {
    const { rerender } = render(<Kbd data-testid="kbd" size="sm">K</Kbd>)

    const kbd = screen.getByTestId('kbd')
    expect(kbd).toHaveClass('h-5')
    expect(kbd).not.toHaveClass('h-6')

    rerender(<Kbd data-testid="kbd" size="lg">K</Kbd>)
    expect(kbd).toHaveClass('h-7', 'text-sm')
  })

  it('传入 abbrTitle 时用 abbr 包裹内容并附带 title', () => {
    render(<Kbd abbrTitle="Command">⌘</Kbd>)

    const abbr = screen.getByTitle('Command')
    expect(abbr.tagName).toBe('ABBR')
    expect(abbr).toHaveTextContent('⌘')
  })

  it('未传 abbrTitle 时不渲染 abbr 元素', () => {
    render(<Kbd data-testid="kbd">Esc</Kbd>)

    expect(screen.getByTestId('kbd').querySelector('abbr')).toBeNull()
  })

  it('合并自定义 className', () => {
    render(<Kbd data-testid="kbd" className="ml-2">K</Kbd>)

    expect(screen.getByTestId('kbd')).toHaveClass('ml-2', 'font-mono')
  })
})

describe('ShortcutKbd', () => {
  it('Mac 平台下 mod 显示 ⌘ 并带 Command 无障碍说明', () => {
    mockPlatform('MacIntel')
    render(<ShortcutKbd keys={['mod', 'k']} />)

    // mod 键：标签为 ⌘，abbr title 为 Command
    const modAbbr = screen.getByTitle('Command')
    expect(modAbbr).toHaveTextContent('⌘')
    // 单字符键位转为大写，且不包 abbr
    const plainKey = screen.getByText('K')
    expect(plainKey.tagName).toBe('KBD')
    expect(plainKey.querySelector('abbr')).toBeNull()
  })

  it('非 Mac 平台下 mod 显示 Ctrl 并带 Control 无障碍说明', () => {
    mockPlatform('Win32')
    render(<ShortcutKbd keys={['mod', 'shift']} />)

    expect(screen.getByTitle('Control')).toHaveTextContent('Ctrl')
    expect(screen.getByText('Shift')).toBeInTheDocument()
  })

  it('平台无关键位按映射表渲染且不带 abbr', () => {
    mockPlatform('Win32')
    render(<ShortcutKbd keys={['up', 'esc']} />)

    expect(screen.getByText('↑')).toBeInTheDocument()
    expect(screen.getByText('Esc')).toBeInTheDocument()
    expect(screen.queryByTitle('Control')).toBeNull()
    expect(screen.queryByTitle('Command')).toBeNull()
  })

  it('size 属性会透传到每个 Kbd 子项', () => {
    mockPlatform('Win32')
    render(<ShortcutKbd keys={['esc', 'enter']} size="sm" />)

    expect(screen.getByText('Esc')).toHaveClass('h-5')
    expect(screen.getByText('Enter')).toHaveClass('h-5')
  })
})
